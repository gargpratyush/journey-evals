"""The browser viewer for an agent evaluation: phases, redaction, and local-only access."""

import json
import threading

from journey_evals import agent_console, cli
from journey_evals.agent_evals import agent_spec_from_dict


def document(**overrides):
    value = {
        "schema_version": 1,
        "id": "support-agent",
        "task": "Determine whether the order can be refunded.",
        "runtime": {"framework": "langgraph", "entrypoint": "examples.agent_graph:graph"},
        "input": {"messages": [{"role": "user", "content": "Can this be refunded?"}]},
        "acceptance": {"output_contains": ["eligible for a refund"]},
        "judges": [],
        "budgets": {"wall_ms": 60000, "steps": 20, "model_requests": 4, "usd": "0.10"},
    }
    value.update(overrides)
    return value


def finished_session(tmp_path, **overrides):
    spec = agent_spec_from_dict(document(**overrides))
    session = agent_console.AgentSession(spec, tmp_path)
    session.start()
    session.thread.join(timeout=30)
    return session


def test_the_viewer_shows_the_conversation_before_it_shows_a_verdict(tmp_path):
    session = finished_session(tmp_path)
    state = session.state(0)
    assert state["status"] == "finished"
    assert state["phase"] == "done"
    kinds = [item["kind"] for item in state["events"]]
    assert "agent_message" in kinds
    assert state["summary"]["result"] == "PASS"


def test_the_phase_is_read_from_recorded_events_not_guessed(tmp_path):
    # The page must not claim judging has begun while the conversation is still running.
    spec = agent_spec_from_dict(document())
    session = agent_console.AgentSession(spec, tmp_path)
    assert session.phase() == "starting"
    session._observe({"kind": "agent_run_started", "data": {}})
    assert session.phase() == "conversation"
    session._observe({"kind": "agent_completed", "data": {"steps": 4}})
    assert session.phase() == "judging"
    session.status = "finished"
    assert session.phase() == "done"


def test_events_are_handed_out_once_so_the_page_can_resume_where_it_stopped(tmp_path):
    session = finished_session(tmp_path)
    first = session.state(0)
    assert first["events"]
    last = first["events"][-1]["sequence"]
    assert session.state(last)["events"] == []


def test_a_failing_run_is_shown_rather_than_crashing_the_viewer(tmp_path):
    spec = agent_spec_from_dict(document())

    def explode(*_args, **_kwargs):
        raise RuntimeError("judge unreachable")

    session = agent_console.AgentSession(spec, tmp_path, runner=explode)
    session.start()
    session.thread.join(timeout=10)
    state = session.state(0)
    assert state["status"] == "failed"
    assert "judge unreachable" in state["error"]
    assert state["phase"] == "done"


def test_the_viewer_reads_the_journal_after_redaction(tmp_path):
    # The observer is the journal's own hook, so a value masked in the artifacts is masked here.
    from journey_evals.agent_evals import run_agent_eval

    spec = agent_spec_from_dict(document(
        input={"messages": [{"role": "user", "content": "my key is sk-live-secret-value"}]},
    ))

    def run_with_secret(spec_, directory, **kwargs):
        return run_agent_eval(spec_, directory, secrets=["sk-live-secret-value"], **kwargs)

    session = agent_console.AgentSession(spec, tmp_path, runner=run_with_secret)
    session.start()
    session.thread.join(timeout=30)
    text = json.dumps(session.state(0))
    assert "sk-live-secret-value" not in text
    assert session.state(0)["events"]


def serve_in_background(session, port=0):
    token = "test-token"
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", port), agent_console.build_handler(session, token, port))
    actual = server.server_address[1]
    server.server_close()
    server = ThreadingHTTPServer(("127.0.0.1", actual),
                                 agent_console.build_handler(session, token, actual))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual, token


def test_the_console_answers_only_requests_addressed_to_the_loopback_viewer(tmp_path):
    import http.client

    session = finished_session(tmp_path)
    server, port, _token = serve_in_background(session)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/state", headers={"Host": "evil.example"})
        assert connection.getresponse().status == 403
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["phase"] == "done"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_page_and_its_assets_are_served_with_the_run_token(tmp_path):
    import http.client

    session = finished_session(tmp_path)
    server, port, token = serve_in_background(session)
    try:
        for path, expected in [("/", "text/html"), ("/agent-console.js", "text/javascript"),
                               ("/agent-console.css", "text/css")]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
            response = connection.getresponse()
            assert response.status == 200
            assert expected in response.getheader("Content-Type")
            body = response.read().decode("utf-8")
            assert "__TOKEN__" not in body
            if path == "/":
                assert token in body
            connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_an_unknown_path_is_not_served(tmp_path):
    import http.client

    session = finished_session(tmp_path)
    server, port, _token = serve_in_background(session)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/../pyproject.toml", headers={"Host": f"127.0.0.1:{port}"})
        assert connection.getresponse().status == 404
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_console_command_is_reachable_from_the_cli():
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "console", "--eval", "spec.json", "--no-open"])
    assert args.agent_command == "console"
    assert args.port == 0
    assert args.no_open is True
