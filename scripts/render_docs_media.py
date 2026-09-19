"""Capture the screenshots the documentation and the website use.

Everything here is a real page: the console is photographed while a real journey is running, the
example application is photographed at the sizes journeys actually declare, and the report is the
HTML a real run wrote. Nothing is mocked up, because a screenshot of a mock-up is a claim nobody
checked.

    python scripts/render_docs_media.py            # apps and report only, no model calls
    python scripts/render_docs_media.py --console  # also drive a live run and photograph it
"""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MEDIA = ROOT / "docs" / "media"
SCRATCH = ROOT / "artifacts" / "media-work"


def capture(browser, path, viewport):
    """A PNG of the whole viewport, taken through CDP so it is not re-encoded as JPEG."""
    data = browser.call("Page.captureScreenshot", format="png")["data"]
    path.write_bytes(base64.b64decode(data))
    print(f"wrote {path.relative_to(ROOT)} ({viewport[0]}x{viewport[1]})")


def shoot_pages(session_dir, port):
    from examples import flight_app
    from jev_ultrafast.browser import Browser

    server = flight_app.build(port=port, fault="none", app="flight")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        for name, viewport, steps in [
            ("app-search", (1280, 800), []),
            ("app-results", (1280, 800), ["search"]),
            ("app-mobile", (390, 844), []),
        ]:
            browser = Browser(base + "/", viewport=viewport)
            try:
                if "search" in steps:
                    page = browser.observe(screenshot=False)
                    action = next(a for a in page["actions"] if "Search" in (a.get("label") or ""))
                    browser.act(action, page)
                    time.sleep(1.2)
                capture(browser, MEDIA / f"{name}.png", viewport)
            finally:
                browser.close()
    finally:
        server.shutdown()


def shoot_report(run_directory):
    from jev_ultrafast.browser import Browser

    report = Path(run_directory) / "report.html"
    if not report.exists():
        print(f"no report at {report}; skipping", file=sys.stderr)
        return
    browser = Browser(report.resolve().as_uri(), viewport=(1280, 900))
    try:
        capture(browser, MEDIA / "report.png", (1280, 900))
    finally:
        browser.close()


def shoot_console(journey, port):
    """Photograph the console while a real run is in flight, then once it has settled."""
    from jev_ultrafast.browser import Browser

    command = [sys.executable, "-m", "journey_evals.cli", "watch", "--journey", str(journey),
               "--serve-app", "flight", "--no-open", "--port", str(port)]
    watch = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(8)
        browser = Browser(f"http://127.0.0.1:{port}/", viewport=(1600, 1000))
        try:
            time.sleep(10)
            capture(browser, MEDIA / "console-running.png", (1600, 1000))
            for _ in range(60):
                status = str(browser.evaluate("document.getElementById('status').textContent"))
                if any(word in status for word in ("finished", "stopped", "failed")):
                    break
                time.sleep(4)
            time.sleep(3)
            capture(browser, MEDIA / "console-finished.png", (1600, 1000))
        finally:
            browser.close()
    finally:
        watch.terminate()
        try:
            watch.wait(timeout=20)
        except subprocess.TimeoutExpired:
            watch.kill()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", action="store_true",
                        help="Also drive a live journey and photograph the console (spends model calls)")
    parser.add_argument("--journey", default=str(ROOT / "examples" / "journeys" / "flight-booking-matrix.json"))
    parser.add_argument("--app-port", type=int, default=8114)
    parser.add_argument("--console-port", type=int, default=8771)
    parser.add_argument("--run", default=None, help="A finished run directory to photograph report.html from")
    args = parser.parse_args(argv)

    MEDIA.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    profile = SCRATCH / "profile"
    profile.mkdir(parents=True, exist_ok=True)

    from journey_evals.isolation import OwnedSession

    # One owned browser session per process: the harness refuses a second one, and it is right to.
    # Run this script twice rather than weakening that.
    if args.console:
        with OwnedSession(profile):
            shoot_console(args.journey, args.console_port)
    else:
        with OwnedSession(profile):
            shoot_pages(profile, args.app_port)
            if args.run:
                shoot_report(args.run)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    index = {path.name: path.stat().st_size for path in sorted(MEDIA.glob("*.png"))}
    print(json.dumps(index, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
