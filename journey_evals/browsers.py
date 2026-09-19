"""Getting the pinned browser, wherever this copy of Journey Evals happens to live.

The browser version is pinned on purpose: a run is evidence, and evidence that silently changed
engine between Tuesday and Wednesday is worth less. In a checkout the binary lives in ``.tools``
beside the source; installed, it lives in the user's cache directory, because writing into
site-packages is not ours to do.

Nothing here is downloaded implicitly. `journey-evals install-browser` is an explicit command, so a
tool that is about to fetch 150 MB says so first.
"""

import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .feasibility import ROOT
from .paths import IN_CHECKOUT

BROWSER_VERSION = "153.0.8010.52"
DOWNLOAD = "https://storage.googleapis.com/chrome-for-testing-public/{version}/{key}/chrome-{key}.zip"

# Where the executable sits inside each published archive.
LAYOUT = {
    "win64": "chrome-win64/chrome.exe",
    "linux64": "chrome-linux64/chrome",
    "mac-x64": "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "mac-arm64": "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
}


def platform_key():
    """The Chrome for Testing platform name for this machine."""
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    if sys.platform.startswith("linux"):
        return "linux64"
    raise RuntimeError(f"No pinned Chrome for Testing build for {sys.platform}")


def cache_root():
    """Where pinned browsers are kept: the checkout, or a per-user cache directory."""
    if IN_CHECKOUT:
        return ROOT / ".tools"
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "journey-evals" / "browsers"


def chrome_path(version=BROWSER_VERSION, key=None):
    key = key or platform_key()
    return cache_root() / f"chrome-{version}" / Path(LAYOUT[key])


def install(version=BROWSER_VERSION, force=False, log=print):
    """Download and unpack the pinned browser. Returns the path to the executable."""
    key = platform_key()
    target = chrome_path(version, key)
    if target.is_file() and not force:
        log(f"already installed: {target}")
        return target
    destination = cache_root() / f"chrome-{version}"
    if force and destination.exists():
        shutil.rmtree(destination)
    url = DOWNLOAD.format(version=version, key=key)
    destination.mkdir(parents=True, exist_ok=True)
    log(f"downloading Chrome for Testing {version} ({key})")
    log(f"  from {url}")
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "chrome.zip"
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        log(f"  unpacking into {destination}")
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
    if not target.is_file():
        raise RuntimeError(f"the archive did not contain {target}")
    if not sys.platform.startswith("win"):
        # Zip archives do not carry the executable bit through every extractor.
        for path in destination.rglob("*"):
            if path.is_file() and not path.suffix:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"installed {target}")
    return target
