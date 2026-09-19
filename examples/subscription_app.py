"""The second synthetic application: subscription signup, served for journey-evals journeys.

This reuses the page already exercised by the feasibility campaign's CP6 and adds only what the
product needs: a test-only verifier endpoint and the same hidden-scenario discipline as the flight
fixture. The frozen campaign module is left untouched.
"""

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import flight_app

APP = Path(__file__).resolve().parent
PLANS = {"P-STARTER": ("Starter", 0), "P-TEAM": ("Team", 4000), "P-BUSINESS": ("Business", 9000)}
# Server-owned scenario state. Presentation flags are the subset the page is allowed to read.
DEFAULTS = {
    "availability_delay_ms": 60, "cents": None, "disclosure": "", "subscribe_fails": False,
    "price_fails": False,
    "progress_text": "Checking workspace availability", "clip_subscribe": False,
}
PRESENTATION = ("progress_text", "clip_subscribe")
SCENARIOS = {
    "none": {},
    "silent_price_increase": {"cents": 6000, "disclosure": ""},
    "missing_loading_feedback": {"availability_delay_ms": 2600, "progress_text": ""},
    "subscription_never_recorded": {"subscribe_fails": True},
    "availability_does_nothing": {"price_fails": True},
    "clipped_subscribe_button": {"clip_subscribe": True},
    "disclosed_price_increase": {
        "cents": 6000,
        "disclosure": ("Your plan price changed from USD 40.00 to USD 60.00 per month because the "
                       "promotional rate ended. Accept to continue."),
    },
    "fast_availability": {"availability_delay_ms": 60},
    "terse_loading_feedback": {"availability_delay_ms": 2600, "progress_text": "Working"},
}


class SubscriptionApplication:
    def __init__(self, fault="none"):
        if fault not in SCENARIOS:
            raise ValueError(f"Unknown scenario {fault!r}; choose from {sorted(SCENARIOS)}")
        self.fault = fault
        self.flags = {**DEFAULTS, **SCENARIOS[fault]}
        self.subscriptions = []
        self.checks = []
        self.lock = threading.Lock()

    def availability(self):
        started = time.perf_counter()
        time.sleep(self.flags["availability_delay_ms"] / 1000)
        with self.lock:
            self.checks.append(round((time.perf_counter() - started) * 1000, 3))
        return {"available": True}

    def price(self):
        cents = self.flags["cents"]
        return {"cents": PLANS["P-TEAM"][1] if cents is None else cents,
                "currency": "USD", "disclosure": self.flags["disclosure"]}

    def presentation(self):
        return {name: self.flags[name] for name in PRESENTATION}

    def subscribe(self, payload):
        plan_id = payload.get("plan_id")
        if plan_id not in PLANS:
            return 400, {"error": "Unknown synthetic plan"}
        for name in ("workspace", "email"):
            value = payload.get(name)
            if not isinstance(value, str) or not 2 <= len(value.strip()) <= 80:
                return 400, {"error": f"A {name} is required"}
        record = {
            "plan_id": plan_id, "plan": PLANS[plan_id][0], "workspace": payload["workspace"].strip(),
            "email": payload["email"].strip(), "cents": self.price()["cents"], "currency": "USD",
            "selected_cents": payload.get("selected_cents"),
        }
        if "acknowledged_cents" in payload:
            record["acknowledged_cents"] = payload["acknowledged_cents"]
        if self.flags["subscribe_fails"]:
            return 200, {**record, "id": "SUB-1"}
        with self.lock:
            record["id"] = f"SUB-{len(self.subscriptions) + 1}"
            self.subscriptions.append(record)
        return 200, record

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.subscriptions)


def build_handler(application, html):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "journey-evals-fixture"

        def log_message(self, *_args):
            pass

        def reply(self, status, data, content_type="application/json"):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def permitted(self):
            return (self.headers.get("Host") or "").split(":")[0] in {"127.0.0.1", "localhost"}

        def do_GET(self):
            if not self.permitted():
                self.reply(403, {"error": "Loopback fixture only"})
            elif self.path == "/favicon.ico":
                self.reply(200, b"", "image/x-icon")
            elif self.path == "/":
                self.reply(200, html, "text/html; charset=utf-8")
            elif self.path == "/price":
                if application.flags["price_fails"]:
                    self.reply(503, {"error": "Synthetic quote service unavailable"})
                else:
                    self.reply(200, application.price())
            elif self.path == "/api/flags":
                self.reply(200, application.presentation())
            elif self.path == "/__test__/subscriptions":
                self.reply(200, application.snapshot())
            else:
                self.reply(404, {"error": "Unknown fixture route"})

        def do_POST(self):
            if not self.permitted():
                self.reply(403, {"error": "Loopback fixture only"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length < 8192:
                    raise ValueError("Invalid payload size")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
            except (ValueError, json.JSONDecodeError):
                self.reply(400, {"error": "Invalid fixture request"})
                return
            if self.path == "/availability":
                self.reply(200, application.availability())
            elif self.path == "/subscribe":
                status, body = application.subscribe(payload)
                self.reply(status, body)
            elif self.path == "/__test__/reset":
                # Seeded test data has to be resettable between runs. Without this a second run
                # inherits the first run's records, and an acceptance contract that (correctly)
                # rejects unexpected leftovers will fail a journey that actually succeeded.
                with application.lock:
                    application.subscriptions.clear()
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "Unknown fixture route"})

    return Handler


def build(*, port=0, fault="none"):
    application = SubscriptionApplication(fault)
    flight_app.refuse_taken_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 build_handler(application, (APP / "subscription.html").read_bytes()))
    server.application = application
    return server
