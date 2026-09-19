"""One fresh, explicitly owned Chrome/harness pair per worker process."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .browsers import BROWSER_VERSION, chrome_path
from .feasibility import SLOW_MACHINE_TIMEOUT
from .paths import work_root

# Resolved once: the checkout's .tools, or the user's cache directory when installed.
BROWSER = chrome_path()
CHROME_FLAGS = (
    "--headless=new", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-sync", "--disable-extensions",
    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
)
# The browser gets an explicitly listed environment and nothing else, so no credential can reach
# it by accident. The posix names are here so the same rule works off Windows.
OS_ENV = {
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "COMSPEC",
    "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TMPDIR", "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
}


async def close_browser(url):
    from cdp_use.client import CDPClient

    client = CDPClient(url)
    try:
        await asyncio.wait_for(client.start(), timeout=SLOW_MACHINE_TIMEOUT)
        await asyncio.wait_for(client.send_raw("Browser.close"), timeout=SLOW_MACHINE_TIMEOUT)
    finally:
        await asyncio.wait_for(client.stop(), timeout=SLOW_MACHINE_TIMEOUT)


def stop_daemon():
    from browser_harness.helpers import _send

    if _send({"meta": "shutdown"}, response_timeout=SLOW_MACHINE_TIMEOUT) != {"ok": True}:
        raise RuntimeError("Owned daemon did not acknowledge shutdown")


class OwnedSession:
    def __init__(self, diagnostics):
        self.diagnostics = Path(diagnostics)
        self.cleanup_retries = 0
        self.cleanup_events = []
        self.cleanup_errors = []
        self.browser_ws = None
        self.closed = False

    def __enter__(self):
        if any(name in sys.modules for name in ("browser_harness.helpers", "browser_harness.admin")):
            raise RuntimeError("Owned sessions require a fresh worker; harness configuration was already imported")
        if not BROWSER.is_file():
            raise FileNotFoundError(
                f"The pinned Chrome for Testing {BROWSER_VERSION} is not installed at {BROWSER}. "
                f"Run `journey-evals install-browser`."
            )
        work = work_root()
        work.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="browser-", dir=work)).resolve()
        self.profile = self.directory / "profile"
        self.chrome = self.daemon = None
        self.log = (self.diagnostics / "runtime.log").open("wb")
        self.previous = {
            key: os.environ.get(key) for key in ("BU_NAME", "BH_HOME", "BU_CDP_URL", "JEV_OWNED_SESSION")
        }
        self.environment = {key: value for key, value in os.environ.items() if key.upper() in OS_ENV}
        try:
            self.chrome = subprocess.Popen([
                str(BROWSER), *CHROME_FLAGS, f"--user-data-dir={self.profile}", "about:blank",
            ], env=self.environment, stdout=self.log, stderr=subprocess.STDOUT)
            active = self.profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            port = None
            while port is None:
                if self.chrome.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Owned Chrome did not start; no normal browser was contacted")
                try:
                    # The file appears before Chrome has finished writing it, and on Windows the
                    # write holds an exclusive handle. Both states are transient, so keep waiting
                    # rather than turning a startup race into a failed run.
                    port = int(active.read_text().splitlines()[0])
                except (FileNotFoundError, PermissionError, IndexError, ValueError, OSError):
                    port = None
                    time.sleep(0.05)
            endpoint = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(endpoint + "/json/version", timeout=5) as response:
                metadata = json.load(response)
            self.browser_ws = metadata["webSocketDebuggerUrl"]
            version = metadata["Browser"]
            if version.split("/")[-1] != BROWSER_VERSION:
                raise RuntimeError("Chrome version differs from the pinned experiment binary")
            self.version = version
            self.environment.update(
                BU_CDP_URL=endpoint, BU_NAME=self.directory.name, BH_HOME=str(self.directory / "harness"),
                JEV_OWNED_SESSION="1",
            )
            os.environ.update({key: self.environment[key] for key in self.previous})
            self.daemon = subprocess.Popen(
                [sys.executable, "-m", "browser_harness.daemon"],
                env=self.environment, stdout=self.log, stderr=subprocess.STDOUT,
            )
            from browser_harness.admin import daemon_alive, daemon_browser_ready
            deadline = time.monotonic() + 15
            while not daemon_alive() or not daemon_browser_ready():
                if self.daemon.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Owned harness did not become responsive")
                time.sleep(0.05)
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, _type=None, error=None, _traceback=None):
        try:
            # Stop the real daemon through its private IPC endpoint, not its Windows launcher.
            for name, process in (("daemon", self.daemon), ("browser", self.chrome)):
                if process is None:
                    continue
                if process.poll() is None:
                    try:
                        if name == "daemon":
                            stop_daemon()
                        elif self.browser_ws:
                            asyncio.run(close_browser(self.browser_ws))
                    except (RuntimeError, OSError, TimeoutError) as failure:
                        self.cleanup_events.append(f"{name} graceful close: {type(failure).__name__}: {failure}")
                try:
                    try:
                        process.wait(timeout=SLOW_MACHINE_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        if name == "daemon":
                            # Preserve its endpoint for recovery rather than orphaning the real child.
                            raise
                        self.cleanup_events.append(f"browser forced termination: PID {process.pid}")
                        process.terminate()
                        process.wait(timeout=SLOW_MACHINE_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired) as failure:
                    self.cleanup_errors.append(f"{name} did not exit: {type(failure).__name__}: {failure}")
            self.closed = all(p is None or p.poll() is not None for p in (self.chrome, self.daemon))
            if self.closed:
                try:
                    self.preserve_logs()
                    self.remove_profile()
                except (OSError, RuntimeError) as failure:
                    self.cleanup_errors.append(f"profile cleanup: {type(failure).__name__}: {failure}")
        finally:
            self.log.close()
            for key, value in self.previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if self.cleanup_errors:
            message = "Owned cleanup failed: " + "; ".join(self.cleanup_errors)
            if error is not None:
                error.add_note(message)
            else:
                raise RuntimeError(message)

    def preserve_logs(self):
        """Keep the owned daemon's own log, which otherwise dies with the private profile."""
        for log in sorted((self.directory / "harness" / "tmp").glob("*.log")):
            shutil.copy2(log, self.diagnostics / ("harness-" + log.name))

    def remove_profile(self):
        if self.directory.parent != work_root().resolve() or not self.directory.name.startswith("browser-"):
            raise RuntimeError("Refusing cleanup outside the owned browser directory")
        deadline = time.monotonic() + 3
        while self.directory.exists():
            try:
                shutil.rmtree(self.directory)
            except PermissionError:
                self.cleanup_retries += 1
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
