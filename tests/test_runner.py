"""The run lifecycle: evidence windows, independent acceptance, runtime findings and accounting.

These are the parts that decide what a run is allowed to claim. The recurring theme is that an
absent or unreadable piece of evidence must produce an honest "unknown", never a quiet pass, and
that timing is measured against a clock the page cannot influence.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from journey_evals.contracts import journey_from_dict
from journey_evals.runner import (
    Run,
    account,
    effect_window,
    loading_window,
    runtime_findings,
    verify_goal,
)

CHECK = {
    "id": "c1", "family": "interaction_correctness", "scope": "transition",
    "applies_when": {"predicate": "action_executed", "value": "Search"},
    "requirement": "Searching shows results.", "deadline_ms": 4000, "severity": "high",
    "required_evidence": ["observation"],
    "expect": {"effect": "Results are visible.",
               "ready_when": {"predicate": "text_contains", "value": "Available flights"}},
}


def journey(**overrides):
    document = {
        "schema_version": 1, "id": "demo", "mode": "verify", "url": "http://127.0.0.1:8111/",
        "allowed_origins": ["http://127.0.0.1:8111"], "task": "Do the declared thing.",
        "viewport": {"width": 1120, "height": 780}, "facts": {}, "probes": [],
        "checks": [json.loads(json.dumps(CHECK))],
        "acceptance": {"url_path_is": "/done", "text_contains": ["Confirmed"]},
        "budgets": {"wall_ms": 300000, "model_requests": 80, "steps": 24, "usd": "0.10"},
    }
    return journey_from_dict({**document, **overrides})


def check_spec(**expect):
    document = json.loads(json.dumps(CHECK))
    document["expect"] = {**document["expect"], **expect}
    return journey(checks=[document]).checks[0]


def timeline(*entries):
    """Build a journal and observation list with explicit sequences and host clock readings."""
    events, observations, sequence = [], [], 0
    for kind, offset_ms, payload in entries:
        sequence += 1
        event = {"schema_version": 1, "sequence": sequence, "host_monotonic_ns": offset_ms * 1_000_000,
                 "kind": kind, "data": payload}
        events.append(event)
        if kind == "observation":
            observations.append({
                "id": f"obs-{sequence:06d}", "sequence": sequence,
                "host_monotonic_ns": event["host_monotonic_ns"], "phase": payload.get("phase", "x"),
                "text": payload.get("page", {}).get("text", ""),
                "url": payload.get("page", {}).get("url", ""),
                "title": "", "actions": [], "evaluation_elements": [], "complete": True,
            })
    return events, observations


# -- the effect window --------------------------------------------------------------------------


def test_an_action_that_was_never_executed_leaves_the_window_unacknowledged():
    events, observations = timeline(("observation", 0, {"page": {"text": "before"}}))
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=99, deadline_ms=4000)
    assert window["action_acknowledged"] is False


def test_a_window_with_no_observation_after_the_action_has_not_reached_its_deadline():
    events, observations = timeline(
        ("observation", 0, {"page": {"text": "before"}}),
        ("action_executed", 10, {"action": "Search"}),
    )
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=2, deadline_ms=4000)
    assert window["action_acknowledged"] is True
    assert window["deadline_elapsed"] is False


def test_an_effect_not_yet_due_is_reported_as_pending_rather_than_absent():
    events, observations = timeline(
        ("observation", 0, {"page": {"text": "before"}}),
        ("action_executed", 10, {"action": "Search"}),
        ("observation", 60, {"page": {"text": "still working"}}),
    )
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=2, deadline_ms=4000)
    assert window["deadline_elapsed"] is False
    assert window["observed_after_ms"] == 50


def test_observing_the_declared_effect_closes_the_window_before_its_deadline():
    events, observations = timeline(
        ("observation", 0, {"page": {"text": "before"}}),
        ("action_executed", 10, {"action": "Search"}),
        ("observation", 70, {"page": {"text": "Available flights: 3"}}),
    )
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=2, deadline_ms=4000,
                           ready_when=CHECK["expect"]["ready_when"])
    assert window["ready_observed"] is True
    assert window["deadline_elapsed"] is True
    assert window["observed_after_ms"] == 60


def test_an_elapsed_deadline_closes_the_window_even_without_the_effect():
    events, observations = timeline(
        ("observation", 0, {"page": {"text": "before"}}),
        ("action_executed", 10, {"action": "Search"}),
        ("observation", 4500, {"page": {"text": "nothing happened"}}),
    )
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=2, deadline_ms=4000,
                           ready_when=CHECK["expect"]["ready_when"])
    assert window["ready_observed"] is False
    assert window["deadline_elapsed"] is True
    assert window["before_text"] == "before"
    assert window["after_text"] == "nothing happened"


def test_the_window_cites_the_observations_it_compared():
    events, observations = timeline(
        ("observation", 0, {"page": {"text": "before"}}),
        ("action_executed", 10, {"action": "Search"}),
        ("observation", 4500, {"page": {"text": "after"}}),
    )
    window = effect_window(events, observations, check_spec(), action="Search",
                           executed_sequence=2, deadline_ms=4000)
    assert window["observation_ids"] == ["obs-000001", "obs-000003"]


# -- the loading window -------------------------------------------------------------------------


def loading_check(**expect):
    document = json.loads(json.dumps(CHECK))
    document["family"] = "experience_feedback"
    document["required_evidence"] = ["telemetry_window"]
    document["expect"] = {"operation": "the search", "threshold_ms": 1000,
                          "ready_when": {"predicate": "text_contains", "value": "Available flights"},
                          **expect}
    return journey(checks=[document]).checks[0]


def test_the_interval_is_measured_to_the_first_observation_that_satisfies_readiness():
    events, observations = timeline(
        ("action_attempt", 0, {"action": {"label": "Search"}}),
        ("action_executed", 10, {"entry": {"action": "Search", "kind": "click"}}),
        ("browser_event", 30, {"kind": "feedback",
                               "data": {"regions": [{"text": "Searching", "visible": True}]}}),
        ("observation", 210, {"page": {"text": "Available flights: 3"}}),
        ("observation", 4010, {"page": {"text": "Available flights: 3"}, "phase": "settled"}),
    )
    window = loading_window(events, observations, loading_check(), action="Search",
                            executed_sequence=2, complete=True)
    assert window["anchored"] is True
    assert window["ready_observed"] is True
    assert window["response_ms"] == pytest.approx(200, abs=1)


def test_an_unobserved_readiness_condition_is_reported_as_a_lower_bound():
    events, observations = timeline(
        ("action_attempt", 0, {"action": {"label": "Search"}}),
        ("action_executed", 10, {"entry": {"action": "Search", "kind": "click"}}),
        ("observation", 4010, {"page": {"text": "nothing"}, "phase": "settled"}),
    )
    window = loading_window(events, observations, loading_check(), action="Search",
                            executed_sequence=2, complete=True)
    assert window["ready_observed"] is False
    assert "lower bound" in window["interval_source"]


def test_an_unanchored_window_is_incomplete_rather_than_fast():
    events, observations = timeline(("observation", 0, {"page": {"text": "x"}}))
    window = loading_window(events, observations, loading_check(), action="Search",
                            executed_sequence=1, complete=True)
    assert window["anchored"] is False
    assert window["complete"] is False
    assert window["response_ms"] is None


def test_missing_telemetry_makes_the_window_incomplete():
    events, observations = timeline(
        ("action_attempt", 0, {"action": {"label": "Search"}}),
        ("action_executed", 10, {"entry": {"action": "Search", "kind": "click"}}),
        ("observation", 210, {"page": {"text": "Available flights"}}),
    )
    window = loading_window(events, observations, loading_check(), action="Search",
                            executed_sequence=2, complete=False)
    assert window["complete"] is False


# -- independent acceptance ----------------------------------------------------------------------


class Verifier:
    """A loopback stand-in for the application's own test-only record endpoint."""

    def __init__(self, records, status=200):
        self.records, self.status = records, status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = json.dumps(outer.records).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()


def observation(text="Confirmed", url="http://127.0.0.1:8111/done", **extra):
    return {"text": text, "url": url, "evaluation_elements": [], **extra}


def test_a_journey_without_acceptance_cannot_certify_success():
    explore = journey(mode="explore", acceptance={})
    status, evidence = verify_goal(explore, observation())
    assert status == "unverified"
    assert evidence[0]["state"] == "not_declared"


def test_the_declared_page_state_must_actually_be_observed():
    assert verify_goal(journey(), observation())[0] == "verified"
    assert verify_goal(journey(), observation(text="Something else"))[0] == "violated"
    assert verify_goal(journey(), observation(url="http://127.0.0.1:8111/checkout"))[0] == "violated"


def test_text_that_must_be_absent_is_checked_too():
    spec = journey(acceptance={"url_path_is": "/done", "text_absent": ["Error"]})
    assert verify_goal(spec, observation(text="All good"))[0] == "verified"
    assert verify_goal(spec, observation(text="Error occurred"))[0] == "violated"


def test_declared_control_values_are_read_from_the_evaluation_state():
    spec = journey(acceptance={"control_values": {"Passenger": "Test Passenger"}})
    present = observation(evaluation_elements=[{"label": "Passenger", "value": "Test Passenger"}])
    assert verify_goal(spec, present)[0] == "verified"
    assert verify_goal(spec, observation())[0] == "violated"


def test_a_page_that_merely_claims_success_does_not_satisfy_the_backend():
    with Verifier(records=[]) as verifier:
        spec = journey(url=f"http://127.0.0.1:{verifier.port}/",
                       allowed_origins=[f"http://127.0.0.1:{verifier.port}"],
                       acceptance={"backend": {"path": "/records",
                                               "expect_records": [{"id": "A1"}]}})
        status, evidence = verify_goal(spec, observation())
    assert status == "violated"
    assert evidence[-1]["criterion"] == "backend"


def test_a_matching_backend_record_verifies_the_goal():
    with Verifier(records=[{"id": "A1", "extra": "ignored"}]) as verifier:
        spec = journey(url=f"http://127.0.0.1:{verifier.port}/",
                       allowed_origins=[f"http://127.0.0.1:{verifier.port}"],
                       acceptance={"backend": {"path": "/records",
                                               "expect_records": [{"id": "A1"}]}})
        assert verify_goal(spec, observation())[0] == "verified"


def test_extra_backend_records_are_a_violation_not_a_match():
    with Verifier(records=[{"id": "A1"}, {"id": "A2"}]) as verifier:
        spec = journey(url=f"http://127.0.0.1:{verifier.port}/",
                       allowed_origins=[f"http://127.0.0.1:{verifier.port}"],
                       acceptance={"backend": {"path": "/records",
                                               "expect_records": [{"id": "A1"}]}})
        assert verify_goal(spec, observation())[0] == "violated"


def test_an_unreachable_verifier_is_unavailable_rather_than_verified():
    spec = journey(url="http://127.0.0.1:1/",
                   allowed_origins=["http://127.0.0.1:1"],
                   acceptance={"backend": {"path": "/records", "expect_records": []}})
    status, evidence = verify_goal(spec, observation(), timeout=1)
    assert status == "unavailable"
    assert evidence[-1]["state"] == "unavailable"


def test_a_verifier_returning_the_wrong_shape_is_a_violation():
    with Verifier(records={"not": "a list"}) as verifier:
        spec = journey(url=f"http://127.0.0.1:{verifier.port}/",
                       allowed_origins=[f"http://127.0.0.1:{verifier.port}"],
                       acceptance={"backend": {"path": "/records", "expect_records": []}})
        assert verify_goal(spec, observation())[0] == "violated"


def test_a_missing_final_observation_cannot_verify_a_goal():
    assert verify_goal(journey(), None)[0] == "violated"


# -- which state a transition check is judged on -----------------------------------------------


DISCLOSURE = {
    "id": "disclosure", "family": "unexpected_state", "scope": "transition",
    "applies_when": {"predicate": "text_contains", "value": "Review plan change"},
    "requirement": "Before confirming, the page says what the change takes away.",
    "deadline_ms": 1500, "severity": "high", "required_evidence": ["observation"],
    "expect": {"require_acknowledgement": True},
}


def run_for(spec, tmp_path):
    return Run(spec, tmp_path / "run")


def state(text, *, identifier):
    return {"id": identifier, "url": "http://127.0.0.1:8111/billing", "title": "t", "text": text,
            "evaluation_elements": [], "telemetry_window": {}, "complete": True}


def test_a_check_that_only_applied_before_the_action_is_still_judged(tmp_path):
    """The actor can leave the state a pre-action requirement lives in within a single step."""
    spec = journey(checks=[json.loads(json.dumps(DISCLOSURE))])
    run = run_for(spec, tmp_path)
    before = state("Review plan change", identifier="obs-before")
    after = state("Change scheduled", identifier="obs-after")
    paired = run.applicable_states("transition", observation=after, history=[],
                                   action_context={"before": before,
                                                   "entry": {"action": "Confirm downgrade"}})
    assert [(check.id, judged["id"]) for check, judged in paired] == [("disclosure", "obs-before")]


def test_the_state_after_the_action_is_preferred_when_both_apply(tmp_path):
    spec = journey(checks=[json.loads(json.dumps(DISCLOSURE))])
    run = run_for(spec, tmp_path)
    before = state("Review plan change", identifier="obs-before")
    after = state("Review plan change, still", identifier="obs-after")
    paired = run.applicable_states("transition", observation=after, history=[],
                                   action_context={"before": before,
                                                   "entry": {"action": "Confirm downgrade"}})
    assert [(check.id, judged["id"]) for check, judged in paired] == [("disclosure", "obs-after")]


def test_a_check_is_never_judged_twice_for_one_transition(tmp_path):
    spec = journey(checks=[json.loads(json.dumps(DISCLOSURE))])
    run = run_for(spec, tmp_path)
    shared = state("Review plan change", identifier="obs-one")
    paired = run.applicable_states("transition", observation=shared, history=[],
                                   action_context={"before": shared,
                                                   "entry": {"action": "Confirm downgrade"}})
    assert len(paired) == 1


def test_the_final_state_is_judged_on_itself_alone(tmp_path):
    """A final check reads the end of the journey, not a state the actor passed through."""
    final = json.loads(json.dumps(DISCLOSURE))
    final["scope"] = "final"
    spec = journey(checks=[final])
    run = run_for(spec, tmp_path)
    paired = run.applicable_states("final", observation=state("Change scheduled", identifier="end"),
                                   history=[],
                                   action_context={"before": state("Review plan change",
                                                                   identifier="obs-before")})
    assert paired == []


# -- the actor's account of a step versus what was observed -------------------------------------


def test_an_action_the_actor_called_a_no_op_is_corrected_by_the_observation(tmp_path):
    run = run_for(journey(), tmp_path)
    before = state("Choose a plan", identifier="obs-before")
    run.observations.append(before)
    run.observations.append(state("Review plan change", identifier="obs-after"))
    history = [{"action": "Switch to Starter", "page_changed": False}]
    run.reconcile(history, {"before": before, "entry": history[-1]})
    assert run.reconciled(history)[0]["page_changed"] is True
    assert history[0]["page_changed"] is False


def test_the_journalled_copy_is_not_what_reconciliation_reads(tmp_path):
    """`action_executed` is emitted before the actor knows whether the page changed.

    The journalled entry is frozen at `page_changed: None` and the actor fills the live entry in
    afterwards. Reading the frozen copy made reconciliation a no-op in every real run while the
    unit tests, which built the entry by hand, still passed.
    """
    run = run_for(journey(), tmp_path)
    before = state("Choose a plan", identifier="obs-before")
    run.observations.append(before)
    run.observations.append(state("Review plan change", identifier="obs-after"))
    history = [{"action": "Switch to Starter", "page_changed": False}]
    journalled = {"action": "Switch to Starter", "page_changed": None}
    run.reconcile(history, {"before": before, "entry": journalled})
    assert run.reconciled(history)[0]["page_changed"] is True


def test_a_step_that_changed_nothing_is_left_as_the_actor_recorded_it(tmp_path):
    run = run_for(journey(), tmp_path)
    before = state("Choose a plan", identifier="obs-before")
    run.observations.append(before)
    run.observations.append(state("Choose a plan", identifier="obs-after"))
    history = [{"action": "Switch to Starter", "page_changed": False}]
    run.reconcile(history, {"before": before, "entry": history[-1]})
    assert run.reconciled(history)[0]["page_changed"] is False


def test_an_action_the_actor_already_called_a_change_is_not_re_examined(tmp_path):
    run = run_for(journey(), tmp_path)
    before = state("Choose a plan", identifier="obs-before")
    run.observations.append(before)
    run.observations.append(state("Choose a plan", identifier="obs-after"))
    history = [{"action": "Switch to Starter", "page_changed": True}]
    run.reconcile(history, {"before": before, "entry": history[-1]})
    assert run.observed_changes == set()


# -- runtime findings ------------------------------------------------------------------------------


def events_of(*entries):
    return [{"schema_version": 1, "sequence": index + 1, "host_monotonic_ns": index,
             "kind": kind, "data": data} for index, (kind, data) in enumerate(entries)]


def test_an_uncaught_exception_is_reported_without_anyone_declaring_it():
    findings = runtime_findings(events_of(("console_exception", {"text": "TypeError: x"})),
                                journey())
    assert len(findings) == 1
    assert findings[0]["provenance"] == "deterministic"


def test_a_console_error_the_journey_declared_as_expected_is_not_reported():
    spec = journey(expected_console_errors=["TypeError: x"])
    assert runtime_findings(events_of(("console_exception", {"text": "TypeError: x"})), spec) == []


def test_a_server_error_during_the_journey_is_a_high_severity_finding():
    findings = runtime_findings(
        events_of(("http_failure", {"url": "http://127.0.0.1:8111/price", "status": 503})), journey())
    assert findings[0]["severity"] == "high"


def test_a_client_error_is_reported_at_a_lower_severity():
    findings = runtime_findings(
        events_of(("http_failure", {"url": "http://127.0.0.1:8111/x", "status": 404})), journey())
    assert findings[0]["severity"] == "medium"


def test_a_missing_favicon_is_not_a_defect():
    assert runtime_findings(
        events_of(("http_failure", {"url": "http://127.0.0.1:8111/favicon.ico", "status": 404})),
        journey()) == []


def test_a_network_failure_is_reported():
    findings = runtime_findings(events_of(("network_failure", {"error": "net::ERR_FAILED"})),
                                journey())
    assert findings[0]["evaluator"] == "runtime:network_failure"


def test_the_same_runtime_problem_seen_repeatedly_is_reported_once():
    repeated = events_of(*[("console_exception", {"text": "TypeError: x"})] * 3)
    findings = runtime_findings(repeated, journey())
    assert len(findings) == 1
    assert findings[0]["occurrences"] == 3


# -- accounting --------------------------------------------------------------------------------------


class FakeRun:
    def __init__(self, directory, ledger):
        self.directory = directory
        self.ledger = ledger


def test_a_run_is_priced_from_the_tokens_it_actually_sent(tmp_path):
    run = FakeRun(tmp_path, [{"role": "evaluator", "subjects": 2,
                              "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}])
    usd, helper = account(run, [{"usage": {"prompt_tokens": 500, "completion_tokens": 50}}], [])
    written = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert written["priced_input_tokens"] == 1500
    assert written["priced_output_tokens"] == 150
    assert float(usd) > 0
    assert helper["requests"] == 0


def test_an_unpriced_provider_is_counted_and_named_rather_than_valued_at_zero(tmp_path):
    run = FakeRun(tmp_path, [])
    usd, helper = account(run, [], [{"usage": {"prompt_tokens": 267, "completion_tokens": 12}}])
    written = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert usd == "0.000000"
    assert helper == {"requests": 1, "input": 267, "output": 12}
    assert written["unpriced"]["reason"]
    assert any(entry["priced"] is False for entry in written["entries"])


def test_accounting_survives_a_provider_that_reported_no_usage(tmp_path):
    run = FakeRun(tmp_path, [{"role": "evaluator", "subjects": 1, "usage": {}}])
    usd, _ = account(run, [{}], [{}])
    assert usd == "0.000000"
