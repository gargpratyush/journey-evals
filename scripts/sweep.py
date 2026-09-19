"""Run declared journeys against seeded scenarios and report what came back.

Each row is one real run: the fixture is served with one scenario, the journey is run unchanged,
and whatever the report says is recorded. Nothing is retried and no run is dropped, so the
denominator below is the number of runs that actually happened. Every run writes to its own
directory, because the journal is append-only and measured intervals are read back out of it.

    python scripts/sweep.py meridian        # the degraded-state examples
    python scripts/sweep.py first-apps      # non-regression for the two earlier applications
    python scripts/sweep.py meridian plan   # only journeys whose id contains "plan"
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (journey, fixture app, port, scenario, what this run is for, what the journey should report)
MERIDIAN = [
    ("plan-downgrade", "admin", 8113, "none", "clean", "PASS"),
    ("plan-downgrade", "admin", 8113, "silent_downgrade", "defect", "FAIL"),
    ("plan-downgrade", "admin", 8113, "hidden_seat_loss", "defect", "FAIL"),
    ("plan-downgrade", "admin", 8113, "terse_downgrade_notice", "control", "PASS"),
    ("plan-downgrade", "admin", 8113, "schedule_claim_false", "defect", "FAIL"),
    ("workspace-deletion", "admin", 8113, "none", "clean", "PASS"),
    ("workspace-deletion", "admin", 8113, "generic_delete_warning", "defect", "FAIL"),
    ("workspace-deletion", "admin", 8113, "terse_delete_warning", "control", "PASS"),
    ("workspace-deletion", "admin", 8113, "false_restore_promise", "defect", "FAIL"),
    ("revenue-freshness", "admin", 8113, "none", "clean", "PASS"),
    ("revenue-freshness", "admin", 8113, "stale_without_caveat", "defect", "FAIL"),
    ("revenue-freshness", "admin", 8113, "terse_freshness_caveat", "control", "PASS"),
    ("revenue-freshness", "admin", 8113, "missing_refresh_feedback", "defect", "FAIL"),
    ("revenue-freshness", "admin", 8113, "slow_refresh_with_feedback", "control", "PASS"),
]

FIRST_APPS = [
    ("flight-booking", "flight", 8111, "none", "clean", "PASS"),
    ("flight-booking", "flight", 8111, "silent_fare_increase", "defect", "FAIL"),
    ("flight-booking", "flight", 8111, "disclosed_fare_increase", "control", "PASS"),
    ("flight-booking", "flight", 8111, "missing_loading_feedback", "defect", "FAIL"),
    ("flight-booking", "flight", 8111, "terse_loading_feedback", "control", "PASS"),
    ("flight-booking", "flight", 8111, "booking_never_recorded", "defect", "FAIL"),
    ("flight-booking", "flight", 8111, "clipped_confirm_button", "defect", "FAIL"),
    ("flight-booking", "flight", 8111, "janky_render", "defect", "FAIL"),
    ("flight-booking", "flight", 8111, "brief_render_work", "control", "PASS"),
    ("workspace-subscription", "subscription", 8112, "none", "clean", "PASS"),
]

SUITES = {"meridian": MERIDIAN, "first-apps": FIRST_APPS}


def port_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((host, port)) != 0


def serve(app, scenario, port):
    command = [sys.executable, "-m", "journey_evals.cli", "serve", "--app", app,
               "--fault", scenario, "--port", str(port)]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        if not port_free(port):
            return process
        if process.poll() is not None:
            raise SystemExit(f"fixture exited: {process.stdout.read().decode(errors='replace')}")
        time.sleep(0.25)
    raise SystemExit("fixture never started listening")


def stop(process, port):
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
    for _ in range(40):
        if port_free(port):
            return
        time.sleep(0.25)


def run(journey, directory):
    command = [sys.executable, "-m", "journey_evals.cli", "run", "--journey",
               str(ROOT / "examples" / "journeys" / f"{journey}.json"), "--out", str(directory),
               "--quiet"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return completed.returncode, completed.stdout + completed.stderr


def report_for(directory):
    candidates = sorted(directory.glob("**/report.json"))
    return json.loads(candidates[-1].read_text(encoding="utf-8")) if candidates else None


def sweep(cases, out):
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for index, (journey, app, port, scenario, kind, expected) in enumerate(cases, start=1):
        if not port_free(port):
            raise SystemExit(f"something is already listening on 127.0.0.1:{port}; stop it first")
        directory = out / f"{stamp}-{index:02d}-{journey}-{scenario}"
        fixture = serve(app, scenario, port)
        started = time.time()
        try:
            code, output = run(journey, directory)
        finally:
            stop(fixture, port)
        payload = report_for(directory) or {}
        findings = payload.get("findings") or []
        row = {
            "journey": journey, "app": app, "scenario": scenario, "kind": kind,
            "expected": expected, "result": payload.get("result", "NO REPORT"), "exit": code,
            "goal": payload.get("goal_status"), "execution": payload.get("execution_status"),
            "findings": [f.get("evaluator") for f in findings],
            "cost_usd": (payload.get("usage") or {}).get("usd"),
            "seconds": round(time.time() - started, 1),
            "errors": len(payload.get("errors") or []),
            "directory": directory.name,
        }
        rows.append(row)
        verdict = "as declared" if row["result"] == expected else "NOT as declared"
        print(f"[{index}/{len(cases)}] {journey} · {scenario} ({kind}) -> {row['result']} "
              f"exit {code} · goal {row['goal']} · findings {row['findings']} · {verdict}")
        if row["result"] == "NO REPORT":
            print(output[-1500:])
    (out / f"sweep-{stamp}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    agreed = sum(1 for r in rows if r["result"] == r["expected"])
    print(f"\n{agreed}/{len(rows)} runs reported what the scenario declared")
    for row in rows:
        if row["result"] != row["expected"]:
            print(f"  disagreed: {row['journey']} · {row['scenario']} expected {row['expected']}, "
                  f"got {row['result']} (goal {row['goal']}, findings {row['findings']})")
    return rows


def main(argv):
    suite = argv[0] if argv else "meridian"
    if suite not in SUITES:
        raise SystemExit(f"unknown suite {suite!r}; choose from {sorted(SUITES)}")
    selector = argv[1] if len(argv) > 1 else ""
    cases = [c for c in SUITES[suite] if selector in c[0]] if selector else SUITES[suite]
    sweep(cases, ROOT / "artifacts" / f"sweep-{suite}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main(sys.argv[1:])
