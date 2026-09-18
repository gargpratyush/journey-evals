import pytest

from jev_ultrafast import evaluators
from jev_ultrafast.screening import corpus, metrics


def window():
    return {
        "id": "opaque", "kind": "fare", "complete": True, "same_basis": True,
        "before_cents": 10000, "after_cents": 12000, "checkout_text": "Total USD 120.00",
    }


@pytest.mark.parametrize("change", [{"complete": False}, {"same_basis": False}])
def test_missing_required_evidence_abstains_without_model(monkeypatch, change):
    monkeypatch.setattr(evaluators.model, "post_json", lambda *_: pytest.fail("No inference for evidence gaps"))
    assert evaluators.evaluate([dict(window(), **change)])[0]["outcome"] == "unknown"


def test_exact_price_comparison_is_not_inferred(monkeypatch):
    monkeypatch.setattr(evaluators.model, "post_json", lambda *_: pytest.fail("Arithmetic belongs in code"))
    assert evaluators.evaluate([dict(window(), after_cents=9999)])[0]["outcome"] == "clean"


def test_batch_never_sends_ground_truth_and_retains_finite_evidence_ids(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unit-only")

    def respond(_url, _key, body):
        assert body["state"] == {"cases": {"w0": {"checkout_text": "Total USD 120.00"}}}
        assert "state.cases.w0" in body["questions"]["w0"]["instructions"]
        return {
            "model": "jev-1.13.0",
            "answers": {"w0": {"choice": "defect", "confidence": 1,
                              "probabilities": {"defect": 1, "clean": 0, "unknown": 0}}},
        }

    monkeypatch.setattr(evaluators.model, "post_json", respond)
    result = evaluators.evaluate([dict(window(), ground_truth="defect")])[0]
    assert result["evidence_id"] == "opaque" and result["requires_review"]


@pytest.mark.parametrize("amount", [True, -1, "100", 1.5])
def test_invalid_price_input_is_an_error(amount):
    with pytest.raises(ValueError, match="integer cents"):
        evaluators.evaluate([dict(window(), before_cents=amount)])


def test_corpus_keeps_related_cases_together_and_preserves_denominators():
    cases = corpus()
    assert len(cases) == 36
    for kind in evaluators.RUBRICS:
        for split in ("development", "holdout"):
            group = [c for c in cases if c["kind"] == kind and c["split"] == split]
            assert len(group) == 6
            assert sum(c["ground_truth"] == "defect" for c in group) == 3
    for family in {c["family"] for c in cases}:
        assert len({c["split"] for c in cases if c["family"] == family}) == 1
    holdout = [c for c in cases if c["split"] == "holdout"]
    result = metrics(holdout, [
        {"evidence_id": c["id"], "outcome": "defect" if c["ground_truth"] == "defect" else "unknown"}
        for c in holdout
    ])
    assert result["true_positive"] == 9 and result["false_positive"] == 0
    assert result["definitive_controls"] == 0 and result["unknown"] == 9
