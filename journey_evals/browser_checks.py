"""Checkpoint workers: one browser process/profile, fixture, and evidence journal."""

import argparse
import contextlib
import hashlib
import io
import json
import os
import runpy
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .evidence import Collector, Journal
from .feasibility import (
    ARTIFACTS,
    CAP,
    ROOT,
    SETTLE_MS,
    SLOW_MACHINE_TIMEOUT,
    Budget,
    checkpoint_receipt,
    load_environment,
    write_json,
)
from .fixture import GOAL, BookingFixture, fare_facts, verify
from .isolation import OS_ENV, OwnedSession

# A worker bounds startup and cleanup at SLOW_MACHINE_TIMEOUT each, so allow both plus the journey.
WORKER_TIMEOUT = 180 + 2 * SLOW_MACHINE_TIMEOUT


def source_fingerprint():
    digest = hashlib.sha256()
    for folder in (ROOT / "journey_evals", ROOT / "jev_ultrafast", ROOT / "tests"):
        for path in sorted(folder.rglob("*")):
            if path.suffix in {".py", ".js", ".html"} and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def scripted_checks(browser, collector, journal):
    def observe(label):
        page = browser.observe(screenshot=True)
        journal.append("observation", {"phase": label, "page": page, "fare_facts": fare_facts(page)})
        assert collector.checkpoint(label)["complete"], "Required evidence gap in scripted clean flow"
        return page

    def act(kind, label=None, value=None):
        page = observe("before-scripted-action")
        action = next(a for a in page["actions"] if a["kind"] == kind and (
            a.get("value") == value if value is not None else a["label"] == label
        ))
        journal.append("action_attempt", {"action": action, "scripted": True})
        browser.act(action, page)
        journal.append("action_executed", {"action": action, "scripted": True})
        return observe("after-scripted-action")

    initial = observe("initial")
    assert any(c["label"] == "Search flights" and c["disabled"] for c in initial["evaluation"]["controls"])
    assert not any(a["label"] == "Search flights" for a in initial["actions"])
    assert browser.evaluate("localStorage.getItem('jev-profile-marker')") is None
    assert "jev_profile_marker" not in browser.evaluate("document.cookie")
    browser.evaluate("localStorage.setItem('jev-profile-marker','previous');document.cookie='jev_profile_marker=previous'")
    act("select", value="London")
    act("select", value="2026-09-20")
    act("click", "Search flights")
    deadline = time.monotonic() + 3
    while "Choose Economy fare USD 100.00" not in browser.observe(screenshot=False)["text"]:
        if time.monotonic() > deadline:
            raise TimeoutError("Synthetic search did not produce results")
        time.sleep(0.03)
    act("click", "Choose Economy fare USD 100.00")
    act("select", value="Test Passenger")
    checkout = act("click", "Review booking")
    assert fare_facts(checkout)["selected_cents"] == fare_facts(checkout)["checkout_cents"] == 10000
    final = act("click", "Confirm sandbox booking")
    assert "Booking confirmed" in final["text"]
    booking_watermark = collector.checkpoint("booking-complete")
    browser.evaluate("""const select=document.createElement('select');
      select.setAttribute('aria-label','Capture choice');
      select.innerHTML='<option>A</option><option>B</option>';document.body.append(select)""")
    for index in range(10):
        page = observe("capture-before-delay")
        action = next(a for a in page["actions"] if a["kind"] == "select")
        time.sleep(0.4)
        browser.act(action, page)
        observe(f"capture-after-delay-{index}")
    browser.evaluate("""const pulse=document.createElement('button');pulse.textContent='Pulse loading';
      pulse.onclick=()=>{document.querySelector('#status').textContent='Brief loading';
        setTimeout(()=>document.querySelector('#status').textContent='',30)};
      document.body.append(pulse)""")
    before = observe("before-pulse")
    browser.act(next(a for a in before["actions"] if a["label"] == "Pulse loading"), before)
    time.sleep(0.15)
    after = observe("after-pulse")
    assert "Brief loading" not in before["text"] and "Brief loading" not in after["text"]
    events = [json.loads(line) for line in (journal.directory / "events.jsonl").read_text().splitlines()]
    assert any(
        e["kind"] == "browser_event" and e["data"]["kind"] == "feedback"
        and any(r["text"] == "Brief loading" and r["visible"] for r in e["data"]["data"]["regions"])
        for e in events
    ), "Continuous collector missed a pulse between endpoint snapshots"
    browser.call(
        "Runtime.evaluate",
        expression="(async()=>{console.error('Synthetic console check');"
                   "await fetch('/intentional-error');return true})()",
        awaitPromise=True, returnByValue=True,
    )
    assert collector.checkpoint("after-error-probes")["complete"]
    error_events = [json.loads(line) for line in (journal.directory / "events.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "console_message" and "Synthetic console check" in e["data"]["text"] for e in error_events)
    assert any(e["kind"] == "http_failure" and e["data"]["status"] == 404 for e in error_events)
    page = observe("before-navigation")
    stale_action = next(a for a in page["actions"] if a["label"] == "Pulse loading")
    browser.evaluate("setTimeout(()=>location.href='/navigated',30)")
    time.sleep(0.25)
    from jev_ultrafast.browser import StalePage
    try:
        browser.act(stale_action, page)
    except StalePage:
        journal.append("action_rejected_stale", {"reason": "navigation during simulated inference"})
    else:
        raise AssertionError("Stale action executed after navigation")
    navigation = collector.checkpoint("after-navigation")
    assert not navigation["complete"]
    assert any("navigation_tail_unacknowledged" in d["gaps"] for d in navigation["documents"].values())
    return {
        "booking_evidence_complete": booking_watermark["complete"],
        "disabled_controls_observed_not_actionable": True, "capture_regression_rounds": 10,
        "transient_feedback_captured": True, "navigation_gap_explicit": True, "stale_navigation_rejected": True,
        "console_and_network_errors_captured": True,
    }


def overhead_script(browser, collector, journal, instrumented):
    """The identical interaction script under both conditions. Only native step time is compared."""
    steps = []

    def observe():
        started = time.perf_counter()
        page = browser.observe(screenshot=False)
        steps.append(round((time.perf_counter() - started) * 1000, 3))
        if instrumented:
            journal.append("observation", {"phase": "overhead", "page": page})
            collector.checkpoint("overhead-step")
        return page

    def act(kind, label=None, value=None):
        page = observe()
        action = next(a for a in page["actions"] if a["kind"] == kind and (
            a.get("value") == value if value is not None else a["label"] == label
        ))
        started = time.perf_counter()
        browser.act(action, page)
        steps.append(round((time.perf_counter() - started) * 1000, 3))

    observe()
    act("select", value="London")
    act("select", value="2026-09-20")
    act("click", "Search flights")
    deadline = time.monotonic() + 5
    while "Choose Economy fare USD 100.00" not in browser.observe(screenshot=False)["text"]:
        if time.monotonic() > deadline:
            raise TimeoutError("Synthetic search did not produce results")
        time.sleep(0.03)
    act("click", "Choose Economy fare USD 100.00")
    act("select", value="Test Passenger")
    act("click", "Review booking")
    act("click", "Confirm sandbox booking")
    observe()
    events = 0
    if instrumented:
        journal_path = journal.directory / "events.jsonl"
        events = sum(1 for line in journal_path.read_text(encoding="utf-8").splitlines()
                     if json.loads(line)["kind"] == "browser_event")
    return {
        "instrumented": instrumented, "steps": len(steps), "native_step_ms": steps,
        "native_total_ms": round(sum(steps), 3),
        "native_median_step_ms": round(statistics.median(steps), 3),
        "collector_events": events,
    }


def worker(args):
    url = urlparse(args.url)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("Checkpoint workers accept only the owned loopback fixture")
    directory = args.out.resolve()
    if not directory.is_relative_to((ARTIFACTS / "runs").resolve()):
        raise ValueError("Worker artifacts must stay under the project run directory")
    journal, session = Journal(directory), OwnedSession(directory)
    collector = Collector(journal)
    result = {"schema_version": 1, "status": "RUNNING", "mode": args.mode, "source_sha256": source_fingerprint()}
    key = None
    try:
        with session:
            result["browser_version"] = session.version
            result["child_credentials_absent"] = not any(k.endswith("_API_KEY") for k in session.environment)
            from jev_ultrafast import model
            from jev_ultrafast.agent import Agent
            from jev_ultrafast.browser import Browser

            if args.mode != "autonomous":
                def no_inference(_request):
                    raise RuntimeError("Paid inference is disabled in this mode")
                model.CLIENT.event_hooks = {"request": [no_inference]}
            browser = None
            instrumented = args.mode != "overhead-off"
            try:
                browser = Browser(args.url, before_navigate=collector.install if instrumented else None,
                                  foreground=True)
                if args.mode == "cancel":
                    raise KeyboardInterrupt
                if args.mode == "isolation":
                    assert browser.evaluate("localStorage.getItem('jev-profile-marker')") is None
                    assert "jev_profile_marker" not in browser.evaluate("document.cookie")
                    assert collector.checkpoint("fresh-profile")["complete"]
                    result["fresh_profile_isolated"] = True
                elif args.mode == "scripted":
                    result.update(scripted_checks(browser, collector, journal))
                    guard_output = io.StringIO()
                    with contextlib.redirect_stdout(guard_output):
                        runpy.run_path(str(ROOT / "scripts" / "check_guards.py"), run_name="__main__")
                    text = guard_output.getvalue()
                    (directory / "browser-guards.txt").write_text(text, encoding="utf-8")
                    last = text.strip().splitlines()[-1]
                    assert last.startswith("PASS: ")
                    result["native_guard_checks"] = int(last.split()[1])
                elif args.mode in ("overhead-on", "overhead-off"):
                    measured = overhead_script(browser, collector, journal, instrumented)
                    write_json(directory / "overhead.json", measured)
                    result["overhead"] = {k: v for k, v in measured.items() if k != "native_step_ms"}
                elif args.mode == "autonomous":
                    key = load_environment(ROOT / ".env")
                    with Budget(ARTIFACTS / "budget.json", args.jev_budget_usd, secrets=(key,)) as budget:
                        model.CLIENT.event_hooks = {"request": [budget.reserve], "response": [budget.reconcile]}
                        started = time.perf_counter()
                        with Agent(args.url, args.goal, browser_factory=lambda _url: browser, screenshots=True,
                                   event_sink=journal.append) as agent:
                            for state in agent.run():
                                watermark = collector.checkpoint("after-agent-cycle")
                                if not watermark["complete"]:
                                    raise RuntimeError("Required journey evidence is incomplete")
                                if time.perf_counter() - started > 90:
                                    raise TimeoutError("Journey wall-time budget exceeded")
                            time.sleep(SETTLE_MS / 1000)
                            settled = browser.observe(screenshot=False)
                            journal.append("settled_observation", {"page": settled, "waited_ms": SETTLE_MS})
                            result.update(
                                agent_status=state["status"], actions=len(state["history"]),
                                decisions=len(state["decisions"]), actor_elapsed_ms=state["elapsed_ms"],
                                # Reported from what the journey actually did, so a checkpoint can never
                                # inherit a text-entry claim the run did not exercise.
                                text_entry="TESTED" if state["text_calls"] else "NOT_TESTED",
                                text_calls=[{k: v for k, v in call.items() if k != "value"}
                                            for call in state["text_calls"]],
                                evidence_complete=watermark["complete"],
                            )
                            frozen = agent.snapshot()
                            frozen["page"].pop("screenshot", None)
                            write_json(directory / "agent.json", frozen)
                        result["budget"] = budget.receipt()
                result["status"] = "COMPLETED"
            finally:
                collector.close()
                if browser:
                    browser.close()
                if collector.errors:
                    raise RuntimeError("Evidence consumer failed; inspect telemetry.json")
    except KeyboardInterrupt:
        result["status"] = "CANCELLED_AS_EXPECTED" if args.mode == "cancel" else "CANCELLED"
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        result.update(status="ERROR", error=message.replace(key, "[REDACTED]") if key else message)
    finally:
        journal.close()
        result["resources_closed"] = getattr(session, "closed", False)
        result["profile_removed"] = hasattr(session, "directory") and not session.directory.exists()
        result["cleanup_retries"] = session.cleanup_retries
        result["cleanup_events"] = session.cleanup_events
        result["cleanup_errors"] = session.cleanup_errors
        if session.cleanup_errors and result["status"] != "ERROR":
            result.update(status="ERROR", error="Owned resource cleanup failed")
        write_json(directory / "worker.json", result)
    return int(result["status"] not in {"COMPLETED", "CANCELLED_AS_EXPECTED"})


def _run_worker_once(mode, url, cap, goal):
    directory = ARTIFACTS / "runs" / (mode + "-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    command = [sys.executable, "-m", "journey_evals.browser_checks", "--mode", mode,
               "--url", url, "--out", str(directory), "--jev-budget-usd", cap, "--goal", goal]
    environment = {k: v for k, v in os.environ.items() if k.upper() in OS_ENV}
    started = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True,
                                 timeout=WORKER_TIMEOUT)
    except subprocess.TimeoutExpired as expired:
        # Killing the worker would orphan its Chrome and daemon, so this is never retried.
        (directory / "worker-output.txt").write_text(str(expired), encoding="utf-8")
        return {"status": "ERROR", "error": f"Worker exceeded {WORKER_TIMEOUT}s and was killed",
                "directory": str(directory), "resources_closed": False, "profile_removed": False,
                "exit_code": None}
    (directory / "worker-output.txt").write_text(process.stdout + process.stderr, encoding="utf-8")
    receipt = directory / "worker.json"
    if not receipt.exists():
        return {"status": "ERROR", "error": "Worker exited without a receipt", "directory": str(directory)}
    result = json.loads(receipt.read_text())
    result.update(directory=str(directory), exit_code=process.returncode,
                  worker_wall_ms=round((time.perf_counter() - started) * 1000))
    return result


def browser_stalled(result):
    """A Chrome/CDP command that never returned, as opposed to anything this code decided."""
    return result.get("status") == "ERROR" and "waiting for the daemon" in str(result.get("error", ""))


def run_worker(mode, url, *, cap=CAP, attempts=3, goal=GOAL):
    """Retry a stalled browser only while no inference has happened, so no judgement is ever resampled."""
    ledger = ARTIFACTS / "budget.json"
    abandoned = []
    for attempt in range(attempts):
        before = ledger.read_bytes() if ledger.exists() else b""
        result = _run_worker_once(mode, url, cap, goal)
        spent = (ledger.read_bytes() if ledger.exists() else b"") != before
        if not browser_stalled(result) or spent or attempt == attempts - 1:
            result["inference_started"] = spent
            result["abandoned_browser_stalls"] = abandoned
            return result
        abandoned.append({"attempt": attempt, "directory": result["directory"], "error": result["error"],
                          "inference_started": False})


def require_checkpoint(name):
    path = ARTIFACTS / "checkpoints" / f"{name}.json"
    if not path.exists() or json.loads(path.read_text())["status"] != "GREEN":
        raise RuntimeError(f"{name.upper()} must be GREEN before this checkpoint")
    return json.loads(path.read_text())


def scan_published_artifacts(key):
    scanned = [
        path for path in ARTIFACTS.rglob("*")
        if not path.is_relative_to(ARTIFACTS / "work") and path.is_file()
    ]
    return {"files": len(scanned), "passed": not any(key in path.read_bytes() for path in scanned),
            "scope": "Published artifacts only; private browser work profiles excluded"}


def browser_checkpoint():
    require_checkpoint("cp0")
    ledger_before = (ARTIFACTS / "budget.json").read_bytes()
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
    (ARTIFACTS / "offline-tests.txt").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    result = {"status": "AMBER", "source_sha256": source_fingerprint(), "offline_tests_passed": tests.returncode == 0}
    if tests.returncode == 0:
        with BookingFixture() as fixture:
            scripted = run_worker("scripted", fixture.url)
            scripted["independent_verifier"] = verify(fixture.snapshot())
            isolated = run_worker("isolation", fixture.url)
            cancelled = run_worker("cancel", fixture.url)
        result["workers"] = [scripted, isolated, cancelled]
        unchanged = (ARTIFACTS / "budget.json").read_bytes() == ledger_before
        result["inference_attempts"] = 0 if unchanged else None
        key = load_environment(ROOT / ".env").encode()
        result["secret_scan"] = scan_published_artifacts(key)
        if (
            scripted["status"] == isolated["status"] == "COMPLETED"
            and cancelled["status"] == "CANCELLED_AS_EXPECTED"
            and scripted["independent_verifier"]["passed"]
            and all(x["resources_closed"] and x["profile_removed"] and x["exit_code"] == 0 for x in result["workers"])
            and unchanged and result["secret_scan"]["passed"]
        ):
            result["status"] = "GREEN"
    result = checkpoint_receipt("cp1", result)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")


def clean_checkpoint(cap):
    require_checkpoint("cp0")
    prerequisite = require_checkpoint("cp1")
    if prerequisite["source_sha256"] != source_fingerprint():
        raise RuntimeError("Runtime changed after CP1; rerun its offline gate first")
    batch = uuid.uuid4().hex
    write_json(ARTIFACTS / "cohorts" / f"cp2-{batch}.json", {
        "attempts_planned": 3, "goal": GOAL, "source_sha256": source_fingerprint(), "batch_id": batch,
    })
    runs = []
    key = load_environment(ROOT / ".env").encode()
    with BookingFixture() as fixture:
        for _ in range(3):
            started = time.perf_counter()
            fixture.reset()
            run = run_worker("autonomous", fixture.url, cap=cap)
            run["independent_verifier"] = verify(fixture.snapshot())
            run["artifacts_secret_free"] = not any(
                key in path.read_bytes() for path in Path(run["directory"]).rglob("*") if path.is_file()
            )
            run["passed"] = bool(
                run["status"] == "COMPLETED" and run.get("agent_status") == "done"
                and run.get("evidence_complete") and run["independent_verifier"]["passed"]
                and run["resources_closed"] and run["profile_removed"]
                and run["exit_code"] == 0 and run["artifacts_secret_free"]
            )
            write_json(Path(run["directory"]) / "report.json", run)
            timing = {"through_first_report_persistence_ms": round((time.perf_counter() - started) * 1000)}
            write_json(Path(run["directory"]) / "timing.json", timing)
            run["timing"] = timing
            runs.append(run)
    result = checkpoint_receipt("cp2", {
        "status": "GREEN" if all(r["passed"] for r in runs) else "AMBER",
        "batch_id": batch, "planned": 3, "completed_and_verified": sum(r["passed"] for r in runs),
        "runs": runs, "text_entry": "NOT_TESTED", "source_sha256": source_fingerprint(),
    })
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("scripted", "isolation", "cancel", "autonomous",
                                                          "overhead-on", "overhead-off"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--jev-budget-usd", default=CAP)
    parser.add_argument("--goal", default=GOAL,
                        help="The journey to pursue. Defaults to the first application's goal.")
    return worker(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
