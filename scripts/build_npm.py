"""Build the npm distribution: the wheel, vendored, with the version kept in one place.

The npm package is a shim around the Python distribution, so the two must never disagree about
what version they are. The version in ``pyproject.toml`` is the only one anybody edits; this script
writes it into ``npm/package.json`` and puts the freshly built wheel in ``npm/vendor``.

    python scripts/build_npm.py          # build and stage
    python scripts/build_npm.py --pack   # and produce the .tgz npm would publish
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPM = ROOT / "npm"
VENDOR = NPM / "vendor"


def project_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not read the version from pyproject.toml")
    return match.group(1)


def build_wheel():
    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel"],
                   cwd=ROOT, check=True)
    wheels = sorted((ROOT / "dist").glob("journey_evals-*.whl"),
                    key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise SystemExit("hatchling produced no wheel")
    return wheels[-1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="store_true", help="also run `npm pack`")
    args = parser.parse_args(argv)

    version = project_version()
    wheel = build_wheel()

    VENDOR.mkdir(parents=True, exist_ok=True)
    for stale in VENDOR.glob("*.whl"):
        stale.unlink()
    shutil.copyfile(wheel, VENDOR / wheel.name)

    manifest_path = NPM / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")

    # The npm tarball must carry the licence it claims; npm does not follow the parent directory.
    shutil.copyfile(ROOT / "LICENSE", NPM / "LICENSE")

    print(f"staged {wheel.name} in npm/vendor and set npm version {version}")
    if args.pack:
        subprocess.run(["npm", "pack"], cwd=NPM, check=True, shell=sys.platform == "win32")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
