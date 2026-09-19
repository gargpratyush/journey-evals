"""Turn calibration sweeps into a receipt with the numbers the plan asks to publish.

Reads the `records.json` a sweep wrote and reports detection, false-finding rate, coverage,
latency, model requests, and measured cost. It does not re-decide any run: each record's own
judgement, made when the run happened, is the input. Runs that errored stay in the denominator,
because a tool that could not complete a journey has not shown the application is fine.
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from journey_evals.browser_checks import source_fingerprint  # noqa: E402


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def describe(records):
    faults = [r for r in records if r["kind"] == "fault"]
    controls = [r for r in records if r["kind"] == "control"]
    clean = [r for r in controls
             if r["scenario"] in {"none", "fast_search", "fast_availability"}]
    detected = [r for r in faults if r["judgement"]["detected"]]
    false_findings = [r for r in controls if r["judgement"]["false_finding"]]
    # A false pass is the failure this campaign exists to prevent: a seeded defect present, the
    # run reported clean, and the journey claimed complete.
    false_passes = [r for r in faults
                    if r["result"] == "PASS" and not r["judgement"]["detected"]]
    # A journey that ends because the actor ran out of steps on a deliberately broken page has
    # not errored: that is the correct terminal state for that scenario, and the defect was still
    # reported. Only a run where the tool itself failed counts as an execution error, and the full
    # breakdown is published either way so the distinction can be audited rather than trusted.
    tool_failures = {"no_report", "error", "cancelled", "timeout"}
    errors = [r for r in records if r["execution_status"] in tool_failures]
    statuses = {}
    for record in records:
        statuses[record["execution_status"]] = statuses.get(record["execution_status"], 0) + 1

    wall = [r["wall_ms"] for r in records]
    usd = sum((Decimal(str(r.get("usage", {}).get("usd", "0") or "0")) for r in records),
              Decimal("0"))
    requests = sum(r.get("usage", {}).get("model_requests", 0) for r in records)

    unresolved = 0
    resolved = 0
    for record in records:
        for state in (record.get("checks") or {}).values():
            if state in {"unknown", "pending", "observed"}:
                unresolved += 1
            else:
                resolved += 1

    by_scenario = {}
    for record in records:
        entry = by_scenario.setdefault(record["scenario"], {
            "kind": record["kind"], "runs": 0, "detected": 0, "false_findings": 0,
            "goal_verified": 0, "results": {}, "wall_ms": []})
        entry["runs"] += 1
        entry["detected"] += bool(record["judgement"]["detected"])
        entry["false_findings"] += bool(record["judgement"]["false_finding"])
        entry["goal_verified"] += record["goal_status"] == "verified"
        entry["results"][record["result"]] = entry["results"].get(record["result"], 0) + 1
        entry["wall_ms"].append(record["wall_ms"])
    for entry in by_scenario.values():
        entry["median_wall_ms"] = int(statistics.median(entry.pop("wall_ms")))

    return {
        "runs_executed": len(records),
        "detection": {
            "faults_run": len(faults), "detected": len(detected),
            "rate": round(len(detected) / len(faults), 4) if faults else None,
            "missed": sorted({r["scenario"] for r in faults if not r["judgement"]["detected"]}),
        },
        "false_findings": {
            "controls_run": len(controls), "with_a_spurious_finding": len(false_findings),
            "rate": round(len(false_findings) / len(controls), 4) if controls else None,
            "scenarios": sorted({r["scenario"] for r in false_findings}),
        },
        "clean_journeys": {
            "runs": len(clean),
            "goal_verified": sum(1 for r in clean if r["goal_status"] == "verified"),
        },
        "false_passes": len(false_passes),
        "execution_errors": {"count": len(errors),
                             "scenarios": sorted({r["scenario"] for r in errors}),
                             "status_breakdown": dict(sorted(statuses.items()))},
        "coverage": {"checks_resolved": resolved, "checks_unresolved": unresolved,
                     "resolution_rate": round(resolved / (resolved + unresolved), 4)
                     if resolved + unresolved else None},
        "latency_ms": {"median": percentile(wall, 0.5), "p90": percentile(wall, 0.9),
                       "min": min(wall) if wall else None, "max": max(wall) if wall else None},
        "cost": {"total_usd": f"{usd:.6f}",
                 "per_journey_usd": f"{usd / len(records):.6f}" if records else None,
                 "model_requests": requests,
                 "note": "Published-rate estimate for the evaluation model. "
                         "The text helper has no configured rate and is counted, not valued."},
        "by_scenario": by_scenario,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweeps", nargs="+", help="Sweep directories containing records.json")
    parser.add_argument("--out", required=True, help="Where to write the receipt")
    parser.add_argument("--phase", required=True, help="Which plan phase this receipt covers")
    args = parser.parse_args(argv)

    suites = {}
    everything = []
    for name in args.sweeps:
        path = Path(name)
        records = json.loads((path / "records.json").read_text(encoding="utf-8"))
        suites[path.name] = {"directory": str(path), **describe(records)}
        everything.extend(records)

    receipt = {
        "phase": args.phase,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": source_fingerprint(),
        "combined": describe(everything),
        "suites": suites,
        "limits": [
            "One run of one journey cannot establish flakiness; these are small denominators.",
            "Faults and controls are seeded by us in fixtures we own. Precision measured here "
            "does not transfer to an arbitrary application.",
            "Cost is a published-rate estimate from reported usage, not a provider invoice.",
            "No scenario was rerun to replace an outcome. Execution errors remain counted.",
            "These numbers do not justify making semantic checks blocking; that needs a larger, "
            "representative holdout and an agreed false-positive tolerance.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt["combined"], indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
