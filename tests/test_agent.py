"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_text_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_unchanged_waits_stop_at_the_code_owned_bound(runner):
    from jev_ultrafast.questions import MAX_UNCHANGED_WAITS

    for _ in range(MAX_UNCHANGED_WAITS):
        assert runner.state["status"] != "blocked"
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    assert len(runner.state["history"]) == MAX_UNCHANGED_WAITS


def test_a_wait_that_changes_the_page_restarts_the_wait_bound(runner):
    from jev_ultrafast.questions import MAX_UNCHANGED_WAITS

    for _ in range(MAX_UNCHANGED_WAITS - 1):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    changed = deepcopy(runner.state["page"])
    changed["text"] = "Results ready"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.return_value = changed
    runner.state["decision"] = decision("wait")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    for _ in range(MAX_UNCHANGED_WAITS - 1):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"


def test_the_policy_licenses_waiting_while_an_operation_is_running():
    from jev_ultrafast.questions import NEXT_ACTION

    assert "Recent WAIT actions are not evidence of loading" not in NEXT_ACTION
    assert "still running" in NEXT_ACTION and "is not a reason to" in NEXT_ACTION


def test_the_policy_scrolls_for_incomplete_goals_before_done():
    from jev_ultrafast.questions import NEXT_ACTION

    assert "SCROLL_DOWN before choosing DONE or BLOCKED" in NEXT_ACTION


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_text_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_snapshot_does_not_alias_mutable_agent_state(runner):
    saved = runner.snapshot()
    runner.state["page"]["text"] = "Changed after the observation"
    runner.state["history"].append({"action": "Later"})
    assert saved["page"]["text"] == "Search"
    assert saved["history"] == []


def test_executed_event_is_durable_before_capture_failure(runner):
    events = []
    runner.event_sink = lambda kind, data: events.append((kind, data))
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = TimeoutError("capture failed")
    with pytest.raises(TimeoutError):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert [kind for kind, _ in events] == [
        "decision_consumed", "action_attempt", "action_executed", "observation_failed",
    ]
    assert len(runner.state["history"]) == 1
    assert runner.state["decision"] is None


def test_uncertain_mutation_stops_without_reexecution(runner):
    events = []
    runner.event_sink = lambda kind, data: events.append(kind)
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = RuntimeError("uncertain mutation")
    with pytest.raises(RuntimeError):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked" and runner.state["decision"] is None
    with pytest.raises(ValueError, match="Observe and choose"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["browser"].act.call_count == 1
    assert "action_uncertain" in events and "action_executed" not in events


@pytest.mark.parametrize(("base", "expected"), [
    ("https://api.deepseek.com/v1", "deepseek"),
    ("https://openrouter.ai/api/v1", "openrouter"),
    ("https://example-resource.services.ai.azure.com/openai/v1", "azure"),
    ("https://example.openai.azure.com/openai/v1", "azure"),
])
def test_text_dialect_is_detected_from_the_endpoint(monkeypatch, base, expected):
    monkeypatch.delenv("TEXT_MODEL_DIALECT", raising=False)
    assert model.text_dialect(base) == expected


def test_declared_text_dialect_overrides_detection(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_DIALECT", "azure")
    assert model.text_dialect("https://api.deepseek.com/v1") == "azure"
    monkeypatch.setenv("TEXT_MODEL_DIALECT", "nonsense")
    with pytest.raises(ValueError, match="TEXT_MODEL_DIALECT"):
        model.text_dialect("https://api.deepseek.com/v1")


def test_azure_tuning_uses_the_parameters_azure_accepts(monkeypatch):
    """Azure Foundry rejects max_tokens and the reasoning object outright."""
    monkeypatch.delenv("TEXT_MODEL_REASONING", raising=False)
    tuning = model.text_tuning("azure")
    assert tuning == {"max_completion_tokens": 1024, "reasoning_effort": "low"}
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    assert model.text_tuning("azure") == {"max_completion_tokens": 1024}


def test_existing_vendor_tuning_is_unchanged(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_REASONING", raising=False)
    assert model.text_tuning("deepseek") == {"max_tokens": 1024, "thinking": {"type": "disabled"}}
    assert model.text_tuning("openrouter") == {"max_tokens": 1024, "reasoning": {"effort": "low"}}
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    assert model.text_tuning("openrouter") == {"max_tokens": 1024, "reasoning": {"enabled": False}}


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_a_dropped_connection_is_retried_because_no_action_was_executed(monkeypatch):
    """A transport failure happens before anything is clicked or typed, so the request is safe
    to resend. The rule it must not break is that a browser mutation is never retried."""
    import httpx

    monkeypatch.setattr(model.time, "sleep", lambda _s: None)
    attempts = []

    class Client:
        def post(self, url, json, headers):
            attempts.append(json)
            if len(attempts) < 3:
                raise httpx.ConnectError("connection reset")
            return _Response(200, {"ok": True})

    assert model._post_json(Client(), "http://127.0.0.1/v1", "k", {"body": 1}) == {"ok": True}
    assert len(attempts) == 3
    assert attempts[0] == attempts[-1], "the retry must resend the same request, not a new one"


def test_a_connection_that_never_recovers_reports_the_transport_cause(monkeypatch):
    import httpx

    monkeypatch.setattr(model.time, "sleep", lambda _s: None)
    calls = []

    class Client:
        def post(self, url, json, headers):
            calls.append(1)
            raise httpx.ReadTimeout("timed out")

    with pytest.raises(RuntimeError) as failure:
        model._post_json(Client(), "http://127.0.0.1/v1", "k", {})
    assert len(calls) == 3
    assert "ReadTimeout" in str(failure.value)
    assert "no action executed" in str(failure.value)


def test_a_provider_refusal_is_not_retried(monkeypatch):
    """A 400 is the provider's considered answer. Resending it only burns budget."""
    monkeypatch.setattr(model.time, "sleep", lambda _s: None)
    calls = []

    class Client:
        def post(self, url, json, headers):
            calls.append(1)
            return _Response(400)

    with pytest.raises(RuntimeError, match="HTTP 400"):
        model._post_json(Client(), "http://127.0.0.1/v1", "k", {})
    assert calls == [1]
