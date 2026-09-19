"""The live console is a viewer. These tests hold it to that: it must project the journal without
inventing anything, it must not ship page text or model requests wholesale, and it must never
report a result the report file does not contain.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from journey_evals import console


def event(kind, data, sequence=1):
    return {"schema_version": 1, "sequence": sequence, "host_monotonic_ns": 1, "kind": kind,
            "data": data}


def test_unknown_kinds_are_not_rendered():
    assert console.project(event("something_new", {"secret": "value"})) is None


def test_uninteresting_browser_events_are_dropped():
    assert console.project(event("browser_event", {"kind": "layout_shift", "data": {}})) is None
    shown = console.project(event("browser_event", {"kind": "navigation", "data": {"url": "/x"}}))
    assert shown["event"] == "navigation"


def test_feedback_frames_render_their_status_text_and_silence_is_not_a_row():
    """An empty status region is the page saying nothing, not an event worth a timeline row."""
    silent = console.project(event("browser_event", {
        "kind": "feedback",
        "data": {"regions": [{"text": "   ", "visible": True}, {"text": "hidden", "visible": False}]}}))
    assert silent is None

    speaking = console.project(event("browser_event", {
        "kind": "feedback",
        "data": {"regions": [{"text": "Searching", "visible": True},
                             {"text": "later", "visible": False}]}}))
    assert speaking["text"] == "Searching"


def test_observation_projection_clips_page_text_and_keeps_evidence():
    item = console.project(event("observation", {
        "phase": "initial",
        "page": {"url": "http://127.0.0.1:1/", "title": "T", "text": "x" * 5000,
                 "screenshot_file": "evidence/000001.jpg",
                 "actions": [{"label": "Go", "kind": "click"}] * 40},
    }))
    assert len(item["text"]) <= console.TEXT_CAP + 1
    assert item["screenshot"] == "evidence/000001.jpg"
    assert item["action_count"] == 40
    assert len(item["actions"]) == 12


def test_decision_projection_drops_the_model_request():
    item = console.project(event("decision_consumed", {"decision": {
        "operation": "CLICK", "target": "1", "confidence": 0.98, "model": "m",
        "latency_ms": 700, "usage": {"input_tokens": 10, "output_tokens": 2},
        "request": {"state": {"page": {"text": "the entire page"}}},
        "raw_answers": {"operation": {"choice": "CLICK"}},
    }}))
    assert item["operation"] == "CLICK"
    assert "request" not in item and "raw_answers" not in item
    assert json.dumps(item).find("the entire page") == -1


def test_evaluation_projection_carries_the_settled_state():
    item = console.project(event("evaluation", {
        "check_id": "c1", "family": "interaction_correctness", "scope": "transition",
        "verdict": "EFFECT_OBSERVED", "state": "passed", "step": 2, "source": "code",
        "requirement": "r", "reason": "", "observation_ids": ["obs-000001"],
    }))
    assert (item["check_id"], item["state"], item["source"]) == ("c1", "passed", "code")
    assert item["observation_ids"] == ["obs-000001"]


def test_watermark_projection_counts_clipped_controls():
    item = console.project(event("watermark", {
        "url": "u", "title": "t",
        "feedback": {"regions": [{"text": "Searching", "visible": True},
                                 {"text": "", "visible": False}]},
        "evaluation": {"controls": [{"visible": True, "viewportClipped": True},
                                    {"visible": True, "offscreen": False},
                                    {"visible": True}]},
    }))
    assert item["control_count"] == 3
    assert item["visible_count"] == 3
    assert item["clipped"] == 1
    assert item["feedback"] == ["Searching"]


def test_a_hidden_section_is_not_reported_as_clipped():
    """A persistent single-page app keeps its other sections in the DOM and offscreen. Counting
    those as clipped would report a layout problem on every page of every run."""
    item = console.project(event("watermark", {
        "evaluation": {"controls": [
            {"label": "Confirm", "visible": False, "offscreen": True},
            {"label": "Back", "visible": False, "offscreen": True},
            {"label": "Search", "visible": True, "offscreen": False},
        ]},
    }))
    assert (item["control_count"], item["visible_count"], item["clipped"]) == (3, 1, 0)


def test_tail_holds_a_half_written_line_until_it_completes(tmp_path):
    path = tmp_path / "events.jsonl"
    complete = json.dumps(event("terminal_decision", {"status": "done"}, sequence=1))
    partial = json.dumps(event("observation", {"page": {"url": "u"}}, sequence=2))
    # The journal only ever appends, so a torn read is the first half of a line that is about to
    # be finished, not a line that will be replaced.
    path.write_text(complete + "\n" + partial[:12], encoding="utf-8")
    tail = console.Tail(path)
    assert [i["sequence"] for i in tail.poll()] == [1]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(partial[12:] + "\n")
    assert [i["sequence"] for i in tail.poll()] == [1, 2]


def test_tail_skips_unparseable_lines_without_stopping(tmp_path):
    path = tmp_path / "events.jsonl"
    good = json.dumps(event("terminal_decision", {"status": "done"}, sequence=9))
    path.write_text("not json\n" + good + "\n", encoding="utf-8")
    assert [i["sequence"] for i in console.Tail(path).poll()] == [9]


def test_tail_stops_growing_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "EVENT_CAP", 2)
    path = tmp_path / "events.jsonl"
    path.write_text("".join(
        json.dumps(event("terminal_decision", {"status": "done"}, sequence=i)) + "\n"
        for i in range(5)), encoding="utf-8")
    tail = console.Tail(path)
    assert len(tail.poll()) == 2
    assert tail.dropped == 3


def test_session_reports_no_result_before_the_report_exists(tmp_path):
    session = console.Session(tmp_path, ["python", "-c", "pass"])
    assert session.status == "idle"
    assert session.final() is None
    assert session.state()["final"] is None


def test_session_will_not_start_twice(tmp_path):
    session = console.Session(tmp_path, ["python", "-c", "pass"])
    session.start()
    session.process.wait(timeout=30)
    with pytest.raises(ValueError):
        session.start()


def test_session_final_is_read_from_the_report_not_invented(tmp_path):
    (tmp_path / "report.json").write_text(json.dumps({
        "result": "FAIL", "goal_status": "violated", "execution_status": "completed",
        "result_basis": "because", "coverage": {"checks": {"c1": "failed"},
                                                "failed_required_checks": ["c1"]},
        "findings": [{"severity": "high", "title": "t", "expected": {"requirement": "r"},
                      "observed": {}, "evidence_ids": ["obs-1"]}],
    }), encoding="utf-8")
    session = console.Session(tmp_path, ["python", "-c", "pass"])
    session.start()
    session.process.wait(timeout=30)
    final = session.state()["final"]
    assert final["result"] == "FAIL"
    assert final["goal_status"] == "violated"
    assert final["checks"] == {"c1": "failed"}
    assert final["findings"][0]["requirement"] == "r"


def test_session_final_stays_none_when_the_worker_wrote_nothing(tmp_path):
    session = console.Session(tmp_path, ["python", "-c", "raise SystemExit(3)"])
    session.start()
    session.process.wait(timeout=30)
    state = session.state()
    assert state["status"] == "finished"
    assert state["exit_code"] == 3
    assert state["final"] is None


def _cell(tmp_path, label, size, command):
    directory = tmp_path / label
    directory.mkdir(parents=True, exist_ok=True)
    return {"label": label, "viewport": size, "viewport_class": "desktop",
            "session": console.Session(directory, command)}


def _wait_for(predicate, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_matrix_runs_one_cell_at_a_time_and_advances_on_poll(tmp_path):
    quick = ["python", "-c", "pass"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), quick),
                                    _cell(tmp_path, "mobile", (390, 844), quick)])
    assert matrix.status == "idle"
    matrix.start()
    # Only the first cell is ever launched by start(); the second waits its turn.
    assert matrix.cells[1]["session"].status == "idle"
    matrix.cells[0]["session"].process.wait(timeout=30)

    state = matrix.state()
    assert state["cell"] == 1
    assert matrix.cells[1]["session"].status in {"running", "finished"}
    # The finished cell keeps its own exit code even though nobody polled it while it was in front.
    assert state["cells"][0]["exit_code"] == 0
    assert state["cells"][0]["status"] == "finished"

    matrix.cells[1]["session"].process.wait(timeout=30)
    assert matrix.state()["status"] == "finished"


def test_matrix_stop_halts_the_queue_rather_than_the_cell_on_screen(tmp_path):
    slow = ["python", "-c", "import time; time.sleep(30)"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), slow),
                                    _cell(tmp_path, "mobile", (390, 844), slow)])
    matrix.start()
    assert _wait_for(lambda: matrix.cells[0]["session"].status == "running")
    matrix.stop()
    assert matrix.status == "finished"
    # Polling after a stop must not arm the next viewport.
    matrix.state()
    assert matrix.cells[1]["session"].process is None


def test_matrix_reports_each_cell_from_its_own_report(tmp_path):
    quick = ["python", "-c", "pass"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), quick),
                                    _cell(tmp_path, "mobile", (390, 844), quick)])
    (tmp_path / "standard" / "report.json").write_text(json.dumps({
        "result": "PASS", "goal_status": "verified", "execution_status": "completed",
        "coverage": {"checks": {"c1": "passed"}}, "findings": [],
    }), encoding="utf-8")
    matrix.start()
    matrix.cells[0]["session"].process.wait(timeout=30)
    cells = matrix.state()["cells"]
    assert [cell["label"] for cell in cells] == ["standard", "mobile"]
    assert cells[0]["final"]["result"] == "PASS"
    assert cells[0]["viewport"] == [1280, 800]
    assert cells[1]["final"] is None


def test_matrix_evidence_is_scoped_to_the_cell_that_recorded_it(tmp_path):
    quick = ["python", "-c", "pass"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), quick),
                                    _cell(tmp_path, "mobile", (390, 844), quick)])
    assert matrix.cell_directory(1) == tmp_path / "mobile"
    assert matrix.cell_directory(9) is None
    assert matrix.directory == tmp_path / "standard"


def test_a_reopened_cell_is_read_back_from_its_own_journal(tmp_path):
    quick = ["python", "-c", "pass"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), quick),
                                    _cell(tmp_path, "mobile", (390, 844), quick)])
    (tmp_path / "standard" / "events.jsonl").write_text(
        json.dumps(event("evaluation", {"check_id": "c1", "state": "passed",
                                        "verdict": "EFFECT_OBSERVED", "source": "model"})) + "\n",
        encoding="utf-8")
    (tmp_path / "standard" / "report.json").write_text(json.dumps({
        "result": "PASS", "goal_status": "verified", "execution_status": "completed",
        "coverage": {"checks": {"c1": "passed"}}, "findings": [],
    }), encoding="utf-8")
    matrix.start()
    matrix.cells[0]["session"].process.wait(timeout=30)
    matrix.state()  # the matrix has moved on; the first cell is no longer the one being polled

    journal = matrix.cell_journal(0)
    assert journal["label"] == "standard"
    assert [item["check_id"] for item in journal["events"]] == ["c1"]
    assert journal["final"]["result"] == "PASS"
    assert journal["exit_code"] == 0
    assert matrix.cell_journal(7) is None


def test_worker_command_is_the_ordinary_cli(tmp_path):
    command = console.worker_command(journey="j.json", out=tmp_path, headed=True,
                                     blocking_checks=["a"])
    assert command[1:4] == ["-m", "journey_evals.cli", "run"]
    assert "--headed" in command and "--quiet" in command
    assert command[command.index("--blocking-check") + 1] == "a"
    assert "--journey" in command


def test_worker_command_can_run_headless(tmp_path):
    assert "--headed" not in console.worker_command(url="u", task="t", out=tmp_path, headed=False)


def test_port_is_taken_sees_a_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert console.port_is_taken(port) is True
    assert console.port_is_taken(port) is False


class _Served:
    """The console's HTTP surface on a real loopback port."""

    def __init__(self, tmp_path, session=None):
        from http.server import ThreadingHTTPServer

        self.session = session or console.Session(tmp_path, ["python", "-c", "pass"])
        self.token = "test-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = console.build_handler(
            self.session, self.token, self.port, {"id": "j", "task": "t"})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def served(tmp_path):
    instance = _Served(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def status_of(url, **kwargs):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, **kwargs), timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_state_is_served_with_the_journey(served):
    code, body = status_of(served.base + "/api/state")
    assert code == 200
    payload = json.loads(body)
    assert payload["journey"]["id"] == "j"
    assert payload["status"] == "idle"


def test_starting_a_run_requires_the_token(served):
    code, _ = status_of(served.base + "/api/start", data=b"{}", method="POST")
    assert code == 403
    assert served.session.process is None


def test_a_foreign_host_header_is_refused(served):
    code, _ = status_of(served.base + "/api/state", headers={"Host": "example.com"})
    assert code == 403


def test_a_finished_cell_can_be_read_back_over_http(tmp_path):
    quick = ["python", "-c", "pass"]
    matrix = console.MatrixSession([_cell(tmp_path, "standard", (1280, 800), quick),
                                    _cell(tmp_path, "mobile", (390, 844), quick)])
    (tmp_path / "standard" / "events.jsonl").write_text(
        json.dumps(event("terminal_decision", {"status": "done"})) + "\n", encoding="utf-8")
    (tmp_path / "standard" / "evidence").mkdir()
    (tmp_path / "standard" / "evidence" / "shot.jpg").write_bytes(b"jpeg")
    instance = _Served(tmp_path, session=matrix)
    try:
        code, body = status_of(instance.base + "/cell/0/journal")
        assert code == 200
        assert json.loads(body)["label"] == "standard"
        # The screenshots of a collapsed cell stay reachable, and nothing else does.
        assert status_of(instance.base + "/cell/0/evidence/shot.jpg")[0] == 200
        assert status_of(instance.base + "/cell/0/evidence/../report.json")[0] == 404
        assert status_of(instance.base + "/cell/5/journal")[0] == 404
        assert status_of(instance.base + "/cell/0/journal/extra")[0] == 404
    finally:
        instance.close()
        matrix.stop()


def test_evidence_cannot_escape_the_run_directory(served, tmp_path):
    (tmp_path.parent / "secret.jpg").write_bytes(b"no")
    for name in ("../secret.jpg", "..%2Fsecret.jpg", "nested/shot.jpg", "report.json"):
        code, _ = status_of(served.base + "/evidence/" + name)
        assert code == 404, name


def test_evidence_is_served_from_the_run_directory(served, tmp_path):
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / "000001.jpg").write_bytes(b"jpegbytes")
    code, body = status_of(served.base + "/evidence/000001.jpg")
    assert (code, body) == (200, b"jpegbytes")


def test_the_page_carries_the_token_and_no_placeholder_remains(served):
    code, body = status_of(served.base + "/console.js")
    assert code == 200
    assert b"__TOKEN__" not in body
    assert served.token.encode() in body


def test_a_started_run_is_visible_in_state(served):
    request = urllib.request.Request(served.base + "/api/start", data=b"{}", method="POST",
                                     headers={"X-Console-Token": served.token})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
    deadline = time.time() + 30
    while time.time() < deadline:
        payload = json.loads(status_of(served.base + "/api/state")[1])
        if payload["status"] == "finished":
            assert payload["exit_code"] == 0
            return
        time.sleep(0.2)
    pytest.fail("the run never reported that it finished")

def test_serving_the_console_starts_the_run_without_being_asked(tmp_path):
    """Watching means watching something happen: the page is a viewer, never a launcher."""
    started = []

    class Recording:
        status = "idle"

        def start(self):
            started.append(True)

        def stop(self):
            pass

        def state(self, after=0):
            return {"status": "idle", "events": [], "after": after}

    session = Recording()
    thread = threading.Thread(
        target=lambda: console.serve(session, port=0, journey={"id": "j", "task": "t"},
                                     open_browser=False),
        daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not started:
        time.sleep(0.05)
    assert started, "serve() must start the run as soon as the console is listening"


def test_the_imported_agent_does_not_depend_on_the_product():
    """jev_ultrafast is imported code. journey-evals is built around it, not the other way round."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "jev_ultrafast"
    offenders = [path.name for path in sorted(root.rglob("*.py"))
                 if re.search(r"\bjourney_evals\b", path.read_text(encoding="utf-8"))]
    assert offenders == [], f"the imported agent must not import the product: {offenders}"
