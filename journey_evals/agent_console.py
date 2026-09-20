"""A local viewer for an agent evaluation: the conversation as it happens, then the judging.

The page is a viewer over a run, never a launcher for one. It reads the same journal the run
records, *after* redaction, so nothing can appear on screen that the artifacts would have masked.

Judging is deliberately shown as a separate phase rather than as live per-turn verdicts, because
that is when it actually happens: the whole conversation is judged in one request once the last
turn is done. Several requirements can only be decided that way — whether a fee was disclosed at
the moment of booking rather than three turns later is not answerable while the booking turn is
still the newest thing in the trace.
"""

import json
import secrets as secrets_module
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent_evals import run_agent_eval

STATIC = Path(__file__).parent / "static"


class AgentSession:
    """Runs one agent evaluation in a worker thread and buffers its redacted journal."""

    def __init__(self, spec, directory, *, runner=None):
        self.spec = spec
        self.directory = Path(directory)
        self.runner = runner or run_agent_eval
        self.lock = threading.Lock()
        self.events = []
        self.status = "idle"
        self.error = None
        self.report = None
        self.thread = None

    def _observe(self, event):
        with self.lock:
            self.events.append({"sequence": len(self.events) + 1, **event})

    def _run(self):
        try:
            report = self.runner(self.spec, self.directory, observer=self._observe)
            with self.lock:
                self.report = report
                self.status = "finished"
        except Exception as error:  # a viewer must show the failure, not die with it
            with self.lock:
                self.error = f"{type(error).__name__}: {error}"
                self.status = "failed"

    def start(self):
        if self.thread is not None:
            return
        self.status = "running"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        thread = self.thread
        if thread is not None:
            thread.join(timeout=2)

    def phase(self):
        """Which part of the run the viewer is watching.

        This is read from recorded events rather than guessed from a timer, so the page cannot
        claim the judge is working before the conversation has actually finished.
        """
        with self.lock:
            kinds = {item.get("kind") for item in self.events}
            status = self.status
        if status in {"finished", "failed"}:
            return "done"
        if "agent_completed" in kinds:
            return "judging"
        if "agent_run_started" in kinds:
            return "conversation"
        return "starting"

    def state(self, after=0):
        with self.lock:
            events = [item for item in self.events if item["sequence"] > after]
            total = len(self.events)
            status, error, report = self.status, self.error, self.report
        summary = None
        if report:
            summary = {
                "result": report.get("result"),
                "goal_status": report.get("goal_status"),
                "evidence_basis": report.get("evidence_basis"),
                "exit_code": report.get("exit_code"),
                "cost": report.get("cost"),
            }
        return {
            "status": status,
            "phase": self.phase(),
            "error": error,
            "total": total,
            "events": events,
            "summary": summary,
            "task": self.spec.task,
            "evaluation_id": self.spec.id,
            "turns_expected": len(self.spec.conversation),
            "judges": [
                {"id": judge.id, "requirement": judge.requirement,
                 "enforcement": "blocking" if judge.blocking else "advisory"}
                for judge in self.spec.judges
            ],
        }


def build_handler(session, token, port):
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
            if parsed.path == "/api/state":
                query = parse_qs(parsed.query)
                try:
                    after = int((query.get("after") or ["0"])[0])
                except ValueError:
                    after = 0
                return self.send(200, json.dumps(session.state(after)))
            files = {"/": ("agent-console.html", "text/html"),
                     "/agent-console.js": ("agent-console.js", "text/javascript"),
                     "/agent-console.css": ("agent-console.css", "text/css")}
            if parsed.path not in files:
                return self.send(404, "Not found", "text/plain")
            name, mime = files[parsed.path]
            content = (STATIC / name).read_text(encoding="utf-8").replace("__TOKEN__", token)
            return self.send(200, content, mime + "; charset=utf-8")

        def log_message(self, *_args):
            pass

    return Handler


def serve(session, *, port=0, open_browser=True):
    token = secrets_module.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(session, token, port))
    actual = server.server_address[1]
    if actual != port:  # port 0 was requested; the handler needs the real one for its Host check
        server.server_close()
        server = ThreadingHTTPServer(("127.0.0.1", actual), build_handler(session, token, actual))
    url = f"http://127.0.0.1:{actual}"
    print(f"agent console: {url}", flush=True)
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
    report = session.report
    return report.get("exit_code", 1) if report else 1
