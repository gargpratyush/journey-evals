"""CP4: a complete defect-to-repair loop against genuinely faulty application source.

Defects are materialised as real edits to a disposable copy of the application under
``artifacts/feasibility/repair/<case>/app``. Nothing here injects a fault through a runtime
flag, because a flag cannot be repaired. The oracle, evaluators, journey and fixture controls
stay outside that copy and are hashed before the repairing agent is given the task.
"""

import difflib
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from . import evaluators, model
from .browser_checks import require_checkpoint, run_worker, source_fingerprint
from .feasibility import (
    ARTIFACTS,
    CAP,
    ROOT,
    SETTLE_MS,
    Budget,
    checkpoint_receipt,
    load_environment,
    write_json,
)
from .fixture import APP, GOAL, BookingFixture, verify
from .screening import live_window

CASES = ARTIFACTS / "repair"
APP_FILES = ("booking.html", "pricing.py")
CONFIRM_ACTIONS = ("Confirm sandbox booking", "Accept fare increase and confirm booking")
DECLARED_EFFECT_DEADLINE_MS = 1000
PROTECTED = (
    r"jev_ultrafast\evaluators.py", r"jev_ultrafast\screening.py", r"jev_ultrafast\fixture.py",
    r"jev_ultrafast\repair.py", r"jev_ultrafast\agent.py", r"jev_ultrafast\browser_checks.py",
    r"jev_ultrafast\evidence.py", r"jev_ultrafast\feasibility.py",
)


def _fare_surcharge(directory):
    """A peak-period surcharge is added to the checkout quote and never disclosed."""
    path = directory / "pricing.py"
    source = path.read_text(encoding="utf-8")
    patched = source.replace(
        "SELECTED_CENTS = 10000",
        "SELECTED_CENTS = 10000\nPEAK_SURCHARGE_CENTS = 2000",
    ).replace(
        '    return {"cents": SELECTED_CENTS, "disclosure": ""}',
        '    return {"cents": SELECTED_CENTS + PEAK_SURCHARGE_CENTS, "disclosure": ""}',
    )
    if patched == source or "PEAK_SURCHARGE_CENTS" not in patched:
        raise RuntimeError("Fare defect did not apply to the application source")
    path.write_text(patched, encoding="utf-8")


def _silent_confirmation(directory):
    """The confirmation markup is built after booking but never rendered."""
    path = directory / "booking.html"
    source = path.read_text(encoding="utf-8")
    original = (
        "          app.innerHTML=`<h2>Booking confirmed</h2>"
        "<p>${facts()} ${booking.cabin}, ${booking.passenger}</p>\n"
    )
    replacement = (
        "          const confirmation=`<h2>Booking confirmed</h2>"
        "<p>${facts()} ${booking.cabin}, ${booking.passenger}</p>\n"
    )
    if original not in source:
        raise RuntimeError("UX defect did not apply to the application source")
    path.write_text(source.replace(original, replacement), encoding="utf-8")


DEFECTS = {
    "fare-surcharge": {
        "category": "fare",
        "file": "pricing.py",
        "apply": _fare_surcharge,
        "controls": {"cents": 12000, "disclosure": (
            "The earlier fare expired. Your fare increased from USD 100 to USD 120. "
            "Accept this increase before booking."
        )},
        "symptom": (
            "A traveller who selects the USD 100.00 Economy fare is shown a different amount at checkout."
        ),
        "criterion": (
            "After the repair the traveller is charged the fare they selected (USD 100.00), or any higher "
            "total is explicitly explained at checkout and acknowledged before booking (recorded as "
            "acknowledged_cents). The frozen fare evaluator must return clean, the checkout total shown must "
            "equal the amount charged, and the independent verifier must pass with exactly one booking."
        ),
    },
    "silent-confirmation": {
        "category": "effect",
        "file": "booking.html",
        "apply": _silent_confirmation,
        "controls": {},
        "symptom": (
            "A traveller who confirms the sandbox booking is left on the checkout page with no visible outcome."
        ),
        "criterion": (
            "After the repair a booking confirmation including its confirmation identifier is visible on the "
            f"page at least {DECLARED_EFFECT_DEADLINE_MS} ms after the confirm action. The frozen effect "
            "evaluator must return clean and the independent verifier must pass with exactly one booking."
        ),
    },
}


def digests(paths):
    return {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}


def app_digests(directory):
    return {name: hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in APP_FILES}


def materialize(target, defect=None):
    target = Path(target)
    if target.exists():
        raise RuntimeError("Application copies are disposable and never reused")
    target.mkdir(parents=True)
    for name in APP_FILES:
        shutil.copy2(APP / name, target / name)
    if defect:
        DEFECTS[defect]["apply"](target)
        if app_digests(target) == app_digests(APP):
            raise RuntimeError("Defect variant is identical to the pristine application")
    return app_digests(target)


def events_of(run):
    path = Path(run["directory"]) / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def effect_window(run):
    """Did confirming the booking visibly do what it claimed, after the declared deadline elapsed?"""
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


def judge(windows, key):
    original = model.CLIENT.event_hooks
    try:
        with Budget(ARTIFACTS / "budget.json", CAP, secrets=(key,)) as budget:
            model.CLIENT.event_hooks = {"request": [budget.reserve], "response": [budget.reconcile]}
            return evaluators.evaluate(windows)
    finally:
        model.CLIENT.event_hooks = original


def journey(app_dir, controls, key, label):
    """One unchanged journey against one application copy, then the advisory findings for it."""
    with BookingFixture(app=app_dir, **controls) as fixture:
        served = fixture.quote()
        run = run_worker("autonomous", fixture.url, cap=CAP)
        bookings = fixture.snapshot()
    fare, acknowledged = live_window(run)
    effect = effect_window(run)
    findings = {f["category"]: f for f in judge([fare, effect], key)}
    independent = verify(bookings, expected_cents=served["cents"], require_ack=bool(served["disclosure"]))
    confirmation = bool(re.search(r"Confirmation TEST-\d+", effect["after_text"]))
    return {
        "label": label, "app": str(app_dir), "app_digests": app_digests(app_dir), "controls": controls,
        "served_quote": served, "run": run, "bookings": bookings, "windows": {"fare": fare, "effect": effect},
        "findings": findings, "independent_verifier": independent,
        "acknowledgement_observed": acknowledged, "confirmation_visible": confirmation,
        "usable": bool(run["status"] == "COMPLETED" and run.get("evidence_complete")),
    }


def accepted(defect, outcome):
    """The frozen, target-specific repair criterion. Absence of a finding never counts on its own."""
    if not outcome["usable"] or not outcome["independent_verifier"]["passed"]:
        return False
    if defect == "fare-surcharge":
        served, facts = outcome["served_quote"], outcome["windows"]["fare"]
        charged_as_selected = served["cents"] == 10000
        disclosed = bool(served["disclosure"]) and outcome["acknowledgement_observed"]
        return bool(
            outcome["findings"]["fare"]["outcome"] == "clean"
            and facts["after_cents"] == served["cents"]
            and (charged_as_selected or disclosed)
        )
    return bool(outcome["findings"]["effect"]["outcome"] == "clean" and outcome["confirmation_visible"])


def brief(defect, case, outcome):
    spec = DEFECTS[defect]
    return "\n".join([
        f"# Repair task {case['case_id']}",
        "",
        "## Reported problem",
        spec["symptom"],
        "",
        f"The journey under test is a fixed goal: {GOAL}",
        "",
        "## Evidence",
        f"- Journey run directory: {outcome['run']['directory']}",
        "  (events.jsonl is the ordered evidence log; evidence/*.jpg are the screenshots)",
        f"- Findings and observed page text: {case['directory']}\\pre-fix.json",
        "- Independent booking records observed by the harness: see bookings in pre-fix.json",
        "",
        "## Application source you may change",
        f"- {case['directory']}\\app\\booking.html",
        f"- {case['directory']}\\app\\pricing.py",
        "",
        "These two files are the entire application. Change nothing else: the harness, the oracle, the "
        "evaluators, the journey, the budget ledger and every file under the repository package are "
        "off limits and are hashed before and after your change.",
        "",
        "## What is expected",
        "Diagnose the reported problem from the evidence and the application source, then repair the "
        "application so a traveller completing this journey no longer experiences it. Do not change the "
        "reported symptom's observability, and do not special-case the test: repair the behaviour.",
        "Report what you changed and why.",
    ])


def prepare(defect):
    require_checkpoint("cp0")
    for name in ("cp1", "cp2"):
        if require_checkpoint(name)["source_sha256"] != source_fingerprint():
            raise RuntimeError(f"{name.upper()} must be rerun after the source changes")
    require_checkpoint("cp3")
    if defect not in DEFECTS:
        raise ValueError("Unknown defect target")
    key = load_environment(ROOT / ".env")
    case_id = uuid.uuid4().hex
    directory = CASES / f"{defect}-{case_id}"
    faulty = materialize(directory / "app", defect)
    shutil.copytree(directory / "app", directory / "as-injected")
    freeze = {
        "case_id": case_id, "defect": defect, "created_ms": round(time.time() * 1000),
        "goal": GOAL, "source_sha256": source_fingerprint(),
        "protected_inputs": digests(ROOT / name for name in PROTECTED),
        "pristine_app": app_digests(APP), "injected_app": faulty,
        "repair_criterion": DEFECTS[defect]["criterion"],
        "evaluator_category": DEFECTS[defect]["category"],
        "control_definition": DEFECTS[defect]["controls"],
        "max_repair_attempts": 2,
    }
    write_json(directory / "freeze.json", freeze)
    outcome = journey(directory / "app", {}, key, "pre-fix")
    outcome["finding_is_valid"] = bool(
        outcome["usable"] and outcome["findings"][DEFECTS[defect]["category"]]["outcome"] == "defect"
    )
    write_json(directory / "pre-fix.json", outcome)
    case = {"case_id": case_id, "directory": str(directory)}
    (directory / "brief.md").write_text(brief(defect, case, outcome), encoding="utf-8")
    summary = {
        "case_id": case_id, "defect": defect, "directory": str(directory),
        "brief": str(directory / "brief.md"), "app": str(directory / "app"),
        "finding_is_valid": outcome["finding_is_valid"],
        "finding": outcome["findings"][DEFECTS[defect]["category"]],
        "run": outcome["run"]["directory"], "agent_status": outcome["run"].get("agent_status"),
        "independent_verifier": outcome["independent_verifier"],
    }
    print(json.dumps(summary, indent=2))
    return int(not outcome["finding_is_valid"])


def latest_case(defect):
    cases = sorted(CASES.glob(f"{defect}-*"), key=lambda p: json.loads((p / "freeze.json").read_text())["created_ms"])
    if not cases:
        raise RuntimeError("Prepare the repair case first")
    return cases[-1]


def verify_repair(defect, attempt=1):
    """Check the repair against the frozen criterion, a legitimate control, and the restored defect."""
    key = load_environment(ROOT / ".env")
    directory = latest_case(defect)
    freeze = json.loads((directory / "freeze.json").read_text())
    if attempt > freeze["max_repair_attempts"]:
        raise RuntimeError("Repair attempts are capped for this case")
    protected_now = digests(ROOT / name for name in PROTECTED)
    unchanged = protected_now == freeze["protected_inputs"] and source_fingerprint() == freeze["source_sha256"]
    repaired = app_digests(directory / "app")
    changed_files = sorted(n for n in APP_FILES if repaired[n] != freeze["injected_app"][n])
    result = {
        "case_id": freeze["case_id"], "defect": defect, "attempt": attempt,
        "protected_inputs_unchanged": unchanged, "repair_criterion": freeze["repair_criterion"],
        "application_files_changed": changed_files,
        "repaired_app": repaired, "application_patch": patch(directory),
    }
    if not unchanged:
        result["status"] = "AMBER"
        result["reason"] = "Inputs outside the application copy changed during the repair"
    elif not changed_files:
        result["status"] = "AMBER"
        result["reason"] = "The application source is unchanged, so nothing was repaired"
    else:
        post = journey(directory / "app", {}, key, f"post-fix-{attempt}")
        post["accepted"] = accepted(defect, post)
        write_json(directory / f"post-fix-{attempt}.json", post)
        control_dir = directory / f"control-{attempt}"
        materialize(control_dir)
        control = journey(control_dir, freeze["control_definition"], key, f"control-{attempt}")
        control["accepted"] = accepted(defect, control)
        write_json(directory / f"control-{attempt}.json", control)
        restored_dir = directory / f"restored-{attempt}"
        shutil.copytree(directory / "app", restored_dir)
        shutil.copy2(directory / "as-injected" / DEFECTS[defect]["file"], restored_dir / DEFECTS[defect]["file"])
        restored = journey(restored_dir, {}, key, f"restored-{attempt}")
        restored["caught_again"] = bool(
            restored["usable"] and restored["findings"][DEFECTS[defect]["category"]]["outcome"] == "defect"
        )
        write_json(directory / f"restored-{attempt}.json", restored)
        result.update(
            post_fix={"run": post["run"]["directory"], "accepted": post["accepted"],
                      "finding": post["findings"][DEFECTS[defect]["category"]],
                      "independent_verifier": post["independent_verifier"],
                      "confirmation_visible": post["confirmation_visible"],
                      "served_quote": post["served_quote"]},
            control={"run": control["run"]["directory"], "accepted": control["accepted"],
                     "finding": control["findings"][DEFECTS[defect]["category"]],
                     "independent_verifier": control["independent_verifier"]},
            restored={"run": restored["run"]["directory"], "caught_again": restored["caught_again"],
                      "finding": restored["findings"][DEFECTS[defect]["category"]],
                      "restored_file_matches_injected":
                          app_digests(restored_dir)[DEFECTS[defect]["file"]]
                          == freeze["injected_app"][DEFECTS[defect]["file"]]},
        )
        result["status"] = "GREEN" if (
            post["accepted"] and control["accepted"] and restored["caught_again"]
            and result["restored"]["restored_file_matches_injected"]
        ) else "AMBER"
    write_json(directory / f"verify-{attempt}.json", result)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")


def patch(directory):
    lines = []
    for name in APP_FILES:
        before = (directory / "as-injected" / name).read_text(encoding="utf-8").splitlines(keepends=True)
        after = (directory / "app" / name).read_text(encoding="utf-8").splitlines(keepends=True)
        lines.extend(difflib.unified_diff(before, after, f"as-injected/{name}", f"repaired/{name}"))
    return "".join(lines)


def repair_checkpoint():
    """CP4 is green only when both separately constructed defects complete the whole loop."""
    require_checkpoint("cp3")
    targets = {}
    for defect in DEFECTS:
        directory = latest_case(defect)
        freeze = json.loads((directory / "freeze.json").read_text())
        pre = json.loads((directory / "pre-fix.json").read_text())
        verifications = sorted(directory.glob("verify-*.json"))
        final = json.loads(verifications[-1].read_text()) if verifications else {"status": "AMBER"}
        targets[defect] = {
            "case_id": freeze["case_id"], "directory": str(directory),
            "repair_criterion": freeze["repair_criterion"],
            "protected_inputs": freeze["protected_inputs"],
            "pre_fix": {"run": pre["run"]["directory"], "finding": pre["findings"][freeze["evaluator_category"]],
                        "finding_is_valid": pre["finding_is_valid"],
                        "agent_status": pre["run"].get("agent_status")},
            "attempts_used": len(verifications),
            "verification": final,
            "passed": bool(pre["finding_is_valid"] and final["status"] == "GREEN"
                           and len(verifications) <= freeze["max_repair_attempts"]),
        }
    result = checkpoint_receipt("cp4", {
        "status": "GREEN" if all(t["passed"] for t in targets.values()) else "AMBER",
        "source_sha256": source_fingerprint(), "targets": targets,
        "repairing_agent": "coding sub-agent in this session, given the brief, evidence and application "
                           "source only; never told which fault was injected",
        "text_entry": "NOT_TESTED",
    })
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")
