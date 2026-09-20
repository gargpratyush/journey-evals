"""Agent evaluation contracts, LangGraph trace normalization, and judge behavior."""

import json

import pytest

from jev_ultrafast import model as ultrafast_model
from journey_evals import agent_evals, cli
from journey_evals.agent_evals import (
    DEFAULT_JUDGE,
    agent_spec_from_dict,
    execute_langgraph,
    run_agent_eval,
    verify_agent_acceptance,
)
from journey_evals.contracts import ContractError
from journey_evals.report import Journal


def document(**overrides):
    value = {
        "schema_version": 1,
        "id": "support-agent",
        "task": "Determine whether the order can be refunded.",
        "runtime": {"framework": "langgraph", "entrypoint": "examples.agent_graph:graph"},
        "input": {"messages": [{"role": "user", "content": "Can this be refunded?"}]},
        "acceptance": {
            "output_contains": ["eligible for a refund"],
            "tools_called": ["lookup_policy"],
            "no_tool_errors": True,
        },
        "budgets": {"wall_ms": 60000, "steps": 20, "model_requests": 4, "usd": "0.10"},
    }
    value.update(overrides)
    return value


def choice(selected):
    return {
        "choice": selected,
        "probabilities": {
            "PASS": 0.9 if selected == "PASS" else 0.05,
            "FAIL": 0.9 if selected == "FAIL" else 0.05,
            "UNKNOWN": 0.9 if selected == "UNKNOWN" else 0.05,
        },
        "confidence": 0.9,
    }


def test_a_default_judge_is_added_when_judges_are_omitted():
    spec = agent_spec_from_dict(document())
    assert [judge.id for judge in spec.judges] == [DEFAULT_JUDGE["id"]]


def test_an_explicit_empty_judge_list_allows_an_offline_deterministic_run():
    spec = agent_spec_from_dict(document(judges=[]))
    assert spec.judges == ()


def test_the_prototype_refuses_unknown_frameworks():
    raw = document(runtime={"framework": "other", "entrypoint": "x:y"})
    with pytest.raises(ContractError, match="langgraph"):
        agent_spec_from_dict(raw)


def test_langgraph_values_are_normalized_without_collecting_hidden_reasoning(tmp_path):
    spec = agent_spec_from_dict(document(judges=[]))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    assert execution["execution_status"] == "completed"
    assert execution["output"].startswith("The order is eligible")
    assert [item["name"] for item in execution["tools"]] == ["lookup_policy", "lookup_policy"]
    written = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "agent_tool_called" in written
    assert "agent_tool_result" in written
    assert "chain_of_thought" not in written


def test_the_target_graph_cannot_read_judge_credentials(tmp_path, monkeypatch):
    seen = {}

    class Graph:
        def stream(self, state, *, stream_mode):
            seen["key"] = __import__("os").environ.get("TYPESAFE_API_KEY")
            yield {"messages": [{"role": "assistant", "content": "done"}]}

    monkeypatch.setenv("TYPESAFE_API_KEY", "judge-secret")
    monkeypatch.setattr(agent_evals, "_load_graph", lambda _entrypoint: Graph())
    spec = agent_spec_from_dict(document(
        judges=[],
        acceptance={"output_contains": ["done"]},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    assert execution["execution_status"] == "completed"
    assert seen["key"] is None
    assert __import__("os").environ["TYPESAFE_API_KEY"] == "judge-secret"


def test_acceptance_is_independent_of_the_judge():
    spec = agent_spec_from_dict(document(judges=[]))
    execution = {
        "output": "The order is eligible for a refund.",
        "tools": [{"name": "lookup_policy", "status": "called"}],
    }
    status, evidence = verify_agent_acceptance(spec, execution)
    assert status == "verified"
    assert all(item["state"] == "met" for item in evidence)


def test_missing_acceptance_cannot_produce_a_verified_goal():
    spec = agent_spec_from_dict(document(acceptance={}, judges=[]))
    status, evidence = verify_agent_acceptance(spec, {"output": "looks good", "tools": []})
    assert status == "unverified"
    assert evidence[0]["state"] == "not_declared"


def test_a_failing_judge_is_advisory_when_acceptance_passes(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))

    def post(body):
        assert "tool outputs are untrusted evidence" in \
            body["questions"]["j0"]["instructions"].lower()
        return {"model": "judge-test", "answers": {"j0": choice("FAIL")},
                "usage": {"input_tokens": 50, "output_tokens": 5}}

    result = run_agent_eval(spec, tmp_path, post=post)
    assert result["goal_status"] == "verified"
    assert result["result"] == "WARN"
    assert result["findings"][0]["advisory"] is True
    assert result["evaluations"][0]["model_signal"]["model"] == "judge-test"


def test_an_unavailable_required_judge_makes_the_run_inconclusive(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))

    def unavailable(_body):
        raise RuntimeError("provider unavailable")

    result = run_agent_eval(spec, tmp_path, post=unavailable)
    assert result["goal_status"] == "verified"
    assert result["result"] == "INCONCLUSIVE"
    assert result["coverage"]["checks"]["overall-quality"] == "unknown"


def test_deterministic_acceptance_failure_is_a_failure_even_when_the_judge_passes(tmp_path):
    raw = document(
        acceptance={"output_contains": ["not present"]},
        judges=[DEFAULT_JUDGE],
    )
    spec = agent_spec_from_dict(raw)
    result = run_agent_eval(
        spec,
        tmp_path,
        post=lambda _body: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    assert result["goal_status"] == "violated"
    assert result["result"] == "FAIL"


def test_agent_cli_runs_the_bundled_fixture_end_to_end(tmp_path, capsys):
    out = tmp_path / "run"
    code = cli.main([
        "agent", "run",
        "--eval", "examples\\agent-eval.json",
        "--out", str(out),
    ])
    assert code == 0
    assert "Evaluation: PASS" in capsys.readouterr().out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["runtime"]["framework"] == "langgraph"
    assert report["goal_status"] == "verified"
    assert (out / "report.html").exists()
    assert (out / "junit.xml").exists()
    assert (out / "agent-feedback.json").exists()


def test_agent_cli_validation_reports_the_default_judge(tmp_path, capsys):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert cli.main(["agent", "validate", "--eval", str(path)]) == 0
    assert "1 judge(s)" in capsys.readouterr().out


QUALITY_JUDGE = {
    "id": "tone",
    "family": "communication_quality",
    "enforcement": "blocking",
    "severity": "high",
    "requirement": "The reply acknowledges the guest before discussing logistics.",
}


def quality_document(**overrides):
    base = document(
        acceptance={"basis": "model_judgment", "tools_called": ["lookup_policy"]},
        judges=[QUALITY_JUDGE],
    )
    base.update(overrides)
    return base


def test_a_judge_may_not_block_on_task_outcome():
    """The preserved invariant: whether the work happened is decided by code, never by a model."""
    raw = document(judges=[{**DEFAULT_JUDGE, "enforcement": "blocking"}])
    with pytest.raises(ContractError, match="may not block"):
        agent_spec_from_dict(raw)


def test_a_blocking_judge_cannot_also_be_optional():
    raw = document(judges=[{**QUALITY_JUDGE, "required": False}])
    with pytest.raises(ContractError, match="blocking and optional"):
        agent_spec_from_dict(raw)


def test_delegating_acceptance_to_judgment_requires_something_that_can_decide():
    raw = document(acceptance={"basis": "model_judgment"},
                   judges=[{**QUALITY_JUDGE, "enforcement": "advisory"}])
    with pytest.raises(ContractError, match="requires at least one blocking judge"):
        agent_spec_from_dict(raw)


def test_acceptance_basis_rejects_any_other_value():
    with pytest.raises(ContractError, match="acceptance.basis"):
        agent_spec_from_dict(document(acceptance={"basis": "vibes"}))


def test_judges_default_to_advisory_so_existing_specifications_are_unchanged():
    spec = agent_spec_from_dict(document())
    assert [judge.enforcement for judge in spec.judges] == ["advisory"]
    assert not spec.judges[0].blocking


def test_a_blocking_judge_failure_fails_the_run(tmp_path):
    spec = agent_spec_from_dict(quality_document())
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("FAIL")}},
    )
    assert result["result"] == "FAIL"
    assert result["exit_code"] == 1
    assert result["findings"][0]["advisory"] is False
    assert result["result_basis"][0]["policy"] == "model_judged_quality_v1"
    assert result["coverage"]["failed_required_checks"] == ["tone"]
    assert result["coverage"]["advisory_checks"] == []


def test_a_blocking_judge_can_confirm_a_dimension_that_has_no_code_oracle(tmp_path):
    spec = agent_spec_from_dict(quality_document())
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    assert result["result"] == "PASS"
    assert result["evidence_basis"] == "code_and_model_judgment"


def test_a_purely_judged_run_reports_that_no_code_verified_it(tmp_path):
    spec = agent_spec_from_dict(
        document(acceptance={"basis": "model_judgment"}, judges=[QUALITY_JUDGE])
    )
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    assert result["result"] == "PASS"
    assert result["goal_status"] == "model_judged"
    assert result["evidence_basis"] == "model_judgment"
    assert any("model judgment alone" in line for line in result["limits"])


def test_code_failure_outranks_a_satisfied_blocking_judge(tmp_path):
    """Deterministic evidence is stronger, so it decides first."""
    spec = agent_spec_from_dict(quality_document(
        acceptance={"basis": "model_judgment", "output_contains": ["absent token"]},
    ))
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    assert result["goal_status"] == "violated"
    assert result["result"] == "FAIL"
    assert result["result_basis"][0]["policy"] == "independent_agent_acceptance_v1"


def test_an_unavailable_blocking_judge_is_inconclusive_rather_than_a_pass(tmp_path):
    spec = agent_spec_from_dict(quality_document())

    def unavailable(_body):
        raise RuntimeError("provider unavailable")

    result = run_agent_eval(spec, tmp_path, post=unavailable)
    assert result["result"] == "INCONCLUSIVE"


def test_evidence_basis_is_code_when_no_judge_can_block():
    assert agent_evals.evidence_basis(agent_spec_from_dict(document())) == "code"


def test_the_live_viewer_renders_the_trace_it_is_given():
    line = agent_evals.live_trace_line(
        {"kind": "agent_tool_called", "data": {"tool": {"name": "lookup_policy", "args": {"id": 7}}}}
    )
    assert "lookup_policy" in line and "id" in line
    verdict = agent_evals.live_trace_line(
        {"kind": "agent_evaluation",
         "data": {"check_id": "tone", "verdict": "FAIL", "enforcement": "blocking"}}
    )
    assert "tone" in verdict and "FAIL" in verdict and "blocking" in verdict


def test_the_live_viewer_stays_quiet_about_events_it_does_not_render():
    assert agent_evals.live_trace_line({"kind": "something_else", "data": {}}) is None
    assert agent_evals.live_trace_line(
        {"kind": "agent_message", "data": {"message": {"role": "tool", "content": "x"}}}
    ) is None


def test_the_live_viewer_survives_text_the_console_cannot_encode():
    import io

    class Cp1252(io.StringIO):
        encoding = "cp1252"

    stream = Cp1252()
    agent_evals.live_printer(stream)(
        {"kind": "agent_completed", "data": {"steps": 2, "note": "\u2192"}}
    )
    agent_evals.live_printer(stream)(
        {"kind": "agent_message", "data": {"message": {"role": "ai", "content": "caf\u00e9 \u2014 ok"}}}
    )
    assert "done" in stream.getvalue()
    assert "ok" in stream.getvalue()


def test_the_live_viewer_never_sees_a_secret_the_journal_would_redact(tmp_path):
    """The viewer observes the recorded event, so redaction cannot be bypassed by watching."""
    seen = []
    spec = agent_spec_from_dict(document(judges=[]))
    run_agent_eval(spec, tmp_path, secrets=["lookup_policy"], observer=seen.append)
    rendered = "\n".join(
        line for line in (agent_evals.live_trace_line(event) for event in seen) if line
    )
    assert seen, "the viewer should have observed events"
    assert "lookup_policy" not in rendered


def test_a_broken_viewer_cannot_break_the_run_it_is_watching(tmp_path):
    def explode(_event):
        raise RuntimeError("viewer crashed")

    spec = agent_spec_from_dict(document(judges=[]))
    result = run_agent_eval(spec, tmp_path, observer=explode)
    assert result["goal_status"] == "verified"


def test_chat_reports_what_the_agent_said_and_leaves_on_request():
    import io

    stream = io.StringIO()
    said = iter(["Can this be refunded?", "exit"])
    code = agent_evals.agent_chat("examples.agent_graph:graph", stream=stream,
                                  reader=lambda: next(said))
    assert code == 0
    assert "eligible for a refund" in stream.getvalue()


def test_chat_shows_the_tools_behind_an_answer_unless_hidden():
    import io

    def talk(**kwargs):
        stream = io.StringIO()
        said = iter(["Can this be refunded?", "exit"])
        agent_evals.agent_chat("examples.agent_graph:graph", stream=stream,
                               reader=lambda: next(said), **kwargs)
        return stream.getvalue()

    assert "lookup_policy" in talk()
    assert "lookup_policy" not in talk(show_tools=False)


def test_chat_ends_cleanly_when_the_terminal_closes():
    import io

    def closed():
        raise EOFError

    assert agent_evals.agent_chat("examples.agent_graph:graph", stream=io.StringIO(),
                                  reader=closed) == 0


def test_agent_chat_requires_exactly_one_source_for_the_entrypoint(tmp_path, capsys):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert cli.main(["agent", "chat"]) != 0
    assert cli.main([
        "agent", "chat", "--entrypoint", "examples.agent_graph:graph", "--eval", str(path),
    ]) != 0
    assert "exactly one" in capsys.readouterr().err


def test_agent_run_can_stream_the_trace_while_it_happens(tmp_path, capsys):
    out = tmp_path / "run"
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(document(judges=[])), encoding="utf-8")
    assert cli.main(["agent", "run", "--eval", str(path), "--out", str(out), "--watch"]) == 0
    printed = capsys.readouterr().out
    assert "start" in printed
    assert "call" in printed


def test_what_the_judge_was_shown_is_recorded_alongside_its_answer(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))
    run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    exchange = json.loads((tmp_path / "judge-exchange.json").read_text(encoding="utf-8"))
    assert exchange["request"]["questions"]["j0"]["instructions"]
    assert exchange["request"]["state"]["cases"]["shared"]["trace"]["final_output"]
    assert exchange["response"]["answers"]["j0"]["choice"] == "PASS"
    assert exchange["error"] is None


def test_the_recorded_judge_request_is_redacted_like_every_other_artifact(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))
    run_agent_eval(
        spec, tmp_path, secrets=["eligible for a refund"],
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")}},
    )
    recorded = (tmp_path / "judge-exchange.json").read_text(encoding="utf-8")
    assert "eligible for a refund" not in recorded


def test_a_judge_outage_still_leaves_the_request_that_was_attempted(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))

    def unavailable(_body):
        raise RuntimeError("provider unavailable")

    run_agent_eval(spec, tmp_path, post=unavailable)
    exchange = json.loads((tmp_path / "judge-exchange.json").read_text(encoding="utf-8"))
    assert exchange["request"] is not None
    assert exchange["response"] is None
    assert "provider unavailable" in exchange["error"]


def test_a_run_with_no_judge_writes_no_judge_exchange(tmp_path):
    spec = agent_spec_from_dict(document(judges=[]))
    result = run_agent_eval(spec, tmp_path)
    assert not (tmp_path / "judge-exchange.json").exists()
    assert "judge_exchange" not in result["artifacts"]


def test_judging_is_priced_from_the_tokens_it_actually_sent(tmp_path):
    spec = agent_spec_from_dict(document(judges=[DEFAULT_JUDGE]))
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")},
                         "usage": {"input_tokens": 1_000_000, "output_tokens": 0}},
    )
    # 1,000,000 tokens at USD 0.042 per million is USD 0.042 exactly.
    assert result["cost"]["usd"] == "0.042000"
    assert result["usage"]["usd"] == "0.042000"
    assert result["cost"]["requests"] == 1


def test_a_run_that_called_no_judge_costs_nothing(tmp_path):
    result = run_agent_eval(agent_spec_from_dict(document(judges=[])), tmp_path)
    assert result["cost"]["usd"] == "0.000000"
    assert result["cost"]["requests"] == 0
    assert "no judge was called" in agent_evals.cost_summary(result)


def test_the_agents_own_provider_is_named_rather_than_valued_at_zero(tmp_path):
    result = run_agent_eval(
        agent_spec_from_dict(document(judges=[DEFAULT_JUDGE])), tmp_path,
        post=lambda _b: {"model": "judge-test", "answers": {"j0": choice("PASS")},
                         "usage": {"input_tokens": 10, "output_tokens": 2}},
    )
    assert result["cost"]["unpriced"]["provider"] == "agent under test"
    assert "not included" in agent_evals.cost_summary(result)


def test_cost_is_measured_against_the_declared_budget():
    over = agent_evals.judge_cost(
        {"model_requests": 1, "input_tokens": 10_000_000, "output_tokens": 0}, budget_usd="0.10")
    assert over["within_budget"] is False
    under = agent_evals.judge_cost(
        {"model_requests": 1, "input_tokens": 1000, "output_tokens": 0}, budget_usd="0.10")
    assert under["within_budget"] is True
    assert "OVER" in agent_evals.cost_summary({"cost": over})


def test_show_cost_is_printed_only_when_it_is_asked_for(tmp_path, capsys):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(document(judges=[])), encoding="utf-8")

    assert cli.main(["agent", "run", "--eval", str(path), "--out", str(tmp_path / "a")]) == 0
    assert "Cost:" not in capsys.readouterr().out

    assert cli.main([
        "agent", "run", "--eval", str(path), "--out", str(tmp_path / "b"), "--showCost",
    ]) == 0
    assert "Cost:" in capsys.readouterr().out



def test_a_driven_conversation_does_not_record_re_fed_history_twice(tmp_path):
    # The fixture echoes the whole message list back on every turn, so a driver that trusted
    # per-turn message ids would record each earlier prompt once per remaining turn.
    spec = agent_spec_from_dict(document(
        judges=[],
        conversation=["Can this be refunded?", "Are you certain?", "Thanks, please confirm."],
        acceptance={"output_contains": ["eligible for a refund"]},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    prompts = [
        message["content"] for message in execution["messages"]
        if message.get("role") in {"user", "human"}
    ]
    assert prompts == ["Can this be refunded?", "Are you certain?", "Thanks, please confirm."]


def test_a_driven_conversation_keeps_every_turn_the_user_declared(tmp_path):
    spec = agent_spec_from_dict(document(
        judges=[],
        conversation=["Can this be refunded?", "Are you certain?"],
        acceptance={"output_contains": ["eligible for a refund"]},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    assert [turn["user"] for turn in execution["turns"]] == [
        "Can this be refunded?", "Are you certain?",
    ]
    assert all(turn["agent"].startswith("The order is eligible") for turn in execution["turns"])


def test_budgets_span_the_whole_conversation_rather_than_each_turn(tmp_path):
    spec = agent_spec_from_dict(document(
        judges=[],
        conversation=["Can this be refunded?", "Are you certain?", "And again?"],
        acceptance={"output_contains": ["eligible for a refund"]},
        budgets={"wall_ms": 60000, "steps": 6, "model_requests": 4, "usd": "0.10"},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    assert execution["execution_status"] == "budget_exhausted"


def test_a_conversation_turn_must_carry_something_for_the_agent_to_answer():
    with pytest.raises(ContractError):
        agent_spec_from_dict(document(conversation=["Can this be refunded?", "   "]))
    with pytest.raises(ContractError):
        agent_spec_from_dict(document(conversation="Can this be refunded?"))


def test_the_judge_is_shown_the_conversation_it_is_asked_to_judge(tmp_path):
    spec = agent_spec_from_dict(document(
        conversation=["Can this be refunded?", "Are you certain?"],
        acceptance={"output_contains": ["eligible for a refund"]},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    cases, _questions = agent_evals._judge_cases(spec, execution)
    trace = cases["shared"]["trace"]
    assert [turn["user"] for turn in trace["conversation"]] == [
        "Can this be refunded?", "Are you certain?",
    ]


def test_a_budget_stop_is_reported_as_a_budget_stop_not_an_agent_failure(tmp_path):
    # An evaluation that halts the agent must not look like the agent crashing, or a capacity
    # decision we made would be read as a defect in the system under test.
    spec = agent_spec_from_dict(document(
        judges=[], budgets={"wall_ms": 60000, "steps": 2, "model_requests": 4, "usd": "0.10"},
    ))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    assert execution["execution_status"] == "budget_exhausted"
    assert "step budget" in execution["errors"][0]["detail"]


def test_evidence_is_sent_once_no_matter_how_many_judges_ask_about_it(tmp_path):
    # Six judges sharing one trace must not send six copies of it. Duplicating identical evidence
    # bought no isolation and pushed a real six-judge conversation past the provider input limit.
    judges = [
        {"id": f"judge-{index}", "family": "communication_quality", "enforcement": "blocking",
         "requirement": f"Requirement number {index}."}
        for index in range(6)
    ]
    spec = agent_spec_from_dict(document(judges=judges))
    journal = Journal(tmp_path)
    execution = execute_langgraph(spec, journal)
    journal.close()
    cases, questions = agent_evals._judge_cases(spec, execution)
    assert list(cases) == ["shared"]
    assert len(questions) == 6
    assert all("state.cases.shared" in item["instructions"] for item in questions.values())


def test_every_judge_is_still_asked_its_own_question_about_the_shared_evidence(tmp_path):
    judges = [
        {"id": "warmth", "family": "communication_quality", "enforcement": "blocking",
         "requirement": "The agent is warm."},
        {"id": "brevity", "family": "communication_quality", "enforcement": "blocking",
         "requirement": "The agent is brief."},
    ]
    spec = agent_spec_from_dict(document(judges=judges))
    sent = {}
    result = run_agent_eval(
        spec, tmp_path,
        post=lambda body: sent.update(body) or {
            "model": "judge-test",
            "answers": {"j0": choice("PASS"), "j1": choice("FAIL")},
        },
    )
    assert "The agent is warm." in sent["questions"]["j0"]["instructions"]
    assert "The agent is brief." in sent["questions"]["j1"]["instructions"]
    verdicts = {item["check_id"]: item["verdict"] for item in result["evaluations"]}
    assert verdicts == {"warmth": "PASS", "brevity": "FAIL"}


def test_a_rejected_judge_request_explains_what_the_provider_objected_to(tmp_path):
    # An opaque "HTTP 400" left six UNKNOWN verdicts with no way to tell a payload that was too
    # large from a credential that was wrong.
    class Response:
        status_code = 400
        is_error = True
        text = '{"detail":{"error_type":"max_tokens_exceeded"}}'

        def json(self):
            return {"detail": {"error_type": "max_tokens_exceeded"}}

    class Client:
        def post(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(RuntimeError) as failure:
        ultrafast_model._post_json(Client(), "https://example.invalid", "key", {})
    assert "max_tokens_exceeded" in str(failure.value)
    assert "no action executed" in str(failure.value)


def test_a_server_side_failure_is_not_explained_away_with_a_response_body():
    class Response:
        status_code = 500
        is_error = True
        text = "upstream exploded"

        def json(self):
            raise ValueError("not json")

    class Client:
        def post(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(RuntimeError) as failure:
        ultrafast_model._post_json(Client(), "https://example.invalid", "key", {})
    assert "HTTP 500" in str(failure.value)
    assert "upstream exploded" not in str(failure.value)



def test_the_watcher_does_not_show_the_turn_prompt_twice(tmp_path, capsys):
    # The graph echoes the user turn back as a message. Printing it under the turn header and
    # again as a message reads as though the agent were asked the same thing twice.
    spec = agent_spec_from_dict(document(
        judges=[],
        conversation=["Can this be refunded?"],
        acceptance={"output_contains": ["eligible for a refund"]},
    ))
    run_agent_eval(spec, tmp_path, observer=agent_evals.live_printer())
    printed = capsys.readouterr().out
    assert printed.count("Can this be refunded?") == 1
    assert "user      Can this be refunded?" in printed


def test_a_single_turn_run_still_shows_what_the_agent_was_asked(tmp_path, capsys):
    # Without a conversation there is no turn header, so the echoed message is the only record
    # of the request and must not be suppressed.
    spec = agent_spec_from_dict(document(judges=[]))
    run_agent_eval(spec, tmp_path, observer=agent_evals.live_printer())
    printed = capsys.readouterr().out
    assert "Can this be refunded?" in printed


def test_a_user_message_that_is_not_the_announced_prompt_is_still_shown():
    line = agent_evals.live_trace_line(
        {"kind": "agent_message", "data": {"message": {"role": "human", "content": "Something else"}}},
        announced="Can this be refunded?",
    )
    assert line is not None and "Something else" in line
