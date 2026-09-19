"""Measure what the evaluator actually detects, on declared faults and on lookalike controls.

Every run counts. A scenario is never rerun to replace a bad outcome, and a truncated sweep is
reported as the number of runs that actually happened. The point of the controls is that they are
supposed to look like the faults: a harness that reports them is not detecting anything, it is
guessing.
"""

import argparse
import copy
import json
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples import flight_app, subscription_app  # noqa: E402

# What each scenario is supposed to produce. A fault names the declared check that must fail; a
# control names nothing and must produce no finding at all.
FLIGHT = {
    "none": {"kind": "control", "expect_result": "PASS"},
    "search_does_nothing": {"kind": "fault", "expect_failed": ["search-shows-results"]},
    "silent_fare_increase": {"kind": "fault", "expect_failed": ["checkout-total-explained"]},
    "missing_loading_feedback": {"kind": "fault", "expect_failed": ["search-progress-feedback"]},
    "clipped_confirm_button": {"kind": "fault", "expect_failed": ["confirm-action-usable"]},
    "booking_never_recorded": {"kind": "fault", "expect_goal": "violated"},
    "disclosed_fare_increase": {"kind": "control", "expect_result": "PASS"},
    "fast_search": {"kind": "control", "expect_result": "PASS"},
    "terse_loading_feedback": {"kind": "control", "expect_result": "PASS"},
}
SUBSCRIPTION = {
    "none": {"kind": "control", "expect_result": "PASS"},
    "silent_price_increase": {"kind": "fault", "expect_failed": ["checkout-total-explained"]},
    "missing_loading_feedback": {"kind": "fault", "expect_failed": ["availability-progress-feedback"]},
    "subscription_never_recorded": {"kind": "fault", "expect_goal": "violated"},
    "availability_does_nothing": {"kind": "fault", "expect_failed": ["availability-shows-review"]},
    "clipped_subscribe_button": {"kind": "fault", "expect_failed": ["subscribe-action-usable"]},
    "disclosed_price_increase": {"kind": "control", "expect_result": "PASS"},
    "fast_availability": {"kind": "control", "expect_result": "PASS"},
    "terse_loading_feedback": {"kind": "control", "expect_result": "PASS"},
}
# The Back/persistence journey is declared separately, as the plan requires: it is a different
# journey with different checks, not a viewport variation of the booking journey.
BACK = {
    "none": {"kind": "control", "expect_result": "PASS"},
    "details_lost_on_back": {"kind": "fault", "expect_failed": ["details-survive-going-back"]},
}
SUITES = {
    "flight": {"scenarios": FLIGHT, "journey": "examples/journeys/flight-booking.json", "port": 8111},
    "flight-narrow": {"scenarios": {"none": FLIGHT["none"],
                                    "clipped_confirm_button": FLIGHT["clipped_confirm_button"]},
                      "journey": "examples/journeys/flight-booking-narrow.json", "port": 8113},
    "flight-back": {"scenarios": BACK,
                    "journey": "examples/journeys/flight-booking-back.json", "port": 8114},
    "subscription": {"scenarios": SUBSCRIPTION,
                     "journey": "examples/journeys/workspace-subscription.json", "port": 8112},
}


def serve(app, fault, port):
    server = (subscription_app if app == "subscription" else flight_app).build(port=port, fault=fault)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def journey_for(path, port, directory):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    document = copy.deepcopy(document)
    document["url"] = f"http://127.0.0.1:{port}/"
    document["allowed_origins"] = [f"http://127.0.0.1:{port}"]
    target = directory / "journey.json"
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return target


def reset(port):
    """Every run starts from an empty backend.

    Without this, run N inherits run N-1's records and the acceptance contract - which correctly
    refuses to accept unexpected leftovers as proof of this journey - fails a journey that actually
    succeeded. Resetting is part of running a journey honestly, not a way of hiding a failure.
    """
    request = urllib.request.Request(f"http://127.0.0.1:{port}/__test__/reset", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"fixture reset returned {response.status}")


def run_once(journey, directory):
    command = [sys.executable, "-m", "journey_evals.cli", "run", "--journey", str(journey),
               "--out", str(directory), "--quiet"]
    started = time.perf_counter()
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    report = directory / "report.json"
    if not report.exists():
        return {"result": "ERROR", "goal_status": "unavailable", "findings": [],
                "failed_required_checks": [], "execution_status": "no_report",
                "stderr": (process.stdout + process.stderr)[-2000:],
                "wall_ms": round((time.perf_counter() - started) * 1000), "exit_code": process.returncode}
    payload = json.loads(report.read_text(encoding="utf-8"))
    return {
        "result": payload["result"],
        "goal_status": payload["goal_status"],
        "execution_status": payload["execution_status"],
        "findings": [{"evaluator": f["evaluator"], "category": f["category"], "severity": f["severity"],
                      "provenance": f["provenance"]} for f in payload["findings"]],
        "failed_required_checks": payload["coverage"]["failed_required_checks"],
        "checks": payload["coverage"]["checks"],
        "unvisited_probes": payload["coverage"].get("unvisited_probes", []),
        "steps": payload["coverage"].get("steps", 0),
        "usage": payload["usage"],
        "timings": payload["timings"],
        "errors": payload["errors"],
        "exit_code": process.returncode,
        "wall_ms": round((time.perf_counter() - started) * 1000),
        "directory": str(directory),
    }


def judge(expectation, outcome):
    """Did this run do what the scenario says it should? Recorded per run, never re-decided."""
    if expectation["kind"] == "fault":
        wanted = set(expectation.get("expect_failed", ()))
        detected = bool(wanted & set(outcome["failed_required_checks"])) if wanted else False
        if expectation.get("expect_goal"):
            detected = detected or outcome["goal_status"] == expectation["expect_goal"]
        return {"detected": detected, "false_finding": False,
                "completed": outcome["execution_status"] in {"completed", "actor_blocked"}}
    unexpected = [f for f in outcome["findings"]]
    return {
        "detected": None,
        "false_finding": bool(unexpected) or bool(outcome["failed_required_checks"])
        or outcome["goal_status"] == "violated",
        "completed": outcome["goal_status"] == "verified",
        "matched_expected_result": outcome["result"] == expectation.get("expect_result"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="flight", choices=sorted(SUITES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    suite = SUITES[args.suite]
    scenarios = args.scenario or list(suite["scenarios"])
    app = "subscription" if args.suite == "subscription" else "flight"
    root = Path(args.out) if args.out else ROOT / "artifacts" / "eval" / f"calibration-{args.suite}"
    root.mkdir(parents=True, exist_ok=True)
    records, started = [], time.time()
    try:
        for name in scenarios:
            expectation = suite["scenarios"][name]
            server, thread = serve(app, name, suite["port"])
            try:
                for index in range(args.repeats):
                    directory = root / f"{name}-{index:02d}-{uuid.uuid4().hex[:8]}"
                    directory.mkdir(parents=True)
                    journey = journey_for(ROOT / suite["journey"], suite["port"], directory)
                    reset(suite["port"])
                    outcome = run_once(journey, directory)
                    record = {"suite": args.suite, "scenario": name, "kind": expectation["kind"],
                              "repeat": index, **outcome, "judgement": judge(expectation, outcome)}
                    records.append(record)
                    print(f"{name}[{index}] result={outcome['result']} goal={outcome['goal_status']} "
                          f"failed={outcome['failed_required_checks']} "
                          f"findings={[f['evaluator'] for f in outcome['findings']]} "
                          f"{outcome['wall_ms']}ms", flush=True)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
    finally:
        summary = summarize(records)
        (root / "records.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        (root / "summary.json").write_text(
            json.dumps({"suite": args.suite, "requested_repeats": args.repeats,
                        "started_at": started, "runs_actually_executed": len(records),
                        **summary}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return 0


def summarize(records):
    faults = [r for r in records if r["kind"] == "fault"]
    controls = [r for r in records if r["kind"] == "control"]
    detected = sum(1 for r in faults if r["judgement"]["detected"])
    false_findings = sum(1 for r in controls if r["judgement"]["false_finding"])
    clean = [r for r in controls if r["scenario"] in {"none", "fast_search", "fast_availability"}]
    completed = sum(1 for r in clean if r["goal_status"] == "verified")
    false_passes = sum(1 for r in faults
                       if r["result"] == "PASS" or (r["goal_status"] == "verified"
                                                    and not r["judgement"]["detected"]
                                                    and r["scenario"] != "none"))
    by_scenario = {}
    for record in records:
        entry = by_scenario.setdefault(record["scenario"], {"runs": 0, "detected": 0, "false_findings": 0,
                                                            "results": {}})
        entry["runs"] += 1
        entry["detected"] += bool(record["judgement"]["detected"])
        entry["false_findings"] += bool(record["judgement"]["false_finding"])
        entry["results"][record["result"]] = entry["results"].get(record["result"], 0) + 1
    wall = [r["wall_ms"] for r in records]
    return {
        "faults": {"runs": len(faults), "detected": detected},
        "controls": {"runs": len(controls), "false_findings": false_findings},
        "clean_journeys": {"runs": len(clean), "goal_verified": completed},
        "false_passes": false_passes,
        "median_wall_ms": sorted(wall)[len(wall) // 2] if wall else None,
        "model_requests_total": sum(r.get("usage", {}).get("model_requests", 0) for r in records),
        "by_scenario": by_scenario,
    }


if __name__ == "__main__":
    raise SystemExit(main())
