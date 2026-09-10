(() => {
  if (!/contact/i.test(location.pathname)) {
    return;
  }

  const DATA = {
    name: "Mitra Brinda Mukherjee",
    phone: "1234567890",
    email: "mitra.b.mukherjee@steorasystems.com",
    comments: "DEV TEAM TESTING",
  };

  const FAIL_RE =
    /couldn['’]?t submit|could not submit|unable to submit|failed to submit|security check failed/i;
  const SUCCESS_RE =
    /thank you|your message has been sent|message has been sent|sent successfully|successfully sent|form submitted/i;

  let bannerShown = false;
  let filledOk = false;
  let reported = false;
  let successBefore = 0;

  function showBanner(text, color) {
    let bar = document.getElementById("qa-fill-banner");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "qa-fill-banner";
      bar.style.cssText =
        "position:fixed;z-index:2147483647;top:0;left:0;right:0;padding:8px 12px;" +
        "font:14px/1.4 sans-serif;text-align:center;color:#fff;";
      document.documentElement.appendChild(bar);
    }
    bar.style.background = color;
    bar.textContent = text;
    bannerShown = true;
  }

  function pageText() {
    const bar = document.getElementById("qa-fill-banner");
    const hidden = bar ? bar.style.display : "";
    if (bar) {
      bar.style.display = "none";
    }
    const text = document.body ? document.body.innerText : "";
    if (bar) {
      bar.style.display = hidden || "";
    }
    return text;
  }

  function successHits(text) {
    return (text.match(SUCCESS_RE) || []).length;
  }

  function report(status, reason) {
    if (reported) {
      return;
    }
    reported = true;
    showBanner(
      status === "success" ? "Submitted — success seen. Closing…" : "Submit failed. Closing…",
      status === "success" ? "#063" : "#a20"
    );
    try {
      chrome.runtime.sendMessage({
        type: "qa-result",
        url: location.href,
        status,
        reason,
      });
    } catch (_err) {
      /* extension context may be missing if not reloaded */
    }
  }

  function setValue(el, value) {
    if (!el) {
      return false;
    }
    el.focus();
    const proto =
      el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) {
      setter.call(el, value);
    } else {
      el.value = value;
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
    return (el.value || "").includes(value) || el.value.replace(/\D/g, "") === value.replace(/\D/g, "");
  }

  function visibleInputs() {
    return [...document.querySelectorAll("input, textarea")].filter((el) => {
      const type = (el.type || "text").toLowerCase();
      if (["hidden", "submit", "button", "checkbox", "radio", "file"].includes(type)) {
        return false;
      }
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") {
        return false;
      }
      const box = el.getBoundingClientRect();
      return box.width > 0 && box.height > 0;
    });
  }

  function labelOf(el) {
    const bits = [
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder"),
      el.getAttribute("name"),
      el.id,
    ];
    const control = el.closest(".MuiFormControl-root, .MuiTextField-root, label");
    if (control) {
      bits.push(control.innerText);
    } else if (el.previousElementSibling) {
      bits.push(el.previousElementSibling.innerText);
    }
    return bits.filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, " ");
  }

  function byLabel(re) {
    return visibleInputs().find((el) => re.test(labelOf(el)));
  }

  function selectOthers() {
    const native = [...document.querySelectorAll("select")].find((el) =>
      [...el.options].some((o) => /^others?$/i.test(o.text.trim()) || /^others?$/i.test(o.value))
    );
    if (native) {
      const opt = [...native.options].find((o) => /^others?$/i.test(o.text.trim()) || /^others?$/i.test(o.value));
      native.value = opt.value;
      native.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }

    const trigger = [...document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], div, button')].find(
      (el) =>
        /please select requirement/i.test(el.innerText || "") ||
        /please select requirement/i.test(el.getAttribute("aria-label") || "")
    );
    if (!trigger) {
      return false;
    }
    trigger.click();
    setTimeout(() => {
      const option = [...document.querySelectorAll('[role="option"], li, .MuiMenuItem-root')].find((el) =>
        /^others?$/i.test((el.innerText || "").trim())
      );
      if (option) {
        option.click();
      }
    }, 250);
    return true;
  }

  function fillOnce() {
    const inputs = visibleInputs();
    const name = byLabel(/full name|^name\b/) || inputs.find((el) => el.tagName === "INPUT" && el.type !== "email" && el.type !== "tel");
    const phone = byLabel(/phone|tel|mobile/) || inputs.find((el) => el.type === "tel");
    const email = byLabel(/e-?mail/) || inputs.find((el) => el.type === "email");
    const comments = byLabel(/comment|message/) || document.querySelector("textarea");

    const ordered = inputs.filter((el) => el.tagName === "INPUT");
    const nameEl = name || ordered[0];
    const phoneEl = phone || ordered[1];
    const emailEl = email || ordered[2];
    const commentEl = comments || [...inputs].find((el) => el.tagName === "TEXTAREA");

    let ok = 0;
    if (nameEl && setValue(nameEl, DATA.name)) {
      ok += 1;
    }
    if (phoneEl && setValue(phoneEl, DATA.phone)) {
      ok += 1;
    }
    if (emailEl && setValue(emailEl, DATA.email)) {
      ok += 1;
    }
    if (commentEl && setValue(commentEl, DATA.comments)) {
      ok += 1;
    }
    selectOthers();
    return ok >= 3;
  }

  function checkOutcome() {
    if (reported || !filledOk) {
      return;
    }
    const text = pageText();
    const fail = text.match(FAIL_RE);
    if (fail) {
      report("failed", fail[0]);
      return;
    }
    const alerts = [
      ...document.querySelectorAll(
        '[role="alert"], [role="status"], .MuiAlert-root, .MuiSnackbar-root, .toast, [class*="snackbar" i], [class*="Snackbar"]'
      ),
    ];
    const alertText = alerts.map((el) => el.innerText || "").join(" ");
    if (FAIL_RE.test(alertText)) {
      report("failed", alertText.trim().slice(0, 120));
      return;
    }
    const hit = alertText.match(SUCCESS_RE) || text.match(SUCCESS_RE);
    if (SUCCESS_RE.test(alertText) || successHits(text) > successBefore) {
      report("success", (hit && hit[0]) || "message sent");
    }
  }

  function tick() {
    if (!bannerShown) {
      showBanner("QA filler loaded — filling contact form…", "#0b5");
    }
    if (filledOk) {
      checkOutcome();
      return;
    }
    if (fillOnce()) {
      filledOk = true;
      successBefore = successHits(pageText());
      showBanner("Filled. Click SUBMIT — waiting for success or error…", "#063");
    }
  }

  showBanner("QA filler loaded — waiting for form…", "#0b5");
  tick();
  const timer = setInterval(tick, 400);
  setTimeout(() => {
    if (!filledOk) {
      clearInterval(timer);
    }
  }, 20000);

  const observer = new MutationObserver(() => tick());
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
