"""The bundled LangChain demo agent: contracts only, no model calls.

These guard the pieces the demo's meaning depends on. The verdict tokens must stay
non-overlapping, or an evaluation expecting one outcome would silently accept the other.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = json.loads((ROOT / "examples" / "agent-eval-refund.json").read_text(encoding="utf-8"))
FINAL_SALE = json.loads((ROOT / "examples" / "agent-eval-final-sale.json").read_text(encoding="utf-8"))


def test_both_demo_evaluations_are_valid_agent_specifications():
    from journey_evals.agent_evals import agent_spec_from_dict

    for document in (ELIGIBLE, FINAL_SALE):
        spec = agent_spec_from_dict(document)
        assert spec.framework == "langgraph"
        assert spec.judges, "the demo is meant to exercise the LLM judge"


def test_the_two_expected_verdicts_cannot_satisfy_each_other():
    """'REFUNDABLE' is a substring of 'NOT REFUNDABLE'; the demo must not regress to that."""
    eligible = ELIGIBLE["acceptance"]["output_contains"][0]
    refused = FINAL_SALE["acceptance"]["output_contains"][0]
    assert eligible not in refused
    assert refused not in eligible


def test_the_demo_agent_prompt_requires_the_declared_verdict_tokens():
    source = (ROOT / "examples" / "langchain_demo_agent.py").read_text(encoding="utf-8")
    assert ELIGIBLE["acceptance"]["output_contains"][0] in source
    assert FINAL_SALE["acceptance"]["output_contains"][0] in source


def test_the_demo_agent_module_imports_no_credentials_at_module_scope():
    """Credentials are read inside build(), so importing the module never requires a key."""
    source = (ROOT / "examples" / "langchain_demo_agent.py").read_text(encoding="utf-8")
    body = source.split("def build(")[0]
    assert "TEXT_MODEL_API_KEY" not in body
    assert "graph = build" in source


def test_each_demo_declares_the_tools_its_answer_must_rest_on():
    for document in (ELIGIBLE, FINAL_SALE):
        assert document["acceptance"]["tools_called"] == ["lookup_order", "refund_policy"]
        assert document["acceptance"]["no_tool_errors"] is True


WARM = json.loads((ROOT / "examples" / "agent-eval-concierge.json").read_text(encoding="utf-8"))
BLUNT = json.loads(
    (ROOT / "examples" / "agent-eval-concierge-blunt.json").read_text(encoding="utf-8")
)


def test_the_concierge_demos_are_valid_and_decided_by_blocking_judges():
    from journey_evals.agent_evals import agent_spec_from_dict, evidence_basis

    for document in (WARM, BLUNT):
        spec = agent_spec_from_dict(document)
        assert any(judge.blocking for judge in spec.judges)
        assert evidence_basis(spec) == "code_and_model_judgment"


def test_the_concierge_demo_states_nothing_code_could_check():
    """The point of the demo dies if acceptance starts asserting output text."""
    for document in (WARM, BLUNT):
        assert "output_contains" not in document["acceptance"]
        assert "output_equals" not in document["acceptance"]
        assert document["acceptance"]["basis"] == "model_judgment"


def test_both_personas_are_held_to_the_same_deterministic_bar():
    """Code sees the two personas as identical; only the judges may separate them."""
    assert WARM["acceptance"]["tools_called"] == BLUNT["acceptance"]["tools_called"]
    assert WARM["input"] == BLUNT["input"]
    warm_entry = WARM["runtime"]["entrypoint"]
    blunt_entry = BLUNT["runtime"]["entrypoint"]
    assert warm_entry != blunt_entry
    assert warm_entry.split(":")[0] == blunt_entry.split(":")[0]


def test_the_shared_tone_requirement_is_worded_identically_for_both_personas():
    """A negative control only proves something if the bar did not move."""
    def requirement(document, judge_id):
        return next(j["requirement"] for j in document["judges"] if j["id"] == judge_id)

    for judge_id in ("acknowledges-the-guest", "no-invented-remedies"):
        assert requirement(WARM, judge_id) == requirement(BLUNT, judge_id)


def test_the_concierge_agent_exposes_both_personas():
    source = (ROOT / "examples" / "langchain_concierge_agent.py").read_text(encoding="utf-8")
    assert "graph = build" in source
    assert "blunt_graph = build_blunt" in source
    for document in (WARM, BLUNT):
        assert document["runtime"]["entrypoint"].split(":")[1] in source
