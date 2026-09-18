"""Owned synthetic application and independent, app-specific acceptance checks."""

import copy
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXPECTED = {
    "origin": "Zurich", "destination": "London", "date": "2026-09-20",
    "fare_id": "F001", "cabin": "Economy", "passenger": "Test Passenger", "selected_cents": 10000,
}
GOAL = (
    "Book a one-way flight from Zurich to London departing September 20, 2026, "
    "for one adult. Choose the Economy fare and Test Passenger. "
    "Complete the sandbox booking and stop once confirmation is visible."
)
APP = Path(__file__).with_name("app")


def load_app(directory):
    """Load an application source copy: its page and its own pricing implementation."""
    directory = Path(directory)
    namespace = {"__file__": str(directory / "pricing.py"), "__name__": "sandbox_app_pricing"}
    exec(compile((directory / "pricing.py").read_text(encoding="utf-8"), str(directory / "pricing.py"), "exec"),
         namespace)
    if not callable(namespace.get("quote")):
        raise ValueError("Application pricing module must define quote()")
    return (directory / "booking.html").read_bytes(), namespace["quote"]


def verify(bookings, *, expected_cents=10000, require_ack=False):
    failures = []
    if len(bookings) != 1:
        failures.append("Expected exactly one booking")
    else:
        expected_fields = {**EXPECTED, "cents": expected_cents, "currency": "USD"}
        if require_ack:
            expected_fields["acknowledged_cents"] = expected_cents
        for field, expected in expected_fields.items():
            if bookings[0].get(field) != expected:
                failures.append(f"Wrong or missing {field}")
    return {"passed": not failures, "failures": failures, "booking_count": len(bookings)}


def fare_facts(page):
    facts = {}
    for name, label in (("selected_cents", "Selected fare"), ("checkout_cents", "Checkout total")):
        match = re.search(re.escape(label) + r": USD (\d+)\.(\d{2})", page["text"])
        if match:
            facts[name] = int(match[1]) * 100 + int(match[2])
    if facts:
        identities = set(re.findall(r"Flight ([A-Z][A-Z0-9]+)", page["text"]))
        facts.update(currency="USD", fare_id=next(iter(identities)) if len(identities) == 1 else None,
                     source="visible DOM text of the synthetic fixture")
    return facts


class BookingFixture:
    def __init__(self, *, cents=None, disclosure=None, delay=0.06, app=APP):
        self.app = Path(app)
        self.html, self.pricing = load_app(self.app)
        self.cents, self.disclosure, self.delay = cents, disclosure, delay
        self.bookings = []
        self.searches = []
        self.lock = threading.Lock()

    def quote(self):
        """The application's own quote, with the test-side controls applied when present."""
        quote = dict(self.pricing())
        if type(quote.get("cents")) is not int or not isinstance(quote.get("disclosure"), str):
            raise ValueError("Application quote must provide integer cents and a disclosure string")
        if self.cents is not None:
            quote["cents"] = self.cents
        if self.disclosure is not None:
            quote["disclosure"] = self.disclosure
        return quote

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, data, content_type="application/json"):
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def permitted(self):
                return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

            def do_GET(self):
                if not self.permitted():
                    self.reply(403, {"error": "Loopback fixture only"})
                elif self.path == "/":
                    self.reply(200, fixture.html, "text/html; charset=utf-8")
                elif self.path == "/quote":
                    self.reply(200, fixture.quote())
                elif self.path == "/navigated":
                    self.reply(200, b"<h1>Another fixture document</h1><button>Continue</button>", "text/html")
                else:
                    self.reply(404, {"error": "Unknown fixture route"})

            def do_POST(self):
                if not self.permitted() or self.headers.get("Origin") not in (None, fixture.url.rstrip("/")):
                    self.reply(403, {"error": "Loopback fixture only"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length < 4096:
                        raise ValueError("Invalid fixture payload size")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("Expected fixture object")
                except (ValueError, json.JSONDecodeError):
                    self.reply(400, {"error": "Invalid fixture request"})
                    return
                if self.path == "/search":
                    started = time.perf_counter()
                    time.sleep(fixture.delay)
                    with fixture.lock:
                        fixture.searches.append(round((time.perf_counter() - started) * 1000, 3))
                    self.reply(200, {"available": True})
                elif self.path == "/book":
                    fields = {key: data.get(key) for key in EXPECTED}
                    valid = all(fields[key] == value for key, value in EXPECTED.items() if key != "passenger")
                    if not valid or fields["passenger"] not in ("Test Passenger", "Other Passenger"):
                        self.reply(400, {"error": "Unsupported synthetic itinerary"})
                        return
                    with fixture.lock:
                        booking = dict(fields, id=f"TEST-{len(fixture.bookings) + 1}",
                                       cents=fixture.quote()["cents"], currency="USD")
                        if "acknowledged_cents" in data:
                            booking["acknowledged_cents"] = data["acknowledged_cents"]
                        fixture.bookings.append(booking)
                    self.reply(200, booking)
                else:
                    self.reply(404, {"error": "Unknown fixture route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.bookings)

    def reset(self):
        with self.lock:
            self.bookings.clear()

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("Fixture server did not stop")
