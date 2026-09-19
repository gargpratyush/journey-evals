"""A loopback live console for one ``journey-evals`` run.

This is a viewer, not a second runner. It starts the ordinary CLI as a subprocess and renders the
append-only journal that run is already writing, so what you watch is the same evidence the report
is built from. Nothing here can change a verdict: the console never evaluates anything, and the
final panel is read from ``report.json`` after the worker exits.

Loopback only, one token for state-changing requests, and no path outside the run directory.
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
EVENT_CAP = 4000
TEXT_CAP = 600

# Only these journal kinds reach the browser, and only the fields named in ``project``. The journal
# holds full page text and complete model requests; a viewer has no reason to ship either.
TIMELINE_KINDS = {
    "observation", "settled_observation", "watermark", "decision_consumed", "action_attempt",
    "action_executed", "action_rejected_stale", "evaluation", "terminal_decision",
    "browser_event", "network_failure", "http_failure", "console_exception", "collector_failed",
}
BROWSER_EVENT_KINDS = {"begin", "navigation", "feedback", "committed"}


def _clip(value, limit=TEXT_CAP):
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "\u2026"


def project(event):
    """Turn one journal event into the smallest thing the console can render.

    Returns ``None`` for events the console does not show, so the caller can keep the journal
    sequence numbers authoritative without shipping every frame of telemetry.
    """
    kind = event.get("kind")
    if kind not in TIMELINE_KINDS:
        return None
    data = event.get("data") or {}
    item = {"sequence": event.get("sequence"), "kind": kind,
            "at_ns": event.get("host_monotonic_ns")}

    if kind in {"observation", "settled_observation"}:
        page = data.get("page") or {}
        actions = page.get("actions") or []
        item.update(phase=data.get("phase", "settled"), url=page.get("url", ""),
                    title=page.get("title", ""), text=_clip(page.get("text", "")),
                    screenshot=page.get("screenshot_file"), action_count=len(actions),
                    actions=[{"label": _clip(a.get("label", ""), 80), "kind": a.get("kind")}
                             for a in actions[:12]])
        return item

    if kind == "watermark":
        projection = data.get("evaluation") or {}
        controls = projection.get("controls") or []
        regions = ((data.get("feedback") or {}).get("regions")) or []
        visible = [c for c in controls if c.get("visible")]
        item.update(url=data.get("url", ""), title=data.get("title", ""),
                    control_count=len(controls), visible_count=len(visible),
                    # Only a control the traveller can actually see can be clipped away from them.
                    # A hidden section of a persistent single-page app is offscreen by design, and
                    # counting it here would report a layout problem on every page.
                    clipped=sum(1 for c in visible
                                if c.get("viewportClipped") or c.get("offscreen")
                                or c.get("ancestorClipped")),
                    feedback=[_clip(r.get("text", ""), 160) for r in regions
                              if r.get("visible") and (r.get("text") or "").strip()])
        return item

    if kind == "decision_consumed":
        decision = data.get("decision") or {}
        usage = decision.get("usage") or {}
        item.update(operation=decision.get("operation"), target=decision.get("target"),
                    confidence=decision.get("confidence"),
                    operation_probabilities=decision.get("operation_probabilities") or {},
                    model=decision.get("model"), latency_ms=decision.get("latency_ms"),
                    input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
        return item

    if kind == "action_attempt":
        action = data.get("action") or {}
        item.update(label=_clip(action.get("label", ""), 120), action_kind=action.get("kind"),
                    scripted=bool(data.get("scripted")))
        return item

    if kind == "action_executed":
        entry = data.get("entry") or {}
        item.update(step=entry.get("step"), label=_clip(entry.get("action", ""), 120),
                    action_kind=entry.get("kind"), url=entry.get("url", ""),
                    text=_clip(entry.get("text") or "", 120),
                    latency_ms=entry.get("latency_ms"))
        return item

    if kind == "evaluation":
        item.update(check_id=data.get("check_id"), family=data.get("family"),
                    scope=data.get("scope"), verdict=data.get("verdict"),
                    state=data.get("state"), step=data.get("step"),
                    source=data.get("source"), severity=data.get("severity"),
                    requirement=_clip(data.get("requirement") or "", 240),
                    reason=_clip(data.get("reason") or "", 240),
                    observation_ids=data.get("observation_ids") or [])
        return item

    if kind == "terminal_decision":
        item.update(status=data.get("status"))
        return item

    if kind == "browser_event":
        inner = data.get("kind")
        if inner not in BROWSER_EVENT_KINDS:
            return None
        payload = data.get("data") or {}
        if inner == "feedback":
            # A feedback frame carries status regions, not a text field. When every region is
            # empty or hidden the page is saying nothing, and an empty row would only be noise:
            # the absence of feedback is already visible on the watermark.
            texts = [(r.get("text") or "").strip() for r in (payload.get("regions") or [])
                     if r.get("visible")]
            texts = [t for t in texts if t]
            if not texts:
                return None
            item.update(event=inner, url="", text=_clip(" | ".join(texts), 160))
            return item
        item.update(event=inner, url=payload.get("url", ""),
                    text=_clip(payload.get("text") or "", 160))
        return item

    if kind == "action_rejected_stale":
        item.update(reason=_clip(data.get("reason", ""), 160))
        return item

    # Everything left is a runtime problem the page itself reported.
    item.update(detail=_clip(json.dumps(data, sort_keys=True), 300))
    return item


class Tail:
    """Incremental reader for the journal. A half-written final line is held, never parsed."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.partial = ""
        self.items = []
        self.dropped = 0

    def poll(self):
        if not self.path.exists():
            return self.items
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        if not chunk:
            return self.items
        buffer = self.partial + chunk
        lines = buffer.split("\n")
        self.partial = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = project(event)
            if item is None:
                continue
            if len(self.items) >= EVENT_CAP:
                self.dropped += 1
                continue
            self.items.append(item)
        return self.items


class Session:
    """Owns the worker process and the view of its journal."""

    def __init__(self, directory, command, *, environment=None, cwd=None):
        self.directory = Path(directory)
        self.command = list(command)
        self.environment = environment
        self.cwd = cwd
        self.tail = Tail(self.directory / "events.jsonl")
        self.process = None
        self.started_at = None
        self.finished_at = None
        self.exit_code = None
        self.lock = threading.RLock()

    @property
    def status(self):
        if self.process is None:
            return "idle"
        if self.process.poll() is None:
            return "running"
        return "finished"

    def start(self):
        with self.lock:
            if self.process is not None:
                raise ValueError("This run has already started. Restart the console for another.")
            self.directory.mkdir(parents=True, exist_ok=True)
            self.started_at = time.time()
            self.process = subprocess.Popen(
                self.command, cwd=self.cwd, env=self.environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.process.kill()

    def final(self):
        report = self.directory / "report.json"
        if self.status != "finished" or not report.exists():
            return None
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except ValueError:
            return None
        coverage = payload.get("coverage") or {}
        return {
            "result": payload.get("result"),
            "result_basis": payload.get("result_basis"),
            "goal_status": payload.get("goal_status"),
            "goal_evidence": payload.get("goal_evidence"),
            "execution_status": payload.get("execution_status"),
            "checks": coverage.get("checks") or {},
            "probes": coverage.get("probes") or {},
            "failed_required_checks": coverage.get("failed_required_checks") or [],
            "missing_required_checks": coverage.get("missing_required_checks") or [],
            "findings": [{
                "severity": f.get("severity"), "title": f.get("title"),
                "requirement": (f.get("expected") or {}).get("requirement", ""),
                "observed": f.get("observed"), "provenance": f.get("provenance"),
                "review_state": f.get("review_state"),
                "evidence_ids": f.get("evidence_ids") or [],
            } for f in payload.get("findings") or []],
            "usage": payload.get("usage") or {},
            "timings": payload.get("timings") or {},
            "errors": payload.get("errors") or [],
        }

    def settle(self):
        """Record the exit code once the worker is gone, whether or not anyone is watching it."""
        with self.lock:
            if self.process is not None and self.process.poll() is not None \
                    and self.exit_code is None:
                self.exit_code = self.process.returncode
                self.finished_at = time.time()

    def state(self, after=0):
        with self.lock:
            items = self.tail.poll()
            self.settle()
            return {
                "status": self.status,
                "exit_code": self.exit_code,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total": len(items),
                "dropped": self.tail.dropped,
                "events": [i for i in items if (i.get("sequence") or 0) > after],
                "final": self.final(),
            }


class MatrixSession:
    """Runs one journey at several viewports, one after another, for the live console.

    Each viewport is an ordinary, independent run with its own worker, directory and report. This
    only decides which one is in front of you: when a cell finishes, the next one starts and the
    finished one becomes a summary. Nothing is merged, and a cell that failed does not stop the
    ones after it — a journey that passes on a desktop and fails on a phone is the result, not a
    problem to be averaged away.
    """

    def __init__(self, cells):
        self.cells = list(cells)
        self.lock = threading.RLock()
        self.started = False
        self.halted = False

    @property
    def index(self):
        """The cell in front of you: the first that has not finished, else the last."""
        for position, cell in enumerate(self.cells):
            if cell["session"].status != "finished":
                return position
        return len(self.cells) - 1

    @property
    def active(self):
        return self.cells[self.index]["session"]

    @property
    def directory(self):
        return self.active.directory

    @property
    def status(self):
        if self.halted:
            return "finished"
        if not self.started:
            return "idle"
        if all(cell["session"].status == "finished" for cell in self.cells):
            return "finished"
        return "running"

    def start(self):
        with self.lock:
            if self.started or self.halted:
                raise ValueError("This run has already started. Restart the console for another.")
            self.started = True
            self.cells[0]["session"].start()

    def stop(self):
        with self.lock:
            # Stopping means stopping the matrix, not just the cell on screen. Leaving the queue
            # armed would start the next viewport moments after someone asked it to stop.
            self.halted = True
            for cell in self.cells:
                cell["session"].stop()

    def _advance(self):
        if not self.started or self.halted:
            return
        current = self.cells[self.index]["session"]
        if current.status == "idle":
            current.start()

    def state(self, after=0):
        with self.lock:
            self._advance()
            for cell in self.cells:
                cell["session"].settle()
            index = self.index
            active = self.cells[index]["session"]
            payload = active.state(after)
            payload.update(
                status=self.status,
                cell=index,
                cells=[{
                    "label": cell["label"],
                    "viewport": list(cell["viewport"]),
                    "viewport_class": cell["viewport_class"],
                    "status": cell["session"].status,
                    "exit_code": cell["session"].exit_code,
                    "final": cell["session"].final(),
                } for cell in self.cells],
            )
            return payload

    def cell_directory(self, index):
        if 0 <= index < len(self.cells):
            return self.cells[index]["session"].directory
        return None

    def cell_journal(self, index):
        """The whole projected journal of one cell, so a finished viewport can be reopened.

        A collapsed cell is not a summary of what was on screen when it finished: it is read back
        from that cell's own append-only journal, so reopening it shows what the run recorded.
        """
        if not (0 <= index < len(self.cells)):
            return None
        cell = self.cells[index]
        session = cell["session"]
        with self.lock:
            events = session.tail.poll()
            session.settle()
            return {
                "label": cell["label"],
                "viewport": list(cell["viewport"]),
                "viewport_class": cell["viewport_class"],
                "status": session.status,
                "exit_code": session.exit_code,
                "total": len(events),
                "dropped": session.tail.dropped,
                "events": events,
                "final": session.final(),
            }


def build_handler(session, token, port, journey):
    origin = f"http://127.0.0.1:{port}"

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, mime="application/json"):
            body = content if isinstance(content, bytes) else content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            return self.headers.get("Host") == f"127.0.0.1:{port}"

        def do_GET(self):
            if not self.local():
                return self.send(403, "Forbidden", "text/plain")
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/state":
                query = parse_qs(parsed.query)
                try:
                    after = int((query.get("after") or ["0"])[0])
                except ValueError:
                    after = 0
                return self.send(200, json.dumps({**session.state(after), "journey": journey}))
            if path.startswith("/cell/"):
                rest = path.removeprefix("/cell/")
                number, _, name = rest.partition("/evidence/")
                if name and number.isdigit():
                    directory = getattr(session, "cell_directory",
                                        lambda _i: None)(int(number))
                    if directory is None:
                        return self.send(404, "Not found", "text/plain")
                    return self.evidence(name, directory)
                number, sep, tail = rest.partition("/journal")
                if sep and not tail and number.isdigit():
                    journal = getattr(session, "cell_journal", lambda _i: None)(int(number))
                    if journal is None:
                        return self.send(404, "Not found", "text/plain")
                    return self.send(200, json.dumps(journal))
                return self.send(404, "Not found", "text/plain")
            if path.startswith("/evidence/"):
                return self.evidence(path.removeprefix("/evidence/"), session.directory)
            files = {"/": ("console.html", "text/html"),
                     "/console.js": ("console.js", "text/javascript"),
                     "/console.css": ("console.css", "text/css")}
            if path not in files:
                return self.send(404, "Not found", "text/plain")
            name, mime = files[path]
            content = (STATIC / name).read_text(encoding="utf-8").replace("__TOKEN__", token)
            self.send(200, content, mime + "; charset=utf-8")

        def evidence(self, name, directory):
            # A screenshot name comes from the journal, so it is always a bare file name. Rebuild
            # it from its parts rather than trusting the request to stay inside the run directory.
            if not name.endswith(".jpg") or "/" in name or "\\" in name or ".." in name:
                return self.send(404, "Not found", "text/plain")
            path = directory / "evidence" / name
            if not path.exists():
                return self.send(404, "Not found", "text/plain")
            self.send(200, path.read_bytes(), "image/jpeg")

        def do_POST(self):
            if not self.local() or self.headers.get("X-Console-Token") != token \
                    or self.headers.get("Origin") not in (None, origin):
                return self.send(403, json.dumps({"error": "Local console requests only"}))
            action = urlparse(self.path).path.removeprefix("/api/")
            try:
                if action == "start":
                    session.start()
                elif action == "stop":
                    session.stop()
                else:
                    return self.send(404, json.dumps({"error": "Unknown action"}))
            except (ValueError, OSError) as error:
                return self.send(400, json.dumps({"error": str(error)}))
            self.send(200, json.dumps(session.state(0)))

        def log_message(self, *_args):
            pass

    return Handler


def serve(session, *, port, journey, open_browser=True):
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(session, token, port, journey))
    actual = server.server_address[1]
    if actual != port:  # port 0 was requested; the handler needs the real one for its Host check
        server.server_close()
        server = ThreadingHTTPServer(("127.0.0.1", actual),
                                     build_handler(session, token, actual, journey))
    url = f"http://127.0.0.1:{actual}"
    print(f"live console: {url}", flush=True)
    # Watching means watching something happen. The run starts with the console rather than waiting
    # for a click, so the page is a viewer over a run in progress and never a launcher for one.
    session.start()
    if open_browser:
        import webbrowser

        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)),
                         daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        server.server_close()
    return 0


def worker_command(*, journey=None, url=None, task=None, mode=None, viewport=None,
                   out, headed=True, blocking_checks=()):
    """The ordinary CLI, exactly as a person would type it. No second code path."""
    command = [sys.executable, "-m", "journey_evals.cli", "run", "--out", str(out), "--quiet"]
    if journey:
        command += ["--journey", str(journey)]
    if url:
        command += ["--url", url]
    if task:
        command += ["--task", task]
    if mode:
        command += ["--mode", mode]
    if viewport:
        command += ["--viewport", f"{viewport[0]}x{viewport[1]}"]
    for check in blocking_checks:
        command += ["--blocking-check", check]
    if headed:
        command.append("--headed")
    return command


def port_is_taken(port, host="127.0.0.1", timeout=0.4):
    """True when something already answers on this port.

    Windows honours ``SO_REUSEADDR`` on a listening socket, so a second server can bind a port that
    is already in use and requests are then split between the two. For a fixture that holds the
    records the acceptance contract reads, that is not a cosmetic problem: the run reads another
    process's leftovers and a correct contract fails a journey that actually succeeded. Refuse
    instead.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
