"""Checkpoint 5 arm D: collector overhead on matched scripted pairs, with no model calls.

Each pair drives the identical interaction script twice, once with telemetry installed and
once without, in a randomized within-pair order so warm-up cannot favour one condition.
Only native interaction time is compared; the instrumented run's extra bookkeeping is the
quantity under test.
"""

import json
import random
import statistics
import time
import uuid
from pathlib import Path

from .browser_checks import require_checkpoint, run_worker, source_fingerprint
from .feasibility import ARTIFACTS, CAP, write_json
from .fixture import BookingFixture

PAIRS = 10
STEP_TOLERANCE_MS = 20
SLOWDOWN_TOLERANCE = 0.10


def timings(run):
    path = Path(run["directory"]) / "overhead.json"
    return json.loads(path.read_text()) if path.exists() else None


def measure(mode, url):
    started = time.perf_counter()
    run = run_worker(mode, url, cap=CAP)
    return {"mode": mode, "status": run["status"], "directory": run["directory"],
            "wall_ms": round((time.perf_counter() - started) * 1000), "steps": timings(run),
            "error": run.get("error")}


def overhead_checkpoint(pairs=PAIRS, seed=20260918):
    """No inference runs here, so this arm costs nothing and can be repeated freely."""
    require_checkpoint("cp4")
    rng = random.Random(seed)
    directory = ARTIFACTS / "overhead" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    results = []
    with BookingFixture() as fixture:
        for index in range(pairs):
            order = ["overhead-on", "overhead-off"]
            rng.shuffle(order)
            pair = {"pair": index, "order": list(order)}
            for mode in order:
                pair[mode] = measure(mode, fixture.url)
            results.append(pair)
            print(json.dumps({"pair": index, "order": order,
                              "on": pair["overhead-on"]["status"], "off": pair["overhead-off"]["status"]}),
                  flush=True)

    usable = [p for p in results
              if p["overhead-on"]["steps"] and p["overhead-off"]["steps"]
              and p["overhead-on"]["status"] == p["overhead-off"]["status"] == "COMPLETED"]
    summary = {"pairs_planned": pairs, "pairs_usable": len(usable)}
    if usable:
        on = [p["overhead-on"]["steps"]["native_total_ms"] for p in usable]
        off = [p["overhead-off"]["steps"]["native_total_ms"] for p in usable]
        per_step_on = [p["overhead-on"]["steps"]["native_median_step_ms"] for p in usable]
        per_step_off = [p["overhead-off"]["steps"]["native_median_step_ms"] for p in usable]
        median_on, median_off = statistics.median(on), statistics.median(off)
        step_delta = statistics.median(per_step_on) - statistics.median(per_step_off)
        slowdown = (median_on - median_off) / median_off if median_off else None
        summary.update(
            steps_per_run=usable[0]["overhead-on"]["steps"]["steps"],
            native_total_median_ms={"instrumented": round(median_on), "bare": round(median_off)},
            native_median_step_ms={"instrumented": round(statistics.median(per_step_on), 2),
                                   "bare": round(statistics.median(per_step_off), 2)},
            median_slowdown=round(slowdown, 4) if slowdown is not None else None,
            median_step_delta_ms=round(step_delta, 2),
            collector_events_median=statistics.median(
                [p["overhead-on"]["steps"]["collector_events"] for p in usable]),
            within_tolerance=bool(
                slowdown is not None
                and (slowdown <= SLOWDOWN_TOLERANCE or step_delta <= STEP_TOLERANCE_MS)),
            tolerance=(f"investigate above {SLOWDOWN_TOLERANCE:.0%} median slowdown or "
                       f"{STEP_TOLERANCE_MS} ms per scripted step, whichever is larger"),
        )
    result = {
        "status": "GREEN" if summary.get("within_tolerance") and len(usable) >= 8 else "AMBER",
        "source_sha256": source_fingerprint(), "seed": seed, "summary": summary,
        "inference": "none; this arm never enables a model client",
        "scope": "Arm D only: collector overhead on this fixture, not a browser-wide guarantee.",
    }
    write_json(directory / "overhead.json", dict(result, pairs=results))
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")
