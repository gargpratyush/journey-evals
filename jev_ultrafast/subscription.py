"""Checkpoint 6: transfer the same detectors to a second, differently structured application.

The evaluator rubrics, deterministic pre-decisions, evidence envelope, browser transport and
actor loop are reused byte-for-byte. What this module adds is what the feasibility plan expects
to stay application-owned: a second journey, that application's trusted test-data bindings, and
an independent verifier that knows what a correct subscription looks like.
"""

import copy
import hashlib
import json
import random
import re
import shutil
import statistics
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .browser_checks import require_checkpoint, run_worker, source_fingerprint
from .feasibility import ARTIFACTS, CAP, RATE, ROOT, SETTLE_MS, load_environment, write_json
from .live import JOURNEY_BUDGET_MS, LOADING_THRESHOLD_MS, judge
from .repair import DECLARED_EFFECT_DEADLINE_MS, events_of

APP = ROOT / "jev_ultrafast" / "app2"
RUNS = ARTIFACTS / "second-app"
APP_FILES = ("signup.html", "pricing.py")
AVAILABILITY_ACTION = "Check workspace availability"
SUBSCRIBE_ACTIONS = ("Start sandbox subscription", "Accept price change and start subscription")

EXPECTED = {
    "plan_id": "P-TEAM", "plan": "Team",
    "workspace": "Sandbox Analytics", "email": "admin@sandbox.test",
    "selected_cents": 4000,
}
GOAL = (
    "Start a sandbox subscription on the Team plan for a workspace named Sandbox Analytics, "
    "administered by the email address admin@sandbox.test. Choose the Team plan, enter those "
    "workspace details, check that the workspace is available, and complete the sandbox "
    "subscription. Stop once the subscription confirmation is visible."
)


def app_digests(directory):
    """This application's own file set, so the first application's file list stays untouched."""
    return {name: hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in APP_FILES}


def load_app(directory):
    """Load an application source copy: its page and its own pricing implementation."""
    directory = Path(directory)
    namespace = {"__file__": str(directory / "pricing.py"), "__name__": "sandbox_app2_pricing"}
    exec(compile((directory / "pricing.py").read_text(encoding="utf-8"), str(directory / "pricing.py"), "exec"),
         namespace)
    if not callable(namespace.get("quote")):
        raise ValueError("Application pricing module must define quote()")
    return (directory / "signup.html").read_bytes(), namespace["quote"]


def verify(subscriptions, *, expected_cents=4000, require_ack=False):
    """The second application's own acceptance rules, independent of anything the model said."""
    failures = []
    if len(subscriptions) != 1:
        failures.append("Expected exactly one subscription")
    else:
        expected_fields = {**EXPECTED, "cents": expected_cents, "currency": "USD"}
        if require_ack:
            expected_fields["acknowledged_cents"] = expected_cents
        for field, expected in expected_fields.items():
            if subscriptions[0].get(field) != expected:
                failures.append(f"Wrong or missing {field}")
    return {"passed": not failures, "failures": failures, "subscription_count": len(subscriptions)}


def plan_facts(page):
    """Code owns the arithmetic and the basis. The model never extracts an amount."""
    facts = {}
    for name, label in (("selected_cents", "Selected plan"), ("checkout_cents", "Checkout total")):
        match = re.search(re.escape(label) + r": USD (\d+)\.(\d{2}) per month", page["text"])
        if match:
            facts[name] = int(match[1]) * 100 + int(match[2])
    if facts:
        identities = set(re.findall(r"Plan (P-[A-Z]+)", page["text"]))
        facts.update(currency="USD", plan_id=next(iter(identities)) if len(identities) == 1 else None,
                     period="per month", source="visible DOM text of the synthetic fixture")
    return facts


class SubscriptionFixture:
    """Loopback-only, same isolation contract as the first application's fixture."""

    def __init__(self, *, cents=None, disclosure=None, delay=0.06, app=APP):
        self.app = Path(app)
        self.html, self.pricing = load_app(self.app)
        self.cents, self.disclosure, self.delay = cents, disclosure, delay
        self.subscriptions = []
        self.availability_checks = []
        self.lock = threading.Lock()

    def quote(self):
        quote = dict(self.pricing())
        if type(quote.get("cents")) is not int or not isinstance(quote.get("disclosure"), str):
            raise ValueError("Application quote must provide integer cents and a disclosure string")
        if self.cents is not None:
            quote["cents"] = self.cents
        if self.disclosure is not None:
            quote["disclosure"] = self.disclosure
        return quote

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, data, content_type="application/json"):
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def permitted(self):
                return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

            def do_GET(self):
                if not self.permitted():
                    self.reply(403, {"error": "Loopback fixture only"})
                elif self.path == "/":
                    self.reply(200, fixture.html, "text/html; charset=utf-8")
                elif self.path == "/price":
                    self.reply(200, fixture.quote())
                else:
                    self.reply(404, {"error": "Unknown fixture route"})

            def do_POST(self):
                if not self.permitted() or self.headers.get("Origin") not in (None, fixture.url.rstrip("/")):
                    self.reply(403, {"error": "Loopback fixture only"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length < 4096:
                        raise ValueError("Invalid fixture payload size")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected fixture object")
                except (ValueError, json.JSONDecodeError):
                    self.reply(400, {"error": "Invalid fixture request"})
                    return
                if self.path == "/availability":
                    started = time.perf_counter()
                    time.sleep(fixture.delay)
                    with fixture.lock:
                        fixture.availability_checks.append(round((time.perf_counter() - started) * 1000, 3))
                    self.reply(200, {"available": True})
                elif self.path == "/subscribe":
                    fields = {key: data.get(key) for key in EXPECTED}
                    if any(fields[key] != value for key, value in EXPECTED.items()):
                        self.reply(400, {"error": "Unsupported synthetic subscription"})
                        return
                    with fixture.lock:
                        record = dict(fields, id=f"SUB-{len(fixture.subscriptions) + 1}",
                                      cents=fixture.quote()["cents"], currency="USD")
                        if "acknowledged_cents" in data:
                            record["acknowledged_cents"] = data["acknowledged_cents"]
                        fixture.subscriptions.append(record)
                    self.reply(200, record)
                else:
                    self.reply(404, {"error": "Unknown fixture route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.subscriptions)

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("Fixture server did not stop")


def _price_increase(cents):
    """A real source defect: the plan reprices at checkout with nothing said about it."""

    def apply(directory):
        path = directory / "pricing.py"
        source = path.read_text(encoding="utf-8")
        patched = source.replace(
            "TEAM_MONTHLY_CENTS = 4000",
            f"TEAM_MONTHLY_CENTS = 4000\nEXPIRED_PROMOTION_CENTS = {cents}",
        ).replace(
            '    return {"cents": TEAM_MONTHLY_CENTS, "disclosure": ""}',
            '    return {"cents": TEAM_MONTHLY_CENTS + EXPIRED_PROMOTION_CENTS, "disclosure": ""}',
        )
        if patched == source or "EXPIRED_PROMOTION_CENTS" not in patched:
            raise RuntimeError("Price defect did not apply to the application source")
        path.write_text(patched, encoding="utf-8")

    return apply


def _silent_availability(directory):
    """The availability check runs with no status line, so a slow check says nothing."""
    path = directory / "signup.html"
    source = path.read_text(encoding="utf-8")
    original = "  status.textContent = 'Checking workspace availability';\n"
    if original not in source:
        raise RuntimeError("Loading defect did not apply to the application source")
    path.write_text(source.replace(original, ""), encoding="utf-8")


def _unrendered_confirmation(directory):
    """The subscription is created, but its confirmation never reaches the document."""
    path = directory / "signup.html"
    source = path.read_text(encoding="utf-8")
    original = "    el('confirmation-body').textContent =\n"
    if original not in source:
        raise RuntimeError("Effect defect did not apply to the application source")
    path.write_text(source.replace(original, "    const unusedConfirmation =\n"), encoding="utf-8")


def _vague_feedback(directory, phrase):
    path = directory / "signup.html"
    source = path.read_text(encoding="utf-8")
    original = "  status.textContent = 'Checking workspace availability';\n"
    if original not in source:
        raise RuntimeError("Feedback control did not apply to the application source")
    path.write_text(source.replace(original, f"  status.textContent = '{phrase}';\n"), encoding="utf-8")


TERSE_DISCLOSURES = [
    "Price changed: USD 40 to USD {dollars} per month. Accept to subscribe.",
    "Your promotional rate ended. USD {dollars} per month now applies, up from USD 40. Accept the new total.",
    "Repriced to USD {dollars} per month from USD 40 after a seat-tier change. Accept to continue.",
    "Heads up - the held quote lapsed. New monthly total USD {dollars}, up from USD 40. Accept it to subscribe.",
]
VAGUE_FEEDBACK = ["Working", "One moment", "Please wait"]


def plan_cases(seed=20260919):
    """Twenty declared cases: ten real source defects, ten legitimate controls."""
    rng = random.Random(seed)
    planned = []
    for index in range(4):
        planned.append({
            "check": "fare", "group": "fault", "index": index,
            "defect": {"name": f"expired-promotion-{800 + index * 400}", "file": "pricing.py",
                       "apply": _price_increase(800 + index * 400)},
            "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)},
        })
        dollars = 44 + index * 3
        controls = {"delay": round(rng.uniform(0.05, 0.4), 3)}
        if index < 2:
            controls.update(cents=dollars * 100,
                            disclosure=TERSE_DISCLOSURES[index % len(TERSE_DISCLOSURES)].format(dollars=dollars))
        planned.append({"check": "fare", "group": "clean", "index": index, "defect": None, "controls": controls})
    for index in range(3):
        planned.append({
            "check": "effect", "group": "fault", "index": index,
            "defect": {"name": "unrendered-confirmation", "file": "signup.html", "apply": _unrendered_confirmation},
            "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)},
        })
        planned.append({"check": "effect", "group": "clean", "index": index, "defect": None,
                        "controls": {"delay": round(rng.uniform(0.05, 0.4), 3)}})
        planned.append({
            "check": "loading", "group": "fault", "index": index,
            "defect": {"name": "silent-availability", "file": "signup.html", "apply": _silent_availability},
            "controls": {"delay": round(rng.uniform(1.15, 1.75), 3)},
        })
        planned.append({"check": "loading", "group": "clean", "index": index, "defect": None,
                        "controls": {"delay": round(rng.uniform(1.15, 1.75), 3),
                                     "feedback": VAGUE_FEEDBACK[index % len(VAGUE_FEEDBACK)]}})
    rng.shuffle(planned)
    for position, item in enumerate(planned):
        item["position"] = position
        item["id"] = uuid.uuid4().hex
    return planned


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


def fare_window(run):
    events = events_of(run)
    pages = [e["data"]["page"] for e in events if e["kind"] == "observation"]
    review = next((p for p in reversed(pages) if p["title"] == "Sandbox subscription review"), None)
    facts = plan_facts(review) if review else {}
    complete = bool(run.get("evidence_complete") and {"selected_cents", "checkout_cents"} <= facts.keys())
    window = {
        "id": uuid.uuid4().hex, "kind": "fare", "complete": complete,
        "same_basis": complete and facts.get("plan_id") == "P-TEAM" and facts.get("period") == "per month",
        "before_cents": facts.get("selected_cents"), "after_cents": facts.get("checkout_cents"),
        "checkout_text": review["text"] if review else "",
    }
    acknowledged = any(
        e["kind"] == "action_executed"
        and e["data"].get("entry", {}).get("action") == "Accept price change and start subscription"
        for e in events
    )
    return window, acknowledged


def loading_window(run, availability_ms):
    """Feedback is gathered from the attempt to click, not from the recorded execution: the page can
    show its in-progress text synchronously on click, which the harness emits before it journals the
    executed action. Collection then runs until the next real action, ignoring waits, because a wait
    is the actor sitting out the response rather than the next step of the journey.
    """
    events = events_of(run)
    executed = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_executed" and e["data"].get("entry", {}).get("action") == AVAILABILITY_ACTION),
        None,
    )
    start = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_attempt" and e["data"].get("action", {}).get("label") == AVAILABILITY_ACTION),
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
        "complete": bool(run.get("evidence_complete") and start is not None and availability_ms is not None),
        "operation": "Checking whether the workspace name is available",
        "visible_feedback": list(dict.fromkeys(texts)),
        "response_ms": round(availability_ms) if availability_ms is not None else 0,
        "threshold_ms": LOADING_THRESHOLD_MS,
        "interval_source": "server-side duration of the availability request the journey triggered",
    }


def effect_window(run):
    events = events_of(run)
    index = next(
        (i for i, e in enumerate(events)
         if e["kind"] == "action_executed" and e["data"].get("entry", {}).get("action") in SUBSCRIBE_ACTIONS),
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
        "action": "Start the sandbox subscription",
        "expected_effect": "A subscription confirmation, including its subscription identifier, becomes visible.",
        "before_text": before["data"]["page"]["text"] if before else "",
        "after_text": after["data"]["page"]["text"] if after else "",
        "observed_after_ms": round(elapsed),
        "settle_ms": SETTLE_MS,
    }


def window_for(check, run, availability_ms):
    if check == "fare":
        return fare_window(run)
    return (loading_window(run, availability_ms) if check == "loading" else effect_window(run)), None


def run_case(item, key):
    directory = RUNS / item["id"]
    app_dir = directory / "app"
    digests = materialize(app_dir, item)
    controls = {k: v for k, v in item["controls"].items() if k != "feedback"}
    started = time.perf_counter()
    error = None
    run = subscriptions = served = None
    checks = []
    try:
        with SubscriptionFixture(app=app_dir, **controls) as fixture:
            served = fixture.quote()
            run = run_worker("autonomous", fixture.url, cap=CAP, goal=GOAL)
            subscriptions = fixture.snapshot()
            checks = list(fixture.availability_checks)
    except Exception as failure:  # noqa: BLE001 - execution failures stay in the denominator
        error = f"{type(failure).__name__}: {failure}"
    wall_ms = round((time.perf_counter() - started) * 1000)
    availability_ms = checks[0] if checks else None

    record = {
        "id": item["id"], "position": item["position"], "check": item["check"], "group": item["group"],
        "index": item["index"], "defect": item["defect"]["name"] if item["defect"] else None,
        "controls": item["controls"], "app_digests": digests, "served_quote": served,
        "wall_ms": wall_ms, "availability_ms": availability_ms, "execution_error": error,
    }
    if run is None:
        record.update(status="EXECUTION_FAILURE", usable=False, window=None, finding=None,
                      independent_verifier=None, journey_ms=wall_ms, within_budget=False,
                      browser_stalls=0, text_entry=None)
        write_json(directory / "case.json", record)
        return record

    journey_ms = run.get("worker_wall_ms", wall_ms)
    record.update(journey_ms=journey_ms, within_budget=journey_ms <= JOURNEY_BUDGET_MS,
                  browser_stalls=len(run.get("abandoned_browser_stalls") or []))
    window, acknowledged = window_for(item["check"], run, availability_ms)
    finding = judge(window, key) if window["complete"] else None
    expected_cents = served["cents"] if served else 4000
    independent = verify(subscriptions or [], expected_cents=expected_cents,
                         require_ack=bool(served and served["disclosure"]))
    record.update(
        status=run["status"], usable=bool(run["status"] == "COMPLETED" and run.get("evidence_complete")),
        evidence_complete=bool(run.get("evidence_complete")), run_directory=run["directory"],
        window=window, finding=finding, acknowledgement_observed=acknowledged,
        independent_verifier=independent, subscriptions=subscriptions,
        text_entry=run.get("text_entry"), text_calls=run.get("text_calls"),
        confirmation_visible=bool(re.search(r"Subscription SUB-\d+ active", window.get("after_text", ""))),
    )
    write_json(directory / "case.json", record)
    return record


def outcome_of(record):
    return "unknown" if record["finding"] is None else record["finding"]["outcome"]


def false_pass(record):
    if outcome_of(record) != "clean":
        return False
    verifier = record.get("independent_verifier") or {}
    if not verifier.get("passed"):
        return True
    return record["check"] == "effect" and not record.get("confirmation_visible")


def second_app_checkpoint(seed=20260919):
    require_checkpoint("cp5-regression")
    key = load_environment(ROOT / ".env")
    planned = plan_cases(seed)
    fingerprint = source_fingerprint()
    ledger_path = ARTIFACTS / "budget.json"
    before = len(json.loads(ledger_path.read_text())["attempts"]) if ledger_path.exists() else 0

    records = []
    for item in planned:
        records.append(run_case(item, key))
        print(json.dumps({k: records[-1][k] for k in ("position", "check", "group", "status", "wall_ms")}),
              flush=True)

    ledger = json.loads(ledger_path.read_text())
    arm = ledger["attempts"][before:]
    faults = [r for r in records if r["group"] == "fault"]
    controls = [r for r in records if r["group"] == "clean"]
    detected = [r for r in faults if outcome_of(r) == "defect"]
    spurious = [r for r in controls if outcome_of(r) == "defect"]
    definitive = [r for r in controls if outcome_of(r) == "clean"]
    completed = [r for r in controls if r["usable"] and (r["independent_verifier"] or {}).get("passed")]
    text_entered = [r for r in records if r.get("text_entry") == "TESTED"]

    gates = {
        "defects_detected": len(detected) >= 8,
        "few_false_findings": len(spurious) <= 1,
        "clean_journeys_complete": len(completed) >= 9,
        "definitive_controls": len(definitive) >= 8,
        "no_false_pass": not any(false_pass(r) for r in records),
        "text_entry_exercised": len(text_entered) >= 9,
    }
    result = {
        "status": "GREEN" if all(gates.values()) else "AMBER",
        "source_sha256": fingerprint, "seed": seed, "planned": len(planned),
        "application": "second: sandbox workspace subscription",
        "counts": {
            "faults": len(faults), "detected": len(detected),
            "controls": len(controls), "spurious": len(spurious),
            "definitive_controls": len(definitive), "clean_journeys_complete": len(completed),
            "execution_failures": sum(1 for r in records if not r["usable"]),
            "text_entry_tested": len(text_entered),
            "median_journey_ms": round(statistics.median([r["journey_ms"] for r in records])),
            "browser_stalls_retried": sum(r.get("browser_stalls", 0) for r in records),
        },
        "gates": gates,
        "spurious_finding_ids": [r["id"] for r in spurious],
        "false_pass_ids": [r["id"] for r in records if false_pass(r)],
        "receipt": {"arm_requests": len(arm),
                    "arm_input_tokens": sum(a["accounted_tokens"] for a in arm),
                    "arm_usd": str(sum(a["accounted_tokens"] for a in arm) * RATE), "cap_usd": CAP},
        "scope": ("Second-application transfer only. Detector logic, evaluators and evidence contracts "
                  "are the first application's, unchanged; the journey, bindings and verifier are new."),
    }
    write_json(RUNS / "summary" / "second-app.json", dict(result, records=records))
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")
