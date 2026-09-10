# Contact form testing

This project fills `/contact` forms on the transcription sites listed in `sites.py`. Production forms use **Cloudflare Turnstile**, which blocks Playwright. The supported workflow is:

1. Load the **Contact form fill (QA)** extension once in a dedicated Chrome profile.
2. Run `python form_tester.py --real-chrome`.
3. Click **SUBMIT** yourself. The script records success or failure, closes the tab, and opens the next site.

## Prerequisites

- Windows
- Python 3.10+
- Google Chrome (the normal installed browser, not Playwright Chromium)

## One-time setup

```powershell
cd C:\Users\Steora\testing-automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Playwright is only needed if you run the old automated path (`python form_tester.py` without `--real-chrome`). For production Turnstile testing, skip `playwright install`.

## Load the Chrome extension (required, once)

Chrome 137+ ignores `--load-extension` on branded Chrome. You must load the unpacked extension in the **QA profile** by hand.

### 1. Open the QA Chrome profile

Close any leftover QA Chrome windows, then:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir="C:\Users\Steora\testing-automation\.chrome-qa-profile" `
  --profile-directory=Default `
  chrome://extensions
```

This is a separate profile from your everyday Chrome. Personal Chrome can stay open.

Confirm you are in the right window: open `chrome://version` and check **Profile Path**. It must contain `.chrome-qa-profile\Default`, not `Google\Chrome\User Data`.

### 2. Developer mode

On `chrome://extensions`:

1. Do **not** use **More tools → Developer tools** (that is the page debugger).
2. Type `chrome://extensions` in the address bar, or use the three-dot menu → **Extensions** → **Manage extensions**.
3. Top right: turn **Developer mode** on.

### 3. Load unpacked

1. Click **Load unpacked**.
2. Select this folder (the one that contains `manifest.json`):

   `C:\Users\Steora\testing-automation\chrome-extension`

3. Confirm a card named **Contact form fill (QA)** is listed and enabled.

### 4. Smoke-check the filler

In the **same** QA Chrome window, open:

`https://www.verbatimlegal.ca/contact`

You should see a green bar at the top and the fields fill with:

| Field | Value |
| --- | --- |
| Full name | Mitra Brinda Mukherjee |
| Phone | 1234567890 |
| Email | mitra.b.mukherjee@steorasystems.com |
| Requirement | Other / Others |
| Comments | DEV TEAM TESTING |

If the bar is missing, you are in the wrong Chrome profile, or the extension is off.

### 5. Reload after code changes

Whenever `chrome-extension/content.js`, `background.js`, or `manifest.json` changes, go to `chrome://extensions` and click **Reload** on **Contact form fill (QA)**. Then refresh the contact tab.

## Run the tests

Activate the venv, then:

```powershell
.\.venv\Scripts\Activate.ps1
python form_tester.py --real-chrome
```

One site:

```powershell
python form_tester.py --real-chrome --only verbatimlegal.ca
```

Longer wait per site (seconds):

```powershell
python form_tester.py --real-chrome --pause-timeout 300
```

`--pause-submit` is an alias for `--real-chrome`.

Keep the Python process running. It starts a local listener on `127.0.0.1:8765` so the extension can report results.

### What you do

On each contact page:

1. Wait for the green filler bar and filled fields.
2. Click **SUBMIT**.
3. Wait for a success toast (for example **Your message has been sent successfully**) or an error (for example **Security check failed** / **couldn't submit**).

Do not click Submit in a Playwright window. Only the QA Chrome profile with the loaded extension.

### What the script does

- Opens one `/contact` URL at a time in the QA Chrome profile.
- Does **not** click Submit (Turnstile must run in a normal Chrome).
- Treats success when the page shows **thank you**, **your message has been sent**, **sent successfully**, or similar.
- Treats failure when the page shows **couldn't submit**, **security check failed**, or similar.
- Closes that tab after ~2 seconds and opens the next site.
- Writes a summary to the terminal and to `results.json`.

Edit the site list in [`sites.py`](sites.py). Comment out hosts you do not want to run.

## Results

Example terminal output:

```
[1/24] https://www.100percentaccuracy.ca/contact
    OK — your message has been sent (12.4s)
[2/24] https://www.audiatranscription.com/contact
    FAIL — security check failed (8.1s)
...
Done: 20 success, 4 failed
Wrote C:\Users\Steora\testing-automation\results.json
```

`results.json` has `{url, status, reason, duration_s}` per site.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| No green bar, empty form | Wrong Chrome window. Check `chrome://version` Profile Path. Load unpacked again in the QA profile. |
| Filled, Submit works, tab never closes | Extension not reloaded after the last code change. Reload **Contact form fill (QA)**, then re-run the script. |
| Tab never closes after a real success toast | The toast wording may be new. Add it in `chrome-extension/content.js` (`SUCCESS_RE`), reload the extension. |
| Next site never opens | The Python process is still waiting (default 180s). Ctrl+C, reload the extension, run `--real-chrome` again. |
| `python form_tester.py` without `--real-chrome` | Uses Playwright. Production Turnstile will fail with **Security check failed**. Use `--real-chrome` instead. |
| Chrome opens your normal profile | Always pass `--user-data-dir` as in the commands above. Do not launch Chrome from the taskbar for this test. |

## Layout

```
testing-automation/
  form_tester.py          # CLI: --real-chrome opens QA Chrome and records results
  sites.py                # Site list
  chrome-extension/       # Unpacked MV3 extension (fill + report outcome)
    manifest.json
    content.js
    background.js
  .chrome-qa-profile/     # Dedicated Chrome user data (created on first launch; gitignored)
  results.json            # Last run
```
