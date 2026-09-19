"""The Meridian fixture's own behaviour: scenarios, recorded facts, and the notice texts.

These are fixture tests, not journey tests. They assert what the application does, so that a later
journey failure can be attributed to the evaluation rather than to a fixture that never behaved the
way the journey assumed.
"""

import json
import urllib.request

import pytest

from examples import admin_app


def call(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback fixture
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())


@pytest.fixture
def app(request):
    from examples.flight_app import Fixture

    with Fixture(fault=getattr(request, "param", "none"), app="admin") as fixture:
        yield fixture


def test_credit_is_computed_from_the_seeded_facts():
    # Four seats x USD 12.00 x 12 unused days of 30. Every notice quotes this number, so a notice
    # cannot be adequate by accident.
    assert admin_app.CREDIT_CENTS == 1920
    assert admin_app.LOSING_ACCESS == ("dana@meridian.test", "raj@meridian.test",
                                       "kim@meridian.test", "lee@meridian.test")


@pytest.mark.parametrize("variant", ["full", "terse"])
def test_complete_notices_name_every_consequence(variant):
    notice = admin_app.DOWNGRADE_NOTICES[variant]
    assert "19.20" in notice
    assert all(address.split("@")[0] in notice for address in admin_app.LOSING_ACCESS)
    assert "Starter" in notice


def test_the_defect_notices_omit_a_consequence_while_staying_plausible():
    assert admin_app.DOWNGRADE_NOTICES["generic"] == "Your plan will be updated."
    credit_only = admin_app.DOWNGRADE_NOTICES["credit_only"]
    assert "19.20" in credit_only
    assert "lose access" not in credit_only


def test_the_terse_control_is_shorter_than_the_defect_it_neighbours():
    # The point of the control: length, keyword presence and element presence all fail to separate
    # an adequate notice from an inadequate one.
    assert len(admin_app.DELETE_NOTICES["terse"]) > len(admin_app.DELETE_NOTICES["generic"])
    assert len(admin_app.FRESHNESS_NOTICES["terse"]) < len(admin_app.DOWNGRADE_NOTICES["generic"]) * 3
    assert "412" in admin_app.DELETE_NOTICES["terse"]
    assert "412" not in admin_app.DELETE_NOTICES["generic"]


def test_plan_change_records_what_was_applied(app):
    status, preview = call(app.url + "api/plan/preview", {})
    assert status == 200 and preview["credit_cents"] == 1920
    status, change = call(app.url + "api/plan/change", {})
    assert status == 200
    assert change["effective_date"] == "2026-10-01" == change["promised_effective_date"]
    assert change["seats_removed"] == 4
    assert call(app.url + "__test__/plan-changes")[1][0]["id"] == change["id"]


@pytest.mark.parametrize("app", ["schedule_claim_false"], indirect=True)
def test_a_false_schedule_claim_is_visible_only_in_the_record(app):
    _, preview = call(app.url + "api/plan/preview", {})
    _, change = call(app.url + "api/plan/change", {})
    # The page is told one date and the backend applies another. Nothing on screen differs.
    assert preview["effective_date"] == "2026-10-01"
    assert change["promised_effective_date"] == "2026-10-01"
    assert change["effective_date"] == "2026-09-19"


def test_delete_then_restore_returns_the_workspace(app):
    assert call(app.url + "api/workspace/delete", {"id": "atlas"})[0] == 200
    listing = call(app.url + "api/workspaces")[1]
    assert [w["name"] for w in listing["deleted"]] == ["Atlas"]
    assert call(app.url + "api/workspace/restore", {"id": "atlas"})[0] == 200
    records = call(app.url + "__test__/workspaces")[1]
    assert {w["name"]: w["status"] for w in records} == {"Atlas": "active", "Beacon": "active"}


@pytest.mark.parametrize("app", ["false_restore_promise"], indirect=True)
def test_the_broken_promise_is_only_visible_by_acting_on_it(app):
    assert call(app.url + "api/workspace/delete", {"id": "atlas"})[0] == 200
    listing = call(app.url + "api/workspaces")[1]
    # Recently deleted still lists it, and the notice still promises 30 days. Only the restore
    # attempt reveals that the record is gone.
    assert [w["name"] for w in listing["deleted"]] == ["Atlas"]
    assert "30 days" in listing["notice"]
    status, body = call(app.url + "api/workspace/restore", {"id": "atlas"})
    assert status == 410 and "no longer recoverable" in body["error"]


@pytest.mark.parametrize("app", ["generic_delete_warning"], indirect=True)
def test_a_generic_warning_withholds_the_blast_radius_everywhere(app):
    """A page cannot compensate for the missing notice by printing the counts in its summary."""
    listing = call(app.url + "api/workspaces")[1]
    atlas = next(w for w in listing["active"] if w["name"] == "Atlas")
    assert not {"projects", "documents", "members"} & set(atlas)
    assert listing["notice"] == "This action cannot be undone."


def test_the_verifier_still_sees_the_facts_the_page_was_denied(app):
    """Withholding is a presentation decision; the recorded truth is unchanged."""
    records = call(app.url + "__test__/workspaces")[1]
    atlas = next(w for w in records if w["name"] == "Atlas")
    assert (atlas["projects"], atlas["documents"], atlas["members"]) == (3, 412, 12)


def test_a_window_scoped_verifier_answers_only_for_the_declared_window(app):
    """Looking at another window on the way is not a failure of a last-seven-days journey."""
    call(app.url + "api/insights/revenue", {"window": "last_30_days"})
    call(app.url + "api/insights/revenue", {"window": "last_7_days"})
    assert len(call(app.url + "__test__/queries")[1]) == 2
    scoped = call(app.url + "__test__/queries/last_7_days")[1]
    assert [q["window"] for q in scoped] == ["last_7_days"]
    assert call(app.url + "__test__/queries/last_90_days")[1] == []


def test_revenue_records_one_query_per_window_however_often_it_is_refreshed(app):
    first = call(app.url + "api/insights/revenue", {"window": "last_7_days"})[1]
    call(app.url + "api/insights/revenue", {"window": "last_7_days"})
    records = call(app.url + "__test__/queries")[1]
    assert len(records) == 1 and records[0]["requests"] == 2
    assert records[0]["regions_included"] == ["AMER", "APAC", "LATAM"]
    assert records[0]["complete"] is False
    assert [r["code"] for r in first["regions"]] == ["AMER", "APAC", "LATAM"]


@pytest.mark.parametrize("app", ["stale_without_caveat"], indirect=True)
def test_the_stale_defect_looks_current_on_screen_and_is_not(app):
    payload = call(app.url + "api/insights/revenue", {"window": "last_7_days"})[1]
    assert payload["badge"] == "Updated just now"
    assert payload["notice"] == ""
    assert call(app.url + "__test__/queries")[1][0]["extract_age_minutes"] == 1140


@pytest.mark.parametrize("app", ["current_complete_data"], indirect=True)
def test_the_clean_control_is_complete_and_says_so(app):
    payload = call(app.url + "api/insights/revenue", {"window": "last_7_days"})[1]
    assert len(payload["regions"]) == 4
    assert "All four regions" in payload["notice"]
    assert call(app.url + "__test__/queries")[1][0]["complete"] is True


def test_reset_returns_the_fixture_to_a_known_state(app):
    call(app.url + "api/workspace/delete", {"id": "atlas"})
    call(app.url + "api/plan/change", {})
    call(app.url + "__test__/reset", {})
    assert call(app.url + "__test__/plan-changes")[1] == []
    assert all(w["status"] == "active" for w in call(app.url + "__test__/workspaces")[1])


def test_presentation_flags_never_leak_a_scenario_name():
    from examples.flight_app import Fixture

    with Fixture(fault="missing_refresh_feedback", app="admin") as fixture:
        flags = call(fixture.url + "api/flags")[1]
    assert set(flags) == {"refresh_progress", "clip_delete"}
    assert "missing_refresh_feedback" not in json.dumps(flags)
    assert flags["refresh_progress"] == ""


def test_every_defect_has_an_adjacent_control():
    # The suite is only honest if each defect has a lookalike that must not be reported.
    pairs = {
        "silent_downgrade": "terse_downgrade_notice",
        "hidden_seat_loss": "terse_downgrade_notice",
        "generic_delete_warning": "terse_delete_warning",
        "stale_without_caveat": "terse_freshness_caveat",
        "missing_refresh_feedback": "slow_refresh_with_feedback",
    }
    for defect, control in pairs.items():
        assert defect in admin_app.SCENARIOS and control in admin_app.SCENARIOS


def test_the_declared_window_is_never_the_one_the_page_already_shows():
    """A journey that asks for a window the page defaults to asks the actor not to act.

    The actor is biased toward using the control in front of it. When the selector already held
    the required window, the only available change was the wrong one, and two clean runs failed
    a journey the application had served correctly.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    markup = (root / "examples" / "admin.html").read_text(encoding="utf-8")
    block = re.search(r'<select id="window">(.*?)</select>', markup, re.S).group(1)
    options = re.findall(r'<option value="([^"]+)"([^>]*)>', block)
    default = next((value for value, attrs in options if "selected" in attrs), options[0][0])

    spec = json.loads((root / "examples" / "journeys" / "revenue-freshness.json")
                      .read_text(encoding="utf-8"))
    declared = spec["acceptance"]["backend"]["expect_records"][0]["window"]
    assert declared in [value for value, _ in options]
    assert declared != default
