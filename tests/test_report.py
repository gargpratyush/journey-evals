"""Reporting: redaction, escaping, durable journalling and the machine formats CI consumes.

A report is the only lasting product of a run. If it can leak a secret, execute page text as
markup, or lose the record of an event, then the evidence it presents cannot be trusted.
"""

import copy
import json
import xml.etree.ElementTree as ElementTree

from journey_evals.report import (
    Journal,
    Redactor,
    render_agent_feedback,
    render_html,
    render_junit,
    terminal_summary,
    write_agent_feedback,
    write_report,
)

RESULT = {
    "schema_version": 1,
    "run_id": "run-1",
    "tool_version": "0.1.0",
    "upstream_revision": "abc",
    "model_versions": {"actor": "m", "evaluator": "m", "text": "t"},
    "history": [],
    "observations": [],
    "artifacts": {},
    "limits": {},
    "acceptance": {"url_path_is": "/done"},
    "result": "FAIL",
    "result_basis": [{"check_id": "c1", "effect": "fail", "policy": "declared_required_check_v1"}],
    "journey_id": "flight-booking",
    "journey": {"resolved": {"id": "flight-booking", "viewport": {"width": 1120, "height": 780}},
                "supplied": {}},
    "effective_spec_sha256": "abc123",
    "goal_status": "violated",
    "goal_evidence": [{"criterion": "backend", "state": "unmet", "detail": "0 recorded item(s)"}],
    "coverage": {"checks": {"c1": "failed"}, "check_evidence": {"c1": ["obs-1"]},
                 "probes": {"p1": "failed"}, "missing_required_checks": [],
                 "failed_required_checks": ["c1"], "unvisited_probes": [], "complete": True,
                 "steps": 5, "observations": 10, "acceptance": []},
    "findings": [{"id": "finding-1", "evaluator": "c1", "evaluator_version": 1,
                  "category": "interaction_correctness", "severity": "high", "advisory": True,
                  "title": "Searching did not show <img src=x onerror=alert(1)>",
                  "observed": {"after_text": "<img src=x onerror=alert(1)>"},
                  "expected": {"requirement": "Searching shows results."},
                  "evidence_ids": ["obs-1"], "first_step": 2, "last_step": 2, "occurrences": 1,
                  "reproducibility": "single_run", "provenance": "semantic",
                  "confirmation": "review_required", "model_signal": {}}],
    "evaluations": [],
    "errors": [],
    "usage": {"model_requests": 12, "actor_requests": 7, "evaluator_requests": 5,
              "input_tokens": 100, "output_tokens": 10, "usd": "0.000634"},
    "timings": {"setup_ms": 1, "interaction_ms": 2, "verification_ms": 3, "reporting_ms": 4,
                "total_ms": 10},
    "environment": {},
    "execution_status": "completed",
}


# -- redaction --------------------------------------------------------------------------------


def test_a_configured_secret_never_survives_redaction():
    redactor = Redactor(secrets=("sk-live-abcdef123456",))
    assert "sk-live" not in redactor.text("Authorization: Bearer sk-live-abcdef123456")


def test_redaction_reaches_inside_nested_structures():
    redactor = Redactor(secrets=("topsecret",))
    cleaned = redactor.scrub({"a": ["topsecret", {"b": "prefix topsecret suffix"}]})
    assert "topsecret" not in json.dumps(cleaned)


def test_credentials_in_a_url_are_removed_without_losing_the_path():
    redactor = Redactor()
    cleaned = redactor.url("http://user:pw@127.0.0.1:8111/results?api_key=abcd1234&page=2")
    assert "pw@" not in cleaned and "abcd1234" not in cleaned
    assert "/results" in cleaned and "page=2" in cleaned


def test_a_declared_pattern_is_redacted_as_well():
    redactor = Redactor(patterns=[r"\b\d{3}-\d{2}-\d{4}\b"])
    assert "123-45-6789" not in redactor.text("ssn 123-45-6789 here")


def test_an_empty_secret_does_not_shred_every_string():
    redactor = Redactor(secrets=("", None, "short"))
    assert redactor.text("unchanged text") == "unchanged text"


# -- the journal ------------------------------------------------------------------------------


def test_every_event_is_appended_with_a_monotonic_sequence(tmp_path):
    journal = Journal(tmp_path)
    first = journal.append("observation", {"page": {"text": "a"}})
    second = journal.append("action_attempt", {"action": "Search"})
    journal.close()
    assert second["sequence"] > first["sequence"]
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == "observation"


def test_the_journal_redacts_before_anything_reaches_disk(tmp_path):
    journal = Journal(tmp_path, redactor=Redactor(secrets=("hunter2-is-long-enough",)))
    journal.append("observation", {"page": {"text": "password hunter2-is-long-enough"}})
    journal.close()
    assert "hunter2" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_events_can_be_read_back_after_the_journal_is_closed(tmp_path):
    journal = Journal(tmp_path)
    journal.append("observation", {"page": {"text": "a"}})
    journal.close()
    assert [event["kind"] for event in journal.events()] == ["observation"]


def test_every_event_carries_a_host_monotonic_reading(tmp_path):
    journal = Journal(tmp_path)
    event = journal.append("observation", {"page": {}})
    journal.close()
    assert isinstance(event["host_monotonic_ns"], int)


# -- persisted report -------------------------------------------------------------------------


def test_the_report_is_written_as_readable_json(tmp_path):
    write_report(tmp_path, RESULT)
    written = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert written["result"] == "FAIL"
    assert written["effective_spec_sha256"] == "abc123"


# -- html -------------------------------------------------------------------------------------


def test_page_derived_text_is_escaped_rather_than_rendered():
    html = render_html(RESULT, journey_task="Book the flight")
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_the_html_states_the_result_and_what_determined_it():
    html = render_html(RESULT, journey_task="Book the flight")
    assert "FAIL" in html
    assert "c1" in html


def test_a_task_containing_markup_is_escaped_too():
    html = render_html(RESULT, journey_task="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html


def test_the_html_calls_the_journey_by_its_declared_name():
    # A worker's report records the journey under its resolved specification. Reading only a top
    # level journey_id made every report headline itself "Journey: journey".
    payload = dict(RESULT, journey={"resolved": {"id": "flight-booking"}})
    assert "<h1>Journey: flight-booking</h1>" in render_html(payload, journey_task="Book")
    bare = {key: value for key, value in RESULT.items() if key not in ("journey", "journey_id")}
    assert "<h1>Journey: run-1</h1>" in render_html(bare, journey_task="Book")


# -- junit ------------------------------------------------------------------------------------


def test_junit_names_one_case_per_declared_check():
    tree = ElementTree.fromstring(render_junit(RESULT))
    cases = tree.iter("testcase")
    names = {case.get("name") for case in cases}
    assert "c1" in names


def test_a_failed_check_becomes_a_junit_failure():
    tree = ElementTree.fromstring(render_junit(RESULT))
    case = next(c for c in tree.iter("testcase") if c.get("name") == "c1")
    assert case.find("failure") is not None


def test_junit_is_well_formed_for_a_clean_run():
    clean = {**RESULT, "result": "PASS", "findings": [],
             "coverage": {**RESULT["coverage"], "checks": {"c1": "passed"},
                          "failed_required_checks": []}}
    tree = ElementTree.fromstring(render_junit(clean))
    case = next(c for c in tree.iter("testcase") if c.get("name") == "c1")
    assert case.find("failure") is None


# -- terminal ---------------------------------------------------------------------------------


def test_the_terminal_summary_reports_the_result_and_the_goal():
    text = terminal_summary(RESULT, journey_task="Book the flight")
    assert "FAIL" in text
    assert "flight-booking" in text


def test_the_terminal_summary_names_unvisited_probes():
    result = {**RESULT, "coverage": {**RESULT["coverage"], "unvisited_probes": ["reach-results"]}}
    assert "reach-results" in terminal_summary(result, journey_task="t")


def test_the_terminal_summary_counts_execution_errors():
    result = {**RESULT, "errors": [{"stage": "observation_failed", "recoverable": False}]}
    assert "1" in terminal_summary(result, journey_task="t")


# -- agent feedback ---------------------------------------------------------------------------


def test_feedback_keeps_confirmed_and_advisory_findings_apart():
    feedback = render_agent_feedback(RESULT)
    assert [f["id"] for f in feedback["advisory_findings"]] == ["finding-1"]
    assert feedback["confirmed_findings"] == []


def test_a_measured_finding_is_reported_as_confirmed():
    result = copy.deepcopy(RESULT)
    result["findings"][0]["confirmation"] = "confirmed"
    result["findings"][0]["provenance"] = "deterministic"
    feedback = render_agent_feedback(result)
    assert [f["id"] for f in feedback["confirmed_findings"]] == ["finding-1"]
    assert feedback["advisory_findings"] == []


def test_unresolved_checks_are_listed_as_neither_defect_nor_pass():
    result = copy.deepcopy(RESULT)
    result["coverage"]["checks"] = {"c1": "failed", "c2": "unknown", "c3": "passed"}
    feedback = render_agent_feedback(result)
    assert feedback["unresolved_coverage"] == [{"check_id": "c2", "state": "unknown"}]
    assert "c3" not in json.dumps(feedback["unresolved_coverage"])


def test_feedback_tells_the_agent_to_fix_the_application_not_the_oracle():
    joined = " ".join(render_agent_feedback(RESULT)["instructions"]).lower()
    assert "do not edit journeys" in joined
    assert "untrusted" in joined


def test_feedback_carries_the_spec_identity_so_a_rerun_can_be_compared():
    feedback = render_agent_feedback(RESULT)
    assert feedback["effective_spec_sha256"] == "abc123"
    assert feedback["journey_id"] == "flight-booking"
    assert feedback["exit_code"] == 1


def test_feedback_is_written_beside_the_report(tmp_path):
    path = write_agent_feedback(tmp_path, RESULT)
    assert path.name == "agent-feedback.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema"].startswith("journey-evals/")
