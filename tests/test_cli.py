"""The command surface: what a user can ask for, and what the tool refuses before spending money.

A configuration mistake must be caught before a browser exists and before a single model request
is paid for. Credentials must never be handed to the process tree that also runs the browser.
"""

import json
import sys

import pytest

from journey_evals import cli, console
from journey_evals.contracts import exit_code
from journey_evals.isolation import OS_ENV

JOURNEY = {
    "schema_version": 1, "id": "demo", "mode": "verify", "url": "http://127.0.0.1:8111/",
    "allowed_origins": ["http://127.0.0.1:8111"], "task": "Do the declared thing.",
    "viewport": {"width": 1120, "height": 780}, "facts": {},
    "probes": [{"id": "p1", "instruction": "Go.", "required_checks": ["c1"]}],
    "checks": [{
        "id": "c1", "family": "interaction_correctness", "scope": "transition",
        "applies_when": {"predicate": "action_executed", "value": "Search"},
        "requirement": "Searching shows results.", "deadline_ms": 4000, "severity": "high",
        "required_evidence": ["observation"], "expect": {"effect": "Results are visible."},
    }],
    "acceptance": {"url_path_is": "/done", "text_contains": ["Confirmed"]},
    "budgets": {"wall_ms": 300000, "model_requests": 80, "steps": 24, "usd": "0.10"},
}


@pytest.fixture
def journey_file(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps(JOURNEY), encoding="utf-8")
    return path


# -- validate ------------------------------------------------------------------------------------


def test_validate_accepts_a_well_formed_journey(journey_file, capsys):
    assert cli.main(["validate", "--journey", str(journey_file)]) == 0
    out = capsys.readouterr().out
    assert "1 check(s)" in out
    assert "effective_spec_sha256" in out


def test_validate_reports_the_digest_the_run_would_be_judged_against(journey_file, capsys):
    cli.main(["validate", "--journey", str(journey_file)])
    first = capsys.readouterr().out
    cli.main(["validate", "--journey", str(journey_file)])
    assert capsys.readouterr().out == first


def test_validate_names_checks_no_probe_requires(tmp_path, capsys):
    document = json.loads(json.dumps(JOURNEY))
    document["probes"] = []
    path = tmp_path / "j.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    cli.main(["validate", "--journey", str(path)])
    assert "not required by any probe" in capsys.readouterr().out.replace(chr(10), " ")


def test_validate_refuses_a_malformed_journey(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": 1, "id": "x"}', encoding="utf-8")
    assert cli.main(["validate", "--journey", str(path)]) == cli.CONFIGURATION_EXIT
    assert capsys.readouterr().err.strip()


def test_validate_refuses_a_file_that_is_not_json(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    assert cli.main(["validate", "--journey", str(path)]) == cli.CONFIGURATION_EXIT


def test_validate_refuses_a_missing_file(tmp_path, capsys):
    assert cli.main(["validate", "--journey", str(tmp_path / "absent.json")]) \
        == cli.CONFIGURATION_EXIT


# -- run: refusals that happen before any spending ------------------------------------------------


def test_a_run_whose_arguments_contradict_the_journey_is_refused(journey_file, tmp_path, capsys):
    code = cli.main(["run", "--journey", str(journey_file), "--url", "http://127.0.0.1:9999/",
                     "--out", str(tmp_path / "out")])
    assert code == cli.CONFIGURATION_EXIT
    assert "conflicts" in capsys.readouterr().err


def test_a_run_without_a_journey_cannot_certify_success(tmp_path, capsys):
    code = cli.main(["run", "--url", "http://127.0.0.1:8111/", "--task", "Do it",
                     "--mode", "verify", "--out", str(tmp_path / "out")])
    assert code == cli.CONFIGURATION_EXIT


def test_a_run_with_neither_journey_nor_url_is_refused(tmp_path):
    assert cli.main(["run", "--out", str(tmp_path / "out")]) == cli.CONFIGURATION_EXIT


def test_a_run_refuses_to_write_beside_an_earlier_run(journey_file, tmp_path, capsys):
    """Two runs in one directory would be timed against each other's journal."""
    directory = tmp_path / "out"
    directory.mkdir()
    (directory / "events.jsonl").write_text('{"sequence": 1}\n', encoding="utf-8")
    code = cli.main(["run", "--journey", str(journey_file), "--out", str(directory)])
    assert code == cli.CONFIGURATION_EXIT
    assert "already holds a recorded run" in capsys.readouterr().err


def test_an_empty_directory_from_a_refused_run_is_still_usable(journey_file, tmp_path, capsys):
    """An existing but empty journal is not evidence of an earlier run."""
    directory = tmp_path / "out"
    directory.mkdir()
    (directory / "events.jsonl").write_text("", encoding="utf-8")
    code = cli.main(["run", "--journey", str(journey_file), "--url", "http://127.0.0.1:9999/",
                     "--out", str(directory)])
    assert code == cli.CONFIGURATION_EXIT
    assert "already holds a recorded run" not in capsys.readouterr().err


def test_a_conflicting_viewport_is_refused(journey_file, tmp_path, capsys):
    code = cli.main(["run", "--journey", str(journey_file), "--viewport", "390x844",
                     "--out", str(tmp_path / "out")])
    assert code == cli.CONFIGURATION_EXIT


# -- the bundled examples --------------------------------------------------------------------------


def bundled_journeys():
    from journey_evals.paths import example_journeys

    return sorted(example_journeys().glob("*.json"))


def test_every_bundled_journey_is_valid_and_has_a_declared_application():
    from journey_evals.contracts import journey_from_dict

    for path in bundled_journeys():
        document = json.loads(path.read_text(encoding="utf-8"))
        journey_from_dict(document)
        assert path.stem in cli.EXAMPLE_APPS, f"{path.stem} is not mapped to an application"


def test_journeys_for_different_applications_never_share_a_port():
    """Two example applications on one port would serve each other's journeys."""
    from urllib.parse import urlparse

    ports = {}
    for path in bundled_journeys():
        document = json.loads(path.read_text(encoding="utf-8"))
        port = urlparse(document["url"]).port
        application = cli.EXAMPLE_APPS[path.stem]
        assert ports.setdefault(port, application) == application, \
            f"port {port} is declared by more than one example application"


# -- run: the worker boundary ----------------------------------------------------------------------


def test_the_worker_never_inherits_credentials(journey_file, tmp_path, monkeypatch):
    captured = {}

    class Completed:
        stdout = ""
        stderr = "worker refused to start"
        returncode = 3

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setenv("JEV_API_KEY", "secret-value-should-not-travel")
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value-should-not-travel")
    cli.main(["run", "--journey", str(journey_file), "--out", str(tmp_path / "out"), "--quiet"])
    assert "JEV_API_KEY" not in captured["env"]
    assert "TYPESAFE_API_KEY" not in captured["env"]
    assert all(name.upper() in OS_ENV for name in captured["env"])
    assert "secret-value-should-not-travel" not in " ".join(captured["command"])


def test_the_effective_specification_is_persisted_before_the_worker_starts(journey_file, tmp_path,
                                                                          monkeypatch):
    seen = {}

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 3

    def fake_run(command, **kwargs):
        index = command.index("--spec") + 1
        seen["spec"] = json.loads(open(command[index], encoding="utf-8").read())
        return Completed()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli.main(["run", "--journey", str(journey_file), "--out", str(tmp_path / "out"), "--quiet"])
    assert seen["spec"]["resolved"]["id"] == "demo"
    assert seen["spec"]["effective_spec_sha256"]


def test_a_worker_that_produced_no_report_is_an_error_not_a_pass(journey_file, tmp_path,
                                                                 monkeypatch, capsys):
    class Completed:
        stdout = "crash"
        stderr = "traceback"
        returncode = 1

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Completed())
    code = cli.main(["run", "--journey", str(journey_file), "--out", str(tmp_path / "out")])
    assert code == 3
    assert "without a report" in capsys.readouterr().err
    assert (tmp_path / "out" / "worker-output.txt").read_text(encoding="utf-8")


def test_a_worker_that_overran_its_time_is_an_error(journey_file, tmp_path, monkeypatch, capsys):
    def timeout(*_args, **_kwargs):
        raise cli.subprocess.TimeoutExpired(cmd="worker", timeout=1)

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    assert cli.main(["run", "--journey", str(journey_file),
                     "--out", str(tmp_path / "out")]) == 3
    assert (tmp_path / "out" / "worker-output.txt").exists()


def test_the_process_exit_code_follows_the_reported_result(journey_file, tmp_path, monkeypatch):
    directory = tmp_path / "out"

    class Completed:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run(command, **kwargs):
        index = command.index("--out") + 1
        report = {"schema_version": 1, "run_id": "r", "tool_version": "0", "result": "FAIL",
                  "result_basis": [], "goal_status": "violated", "goal_evidence": [],
                  "journey": {"resolved": {"id": "demo"}}, "effective_spec_sha256": "x",
                  "coverage": {"checks": {}, "missing_required_checks": [],
                               "failed_required_checks": [], "unvisited_probes": [],
                               "complete": True, "steps": 1, "observations": 1},
                  "findings": [], "evaluations": [], "errors": [], "usage": {}, "timings": {},
                  "environment": {}, "execution_status": "completed"}
        (Path(command[index]) / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return Completed()

    from pathlib import Path

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main(["run", "--journey", str(journey_file), "--out", str(directory),
                     "--quiet"]) == exit_code("FAIL")


# -- show -----------------------------------------------------------------------------------------


def test_show_renders_a_persisted_report(tmp_path, capsys):
    report = {"schema_version": 1, "run_id": "r", "tool_version": "0", "result": "PASS",
              "result_basis": [], "goal_status": "verified", "goal_evidence": [],
              "journey": {"resolved": {"id": "demo", "task": "Do it"}},
              "effective_spec_sha256": "x",
              "coverage": {"checks": {"c1": "passed"}, "missing_required_checks": [],
                           "failed_required_checks": [], "unvisited_probes": [], "complete": True,
                           "steps": 1, "observations": 1},
              "findings": [], "evaluations": [], "errors": [], "usage": {}, "timings": {},
              "environment": {}, "execution_status": "completed"}
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    assert cli.main(["show", str(tmp_path)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_show_refuses_a_directory_without_a_report(tmp_path, capsys):
    assert cli.main(["show", str(tmp_path)]) == cli.CONFIGURATION_EXIT


# -- serving the fixture -----------------------------------------------------------------------------


def test_serving_onto_an_occupied_port_is_refused_rather_than_silently_shared(monkeypatch, capsys):
    """Windows lets a second server bind a busy port, and the run then reads the other process's
    records. That corrupts the acceptance contract, so refuse instead of serving."""
    from examples import flight_app

    built = []
    monkeypatch.setattr(console, "port_is_taken", lambda port, *a, **k: True)
    monkeypatch.setattr(flight_app, "build", lambda **kw: built.append(kw))
    code = cli.main(["serve", "--app", "flight", "--port", "8111"])
    assert code == cli.CONFIGURATION_EXIT
    assert built == []
    assert "already listening" in capsys.readouterr().err


def test_a_free_port_still_serves(monkeypatch):
    from examples import flight_app

    monkeypatch.setattr(console, "port_is_taken", lambda port, *a, **k: False)
    served = []

    class _Server:
        server_address = ("127.0.0.1", 8111)

        def serve_forever(self):
            served.append(True)
            raise KeyboardInterrupt

        def shutdown(self):
            served.append("shutdown")

    monkeypatch.setattr(flight_app, "build", lambda **kw: _Server())
    assert cli.main(["serve", "--app", "flight", "--port", "8111"]) == 0
    assert served == [True, "shutdown"]


# -- the feasibility surface is preserved ------------------------------------------------------------


def test_every_feasibility_checkpoint_is_still_reachable():
    assert {"preflight", "verify-browser", "verify-clean", "screen-detection",
            "screen-reliability", "live-runs", "measure-overhead",
            "second-app"} <= set(cli.CHECKPOINTS)


def test_a_feasibility_subcommand_is_delegated_rather_than_restated(monkeypatch):
    seen = {}

    def fake_main():

        seen["argv"] = list(sys.argv)
        return 7

    monkeypatch.setattr("journey_evals.feasibility.main", fake_main)
    assert cli.main(["preflight", "--jev-budget-usd", "0.10"]) == 7
    assert seen["argv"] == ["journey-evals", "preflight", "--jev-budget-usd", "0.10"]


def test_an_unknown_command_is_refused(capsys):
    with pytest.raises(SystemExit):
        cli.main(["not-a-command"])


def test_a_crashed_run_still_writes_an_error_report(journey_file, tmp_path, monkeypatch):
    class Completed:
        stdout = ""
        stderr = "chrome fell over"
        returncode = 3

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Completed())
    out = tmp_path / "out"
    assert cli.main(["run", "--journey", str(journey_file), "--out", str(out), "--quiet"]) == 3
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["result"] == "ERROR"
    assert payload["execution_status"] == "error"
    # Not "violated": a tool that fell over has not shown the application is broken either.
    assert payload["goal_status"] == "unavailable"
    assert set(payload["coverage"]["checks"].values()) == {"pending"}
    assert payload["findings"] == []


def test_a_crashed_run_reports_no_passing_checks_to_ci(journey_file, tmp_path, monkeypatch):
    class Completed:
        stdout = ""
        stderr = ""
        returncode = 3

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: Completed())
    out = tmp_path / "out"
    cli.main(["run", "--journey", str(journey_file), "--out", str(out), "--quiet"])
    junit = (out / "junit.xml").read_text(encoding="utf-8")
    assert "<skipped" in junit
    assert 'failures="0"' in junit and 'errors="1"' in junit
    assert "<failure" not in junit
    feedback = json.loads((out / "agent-feedback.json").read_text(encoding="utf-8"))
    assert feedback["confirmed_findings"] == [] and feedback["advisory_findings"] == []
    assert feedback["unresolved_coverage"]


# -- every seeded fault is either swept or explicitly recorded as uncovered ----------------------

# A fixture fault nobody measures is worse than no fault at all: it looks like coverage. These are
# the faults no bundled journey currently detects, each with the reason. Anything not listed here
# must appear in a scripts/sweep.py suite.
UNSWEPT = {
    "flight": {
        "search_does_nothing": "covered by search-shows-results, but not yet in a sweep suite",
        "details_lost_on_back": "exercised by flight-booking-back.json, which has its own suite",
        "fast_search": "a control for missing_loading_feedback; the swept control is terse_loading_feedback",
    },
    "subscription": {
        name: "the subscription suite sweeps only the clean journey"
        for name in ("availability_does_nothing", "clipped_subscribe_button",
                     "disclosed_price_increase", "fast_availability", "silent_price_increase",
                     "subscription_never_recorded")
    },
    "admin": {
        "clipped_delete_button": "no Meridian journey declares a layout_integrity check yet",
        "current_complete_data": "a control equal to the clean baseline already swept as `none`",
    },
}


def test_every_seeded_fault_is_swept_or_recorded_as_uncovered():
    """`janky_render` was served for a whole run and reported PASS because nothing measured it.

    A fault that no journey can detect is a silent hole in the evidence. It is allowed to exist,
    but only where a reader can see it.
    """
    from examples import admin_app, flight_app, subscription_app
    from scripts.sweep import FIRST_APPS, MERIDIAN

    swept = {row[3] for row in list(FIRST_APPS) + list(MERIDIAN)}
    modules = {"flight": flight_app, "subscription": subscription_app, "admin": admin_app}
    for app, module in modules.items():
        uncovered = set(module.SCENARIOS) - swept - {"none"}
        assert uncovered == set(UNSWEPT[app]), (
            f"{app}: {sorted(uncovered ^ set(UNSWEPT[app]))} changed coverage without being recorded"
        )
