"""Drive the Meridian fixture in a real browser without spending a model call.

Proves the flows work and produces screenshots to look at before any journey is run against it.
"""

import base64
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "artifacts" / "admin-shots"
PORT = 8117


def shoot(browser, name, viewport):
    data = browser.call("Page.captureScreenshot", format="png")["data"]
    (OUT / f"{name}.png").write_bytes(base64.b64decode(data))
    print(f"wrote {name}.png ({viewport[0]}x{viewport[1]})")


def press(browser, label, wait=0.6):
    page = browser.observe(screenshot=False)
    action = next((a for a in page["actions"] if (a.get("label") or "").strip() == label), None)
    if action is None:
        labels = [a.get("label") for a in page["actions"]]
        raise SystemExit(f"no control named {label!r}; page offers {labels}")
    browser.act(action, page)
    time.sleep(wait)


def main():
    from examples import flight_app
    from journey_evals.isolation import OwnedSession

    OUT.mkdir(parents=True, exist_ok=True)
    profile = ROOT / "artifacts" / "media-work" / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    server = flight_app.build(port=PORT, fault="none", app="admin")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"

    with OwnedSession(profile):
        from jev_ultrafast.browser import Browser

        viewport = (1120, 780)
        browser = Browser(base + "/billing", viewport=viewport)
        try:
            time.sleep(0.8)
            shoot(browser, "01-billing", viewport)
            press(browser, "Switch to Starter")
            shoot(browser, "02-review", viewport)
            print(browser.evaluate("document.getElementById('review-notice').textContent"))
            press(browser, "Confirm downgrade")
            shoot(browser, "03-scheduled", viewport)
            print("url:", browser.evaluate("location.pathname"))
        finally:
            browser.close()

        browser = Browser(base + "/workspaces", viewport=viewport)
        try:
            time.sleep(0.8)
            shoot(browser, "04-workspaces", viewport)
            press(browser, "Delete Atlas")
            shoot(browser, "05-delete", viewport)
            browser.evaluate("document.getElementById('delete-ack').click()")
            time.sleep(0.2)
            press(browser, "Delete workspace")
            shoot(browser, "06-deleted", viewport)
            press(browser, "Restore Atlas")
            shoot(browser, "07-restored", viewport)
            print("banner:", browser.evaluate("document.getElementById('workspace-state').textContent"))
        finally:
            browser.close()

        browser = Browser(base + "/insights", viewport=viewport)
        try:
            time.sleep(0.8)
            press(browser, "Refresh revenue", wait=1.0)
            shoot(browser, "08-insights", viewport)
            print("badge:", browser.evaluate("document.getElementById('freshness-badge').textContent"))
            print("notice:", browser.evaluate("document.getElementById('freshness-notice').textContent"))
        finally:
            browser.close()

        phone = (390, 844)
        browser = Browser(base + "/workspaces", viewport=phone)
        try:
            time.sleep(0.8)
            shoot(browser, "09-mobile", phone)
        finally:
            browser.close()

    server.shutdown()


if __name__ == "__main__":
    main()
