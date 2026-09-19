"""Contract surface: the journey document, the predicate vocabulary, coverage and the result rule.

These tests exist because everything downstream trusts this module. If a malformed journey were
accepted, or a result were allowed to say PASS on evidence that does not support it, no amount of
careful evaluation further on would make the report true.
"""

import json

import pytest

from journey_evals.contracts import (
    ContractError,
    Coverage,
    canonical_json,
    decide_result,
    effective_spec,
    exit_code,
    finding_id,
    holds,
    journey_from_dict,
    reads_controls,
    validate_predicate,
    validate_result_basis,
)

JOURNEY = {
    "schema_version": 1,
    "id": "demo",
    "mode": "verify",
    "url": "http://127.0.0.1:8111/",
    "allowed_origins": ["http://127.0.0.1:8111"],
    "task": "Do the declared thing.",
    "viewport": {"width": 1120, "height": 780},
    "facts": {"who": "Test Passenger"},
    "probes": [{"id": "reach", "instruction": "Get there.", "required_checks": ["c1"]}],
    "checks": [
        {
            "id": "c1",
            "family": "interaction_correctness",
            "scope": "transition",
            "applies_when": {"predicate": "action_executed", "value": "Search"},
            "requirement": "Searching shows results.",
            "deadline_ms": 4000,
            "severity": "high",
            "required_evidence": ["observation"],
            "expect": {"effect": "Results are visible."},
        }
    ],
    "acceptance": {"url_path_is": "/done", "text_contains": ["Confirmed"]},
    "budgets": {"wall_ms": 300000, "model_requests": 80, "steps": 24, "usd": "0.10"},
}


def spec(**overrides):
    return journey_from_dict({**json.loads(json.dumps(JOURNEY)), **overrides})


# -- document validation --------------------------------------------------------------------


def test_journey_round_trips_through_its_own_canonical_form():
    original = spec()
    assert journey_from_dict(original.canonical()).sha256 == original.sha256


def test_canonical_json_is_stable_under_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


@pytest.mark.parametrize("mutation", [
    {"schema_version": 2},
    {"mode": "explore-everything"},
    {"url": "https://example.com/"},
    {"budgets": {"wall_ms": 1, "model_requests": 1, "steps": 1, "usd": 0.1}},
    {"acceptance": {}},
])
def test_malformed_journeys_are_refused(mutation):
    with pytest.raises(ContractError):
        spec(**mutation)


def test_duplicate_check_ids_are_refused():
    document = json.loads(json.dumps(JOURNEY))
    document["checks"].append(dict(document["checks"][0]))
    with pytest.raises(ContractError):
        journey_from_dict(document)


def test_probe_naming_an_unknown_check_is_refused():
    with pytest.raises(ContractError):
        spec(probes=[{"id": "reach", "instruction": "Go.", "required_checks": ["nope"]}])


def test_unknown_evaluator_family_is_refused():
    document = json.loads(json.dumps(JOURNEY))
    document["checks"][0]["family"] = "vibes"
    with pytest.raises(ContractError):
        journey_from_dict(document)


def test_a_run_without_a_journey_file_cannot_certify_success():
    with pytest.raises(ContractError):
        effective_spec(url="http://127.0.0.1:8111/", task="Do it", mode="verify")
    resolved, provenance = effective_spec(url="http://127.0.0.1:8111/", task="Do it",
                                          mode="explore")
    assert resolved.mode == "explore"
    assert provenance["effective_spec_sha256"] == resolved.sha256


# -- the predicate vocabulary ---------------------------------------------------------------


def test_every_declared_predicate_validates():
    for predicate in [
        {"predicate": "always"},
        {"predicate": "text_contains", "value": "a"},
        {"predicate": "text_absent", "value": "a"},
        {"predicate": "url_path_is", "value": "/x"},
        {"predicate": "url_contains", "value": "x"},
        {"predicate": "title_is", "value": "x"},
        {"predicate": "action_executed", "value": "Search"},
        {"predicate": "action_kind_executed", "value": "click"},
        {"predicate": "control_present", "value": "Search"},
        {"predicate": "control_disabled", "value": "Search"},
        {"predicate": "control_value_is", "value": "Search", "expected": "x"},
        {"predicate": "not", "clause": {"predicate": "always"}},
        {"predicate": "any_of", "clauses": [{"predicate": "always"}]},
        {"predicate": "all_of", "clauses": [{"predicate": "always"}]},
    ]:
        validate_predicate(predicate)


@pytest.mark.parametrize("predicate", [
    {"predicate": "sql_injection", "value": "x"},
    {"predicate": "text_contains"},
    {"predicate": "any_of", "clauses": []},
    {"predicate": "not", "clause": {"predicate": "unknown"}},
    "always",
])
def test_unknown_or_malformed_predicates_are_refused(predicate):
    with pytest.raises(ContractError):
        validate_predicate(predicate)


CONTEXT = {
    "observation": {
        "text": "Available flights", "url": "http://127.0.0.1:8111/results", "title": "Results",
        "actions": [{"label": "Select", "kind": "click"}],
        "evaluation_elements": [{"label": "Search", "disabled": True, "value": "Zurich"}],
    },
    "facts": {"who": "Test Passenger"},
    "history": [{"action": "Search", "kind": "click"}],
}


def test_predicates_read_only_the_context_they_are_given():
    assert holds({"predicate": "always"}, CONTEXT)
    assert holds({"predicate": "text_contains", "value": "Available"}, CONTEXT)
    assert not holds({"predicate": "text_contains", "value": "Sold out"}, CONTEXT)
    assert holds({"predicate": "text_absent", "value": "Sold out"}, CONTEXT)
    assert holds({"predicate": "url_path_is", "value": "/results"}, CONTEXT)
    assert holds({"predicate": "url_contains", "value": "results"}, CONTEXT)
    assert holds({"predicate": "title_is", "value": "Results"}, CONTEXT)
    assert holds({"predicate": "action_executed", "value": "Search"}, CONTEXT)
    assert holds({"predicate": "action_kind_executed", "value": "click"}, CONTEXT)
    assert holds({"predicate": "control_present", "value": "Search"}, CONTEXT)
    assert holds({"predicate": "control_disabled", "value": "Search"}, CONTEXT)
    assert holds({"predicate": "control_value_is", "value": "Search", "expected": "Zurich"},
                 CONTEXT)


def test_combinators_compose_without_leaking_state():
    assert holds({"predicate": "not", "clause": {"predicate": "text_contains", "value": "Sold"}},
                 CONTEXT)
    assert holds({"predicate": "any_of", "clauses": [
        {"predicate": "text_contains", "value": "Sold"},
        {"predicate": "text_contains", "value": "Available"}]}, CONTEXT)
    assert not holds({"predicate": "all_of", "clauses": [
        {"predicate": "text_contains", "value": "Sold"},
        {"predicate": "text_contains", "value": "Available"}]}, CONTEXT)


def test_only_executed_actions_enter_the_history_a_predicate_reads():
    context = {"observation": {}, "facts": {}, "history": []}
    assert not holds({"predicate": "action_executed", "value": "Search"}, context)


def test_a_predicate_on_absent_evidence_is_false_rather_than_an_error():
    empty = {"observation": {}, "facts": {}, "history": []}
    for predicate in ({"predicate": "text_contains", "value": "x"},
                      {"predicate": "url_path_is", "value": "/x"},
                      {"predicate": "control_present", "value": "Search"},
                      {"predicate": "action_executed", "value": "Search"}):
        assert holds(predicate, empty) is False


# -- effective spec and finding identity -----------------------------------------------------


def test_finding_identity_ignores_wording_but_not_substance(tmp_path):
    base = ("c1", "Search", "Searching shows results.")
    assert finding_id(*base) == finding_id(*base)
    assert finding_id(*base) != finding_id("c1", "Confirm", base[2])
    assert finding_id(*base) != finding_id("c2", *base[1:])


# -- coverage -------------------------------------------------------------------------------


def complete_coverage():
    coverage = Coverage(spec())
    coverage.record("c1", "passed", ["obs-000001"])
    return coverage.as_dict({"steps": 1, "observations": 1, "acceptance": []})


def test_a_check_that_was_never_applicable_stays_pending():
    coverage = Coverage(spec())
    report = coverage.as_dict({"steps": 1, "observations": 1, "acceptance": []})
    assert report["checks"]["c1"] == "pending"
    assert "c1" in report["missing_required_checks"]
    assert not report["complete"]


def test_coverage_is_complete_once_every_required_check_and_probe_resolved():
    report = complete_coverage()
    assert report["complete"] and not report["missing_required_checks"]
    assert report["check_evidence"]["c1"] == ["obs-000001"]
    assert report["probes"]["reach"] == "covered"
    assert report["unvisited_probes"] == []


def test_a_probe_whose_checks_never_applied_is_reported_unvisited():
    coverage = Coverage(spec())
    report = coverage.as_dict({"steps": 1, "observations": 1, "acceptance": []})
    assert report["unvisited_probes"] == ["reach"]
    assert not report["complete"]


def test_duplicate_evidence_ids_are_collapsed():
    coverage = Coverage(spec())
    coverage.record("c1", "passed", ["obs-000001", "obs-000001"])
    report = coverage.as_dict({"steps": 1, "observations": 1, "acceptance": []})
    assert report["check_evidence"]["c1"] == ["obs-000001"]


def test_a_failure_outranks_a_later_pass_for_the_same_check():
    coverage = Coverage(spec())
    coverage.record("c1", "failed", ["obs-000001"])
    coverage.record("c1", "passed", ["obs-000002"])
    assert coverage.as_dict({"steps": 1, "observations": 1,
                             "acceptance": []})["checks"]["c1"] == "failed"


# -- the result rule --------------------------------------------------------------------------


def blocking_finding():
    return [{"id": "finding-0000000000000001", "check_id": "c1", "severity": "high",
             "review_state": "advisory", "family": "interaction_correctness",
             "verdict": "NO_EFFECT"}]


def test_a_clean_verified_run_passes():
    result, basis = decide_result(execution_status="completed", goal_status="verified",
                                  findings=[], coverage=complete_coverage(), mode="verify")
    assert result == "PASS"
    validate_result_basis(result, basis, [], complete_coverage())
    assert exit_code(result) == 0


def test_an_unverified_goal_can_never_pass():
    for status in ("unverified", "unavailable"):
        result, _ = decide_result(execution_status="completed", goal_status=status,
                                  findings=[], coverage=complete_coverage(), mode="verify")
        assert result == "INCONCLUSIVE"


def test_a_violated_goal_fails_even_with_no_findings():
    result, _ = decide_result(execution_status="completed", goal_status="violated",
                              findings=[], coverage=complete_coverage(), mode="verify")
    assert result == "FAIL"
    assert exit_code(result) == 1


def test_incomplete_coverage_cannot_pass():
    coverage = Coverage(spec()).as_dict({"steps": 0, "observations": 0, "acceptance": []})
    result, _ = decide_result(execution_status="completed", goal_status="verified",
                              findings=[], coverage=coverage, mode="verify")
    assert result == "INCONCLUSIVE"
    assert exit_code(result) == 2


def test_a_failed_execution_cannot_pass():
    result, _ = decide_result(execution_status="error", goal_status="verified", findings=[],
                              coverage=complete_coverage(), mode="verify")
    assert result in {"ERROR", "INCONCLUSIVE"}
    assert exit_code(result) != 0


def test_an_advisory_finding_warns_rather_than_failing():
    findings = blocking_finding()
    result, basis = decide_result(execution_status="completed", goal_status="verified",
                                  findings=findings, coverage=complete_coverage(), mode="verify")
    assert result == "WARN"
    assert exit_code(result) == 0
    validate_result_basis(result, basis, findings, complete_coverage())


def test_a_finding_promoted_by_policy_fails_the_run():
    findings = [{**blocking_finding()[0], "advisory": False, "evaluator": "c1"}]
    result, basis = decide_result(execution_status="completed", goal_status="verified",
                                  findings=findings, coverage=complete_coverage(), mode="verify",
                                  blocking_policies=("c1",))
    assert result == "FAIL"
    validate_result_basis(result, basis, findings, complete_coverage())


def test_a_failed_required_check_outranks_everything_advisory():
    coverage = Coverage(spec())
    coverage.record("c1", "failed", ["obs-000001"])
    resolved = coverage.as_dict({"steps": 1, "observations": 1, "acceptance": []})
    result, basis = decide_result(execution_status="completed", goal_status="verified",
                                  findings=[], coverage=resolved, mode="verify")
    assert result == "FAIL"
    assert any(item.get("check_id") == "c1" for item in basis)


def test_exploration_cannot_certify_success():
    coverage = complete_coverage()
    result, basis = decide_result(execution_status="completed", goal_status="unverified",
                                  findings=[], coverage=coverage, mode="explore")
    assert result == "INCONCLUSIVE"
    assert any(item.get("policy") == "exploration_cannot_certify_v1" for item in basis)


def test_a_basis_citing_an_unknown_finding_is_refused():
    with pytest.raises(ContractError):
        validate_result_basis("FAIL", [{"finding_id": "finding-does-not-exist"}],
                              blocking_finding(), complete_coverage())


def test_a_basis_citing_an_undeclared_check_is_refused():
    with pytest.raises(ContractError):
        validate_result_basis("FAIL", [{"check_id": "not-a-check"}], [], complete_coverage())


def test_a_non_passing_result_must_say_what_determined_it():
    with pytest.raises(ContractError):
        validate_result_basis("FAIL", [], [], complete_coverage())


# -- readiness conditions clear the same gate as applicability ---------------------------------


def ready_journey(ready_when):
    return {
        "schema_version": 1, "id": "j", "mode": "explore", "url": "http://127.0.0.1:8111/",
        "task": "Do the thing.",
        "checks": [{"id": "c1", "family": "interaction_correctness", "scope": "transition",
                    "requirement": "It must hold.", "severity": "high",
                    "expect": {"effect": "something", "ready_when": ready_when}}],
    }


def test_a_malformed_readiness_predicate_is_rejected_before_anything_is_spent():
    with pytest.raises(ContractError):
        journey_from_dict(ready_journey({"predicate": "control_value_is",
                                         "control": "Name", "expected": "x"}))


def test_an_unknown_readiness_predicate_is_rejected():
    with pytest.raises(ContractError):
        journey_from_dict(ready_journey({"predicate": "looks_right", "value": "x"}))


def test_a_list_of_readiness_predicates_is_normalized():
    spec = journey_from_dict(ready_journey([{"predicate": "text_contains", "value": "a"},
                                            {"predicate": "text_contains", "value": "b"}]))
    assert len(spec.checks[0].expect["ready_when"]) == 2


def test_an_empty_readiness_list_is_rejected():
    with pytest.raises(ContractError):
        journey_from_dict(ready_journey([]))


def test_control_predicates_are_identified_as_needing_the_element_table():
    assert reads_controls({"predicate": "control_value_is", "value": "a", "expected": "b"})
    assert reads_controls({"predicate": "not",
                           "clause": {"predicate": "control_present", "value": "a"}})
    assert not reads_controls({"predicate": "text_contains", "value": "a"})
    assert not reads_controls({"predicate": "always"})


def test_an_unvalidated_predicate_raises_rather_than_returning_a_verdict():
    with pytest.raises(ContractError):
        holds({"predicate": "text_contains"}, {"observation": {"text": "anything"}})
