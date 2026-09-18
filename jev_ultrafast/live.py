"""Checkpoint 5 arm B: 60 live journeys, randomized, every attempt retained.

Each group of ten shares one target check. Fault groups run a real application-source
defect; the paired clean group runs the pristine application under deliberately awkward
but legitimate conditions, so a control is not trivially distinguishable from a fault.
"""

import json
import random
import re
import shutil
import statistics
import time
import uuid
from pathlib import Path

from . import evaluators, model
from .browser_checks import require_checkpoint, run_worker, source_fingerprint
from .feasibility import ARTIFACTS, CAP, RATE, ROOT, SETTLE_MS, Budget, load_environment, write_json
from .fixture import BookingFixture, verify
from .repair import APP_FILES, CONFIRM_ACTIONS, DECLARED_EFFECT_DEADLINE_MS, app_digests, events_of
from .screening import live_window

RUNS = ARTIFACTS / "live"
APP = ROOT / "jev_ultrafast" / "app"
JOURNEY_BUDGET_MS = 90_000
LOADING_THRESHOLD_MS = 1000
SEARCH_ACTION = "Search flights"


def _silent_search(directory):
    """The search spinner is removed, so a slow search reports nothing while it runs."""
    path = directory / "booking.html"
    source = path.read_text(encoding="utf-8")
    original = "  status.textContent='Searching flights';\n"
    if original not in source:
        raise RuntimeError("Loading defect did not apply to the application source")
    path.write_text(source.replace(original, ""), encoding="utf-8")


def _surcharge(cents):
    def apply(directory):
        path = directory / "pricing.py"
        source = path.read_text(encoding="utf-8")
        patched = source.replace(
            "SELECTED_CENTS = 10000",
            f"SELECTED_CENTS = 10000\nPEAK_SURCHARGE_CENTS = {cents}",
        ).replace(
            '    return {"cents": SELECTED_CENTS, "disclosure": ""}',
            '    return {"cents": SELECTED_CENTS + PEAK_SURCHARGE_CENTS, "disclosure": ""}',
        )
        if patched == source or "PEAK_SURCHARGE_CENTS" not in patched:
            raise RuntimeError("Fare defect did not apply to the application source")
        path.write_text(patched, encoding="utf-8")

    return apply


def _unrendered_confirmation(directory):
    path = directory / "booking.html"
    source = path.read_text(encoding="utf-8")
    original = (
        "          app.innerHTML=`<h2>Booking confirmed</h2>"
        "<p>${facts()} ${booking.cabin}, ${booking.passenger}</p>\n"
    )
    if original not in source:
        raise RuntimeError("Effect defect did not apply to the application source")
    replacement = original.replace("app.innerHTML=`", "const confirmation=`")
    path.write_text(source.replace(original, replacement), encoding="utf-8")


# Legitimate but awkward control conditions. These are the cases a careless evaluator
# misreads as defects: real disclosures written tersely, and real feedback written vaguely.
TERSE_DISCLOSURES = [
    "Fare changed: USD 100 to USD {dollars}. Accept to book.",
    "Price update. You selected USD 100; this date is USD {dollars}. Accept the new total.",
    "USD {dollars} now applies (was USD 100) after a schedule change. Accept to continue.",
    "Heads up - the held quote lapsed. New total USD {dollars}, up from USD 100. Accept it to book.",
    "Repriced to USD {dollars} from USD 100 because your cabin sold out. Accept to proceed.",
]
VAGUE_FEEDBACK = ["Working", "One moment", "Please wait", "Loading", "Checking availability"]


def plan_journeys(seed=20260918):
    """Six groups of ten. Order is randomized once and recorded, not chosen per run."""
    rng = random.Random(seed)
    planned = []
    for index in range(10):
        planned.append({
            "check": "fare", "group": "fault", "index": index,
            "defect": {"name": f"fare-surcharge-{1500 + index * 500}", "file": "pricing.py",
                       "apply": _surcharge(1500 + index * 500)},
            "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)},
        })
        dollars = 105 + index * 3
        if index < 7:
            controls = {"cents": dollars * 100,
                        "disclosure": TERSE_DISCLOSURES[index % len(TERSE_DISCLOSURES)].format(dollars=dollars),
                        "delay": round(rng.uniform(0.05, 0.4), 3)}
        else:
            controls = {"delay": round(rng.uniform(0.05, 0.4), 3)}
        planned.append({"check": "fare", "group": "clean", "index": index, "defect": None, "controls": controls})

        planned.append({
            "check": "effect", "group": "fault", "index": index,
            "defect": {"name": "unrendered-confirmation", "file": "booking.html", "apply": _unrendered_confirmation},
            "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)},
        })
        planned.append({"check": "effect", "group": "clean", "index": index, "defect": None,
                        "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)}})

        planned.append({
            "check": "loading", "group": "fault", "index": index,
            "defect": {"name": "silent-search", "file": "booking.html", "apply": _silent_search},
            "controls": {"delay": round(rng.uniform(1.15, 1.75), 3)},
        })
        if index < 7:
            controls = {"delay": round(rng.uniform(1.15, 1.75), 3),
                        "feedback": VAGUE_FEEDBACK[index % len(VAGUE_FEEDBACK)]}
        else:
            controls = {"delay": round(rng.uniform(0.05, 0.3), 3)}
        planned.append({"check": "loading", "group": "clean", "index": index, "defect": None, "controls": controls})
    rng.shuffle(planned)
    for position, item in enumerate(planned):
        item["position"] = position
        item["id"] = uuid.uuid4().hex
    return planned


def _vague_feedback(directory, phrase):
    """A control that keeps real feedback but words it unhelpfully."""
    path = directory / "booking.html"
    source = path.read_text(encoding="utf-8")
    original = "  status.textContent='Searching flights';\n"
    if original not in source:
        raise RuntimeError("Feedback control did not apply to the application source")
    path.write_text(source.replace(original, f"  status.textContent='{phrase}';\n"), encoding="utf-8")


def materialize(target, item):
    target = Path(target)
    target.mkdir(parents=True)
    for name in APP_FILES:
        shutil.copy2(APP / name, target / name)
    pristine = app_digests(APP)
    if item["defect"]:
        item["defect"]["apply"](target)
        if app_digests(target) == pristine:
            raise RuntimeError("Defect variant is identical to the pristine application")
    elif item["controls"].get("feedback"):
        _vague_feedback(target, item["controls"]["feedback"])
    return app_digests(target)


def loading_window(run, search_ms):
    """Code owns the measured response interval; the model judges only whether progress was communicated.

    The server-side duration of the search the journey triggered is the response interval, because the
    actor observes immediately after clicking and would otherwise under-measure the user's real wait.
    Feedback is gathered from the attempt to click, not from the recorded execution: the page can show
    its in-progress text synchronously on click, which the harness emits before it journals the
    executed action. Collection then runs until the next real action, ignoring waits, because a wait
    is the actor sitting out the response rather than the next step of the journey.
    """
    events = events_of(run)
    executed = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_executed" and e["data"].get("entry", {}).get("action") == SEARCH_ACTION),
        None,
    )
    start = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_attempt" and e["data"].get("action", {}).get("label") == SEARCH_ACTION),
        None,
    ) if executed is not None else None
    texts = []
    if start is not None:
        for index, event in enumerate(events[start + 1:], start + 1):
            closing = (event["kind"] == "action_executed" and index != executed
                       and event["data"].get("entry", {}).get("kind") != "wait")
            if closing:
                break
            if event["kind"] == "browser_event" and event["data"].get("kind") == "feedback":
                texts.extend(
                    region["text"] for region in event["data"]["data"]["regions"]
                    if region.get("visible") and (region.get("text") or "").strip()
                )
    return {
        "id": uuid.uuid4().hex, "kind": "loading",
        "complete": bool(run.get("evidence_complete") and start is not None and search_ms is not None),
        "operation": "Searching for available flights",
        "visible_feedback": list(dict.fromkeys(texts)),
        "response_ms": round(search_ms) if search_ms is not None else 0,
        "threshold_ms": LOADING_THRESHOLD_MS,
        "interval_source": "server-side duration of the search request the journey triggered",
    }


def effect_window(run):
    events = events_of(run)
    index = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_executed" and e["data"].get("entry", {}).get("action") in CONFIRM_ACTIONS),
        None,
    )
    before = after = None
    elapsed = 0.0
    if index is not None:
        before = next((e for e in reversed(events[:index]) if e["kind"] == "observation"), None)
        after = next((e for e in reversed(events) if e["kind"] == "settled_observation"), None)
        if after:
            elapsed = (after["host_monotonic_ns"] - events[index]["host_monotonic_ns"]) / 1e6
    deadline_elapsed = elapsed >= DECLARED_EFFECT_DEADLINE_MS
    return {
        "id": uuid.uuid4().hex, "kind": "effect",
        "complete": bool(run.get("evidence_complete") and before and after and deadline_elapsed),
        "action_acknowledged": index is not None,
        "deadline_elapsed": deadline_elapsed,
        "action": "Confirm the sandbox booking",
        "expected_effect": "A booking confirmation, including its confirmation identifier, becomes visible.",
        "before_text": before["data"]["page"]["text"] if before else "",
        "after_text": after["data"]["page"]["text"] if after else "",
        "observed_after_ms": round(elapsed),
        "settle_ms": SETTLE_MS,
    }


def window_for(check, run, search_ms):
    if check == "fare":
        window, acknowledged = live_window(run)
        return window, acknowledged
    return (loading_window(run, search_ms) if check == "loading" else effect_window(run)), None


def judge(window, key):
    """The ledger is opened only around the evaluator call; workers own it during a journey."""
    original = model.CLIENT.event_hooks
    try:
        with Budget(ARTIFACTS / "budget.json", CAP, secrets=(key,)) as budget:
            model.CLIENT.event_hooks = {"request": [budget.reserve], "response": [budget.reconcile]}
            return evaluators.evaluate([window])[0]
    finally:
        model.CLIENT.event_hooks = original


def run_journey(item, key):
    """One planned attempt. Failures are recorded, never retried into a better sample."""
    directory = RUNS / item["id"]
    app_dir = directory / "app"
    digests = materialize(app_dir, item)
    controls = {k: v for k, v in item["controls"].items() if k != "feedback"}
    started = time.perf_counter()
    error = None
    run = bookings = served = None
    searches = []
    try:
        with BookingFixture(app=app_dir, **controls) as fixture:
            served = fixture.quote()
            run = run_worker("autonomous", fixture.url, cap=CAP)
            bookings = fixture.snapshot()
            searches = list(fixture.searches)
    except Exception as failure:  # noqa: BLE001 - execution failures stay in the denominator
        error = f"{type(failure).__name__}: {failure}"
    wall_ms = round((time.perf_counter() - started) * 1000)
    search_ms = searches[0] if searches else None

    record = {
        "id": item["id"], "position": item["position"], "check": item["check"], "group": item["group"],
        "index": item["index"], "defect": item["defect"]["name"] if item["defect"] else None,
        "controls": item["controls"], "app_digests": digests, "served_quote": served,
        "wall_ms": wall_ms, "search_ms": search_ms, "execution_error": error,
    }
    if run is None:
        record.update(status="EXECUTION_FAILURE", usable=False, window=None, finding=None,
                      independent_verifier=None, journey_ms=wall_ms, within_budget=False,
                      browser_stalls=0)
        write_json(directory / "journey.json", record)
        return record

    stalls = len(run.get("abandoned_browser_stalls") or [])
    journey_ms = run.get("worker_wall_ms", wall_ms)
    record.update(journey_ms=journey_ms, within_budget=journey_ms <= JOURNEY_BUDGET_MS,
                  browser_stalls=stalls)
    window, acknowledged = window_for(item["check"], run, search_ms)
    finding = judge(window, key) if window["complete"] else None
    expected_cents = served["cents"] if served else 10000
    independent = verify(bookings or [], expected_cents=expected_cents,
                         require_ack=bool(served and served["disclosure"]))
    record.update(
        status=run["status"], usable=bool(run["status"] == "COMPLETED" and run.get("evidence_complete")),
        evidence_complete=bool(run.get("evidence_complete")), run_directory=run["directory"],
        window=window, finding=finding, acknowledgement_observed=acknowledged,
        independent_verifier=independent, bookings=bookings,
        confirmation_visible=bool(re.search(r"Confirmation TEST-\d+", window.get("after_text", ""))),
    )
    write_json(directory / "journey.json", record)
    return record


def outcome_of(record):
    if record["finding"] is None:
        return "unknown"
    return record["finding"]["outcome"]


def group_metrics(records, check, group):
    subset = [r for r in records if r["check"] == check and r["group"] == group]
    outcomes = [outcome_of(r) for r in subset]
    completed = [r for r in subset if r["usable"]]
    return {
        "planned": len(subset), "completed": len(completed),
        "execution_failures": sum(1 for r in subset if not r["usable"]),
        "defect": outcomes.count("defect"), "clean": outcomes.count("clean"),
        "unknown": outcomes.count("unknown"),
        "within_budget": sum(1 for r in subset if r["within_budget"]),
        "browser_stalls": sum(r.get("browser_stalls", 0) for r in subset),
        "median_journey_ms": round(statistics.median([r["journey_ms"] for r in subset])) if subset else None,
    }


def false_pass(record):
    """A declared acceptance condition failed while the check reported clean."""
    if outcome_of(record) != "clean":
        return False
    verifier = record.get("independent_verifier") or {}
    if not verifier.get("passed"):
        return True
    return record["check"] == "effect" and not record.get("confirmation_visible")


def live_checkpoint(seed=20260918):
    require_checkpoint("cp4")
    key = load_environment(ROOT / ".env")
    planned = plan_journeys(seed)
    directory = RUNS / "summary"
    directory.mkdir(parents=True, exist_ok=True)
    fingerprint = source_fingerprint()

    ledger_path = ARTIFACTS / "budget.json"
    before = len(json.loads(ledger_path.read_text())["attempts"]) if ledger_path.exists() else 0
    records = []
    for item in planned:
        records.append(run_journey(item, key))
        print(json.dumps({k: records[-1][k] for k in ("position", "check", "group", "status", "wall_ms")}),
              flush=True)
    ledger = json.loads(ledger_path.read_text())
    arm = ledger["attempts"][before:]
    receipt = {"arm_requests": len(arm), "arm_input_tokens": sum(a["accounted_tokens"] for a in arm),
               "arm_usd": str(sum(a["accounted_tokens"] for a in arm) * RATE), "cap_usd": CAP}

    metrics = {check: {group: group_metrics(records, check, group) for group in ("fault", "clean")}
               for check in ("fare", "effect", "loading")}
    clean_records = [r for r in records if r["group"] == "clean"]
    spurious = [r["id"] for r in clean_records if outcome_of(r) == "defect"]
    gates = {
        "clean_journeys_complete": sum(1 for r in clean_records if r["usable"]) >= 27,
        "detection_per_check": {c: metrics[c]["fault"]["defect"] >= 8 for c in metrics},
        "spurious_total": len(spurious) <= 2,
        "spurious_per_group": {
            c: sum(1 for r in clean_records if r["check"] == c and outcome_of(r) == "defect") <= 1
            for c in metrics},
        "definitive_controls_per_check": {c: metrics[c]["clean"]["clean"] >= 8 for c in metrics},
        "no_false_pass": not any(false_pass(r) for r in records),
        "latency_budget": sum(1 for r in records if r["within_budget"]) >= round(0.95 * len(records)),
    }
    flat = []
    for value in gates.values():
        flat.extend(value.values() if isinstance(value, dict) else [value])
    result = {
        "status": "GREEN" if all(flat) else "AMBER",
        "source_sha256": fingerprint, "seed": seed, "planned": len(planned),
        "metrics": metrics, "gates": gates, "spurious_finding_ids": spurious,
        "false_pass_ids": [r["id"] for r in records if false_pass(r)],
        "receipt": receipt,
        "all_attempt_rates": {
            "planned": len(records),
            "completed": sum(1 for r in records if r["usable"]),
            "execution_failures": sum(1 for r in records if not r["usable"]),
            "median_wall_ms": round(statistics.median([r["wall_ms"] for r in records])),
            "median_journey_ms": round(statistics.median([r["journey_ms"] for r in records])),
            "browser_stalls_retried": sum(r.get("browser_stalls", 0) for r in records),
            "latency_note": ("within_budget uses the successful worker attempt; stall retries are "
                             "reported separately as runner errors, not hidden in the elapsed time"),
        },
        "scope": "Arm B only: live repeatability. Held-out quality and overhead are separate arms.",
    }
    write_json(directory / "live.json", dict(result, records=records))
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")
