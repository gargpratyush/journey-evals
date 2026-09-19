"""A synthetic booking application with declared, hidden faults.

Everything the evaluator is supposed to discover lives on the server or in the served script, never
in the URL, the DOM, the page text or anything the model is shown. A scenario name in the page
would let a language model pass by reading the label instead of observing behaviour, which is the
single most effective way to make a harness like this look better than it is.

Serve it with ``python -m examples.flight_app --fault silent_fare_increase --port 8000`` or through
``journey-evals serve``. It binds loopback only and refuses requests that did not arrive with a loopback
Host header.
"""

import argparse
import copy
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
BASE_CENTS = 10000
CURRENCY = "USD"
# Behavioural parameters only. No scenario name is ever sent to the browser.
DEFAULTS = {
    "search_delay_ms": 60,
    "progress_text": "Searching for flights…",
    "results": True,
    "checkout_cents": BASE_CENTS,
    "disclosure": "",
    "clip_confirm": False,
    "lose_details_on_back": False,
    "jank_ms": 0,
    "book_fails": False,
}
SCENARIOS = {
    "none": {},
    # Faults the evaluator is expected to detect.
    "search_does_nothing": {"results": False, "progress_text": ""},
    "silent_fare_increase": {"checkout_cents": 12000, "disclosure": ""},
    "missing_loading_feedback": {"search_delay_ms": 2600, "progress_text": ""},
    "clipped_confirm_button": {"clip_confirm": True},
    "details_lost_on_back": {"lose_details_on_back": True},
    "booking_never_recorded": {"book_fails": True},
    "janky_render": {"jank_ms": 240},
    # Controls that look similar and must not be reported.
    "brief_render_work": {"jank_ms": 40},
    "disclosed_fare_increase": {
        "checkout_cents": 12000,
        "disclosure": ("The fare you selected sold out. The checkout total is now USD 120.00 "
                       "instead of USD 100.00. Confirm to accept the new total."),
    },
    "fast_search": {"search_delay_ms": 60, "progress_text": ""},
    "terse_loading_feedback": {"search_delay_ms": 2600, "progress_text": "Working…"},
}
EXPECTED_ITINERARY = {"origin": "Zurich", "destination": "London", "date": "2026-09-20",
                      "fare_id": "F001", "cabin": "Economy"}


class FlightApplication:
    """State and behaviour for one running copy of the synthetic application."""

    def __init__(self, fault="none"):
        if fault not in SCENARIOS:
            raise ValueError(f"Unknown scenario {fault!r}; choose from {sorted(SCENARIOS)}")
        self.fault = fault
        self.flags = {**DEFAULTS, **SCENARIOS[fault]}
        self.bookings = []
        self.searches = []
        self.lock = threading.Lock()

    def search(self):
        started = time.perf_counter()
        time.sleep(self.flags["search_delay_ms"] / 1000)
        with self.lock:
            self.searches.append(round((time.perf_counter() - started) * 1000, 3))
        if not self.flags["results"]:
            return {"results": []}
        return {"results": [{"id": "F001", "cabin": "Economy", "cents": BASE_CENTS,
                             "currency": CURRENCY, "departs": "08:15", "arrives": "09:45"}]}

    def quote(self):
        return {"selected_cents": BASE_CENTS, "checkout_cents": self.flags["checkout_cents"],
                "currency": CURRENCY, "disclosure": self.flags["disclosure"]}

    def book(self, payload):
        for name, value in EXPECTED_ITINERARY.items():
            if payload.get(name) != value:
                return 400, {"error": "Unsupported synthetic itinerary"}
        passenger = payload.get("passenger")
        if not isinstance(passenger, str) or not 2 <= len(passenger.strip()) <= 60:
            return 400, {"error": "A passenger name is required"}
        record = {**EXPECTED_ITINERARY, "passenger": passenger.strip(),
                  "cents": self.flags["checkout_cents"], "currency": CURRENCY}
        if self.flags["book_fails"]:
            # The interface will report success; nothing is recorded. Only the independent
            # verifier can tell the difference, which is exactly the point of having one.
            return 200, {**record, "id": "TEST-1"}
        with self.lock:
            record["id"] = f"TEST-{len(self.bookings) + 1}"
            self.bookings.append(record)
        return 200, record

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.bookings)

    def client_flags(self):
        return {k: self.flags[k] for k in ("progress_text", "clip_confirm",
                                           "lose_details_on_back", "jank_ms")}


def build_handler(application, assets):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "journey-evals-fixture"

        def log_message(self, *_args):
            pass

        def reply(self, status, data, content_type="application/json", cache=False):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=60" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def permitted(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in {"127.0.0.1", "localhost", "[::1]"}

        def do_GET(self):
            if not self.permitted():
                self.reply(403, {"error": "Loopback fixture only"})
                return
            path = self.path.split("?")[0]
            if path == "/favicon.ico":
                self.reply(200, b"", "image/x-icon", cache=True)
            elif path in {"/", "/results", "/details", "/review", "/confirmation"}:
                self.reply(200, assets["html"], "text/html; charset=utf-8")
            elif path == "/app.js":
                self.reply(200, assets["js"], "text/javascript; charset=utf-8")
            elif path == "/api/flags":
                self.reply(200, application.client_flags())
            elif path == "/api/quote":
                self.reply(200, application.quote())
            elif path == "/__test__/bookings":
                # Test-only verifier surface. The actor never sees it and the page never links it.
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
            path = self.path.split("?")[0]
            if path == "/api/search":
                self.reply(200, application.search())
            elif path == "/api/book":
                status, body = application.book(payload)
                self.reply(status, body)
            elif path == "/__test__/reset":
                with application.lock:
                    application.bookings.clear()
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "Unknown fixture route"})

    return Handler


def refuse_taken_port(port, host="127.0.0.1", timeout=0.4):
    """Refuse to bind a fixture port that something already answers on.

    Windows honours ``SO_REUSEADDR`` on a listening socket, so this bind would otherwise *succeed*
    while requests kept going to the process that got there first. The failure is silent and
    total: the run measures someone else's application. A leftover ``watch --fault ...`` server
    has already invalidated a whole calibration sweep this way, with every scenario - faults and
    clean controls alike - reporting the leftover's seeded fault. Fail loudly instead.
    """
    if not port:
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        if probe.connect_ex((host, port)) == 0:
            raise OSError(
                f"something is already listening on {host}:{port}. Stop it before serving the "
                f"fixture there; binding anyway would let this run read another process's "
                f"application and silently measure the wrong thing."
            )


def build(*, port=0, fault="none", app="flight"):
    if app == "subscription":
        from . import subscription_app

        return subscription_app.build(port=port, fault=fault)
    if app == "admin":
        from . import admin_app

        return admin_app.build(port=port, fault=fault)
    if app != "flight":
        raise ValueError(f"Unknown example application {app!r}")
    application = FlightApplication(fault)
    assets = {"html": (HERE / "flight.html").read_bytes(), "js": (HERE / "flight.js").read_bytes()}
    refuse_taken_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(application, assets))
    server.application = application
    return server


class Fixture:
    """Context manager for tests and calibration runs."""

    def __init__(self, fault="none", app="flight"):
        self.fault, self.app = fault, app

    def __enter__(self):
        self.server = build(fault=self.fault, app=self.app)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.application = self.server.application
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Fixture server did not stop")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--fault", default="none", choices=sorted(SCENARIOS))
    parser.add_argument("--app", default="flight", choices=("flight", "subscription"))
    args = parser.parse_args(argv)
    server = build(port=args.port, fault=args.fault, app=args.app)
    print(f"http://127.0.0.1:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
