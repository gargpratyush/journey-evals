"""Prove the tests added for each fix actually fail when that fix is reverted.

A test that has never been seen failing is not evidence. This reverts one fix at a time in the
working tree, runs only the tests that claim to cover it, restores the file, and reports.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

CASES = [
    (
        "withholding STALLED below the declared threshold",
        ROOT / "journey_evals" / "evaluation.py",
        '        if limit and measured.get("consecutive_unchanged_actions", 0) < limit:\n'
        '            heads.discard("STALLED")\n',
        "",
        ["tests/test_evaluation.py::test_a_declared_stall_threshold_is_not_undercut_by_judgement",
         "tests/test_evaluation.py::test_a_withheld_verdict_is_not_offered_to_the_model"],
    ),
    (
        "reconciliation reading the live history entry",
        ROOT / "journey_evals" / "runner.py",
        "        live = history[-1]\n        if live.get(\"page_changed\") is True:\n            return\n",
        "        if entry.get(\"page_changed\") is not False:\n            return\n",
        ["tests/test_runner.py::test_the_journalled_copy_is_not_what_reconciliation_reads"],
    ),
    (
        "the declared window not being the page default",
        ROOT / "examples" / "admin.html",
        '            <option value="last_30_days" selected>Last 30 days</option>\n'
        '            <option value="last_7_days">Last 7 days</option>\n',
        '            <option value="last_7_days" selected>Last 7 days</option>\n'
        '            <option value="last_30_days">Last 30 days</option>\n',
        ["tests/test_admin_fixture.py::test_the_declared_window_is_never_the_one_the_page_already_shows"],
    ),
]


def run(tests):
    return subprocess.run([str(PYTHON), "-m", "pytest", "-q", *tests],
                          cwd=ROOT, capture_output=True, text=True)


def main():
    ok = True
    for name, path, present, reverted, tests in CASES:
        original = path.read_text(encoding="utf-8")
        if present not in original:
            print(f"SKIP  {name}: the fix is not in the working tree as expected")
            ok = False
            continue
        before = run(tests)
        path.write_text(original.replace(present, reverted), encoding="utf-8")
        try:
            after = run(tests)
        finally:
            path.write_text(original, encoding="utf-8")
        passes_with = before.returncode == 0
        fails_without = after.returncode != 0
        verdict = "OK  " if passes_with and fails_without else "BAD "
        ok = ok and passes_with and fails_without
        print(f"{verdict}{name}")
        print(f"      with the fix:    {'passed' if passes_with else 'FAILED'}")
        print(f"      without the fix: {'failed as it should' if fails_without else 'STILL PASSED'}")
    restored = run([str(p) for p in ["tests"]])
    print(f"\nworking tree restored, full suite: "
          f"{'green' if restored.returncode == 0 else 'RED'}")
    return 0 if ok and restored.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
