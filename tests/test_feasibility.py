import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from journey_evals import browser_checks, feasibility, isolation
from journey_evals.evidence import Collector, Journal
from journey_evals.feasibility import (
    ENDPOINT,
    MODEL,
    SLOW_MACHINE_TIMEOUT,
    Budget,
    load_environment,
)
from journey_evals.fixture import EXPECTED, fare_facts, verify


def request():
    return httpx.Request("POST", ENDPOINT, json={"model": MODEL, "state": "Synthetic", "questions": {}})


def test_budget_reserves_before_sending_and_retains_unknowns(tmp_path):
    path = tmp_path / "budget.json"
    with Budget(path, "0.003", max_attempts=3) as budget:
        budget.reserve(request())
        assert json.loads(path.read_text())["attempts"][0]["accounted_tokens"] == 65536
        with pytest.raises(RuntimeError, match="spending cap"):
            budget.reserve(request())
    with Budget(path, "0.003") as budget:
        with pytest.raises(RuntimeError, match="spending cap"):
            budget.reserve(request())


def test_budget_counts_retries_and_reconciles_actual_usage(tmp_path):
    with Budget(tmp_path / "budget.json", "0.01", max_attempts=2) as budget:
        first = request()
        budget.reserve(first)
        budget.reconcile(httpx.Response(503, request=first))
        second = request()
        budget.reserve(second)
        budget.reconcile(httpx.Response(200, request=second, json={
            "model": MODEL, "usage": {"input_tokens": 100},
        }))
        assert budget.accounted_tokens == 65536 + 100
        with pytest.raises(RuntimeError, match="attempt"):
            budget.reserve(request())


def test_missing_usage_and_changed_model_stop(tmp_path):
    with Budget(tmp_path / "budget.json", "0.1") as budget:
        first = request()
        budget.reserve(first)
        with pytest.raises(ValueError, match="token usage"):
            budget.reconcile(httpx.Response(200, request=first, json={"model": MODEL}))
        assert budget.accounted_tokens == 65536
        second = request()
        budget.reserve(second)
        with pytest.raises(ValueError, match="contract"):
            budget.reconcile(httpx.Response(200, request=second, json={
                "model": "changed", "usage": {"input_tokens": 50},
            }))


def test_budget_excludes_secrets_and_other_providers(tmp_path):
    with Budget(tmp_path / "budget.json", "0.1", secrets=("private-test-key",)) as budget:
        for bad in (
            httpx.Request("POST", "https://other.example/"),
            httpx.Request("POST", ENDPOINT, json={"model": MODEL, "state": "private-test-key"}),
        ):
            with pytest.raises(ValueError):
                budget.reserve(bad)
        assert budget.sent == 0


def test_text_helper_uses_a_client_without_jev_budget_hooks(monkeypatch):
    from jev_ultrafast import model

    model.CLIENT.event_hooks = {"request": [Mock(side_effect=AssertionError("Jev hook reached text helper"))]}
    response = Mock(status_code=200, is_error=False)
    response.json.return_value = {"choices": [{"message": {"content": "{\"text\":\"Sandbox\"}"}}]}
    text_post = Mock(return_value=response)
    monkeypatch.setattr(model.TEXT_CLIENT, "post", text_post)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "helper-key")
    try:
        assert model.field_text({"goal": "Enter Sandbox"})[0] == "Sandbox"
    finally:
        model.CLIENT.event_hooks = {"request": [], "response": []}
    text_post.assert_called_once()


@pytest.mark.parametrize("limit", ["NaN", "Infinity", "-1", "0", "bad"])
def test_invalid_budget_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match="finite positive"):
        Budget(tmp_path / "budget.json", limit)


def test_parallel_ledger_and_silent_cap_increase_rejected(tmp_path):
    path = tmp_path / "budget.json"
    with Budget(path, "0.01"):
        with pytest.raises(RuntimeError, match="locked"):
            with Budget(path, "0.01"):
                pass
    with pytest.raises(ValueError, match="cap"):
        with Budget(path, "1"):
            pass
    assert not path.with_suffix(".lock").exists()


def test_env_mapping_does_not_copy_or_expose_values(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in ("TYPESAFE_API_KEY", "JEV_API_KEY", "TYPESAFE_MODEL"):
        os.environ.pop(key, None)
    path = tmp_path / ".env"
    text = 'JEV_API_KEY="local-private-value"\nUNRELATED_SECRET=do-not-load\n'
    path.write_text(text)
    assert load_environment(path) == "local-private-value"
    assert os.environ["TYPESAFE_API_KEY"] == "local-private-value"
    assert "UNRELATED_SECRET" not in os.environ
    assert path.read_text() == text


@pytest.mark.parametrize("field", ["origin", "destination", "date", "cents", "fare_id", "passenger"])
def test_verifier_rejects_wrong_field_and_missing_record(field):
    record = dict(EXPECTED, cents=10000, currency="USD")
    assert verify([record])["passed"]
    record[field] = "wrong"
    assert not verify([record])["passed"]
    assert not verify([])["passed"]
    assert not verify([record, record])["passed"]


def test_money_is_parsed_exactly_in_code():
    assert fare_facts({"text": "Selected fare: USD 100.00\nCheckout total: USD 120.05"})["checkout_cents"] == 12005
    assert fare_facts({"text": "No price evidence"}) == {}


def test_collector_retains_sequence_gaps_and_navigation_tail(tmp_path):
    journal = Journal(tmp_path / "run")
    collector = Collector(journal)
    collector.accept({"documentId": "first", "sequence": 1, "kind": "begin", "data": {}})
    collector.accept({"documentId": "first", "sequence": 3, "kind": "watermark", "data": {}})
    collector.browser = Mock(evaluate=Mock(return_value={
        "documentId": "first", "sequence": 3, "timeOrigin": 1000, "at": 50, "evaluation": {"truncated": False},
    }))
    assert collector.checkpoint("test")["complete"] is False
    collector.accept({"documentId": "second", "sequence": 1, "kind": "begin", "data": {}})
    assert "navigation_tail_unacknowledged" in collector.documents["first"]["gaps"]
    collector.close()
    journal.close()


@pytest.mark.parametrize("watermark", [None, {"sequence": 0, "documentId": "first"},
                                     {"sequence": 1, "documentId": ""}])
def test_absent_collector_cannot_pass(tmp_path, watermark):
    journal = Journal(tmp_path / "run")
    collector = Collector(journal)
    collector.browser = Mock(evaluate=Mock(return_value=watermark))
    with pytest.raises((RuntimeError, ValueError), match="incomplete"):
        collector.checkpoint("absent")
    collector.close()
    journal.close()


def test_profile_cleanup_retries_only_a_bounded_owned_path(tmp_path, monkeypatch):
    monkeypatch.setattr(isolation, "work_root", lambda: tmp_path / "work")
    owner = isolation.OwnedSession(tmp_path)
    owner.directory = tmp_path / "work" / "browser-unit"
    owner.directory.mkdir(parents=True)
    owner.chrome = owner.daemon = None
    owner.previous = {}
    owner.log = io.BytesIO()
    original = isolation.shutil.rmtree
    calls = []

    def remove(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("simulated Windows handle release")
        original(path)

    monkeypatch.setattr(isolation.shutil, "rmtree", remove)
    monkeypatch.setattr(isolation.time, "sleep", lambda _: None)
    owner.__exit__()
    assert owner.closed and owner.cleanup_retries == 1 and not owner.directory.exists()
    assert calls == [owner.directory, owner.directory]


def test_first_frame_dependent_calls_carry_the_startup_bound_not_the_operational_one():
    from jev_ultrafast import browser as browser_module
    source = Path(browser_module.__file__).read_text()
    startup = source.split("def call(", 1)[0]
    for method in ("Emulation.setDeviceMetricsOverride", "Emulation.setFocusEmulationEnabled",
                   "Page.bringToFront"):
        call = startup.split(method, 1)[1].split("self.call(", 1)[0]
        assert "_response_timeout=SLOW_MACHINE_TIMEOUT" in call, method
    assert SLOW_MACHINE_TIMEOUT >= 60


def test_child_environment_preserves_windows_paths_not_credentials():
    assert {"SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "USERPROFILE"} <= isolation.OS_ENV
    assert not {"JEV_API_KEY", "TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"} & isolation.OS_ENV


def test_browser_shutdown_uses_root_cdp_without_a_page_session(monkeypatch):
    from cdp_use import client

    connection = Mock(start=AsyncMock(), send_raw=AsyncMock(), stop=AsyncMock())
    factory = Mock(return_value=connection)
    monkeypatch.setattr(client, "CDPClient", factory)
    asyncio.run(isolation.close_browser("ws://127.0.0.1/owned-browser"))
    factory.assert_called_once_with("ws://127.0.0.1/owned-browser")
    connection.send_raw.assert_awaited_once_with("Browser.close")
    connection.stop.assert_awaited_once()


def test_cleanup_preserves_primary_error_and_attempts_both_processes(tmp_path, monkeypatch):
    owner = isolation.OwnedSession(tmp_path)
    owner.directory = tmp_path / "work" / "browser-unit"
    owner.directory.mkdir(parents=True)
    owner.previous, owner.log = {}, io.BytesIO()
    owner.daemon = Mock(poll=Mock(return_value=None))
    owner.daemon.wait.side_effect = subprocess.TimeoutExpired("owned-daemon", 5)
    owner.chrome = Mock(poll=Mock(return_value=None))
    owner.chrome.wait.side_effect = subprocess.TimeoutExpired("owned-browser", 5)
    stop = Mock(side_effect=TimeoutError("IPC unavailable"))
    monkeypatch.setattr(isolation, "stop_daemon", stop)
    primary = ValueError("original failure")
    owner.__exit__(ValueError, primary, None)
    stop.assert_called_once()
    owner.daemon.terminate.assert_not_called()
    owner.chrome.terminate.assert_called_once()
    assert len(owner.cleanup_errors) == 2
    assert "Owned cleanup failed" in primary.__notes__[0]
    assert not owner.closed and owner.directory.exists() and owner.log.closed


def test_secret_scan_excludes_private_profiles_not_published_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_checks, "ARTIFACTS", tmp_path)
    profile = tmp_path / "work" / "browser-unit"
    profile.mkdir(parents=True)
    (profile / "lockfile").write_bytes(b"test-key")
    report = tmp_path / "report.json"
    report.write_bytes(b"synthetic")
    assert browser_checks.scan_published_artifacts(b"test-key")["passed"]
    report.write_bytes(b"test-key")
    assert not browser_checks.scan_published_artifacts(b"test-key")["passed"]


def test_checkpoint_exception_replaces_stale_green_and_redacts(tmp_path, monkeypatch, capsys):
    original = feasibility.checkpoint_receipt
    monkeypatch.setattr(feasibility, "checkpoint_receipt", lambda name, result: original(name, result, tmp_path))
    original("cp1", {"status": "GREEN"}, tmp_path)
    monkeypatch.setattr(sys, "argv", ["journey-evals", "verify-browser"])
    monkeypatch.setenv("JEV_API_KEY", "private-test-key")
    monkeypatch.setattr(browser_checks, "browser_checkpoint", Mock(side_effect=PermissionError("private-test-key")))
    assert feasibility.main() == 1
    receipt = json.loads((tmp_path / "checkpoints" / "cp1.json").read_text())
    assert receipt["status"] == "AMBER" and "[REDACTED]" in receipt["error"]
    assert len(list((tmp_path / "checkpoints" / "attempts").glob("*.json"))) == 2
    assert "private-test-key" not in capsys.readouterr().err


def _stall(directory):
    return {"status": "ERROR", "error": "_IPCResponseTimeout: Runtime.evaluate timed out after 60s "
            "waiting for the daemon", "directory": directory}


def test_browser_stalls_are_retried_only_before_any_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_checks, "ARTIFACTS", tmp_path)
    results = [_stall("first"), _stall("second"), {"status": "COMPLETED", "directory": "third"}]
    monkeypatch.setattr(browser_checks, "_run_worker_once", lambda *a: results.pop(0))
    outcome = browser_checks.run_worker("autonomous", "http://fixture")
    assert outcome["status"] == "COMPLETED" and outcome["inference_started"] is False
    assert [a["directory"] for a in outcome["abandoned_browser_stalls"]] == ["first", "second"]


def test_a_stall_after_inference_started_is_never_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_checks, "ARTIFACTS", tmp_path)
    ledger = tmp_path / "budget.json"
    ledger.write_text("{}")
    calls = []

    def once(*_args):
        calls.append(1)
        ledger.write_text(json.dumps({"attempts": len(calls)}))
        return _stall("spent")

    monkeypatch.setattr(browser_checks, "_run_worker_once", once)
    outcome = browser_checks.run_worker("autonomous", "http://fixture")
    assert len(calls) == 1 and outcome["inference_started"] is True
    assert outcome["abandoned_browser_stalls"] == [] and outcome["status"] == "ERROR"


def test_retries_are_capped_and_the_final_stall_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_checks, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(browser_checks, "_run_worker_once", lambda *a: _stall("always"))
    outcome = browser_checks.run_worker("autonomous", "http://fixture", attempts=3)
    assert outcome["status"] == "ERROR" and len(outcome["abandoned_browser_stalls"]) == 2


def test_a_killed_worker_is_reported_unclean_and_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_checks, "ARTIFACTS", tmp_path)
    killed = {"status": "ERROR", "error": "Worker exceeded 300s and was killed", "directory": "killed",
              "resources_closed": False, "profile_removed": False, "exit_code": None}
    calls = []
    monkeypatch.setattr(browser_checks, "_run_worker_once", lambda *a: (calls.append(1), killed)[1])
    outcome = browser_checks.run_worker("autonomous", "http://fixture")
    assert len(calls) == 1 and outcome["resources_closed"] is False


def repair_events(tmp_path, *, confirm=True, settled="Booking confirmed Confirmation TEST-1", gap_ms=2000):
    from journey_evals.repair import CONFIRM_ACTIONS

    directory = tmp_path / "run"
    directory.mkdir(parents=True)
    events = [{"sequence": 1, "host_monotonic_ns": 0, "kind": "observation",
               "data": {"page": {"title": "Sandbox checkout", "text": "Review booking Checkout total: USD 120.00"}}}]
    if confirm:
        events.append({"sequence": 2, "host_monotonic_ns": 10**9, "kind": "action_executed",
                       "data": {"entry": {"action": CONFIRM_ACTIONS[0]}}})
        events.append({"sequence": 3, "host_monotonic_ns": 10**9 + gap_ms * 10**6, "kind": "settled_observation",
                       "data": {"page": {"title": "Sandbox confirmation", "text": settled}}})
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return {"directory": str(directory), "evidence_complete": True}


def test_defect_variants_are_real_source_edits(tmp_path):
    from journey_evals import repair

    pristine = repair.app_digests(repair.APP)
    for defect, changed in (("fare-surcharge", "pricing.py"), ("silent-confirmation", "booking.html")):
        faulty = repair.materialize(tmp_path / defect, defect)
        assert faulty[changed] != pristine[changed]
        assert {name for name in faulty if faulty[name] != pristine[name]} == {changed}
        with pytest.raises(RuntimeError, match="disposable"):
            repair.materialize(tmp_path / defect, defect)


def test_injected_fare_defect_is_served_by_the_application_copy(tmp_path):
    from journey_evals.fixture import BookingFixture
    from journey_evals.repair import materialize

    materialize(tmp_path / "app", "fare-surcharge")
    assert BookingFixture(app=tmp_path / "app").quote() == {"cents": 12000, "disclosure": ""}
    controlled = BookingFixture(app=tmp_path / "app", cents=10000, disclosure="Explained")
    assert controlled.quote() == {"cents": 10000, "disclosure": "Explained"}


def test_effect_window_requires_the_declared_deadline_to_elapse(tmp_path):
    from journey_evals.repair import effect_window

    window = effect_window(repair_events(tmp_path))
    assert window["complete"] and window["action_acknowledged"] and window["deadline_elapsed"]
    assert "Confirmation TEST-1" in window["after_text"]
    early = effect_window(repair_events(tmp_path / "early", gap_ms=100))
    assert not early["complete"] and not early["deadline_elapsed"]
    none = effect_window(repair_events(tmp_path / "none", confirm=False))
    assert not none["complete"] and not none["action_acknowledged"]


def test_repair_acceptance_never_passes_on_a_missing_finding(tmp_path):
    from journey_evals.repair import accepted

    outcome = {
        "usable": True, "independent_verifier": {"passed": True},
        "served_quote": {"cents": 10000, "disclosure": ""},
        "windows": {"fare": {"after_cents": 10000}}, "acknowledgement_observed": False,
        "confirmation_visible": True,
        "findings": {"fare": {"outcome": "clean"}, "effect": {"outcome": "clean"}},
    }
    assert accepted("fare-surcharge", outcome) and accepted("silent-confirmation", outcome)
    for outcome["findings"]["fare"]["outcome"] in ("unknown", "defect"):
        assert not accepted("fare-surcharge", outcome)
    outcome["findings"]["fare"]["outcome"] = "clean"
    assert not accepted("silent-confirmation", dict(outcome, confirmation_visible=False))
    assert not accepted("fare-surcharge", dict(outcome, usable=False))
    assert not accepted("fare-surcharge", dict(outcome, independent_verifier={"passed": False}))
    undisclosed = dict(outcome, served_quote={"cents": 12000, "disclosure": ""},
                       windows={"fare": {"after_cents": 12000}})
    assert not accepted("fare-surcharge", undisclosed)
