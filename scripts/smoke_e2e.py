"""The plan's section 18 end-to-end smoke, run as one script.

The shape is deliberate: a clean journey must pass and leave a persisted booking; one hidden
defect must then be caught by the *unchanged* journey; and after the application is fixed the same
unchanged journey must go quiet again. The last step is the one that matters. A tool that reports
a problem but cannot tell you when it is gone is not an oracle, it is an alarm.

Nothing here suppresses an evaluator, edits a journey, or reruns a step to get a better answer.
Each phase runs once and its outcome is recorded as it happened.
"""

import argparse
import json
import shutil
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.calibrate import journey_for, run_once, serve  # noqa: E402

JOURNEY = "examples/journeys/flight-booking.json"
DEFECT = "silent_fare_increase"
DEFECT_EVALUATOR = "checkout-total-explained"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(port, *, deadline_s=20.0):
    """An explicit readiness deadline, not a sleep. A fixture that never answers is an error."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"fixture on port {port} was not ready within {deadline_s:.0f}s")


def bookings(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/__test__/bookings", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def phase(name, fault, out):
    """One journey against a freshly started fixture with its own empty backend."""
    port = free_port()
    server, _thread = serve("flight", fault, port)
    try:
        wait_ready(port)
        assert bookings(port) == [], "the fake backend must start empty for every phase"
        out.mkdir(parents=True, exist_ok=True)
        journey = journey_for(JOURNEY, port, out)
        outcome = run_once(journey, out)
        outcome["phase"] = name
        outcome["fault"] = fault
        outcome["persisted_bookings"] = len(bookings(port))
        return outcome
    finally:
        server.shutdown()
        server.server_close()


def evaluators(outcome):
    return {finding["evaluator"] for finding in outcome["findings"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/eval/smoke")
    args = parser.parse_args(argv)

    # A fresh directory and a fresh owned browser profile per phase; no personal profile is touched.
    root = ROOT / args.out
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    failures = []
    records = []

    clean = phase("clean", "none", root / "1-clean")
    records.append(clean)
    print(f"1. clean journey          -> {clean['result']} / goal {clean['goal_status']} / "
          f"{clean['persisted_bookings']} booking(s)")
    if clean["result"] != "PASS":
        failures.append(f"clean journey returned {clean['result']}, expected PASS")
    if clean["goal_status"] != "verified":
        failures.append(f"clean goal was {clean['goal_status']}, expected verified")
    if clean["persisted_bookings"] != 1:
        failures.append(f"clean journey persisted {clean['persisted_bookings']} bookings, expected 1")
    for artifact in ("report.json", "report.html", "junit.xml", "agent-feedback.json",
                     "events.jsonl", "usage.json"):
        if not (root / "1-clean" / artifact).exists():
            failures.append(f"clean run did not write {artifact}")

    defective = phase("defect", DEFECT, root / "2-defect")
    records.append(defective)
    print(f"2. one hidden defect      -> {defective['result']} / failed "
          f"{defective['failed_required_checks']}")
    if DEFECT_EVALUATOR not in set(defective["failed_required_checks"]) | evaluators(defective):
        failures.append(f"the {DEFECT} defect was not reported by {DEFECT_EVALUATOR}")
    if defective["result"] not in {"FAIL", "WARN"}:
        failures.append(f"defective run returned {defective['result']}, expected FAIL or WARN")

    feedback_path = root / "2-defect" / "agent-feedback.json"
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    reported = {item["evaluator"] for item in
                feedback["confirmed_findings"] + feedback["advisory_findings"]}
    print(f"3. evidence for the agent -> {sorted(reported)}; "
          f"{len(feedback['unresolved_coverage'])} unresolved check(s)")
    if DEFECT_EVALUATOR not in reported:
        failures.append("agent-feedback.json did not carry the defect an agent is meant to fix")
    for item in feedback["confirmed_findings"] + feedback["advisory_findings"]:
        if not item["evidence_ids"]:
            failures.append(f"finding {item['id']} cites no evidence")

    # Fixing the application. The fixture expresses this defect as a presentation flag rather than
    # as edited source, so "fixed" here means the surcharge is no longer applied silently. The
    # journey, the evaluators, and the acceptance contract are byte-identical to phase 2.
    fixed = phase("fixed", "none", root / "3-fixed")
    records.append(fixed)
    print(f"4. after the fix          -> {fixed['result']} / goal {fixed['goal_status']} / "
          f"{fixed['persisted_bookings']} booking(s)")
    if DEFECT_EVALUATOR in set(fixed["failed_required_checks"]) | evaluators(fixed):
        failures.append(f"{DEFECT_EVALUATOR} still reports after the fix")
    if fixed["result"] != "PASS":
        failures.append(f"fixed run returned {fixed['result']}, expected PASS")

    # The evaluator must still be live. A defect that "disappears" because its check stopped
    # running is the failure mode this whole campaign exists to avoid.
    if fixed["checks"].get(DEFECT_EVALUATOR) != "passed":
        failures.append(
            f"{DEFECT_EVALUATOR} was {fixed['checks'].get(DEFECT_EVALUATOR)} after the fix; "
            "it must still have been evaluated, not suppressed")
    else:
        print(f"5. evaluator still live   -> {DEFECT_EVALUATOR} evaluated and passed")

    summary = {"phases": records, "failures": failures,
               "defect": DEFECT, "evaluator": DEFECT_EVALUATOR}
    (root / "smoke.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if failures:
        print("\nFAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS: clean journey verified, defect caught, fix confirmed by the unchanged journey")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
