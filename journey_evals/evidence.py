"""Immutable local observations and one consumer of the harness event queue."""

import base64
import copy
import json
import os
import threading
import time
from pathlib import Path

from .feasibility import write_json

SCRIPT = Path(__file__).with_name("telemetry.js").read_text(encoding="utf-8")


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "evidence").mkdir(exist_ok=True)
        self.stream = (self.directory / "events.jsonl").open("x", encoding="utf-8")
        self.lock = threading.RLock()
        self.sequence = 0

    def append(self, kind, data):
        data = copy.deepcopy(data)
        with self.lock:
            self.sequence += 1
            if kind == "observation" and "screenshot" in data["page"]:
                relative = f"evidence/{self.sequence:06d}.jpg"
                (self.directory / relative).write_bytes(base64.b64decode(data["page"].pop("screenshot")))
                data["page"]["screenshot_file"] = relative
            event = {"schema_version": 1, "sequence": self.sequence, "host_monotonic_ns": time.perf_counter_ns(),
                     "kind": kind, "data": data}
            self.stream.write(json.dumps(event, allow_nan=False) + "\n")
            self.stream.flush()
            if kind.startswith("action_") or kind == "observation":
                os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


class Collector:
    def __init__(self, journal):
        self.journal = journal
        self.documents = {}
        self.current_document = None
        self.errors = []
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None

    def install(self, browser):
        self.browser = browser
        browser.call("Page.enable")
        browser.call("Runtime.enable")
        browser.call("Network.enable")
        browser.call("Runtime.addBinding", name="jevEvidence")
        browser.call("Page.addScriptToEvaluateOnNewDocument", source=SCRIPT)
        self.thread = threading.Thread(target=self._listen, name="jev-evidence", daemon=True)
        self.thread.start()

    def accept(self, payload):
        with self.lock:
            document = payload["documentId"]
            number = payload["sequence"]
            if not isinstance(document, str) or not document or type(number) is not int or number < 1:
                raise ValueError("Invalid telemetry document/sequence")
            if document not in self.documents:
                if self.current_document:
                    self.documents[self.current_document]["gaps"].append("navigation_tail_unacknowledged")
                self.current_document = document
                self.documents[document] = {"last_sequence": 0, "gaps": [], "began": payload["kind"] == "begin"}
            state = self.documents[document]
            if number != state["last_sequence"] + 1:
                state["gaps"].append(f"sequence_gap:{state['last_sequence']}->{number}")
            state["last_sequence"] = number
            if payload["kind"] == "feedback":
                data = payload["data"]
                if data["truncated"] or any(r["textTruncated"] for r in data["regions"]):
                    state["gaps"].append("feedback_truncated")
            self.journal.append("browser_event", payload)

    def _listen(self):
        from browser_harness.helpers import drain_events

        try:
            while not self.stop.is_set():
                for event in drain_events():
                    if event.get("session_id") != self.browser.session:
                        continue
                    method, params = event["method"], event["params"]
                    if method == "Runtime.bindingCalled" and params.get("name") == "jevEvidence":
                        if len(params["payload"]) > 100_000:
                            raise ValueError("Oversized telemetry message")
                        self.accept(json.loads(params["payload"]))
                    elif method == "Network.loadingFailed":
                        self.journal.append("network_failure", {
                            "request_id": params["requestId"], "error": params["errorText"],
                        })
                    elif method == "Network.responseReceived" and params["response"]["status"] >= 400:
                        self.journal.append("http_failure", {
                            "url": params["response"]["url"], "status": params["response"]["status"],
                        })
                    elif method == "Runtime.exceptionThrown":
                        self.journal.append("console_exception", {
                            "text": params["exceptionDetails"].get("text", "")[:300],
                        })
                    elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "warning"}:
                        self.journal.append("console_message", {
                            "level": params["type"],
                            "text": " ".join(str(a.get("value", a.get("description", "")))
                                             for a in params.get("args", []))[:1000],
                        })
                self.stop.wait(0.02)
        except Exception as error:
            with self.lock:
                self.errors.append(type(error).__name__)
            self.journal.append("collector_failed", {"error_type": type(error).__name__})

    def checkpoint(self, label):
        before = time.perf_counter_ns()
        current = self.browser.evaluate(
            "window.__jevTelemetry?.checkpoint(" + json.dumps(label) + ")"
        )
        after = time.perf_counter_ns()
        if not current:
            raise RuntimeError("No document telemetry; required evidence is incomplete")
        if (
            not isinstance(current, dict) or type(current.get("sequence")) is not int
            or current["sequence"] < 1 or not isinstance(current.get("documentId"), str)
            or not current["documentId"]
        ):
            raise ValueError("Invalid document watermark; required evidence is incomplete")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.lock:
                state = self.documents.get(current["documentId"], {})
                if state.get("last_sequence", 0) >= current["sequence"]:
                    break
                if self.errors:
                    break
            time.sleep(0.01)
        with self.lock:
            state = self.documents.get(current["documentId"], {})
            current["complete"] = (
                not self.errors and state.get("last_sequence", 0) >= current["sequence"]
                and all(d["began"] and not d["gaps"] for d in self.documents.values())
                and not current["evaluation"]["truncated"]
            )
            current["documents"] = copy.deepcopy(self.documents)
            current["collector_errors"] = list(self.errors)
        current["clock_anchor"] = {"host_before_ns": before, "host_after_ns": after,
                                   "browser_time_origin_ms": current["timeOrigin"], "browser_now_ms": current["at"]}
        self.journal.append("watermark", current)
        return current

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
            if self.thread.is_alive():
                raise RuntimeError("Evidence consumer did not stop")
        write_json(self.journal.directory / "telemetry.json", {
            "documents": self.documents, "errors": self.errors,
        })
