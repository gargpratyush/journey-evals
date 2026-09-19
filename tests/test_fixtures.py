"""The bundled fixtures, checked without a browser.

These are the applications every calibration number is measured against, so their faults have to
be real and their test data has to be resettable. A fixture that cannot express the defect it
claims to seed produces a detection rate that means nothing.
"""

import json
import threading
import urllib.request

import pytest

from examples import flight_app, subscription_app


def post(port, path, payload=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return response.status, response.read().decode()


@pytest.fixture
def flight():
    server = flight_app.build(port=0, fault="none")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def subscription():
    server = subscription_app.build(port=0, fault="none")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, server.server_address[1]
    server.shutdown()
    server.server_close()


BOOKING = {**flight_app.EXPECTED_ITINERARY, "passenger": "Test Passenger"}
SIGNUP = {"plan_id": sorted(subscription_app.PLANS)[0], "workspace": "Test Workspace",
          "email": "someone@example.test"}


# -- resettable test data ---------------------------------------------------------------------


def test_the_flight_backend_starts_empty(flight):
    _server, port = flight
    assert json.loads(get(port, "/__test__/bookings")[1]) == []


def test_resetting_the_flight_backend_removes_previous_records(flight):
    _server, port = flight
    post(port, "/api/book", BOOKING)
    assert json.loads(get(port, "/__test__/bookings")[1])
    post(port, "/__test__/reset")
    assert json.loads(get(port, "/__test__/bookings")[1]) == []


def test_resetting_the_subscription_backend_removes_previous_records(subscription):
    _server, port = subscription
    post(port, "/subscribe", SIGNUP)
    assert json.loads(get(port, "/__test__/subscriptions")[1])
    post(port, "/__test__/reset")
    assert json.loads(get(port, "/__test__/subscriptions")[1]) == []


# -- the faults are real ----------------------------------------------------------------------


def test_the_never_recorded_fault_actually_fails_to_record():
    server = flight_app.build(port=0, fault="booking_never_recorded")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        status, _body = post(port, "/api/book", BOOKING)
        assert status == 200
        assert json.loads(get(port, "/__test__/bookings")[1]) == []
    finally:
        server.shutdown()
        server.server_close()


def test_the_subscription_never_recorded_fault_actually_fails_to_record():
    server = subscription_app.build(port=0, fault="subscription_never_recorded")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        post(port, "/subscribe", SIGNUP)
        assert json.loads(get(port, "/__test__/subscriptions")[1]) == []
    finally:
        server.shutdown()
        server.server_close()


# -- loopback only ----------------------------------------------------------------------------


def test_the_fixtures_only_bind_loopback(flight, subscription):
    for _server, port in (flight, subscription):
        assert _server.server_address[0] == "127.0.0.1"
        assert get(port, "/")[0] == 200
