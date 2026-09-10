"""Fill and submit /contact forms across the transcription sites."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Iterable
from urllib.parse import urlparse

from playwright.sync_api import (
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

PageLike = Page | Frame

from sites import SITES, contact_url

ROOT = Path(__file__).resolve().parent
SCREENSHOTS_DIR = ROOT / "screenshots"
RESULTS_PATH = ROOT / "results.json"
EXTENSION_DIR = ROOT / "chrome-extension"
CHROME_PROFILE_DIR = ROOT / ".chrome-qa-profile"

TEST_NAME = "Mitra Brinda Mukherjee"
TEST_PHONE = "1234567890"
TEST_EMAIL = "mitra.b.mukherjee@steorasystems.com"
TEST_COMMENTS = "DEV TEAM TESTING"

NAV_TIMEOUT_MS = 30_000
FORM_TIMEOUT_MS = 20_000
SUBMIT_WAIT_MS = 15_000
SITE_DELAY_S = 2.0
PAUSE_SUBMIT_TIMEOUT_MS = 180_000
QA_SERVER_PORT = 8765

NAME_LABELS = ("Full Name", "Your Name", "First Name", "Name")
PHONE_LABELS = ("Phone Number", "Phone", "Telephone", "Mobile")
EMAIL_LABELS = ("Email Address", "E-mail", "Email")
COMMENT_LABELS = ("Additional Comments", "Your Message", "Comments", "Message")
SUBMIT_NAMES = ("Send Message", "Submit", "Send", "SUBMIT")
DROPDOWN_LABELS = (
    "Please Select Requirement",
    "Select Requirement",
    "Requirement",
)
THANK_YOU = re.compile(r"thank you", re.I)
SECURITY_FAIL = re.compile(r"security check failed", re.I)

CAPTCHA_SELECTORS = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    ".g-recaptcha",
    ".h-captcha",
    "#challenge-running",
    ".cf-challenge",
)

COOKIE_BUTTON_NAMES = (
    "Accept",
    "Accept All",
    "Accept Cookies",
    "I Agree",
    "Got it",
    "OK",
    "Allow",
)


@dataclass
class SiteResult:
    url: str
    status: str
    reason: str
    duration_s: float
    screenshot: str | None = None


class ContactSubmitError(Exception):
    """A recoverable per-site failure with a user-facing reason."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill (and optionally submit) /contact forms on the listed sites."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill fields but do not click submit.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    parser.add_argument(
        "--only",
        metavar="HOST",
        help="Run only sites whose hostname contains this string.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=SITE_DELAY_S,
        help=f"Seconds to wait between sites (default: {SITE_DELAY_S}).",
    )
    parser.add_argument(
        "--chrome",
        action="store_true",
        help="Launch installed Google Chrome instead of bundled Chromium.",
    )
    parser.add_argument(
        "--wait-security",
        action="store_true",
        help="If Turnstile fails, wait so you can complete the check and click SUBMIT again (use with --headed).",
    )
    parser.add_argument(
        "--real-chrome",
        action="store_true",
        help="Open real Chrome (QA profile). You click SUBMIT; the extension reports thank-you or failure.",
    )
    parser.add_argument(
        "--pause-submit",
        action="store_true",
        help="Same as --real-chrome.",
    )
    parser.add_argument(
        "--pause-timeout",
        type=float,
        default=PAUSE_SUBMIT_TIMEOUT_MS / 1000,
        help="Seconds to wait for thank-you or submit error per site (default: 180).",
    )
    return parser.parse_args(argv)


def selected_urls(only: str | None) -> list[str]:
    urls = [contact_url(site) for site in SITES]
    if not only:
        return urls
    needle = only.lower()
    matched = [url for url in urls if needle in (urlparse(url).hostname or "")]
    if not matched:
        raise SystemExit(f"No sites matched --only {only!r}")
    return matched


def find_chrome() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    named = shutil.which("chrome") or shutil.which("chrome.exe")
    if named and "WindowsApps" not in named:
        return Path(named)
    raise SystemExit(
        "Google Chrome was not found. Install Chrome, then rerun with --real-chrome."
    )


def same_contact_page(expected: str, reported: str) -> bool:
    exp = urlparse(expected)
    got = urlparse(reported or "")
    return (got.hostname or "").lower() == (exp.hostname or "").lower() and "contact" in (
        got.path or ""
    ).lower()


def open_in_chrome(chrome: Path, url: str) -> None:
    args = [
        str(chrome),
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(args, close_fds=True)


def start_qa_server(queue: Queue) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/result":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            queue.put(payload)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    server = ThreadingHTTPServer(("127.0.0.1", QA_SERVER_PORT), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def wait_for_extension_result(
    queue: Queue, expected_url: str, timeout_s: float
) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            item = queue.get(timeout=min(1.0, remaining))
        except Empty:
            continue
        if same_contact_page(expected_url, str(item.get("url") or "")):
            return item
    return None


def run_real_chrome(urls: list[str], timeout_s: float) -> list[SiteResult]:
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    queue: Queue = Queue()
    server = start_qa_server(queue)
    print(f"Opening real Chrome ({chrome})")
    print("Click SUBMIT on each page. The tab closes after thank you or a submit error.")
    print("If tabs do not auto-close, open chrome://extensions and click Reload on Contact form fill (QA).")
    results: list[SiteResult] = []
    try:
        for index, url in enumerate(urls):
            print(f"[{index + 1}/{len(urls)}] {url}")
            started = time.monotonic()
            open_in_chrome(chrome, url)
            payload = wait_for_extension_result(queue, url, timeout_s)
            duration = round(time.monotonic() - started, 2)
            if payload is None:
                result = SiteResult(
                    url=url,
                    status="failed",
                    reason=f"Timed out after {int(timeout_s)}s waiting for thank you or submit error",
                    duration_s=duration,
                )
            elif str(payload.get("status")) == "success":
                result = SiteResult(
                    url=url,
                    status="success",
                    reason=str(payload.get("reason") or "thank you"),
                    duration_s=duration,
                )
            else:
                result = SiteResult(
                    url=url,
                    status="failed",
                    reason=str(payload.get("reason") or "couldn't submit"),
                    duration_s=duration,
                )
            marker = "OK" if result.status == "success" else "FAIL"
            print(f"    {marker} — {result.reason} ({result.duration_s}s)")
            results.append(result)
            if index < len(urls) - 1:
                time.sleep(1.0)
    finally:
        server.shutdown()
    return results


def host_slug(url: str) -> str:
    host = urlparse(url).hostname or "unknown"
    return host.replace(".", "_")


def first_visible(locator: Locator) -> Locator | None:
    count = locator.count()
    for i in range(count):
        item = locator.nth(i)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def dismiss_cookie_banners(page: Page) -> None:
    for name in COOKIE_BUTTON_NAMES:
        button = page.get_by_role("button", name=re.compile(rf"^{name}$", re.I))
        visible = first_visible(button)
        if visible is None:
            continue
        try:
            visible.click(timeout=1500)
            page.wait_for_timeout(300)
            return
        except Exception:
            continue


def security_check_failure(page: PageLike) -> str | None:
    text = body_text(page)
    if SECURITY_FAIL.search(text) or "security check failed" in text.lower():
        return (
            "Cloudflare Turnstile rejected the submission (Security check failed). "
            "Playwright cannot bypass production Turnstile. Use Cloudflare dummy "
            "sitekeys on a test environment, or rerun with --headed --wait-security "
            "and complete the check yourself."
        )
    return None


def captcha_reason(page: Page) -> str | None:
    body = ""
    try:
        body = page.inner_text("body", timeout=2000)
    except Exception:
        pass
    lowered = body.lower()
    if "checking your browser" in lowered or "verify you are human" in lowered:
        return "CAPTCHA / bot challenge present"
    if "cloudflare" in lowered and ("challenge" in lowered or "attention required" in lowered):
        return "Cloudflare challenge present"
    for selector in CAPTCHA_SELECTORS:
        if first_visible(page.locator(selector)) is not None:
            return "CAPTCHA present"
    return None


def _hide_loading(page: PageLike) -> None:
    loading = page.get_by_text(re.compile(r"loading content", re.I))
    try:
        if loading.count() and loading.first.is_visible():
            loading.first.wait_for(state="hidden", timeout=FORM_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass


def _looks_like_contact_form(ctx: PageLike) -> bool:
    try:
        if labeled_control(ctx, NAME_LABELS, ("textbox",)) and labeled_control(
            ctx, EMAIL_LABELS, ("textbox",)
        ):
            return True
        return ctx.locator("textarea").count() > 0 and ctx.locator("input").count() >= 2
    except Exception:
        return False


def resolve_form_context(page: Page) -> PageLike:
    _hide_loading(page)
    deadline = time.monotonic() + FORM_TIMEOUT_MS / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if _looks_like_contact_form(page):
                return page
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                if _looks_like_contact_form(frame):
                    return frame
        except Exception as exc:
            last_error = exc
        page.wait_for_timeout(400)
    if last_error:
        raise ContactSubmitError(f"Contact form did not appear: {last_error}")
    raise ContactSubmitError("Contact form did not appear")


def _scopes(page: PageLike) -> list[PageLike | Locator]:
    scopes: list[PageLike | Locator] = []
    form = first_visible(page.locator("form"))
    if form is not None:
        scopes.append(form)
    scopes.append(page)
    return scopes


def labeled_control(page: PageLike, labels: Iterable[str], roles: tuple[str, ...]) -> Locator | None:
    for scope in _scopes(page):
        for label in labels:
            pattern = re.compile(re.escape(label), re.I)
            for role in roles:
                found = first_visible(scope.get_by_role(role, name=pattern))
                if found is not None:
                    return found
            found = first_visible(scope.get_by_label(pattern))
            if found is not None:
                return found
            found = first_visible(scope.get_by_placeholder(pattern))
            if found is not None:
                return found
            found = first_visible(scope.locator(f"text={label}").locator("xpath=following::input[1]"))
            if found is not None:
                return found
    return None


def fallback_input(page: PageLike, selectors: Iterable[str]) -> Locator | None:
    for scope in _scopes(page):
        for selector in selectors:
            found = first_visible(scope.locator(selector))
            if found is not None:
                return found
    return None


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value)


def _value_matches(expected: str, actual: str) -> bool:
    if actual.strip() == expected.strip():
        return True
    expected_digits = _digits(expected)
    actual_digits = _digits(actual)
    if expected_digits and expected_digits == actual_digits:
        return True
    return expected.strip().lower() in actual.strip().lower()


def fill_control(locator: Locator, value: str) -> Locator:
    target = locator
    locator.scroll_into_view_if_needed()
    try:
        locator.click(timeout=5000)
        locator.fill(value, timeout=5000)
    except Exception:
        inner = locator.locator("input, textarea, [contenteditable='true']").first
        inner.wait_for(state="visible", timeout=5000)
        inner.click(timeout=5000)
        inner.fill(value, timeout=5000)
        target = inner
    try:
        current = target.input_value(timeout=2000)
    except Exception:
        current = ""
    if not _value_matches(value, current):
        target.fill("")
        target.press_sequentially(value, delay=20)
        try:
            current = target.input_value(timeout=2000)
        except Exception:
            current = ""
        if not _value_matches(value, current):
            raise ContactSubmitError(f"Could not set field value (got {current!r})")
    return target


def find_name_field(page: PageLike) -> Locator | None:
    return labeled_control(page, NAME_LABELS, ("textbox",)) or fallback_input(
        page,
        (
            'input[name*="full" i][name*="name" i]',
            'input[autocomplete="name"]',
            'input[name*="name" i]:not([name*="user" i])',
            'input[id*="name" i]',
        ),
    )


def find_phone_field(page: PageLike) -> Locator | None:
    return labeled_control(page, PHONE_LABELS, ("textbox",)) or fallback_input(
        page,
        (
            'input[type="tel"]',
            'input[name*="phone" i]',
            'input[id*="phone" i]',
            'input[autocomplete="tel"]',
        ),
    )


def find_email_field(page: PageLike) -> Locator | None:
    return labeled_control(page, EMAIL_LABELS, ("textbox",)) or fallback_input(
        page,
        (
            'input[type="email"]',
            'input[name*="email" i]',
            'input[id*="email" i]',
            'input[autocomplete="email"]',
        ),
    )


def find_comments_field(page: PageLike) -> Locator | None:
    return labeled_control(page, COMMENT_LABELS, ("textbox",)) or fallback_input(
        page,
        (
            "textarea",
            '[contenteditable="true"]',
            'input[name*="comment" i]',
            'input[name*="message" i]',
        ),
    )


def find_submit_button(page: PageLike) -> Locator | None:
    for scope in _scopes(page):
        for name in SUBMIT_NAMES:
            pattern = re.compile(rf"^{name}$", re.I)
            found = first_visible(scope.get_by_role("button", name=pattern))
            if found is not None:
                return found
            found = first_visible(scope.locator(f'input[type="submit"][value="{name}" i]'))
            if found is not None:
                return found
        found = first_visible(scope.locator('button[type="submit"], input[type="submit"]'))
        if found is not None:
            return found
        found = first_visible(scope.get_by_role("button", name=re.compile(r"send|submit", re.I)))
        if found is not None:
            return found
        found = first_visible(scope.get_by_text(re.compile(r"^(submit|send message|send)$", re.I)))
        if found is not None:
            return found
    return None


def find_dropdown(page: PageLike) -> Locator | None:
    labeled = labeled_control(page, DROPDOWN_LABELS, ("combobox", "listbox"))
    if labeled is not None:
        return labeled
    wanted = re.compile(r"^others?$", re.I)
    for scope in _scopes(page):
        selects = scope.locator("select")
        for i in range(selects.count()):
            native = selects.nth(i)
            try:
                if not native.is_visible():
                    continue
            except Exception:
                continue
            labels = _option_labels(native)
            if any(wanted.match(label.strip()) for label in labels):
                return native
        combo = first_visible(
            scope.get_by_role("combobox", name=re.compile(r"requirement|please select", re.I))
        )
        if combo is not None:
            return combo
    return None


def option_values(select: Locator) -> list[str]:
    try:
        return select.evaluate(
            """el => Array.from(el.options || []).map(o => o.value)"""
        )
    except Exception:
        return []


def _option_labels(select: Locator) -> list[str]:
    try:
        return select.evaluate(
            """el => Array.from(el.options || []).map(o => (o.textContent || '').trim())"""
        )
    except Exception:
        return []


def select_others(page: PageLike, dropdown: Locator) -> str:
    dropdown.scroll_into_view_if_needed()
    tag = dropdown.evaluate("el => (el.tagName || '').toLowerCase()")
    wanted = re.compile(r"^others?$", re.I)
    if tag == "select":
        labels = _option_labels(dropdown)
        for i, label in enumerate(labels):
            if wanted.match(label.strip()):
                dropdown.select_option(index=i)
                return label.strip()
        values = option_values(dropdown)
        for value in values:
            if wanted.match(value.strip()):
                dropdown.select_option(value=value)
                return value
        raise ContactSubmitError(
            "Requirement option 'Others' not found. Options: " + ", ".join(labels)
        )

    dropdown.click(timeout=5000)
    page.wait_for_timeout(400)
    options = page.get_by_role("option")
    if options.count() == 0:
        options = page.locator('[role="option"], li[role="menuitem"], .MuiMenuItem-root')
    available: list[str] = []
    for i in range(options.count()):
        option = options.nth(i)
        try:
            if not option.is_visible():
                continue
        except Exception:
            continue
        text = (option.inner_text() or "").strip()
        if text:
            available.append(text)
        if wanted.match(text):
            option.click(timeout=5000)
            return text
    raise ContactSubmitError(
        "Requirement option 'Others' not found. Options: " + ", ".join(available)
    )


def visible_validation_text(page: PageLike) -> str:
    snippets: list[str] = []
    selectors = (
        '[role="alert"]',
        ".error",
        ".invalid-feedback",
        ".MuiFormHelperText-root",
        ".field-error",
        "[data-error]",
    )
    for selector in selectors:
        loc = page.locator(selector)
        for i in range(min(loc.count(), 8)):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    text = item.inner_text().strip()
                    if text:
                        snippets.append(text)
            except Exception:
                continue
    invalid = page.locator(":invalid")
    if invalid.count():
        snippets.append("HTML5 validation blocked submit")
    return " | ".join(dict.fromkeys(snippets))


def body_text(page: PageLike) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def thank_you_contexts(text: str) -> set[str]:
    contexts: set[str] = set()
    for match in THANK_YOU.finditer(text):
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        contexts.add(re.sub(r"\s+", " ", text[start:end]).strip().lower())
    return contexts


def wait_for_new_thank_you(
    page: PageLike,
    before_text: str,
    dialog_hits: list[bool],
    timeout_ms: int = SUBMIT_WAIT_MS,
    raise_on_security: bool = True,
) -> bool:
    before_contexts = thank_you_contexts(before_text)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if dialog_hits:
            return True
        after_text = body_text(page)
        new_contexts = thank_you_contexts(after_text) - before_contexts
        if after_text != before_text and new_contexts:
            return True
        toast = page.locator(
            '[role="alert"], [role="status"], .toast, .MuiSnackbar-root, .MuiAlert-root'
        ).filter(has_text=THANK_YOU)
        if first_visible(toast) is not None:
            return True
        if raise_on_security:
            failed = security_check_failure(page)
            if failed:
                raise ContactSubmitError(failed)
        page.wait_for_timeout(400)
    return False


def save_screenshot(page: Page, url: str, suffix: str) -> str:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = SCREENSHOTS_DIR / f"{host_slug(url)}_{suffix}_{stamp}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def fill_form(page: PageLike) -> dict[str, Locator]:
    missing: list[str] = []
    name = find_name_field(page)
    email = find_email_field(page)
    comments = find_comments_field(page)
    phone = find_phone_field(page)

    if name is None:
        missing.append("full name")
    if email is None:
        missing.append("email")
    if comments is None:
        missing.append("additional comments")
    if missing:
        raise ContactSubmitError("Required field not found: " + ", ".join(missing))

    name = fill_control(name, TEST_NAME)
    email = fill_control(email, TEST_EMAIL)
    if phone is not None:
        phone = fill_control(phone, TEST_PHONE)
    comments = fill_control(comments, TEST_COMMENTS)

    dropdown = find_dropdown(page)
    if dropdown is not None:
        select_others(page, dropdown)

    fields = {"name": name, "email": email, "comments": comments}
    if phone is not None:
        fields["phone"] = phone
    return fields


def submit_form(
    page: PageLike,
    dialog_hits: list[bool],
    wait_security: bool = False,
) -> None:
    button = find_submit_button(page)
    if button is None:
        raise ContactSubmitError("Submit button not found")

    before_text = body_text(page)
    button.scroll_into_view_if_needed()
    button.click(timeout=5000)

    try:
        if wait_for_new_thank_you(page, before_text, dialog_hits):
            return
    except ContactSubmitError as exc:
        if wait_security and "Turnstile" in str(exc):
            print(
                "    Turnstile blocked automation. Complete the security check "
                "in the browser, click SUBMIT again, and wait..."
            )
            if wait_for_new_thank_you(
                page,
                before_text,
                dialog_hits,
                timeout_ms=120_000,
                raise_on_security=False,
            ):
                return
        raise

    failed = security_check_failure(page)
    if failed:
        raise ContactSubmitError(failed)
    validation = visible_validation_text(page)
    if validation:
        raise ContactSubmitError("Validation error after submit: " + validation)
    raise ContactSubmitError("No new 'thank you' appeared after submit")


def wait_for_manual_submit(
    page: PageLike,
    dialog_hits: list[bool],
    timeout_ms: int = PAUSE_SUBMIT_TIMEOUT_MS,
) -> None:
    button = find_submit_button(page)
    if button is not None:
        try:
            button.scroll_into_view_if_needed()
        except Exception:
            pass

    before_text = body_text(page)
    seconds = max(1, int(timeout_ms / 1000))
    print(f"    Click SUBMIT in the browser (waiting up to {seconds}s)...")

    if wait_for_new_thank_you(
        page,
        before_text,
        dialog_hits,
        timeout_ms=timeout_ms,
        raise_on_security=False,
    ):
        return

    failed = security_check_failure(page)
    if failed:
        raise ContactSubmitError(failed)
    raise ContactSubmitError(
        "No new 'thank you' appeared after you were asked to click SUBMIT"
    )


def test_site(
    page: Page,
    url: str,
    dry_run: bool,
    wait_security: bool = False,
    pause_submit: bool = False,
    pause_timeout_ms: int = PAUSE_SUBMIT_TIMEOUT_MS,
) -> SiteResult:
    started = time.monotonic()
    dialog_hits: list[bool] = []

    def on_dialog(dialog) -> None:
        try:
            message = dialog.message or ""
            if THANK_YOU.search(message):
                dialog_hits.append(True)
            dialog.accept()
        except Exception:
            try:
                dialog.accept()
            except Exception:
                pass

    page.on("dialog", on_dialog)
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        if response is not None and response.status >= 400:
            raise ContactSubmitError(f"Page did not load (HTTP {response.status})")

        try:
            page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        dismiss_cookie_banners(page)

        blocked = captcha_reason(page)
        if blocked:
            raise ContactSubmitError(blocked)

        form = resolve_form_context(page)
        fill_form(form)

        if dry_run:
            return SiteResult(
                url=url,
                status="success",
                reason="Dry run: fields filled, submit skipped",
                duration_s=round(time.monotonic() - started, 2),
            )

        if pause_submit:
            wait_for_manual_submit(form, dialog_hits, timeout_ms=pause_timeout_ms)
            return SiteResult(
                url=url,
                status="success",
                reason="New 'thank you' appeared after manual submit",
                duration_s=round(time.monotonic() - started, 2),
            )

        submit_form(form, dialog_hits, wait_security=wait_security)
        return SiteResult(
            url=url,
            status="success",
            reason="New 'thank you' appeared after submit",
            duration_s=round(time.monotonic() - started, 2),
        )
    except ContactSubmitError as exc:
        screenshot = None
        try:
            screenshot = save_screenshot(page, url, "fail")
        except Exception:
            pass
        return SiteResult(
            url=url,
            status="failed",
            reason=str(exc),
            duration_s=round(time.monotonic() - started, 2),
            screenshot=screenshot,
        )
    except PlaywrightTimeoutError as exc:
        screenshot = None
        try:
            screenshot = save_screenshot(page, url, "fail")
        except Exception:
            pass
        return SiteResult(
            url=url,
            status="failed",
            reason=f"Timeout: {exc}",
            duration_s=round(time.monotonic() - started, 2),
            screenshot=screenshot,
        )
    except Exception as exc:
        screenshot = None
        try:
            screenshot = save_screenshot(page, url, "fail")
        except Exception:
            pass
        return SiteResult(
            url=url,
            status="failed",
            reason=f"Unexpected error: {exc}",
            duration_s=round(time.monotonic() - started, 2),
            screenshot=screenshot,
        )
    finally:
        page.remove_listener("dialog", on_dialog)


def print_summary(results: list[SiteResult]) -> None:
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    width = max((len(item.url) for item in results), default=10)
    for item in results:
        tag = "OK  " if item.status == "success" else "FAIL"
        extra = f"  — {item.reason}" if item.reason else ""
        print(f"{tag}  {item.url:<{width}}{extra}")
    ok = sum(1 for item in results if item.status == "success")
    failed = len(results) - ok
    print("-" * 72)
    print(f"Done: {ok} success, {failed} failed")
    if failed:
        print()
        print("Failures:")
        for item in results:
            if item.status != "success":
                shot = f" (screenshot: {item.screenshot})" if item.screenshot else ""
                print(f"  - {item.url}: {item.reason}{shot}")


def write_results(results: list[SiteResult]) -> None:
    payload = [asdict(item) for item in results]
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {RESULTS_PATH}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    urls = selected_urls(args.only)
    if args.real_chrome or args.pause_submit:
        results = run_real_chrome(urls, timeout_s=args.pause_timeout)
        print_summary(results)
        write_results(results)
        return 0 if all(item.status == "success" for item in results) else 1

    results: list[SiteResult] = []

    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": not args.headed,
            "slow_mo": 250 if args.headed else 0,
        }
        if args.chrome:
            launch_kwargs["channel"] = "chrome"
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(15_000)

        for index, url in enumerate(urls):
            print(f"[{index + 1}/{len(urls)}] {url}")
            result = test_site(
                page,
                url,
                dry_run=args.dry_run,
                wait_security=args.wait_security,
            )
            results.append(result)
            marker = "OK" if result.status == "success" else "FAIL"
            detail = f" — {result.reason}" if result.reason else ""
            print(f"    {marker}{detail} ({result.duration_s}s)")
            if index < len(urls) - 1 and args.delay > 0:
                time.sleep(args.delay)

        context.close()
        browser.close()

    print_summary(results)
    write_results(results)
    return 0 if all(item.status == "success" for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
