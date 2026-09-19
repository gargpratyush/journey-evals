"""Offline contracts for the second application and its CP6 adapter. No paid APIs."""

import json
import shutil
from pathlib import Path

import pytest

from journey_evals import subscription as second


def variants():
    return second.plan_cases()


def test_twenty_declared_cases_split_evenly(tmp_path):
    cases = variants()
    assert len(cases) == 20
    assert sum(1 for c in cases if c["group"] == "fault") == 10
    assert sum(1 for c in cases if c["group"] == "clean") == 10
    assert {c["check"] for c in cases} == {"fare", "effect", "loading"}


def test_case_plan_is_deterministic_for_a_seed():
    first = [(c["position"], c["check"], c["group"], c["index"]) for c in second.plan_cases(7)]
    again = [(c["position"], c["check"], c["group"], c["index"]) for c in second.plan_cases(7)]
    assert first == again


@pytest.mark.parametrize("index", range(20))
def test_every_declared_variant_applies_to_real_application_source(tmp_path, index):
    case = variants()[index]
    target = tmp_path / "app"
    digests = second.materialize(target, case)
    pristine = second.app_digests(second.APP)
    if case["defect"]:
        assert digests != pristine, "A defect variant must differ from the pristine application"
        assert digests[case["defect"]["file"]] != pristine[case["defect"]["file"]]
    elif case["controls"].get("feedback"):
        assert digests["signup.html"] != pristine["signup.html"]
    else:
        assert digests == pristine


def test_price_defect_changes_the_served_quote(tmp_path):
    case = next(c for c in variants() if c["defect"] and c["defect"]["file"] == "pricing.py")
    second.materialize(tmp_path / "app", case)
    with second.SubscriptionFixture(app=tmp_path / "app") as fixture:
        assert fixture.quote()["cents"] > 4000
        assert fixture.quote()["disclosure"] == ""


def test_pristine_application_quotes_the_advertised_plan_price(tmp_path):
    shutil.copytree(second.APP, tmp_path / "app")
    with second.SubscriptionFixture(app=tmp_path / "app") as fixture:
        assert fixture.quote() == {"cents": 4000, "disclosure": ""}


def test_verifier_rejects_a_missing_or_wrong_subscription():
    assert second.verify([])["passed"] is False
    good = dict(second.EXPECTED, cents=4000, currency="USD", id="SUB-1")
    assert second.verify([good])["passed"] is True
    assert second.verify([good, good])["passed"] is False
    assert second.verify([dict(good, workspace="Someone Else")])["passed"] is False


def test_verifier_requires_acknowledgement_when_the_price_was_disclosed():
    booked = dict(second.EXPECTED, cents=4600, currency="USD", id="SUB-1")
    assert second.verify([booked], expected_cents=4600, require_ack=True)["passed"] is False
    acknowledged = dict(booked, acknowledged_cents=4600)
    assert second.verify([acknowledged], expected_cents=4600, require_ack=True)["passed"] is True


def test_plan_facts_reads_amounts_and_basis_from_visible_text():
    page = {"text": "Team plan for Sandbox Analytics. Plan P-TEAM.\n"
                    "Selected plan: USD 40.00 per month\nCheckout total: USD 52.00 per month"}
    facts = second.plan_facts(page)
    assert facts["selected_cents"] == 4000
    assert facts["checkout_cents"] == 5200
    assert facts["plan_id"] == "P-TEAM" and facts["period"] == "per month"


def test_plan_facts_abstains_when_no_amounts_are_present():
    assert second.plan_facts({"text": "Choose a plan"}) == {}


def _run(tmp_path, events, **extra):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return {"directory": str(directory), "evidence_complete": True, **extra}


def _feedback(text):
    return {"kind": "browser_event", "host_monotonic_ns": 0,
            "data": {"kind": "feedback", "data": {"regions": [{"text": text, "visible": True}]}}}


def test_loading_window_captures_feedback_emitted_on_click(tmp_path):
    """The page can write its status synchronously on click, before the executed action is journalled."""
    events = [
        {"kind": "action_attempt", "host_monotonic_ns": 0,
         "data": {"action": {"label": second.AVAILABILITY_ACTION}}},
        _feedback("Checking workspace availability"),
        {"kind": "action_executed", "host_monotonic_ns": 1,
         "data": {"entry": {"action": second.AVAILABILITY_ACTION, "kind": "click"}}},
    ]
    window = second.loading_window(_run(tmp_path, events), 1500)
    assert window["visible_feedback"] == ["Checking workspace availability"]
    assert window["complete"] is True


def test_loading_window_keeps_collecting_across_waits(tmp_path):
    events = [
        {"kind": "action_attempt", "host_monotonic_ns": 0,
         "data": {"action": {"label": second.AVAILABILITY_ACTION}}},
        {"kind": "action_executed", "host_monotonic_ns": 1,
         "data": {"entry": {"action": second.AVAILABILITY_ACTION, "kind": "click"}}},
        {"kind": "action_executed", "host_monotonic_ns": 2,
         "data": {"entry": {"action": "Wait for the page to update", "kind": "wait"}}},
        _feedback("One moment"),
        {"kind": "action_executed", "host_monotonic_ns": 3,
         "data": {"entry": {"action": "Start sandbox subscription", "kind": "click"}}},
        _feedback("Too late to count"),
    ]
    window = second.loading_window(_run(tmp_path, events), 1500)
    assert window["visible_feedback"] == ["One moment"]


def test_loading_window_is_incomplete_when_the_action_never_executed(tmp_path):
    events = [{"kind": "action_attempt", "host_monotonic_ns": 0,
               "data": {"action": {"label": second.AVAILABILITY_ACTION}}}]
    assert second.loading_window(_run(tmp_path, events), 1500)["complete"] is False


def test_goal_names_only_synthetic_facts_the_journey_needs():
    for value in (second.EXPECTED["workspace"], second.EXPECTED["email"], second.EXPECTED["plan"]):
        assert value in second.GOAL


def test_second_application_is_not_the_first_one_relabelled():
    """The transfer claim is only meaningful if the two applications are structurally different."""
    flight = (Path(second.ROOT) / "journey_evals" / "app" / "booking.html").read_text(encoding="utf-8")
    signup = (second.APP / "signup.html").read_text(encoding="utf-8")
    assert "<select" in flight and "<select" not in signup
    assert 'type="radio"' in signup and 'type="radio"' not in flight
    assert 'type="text"' in signup and 'type="text"' not in flight
    assert "function render()" in signup and "function render()" not in flight


def test_confirmation_is_brought_into_view_without_replacing_sections():
    signup = (second.APP / "signup.html").read_text(encoding="utf-8")
    assert "confirmation-section').scrollIntoView" in signup
    assert "innerHTML" not in signup
