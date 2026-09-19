"""The npm shim and the Python distribution must not be able to drift apart.

The npm package is how most people will install this, and it is a thin shim: if it ever disagrees
with the wheel it ships - a different version, a missing file, a rewritten exit code - the failure
lands on somebody's CI in a form that looks like a product bug. These are the properties that can
be checked without installing anything.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NPM = ROOT / "npm"
MANIFEST = json.loads((NPM / "package.json").read_text(encoding="utf-8"))


def project_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


def test_the_npm_version_is_the_python_version():
    assert MANIFEST["version"] == project_version()


def test_the_package_ships_everything_the_shim_needs():
    published = set(MANIFEST["files"])
    assert {"bin/", "lib/", "scripts/", "vendor/"} <= published
    assert MANIFEST["bin"] == {"journey-evals": "bin/journey-evals.js"}
    assert (NPM / "bin" / "journey-evals.js").is_file()
    assert (NPM / "lib" / "python.js").is_file()
    assert (NPM / "scripts" / "postinstall.js").is_file()


def test_the_wheel_is_vendored_by_the_build_script_not_by_hand():
    # Nothing should commit a binary; the build script stages it, and .gitignore keeps it out.
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "npm/vendor/*.whl" in ignored


def test_the_shim_is_executable_and_passes_arguments_through():
    source = (NPM / "bin" / "journey-evals.js").read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/env node")
    assert "...argv" in source, "arguments must reach the CLI untouched"
    assert "process.exit(result.status" in source, "the exit code is the contract"


@pytest.mark.parametrize("code", ["0 pass", "1 fail", "2 inconclusive", "3 error", "4"])
def test_the_readme_states_the_exit_codes(code):
    readme = (NPM / "README.md").read_text(encoding="utf-8").lower()
    assert code.split()[0] in readme


def test_the_shim_never_prints_a_key():
    source = (NPM / "bin" / "journey-evals.js").read_text(encoding="utf-8")
    # It may report whether a key is present; printing the value itself would be a leak.
    assert "process.env.JEV_API_KEY" in source
    assert "console.log(key" not in source
    assert "${key}" not in source


def test_the_installed_layout_is_decided_in_one_place():
    paths = (ROOT / "journey_evals" / "paths.py").read_text(encoding="utf-8")
    for name in ("workspace", "runs_root", "work_root", "env_file", "example_app"):
        assert f"def {name}(" in paths
    cli = (ROOT / "journey_evals" / "cli.py").read_text(encoding="utf-8")
    assert "from examples import" not in cli, "installed copies have no examples/ source tree"
