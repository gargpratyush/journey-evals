"""Meridian: a synthetic workspace administration console for journey-evals journeys.

The first two fixtures are happy paths with a defect injected into them. This one is not. Every
journey it serves ends in a *degraded* state — a plan change that takes access away from people, a
destructive delete that makes a promise about recovery, a dashboard whose data is stale and
incomplete — because that is where the oracle stops being enumerable.

The distinction this fixture exists to exercise:

  * the **facts** are always owned by code. What the proration is, who loses access and when, how
    many documents a deletion destroys, whether a restore actually restored anything, which regions
    are in the payload and how old the extract is: all of that is computed here, recorded here, and
    verifiable through the ``/__test__`` endpoints without asking a model anything.
  * the **communication** is not enumerable. There are unboundedly many adequate ways to tell an
    administrator that four named colleagues lose access on 1 October. A string assertion has to
    either hardcode one of them, which rots on the next copy edit, or assert something weak like
    "the notice is non-empty", which passes on "Your plan will be updated."

So every scenario below comes in pairs: a defect, and a *legitimate lookalike* that is textually
adjacent to it and must not be reported. ``terse_delete_warning`` is nine words long and complete;
``generic_delete_warning`` is eight words long and says nothing. Any evaluator that separates those
two by length, by keyword, or by the presence of an element is measuring the wrong thing.

The two fixture rules from the other applications still hold, and they outrank the product design:
nothing may signal progress that a scenario has removed, and nothing may clip or overlay a control
except where a scenario asks for it.
"""

import copy
import json
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import flight_app

APP = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------------------------
# Seeded state. These are the facts; every number in every notice below is derived from them, so a
# notice cannot be "adequate" by luck.
# ---------------------------------------------------------------------------------------------
TODAY = date(2026, 9, 19)
RENEWAL = date(2026, 10, 1)
PRO_CENTS_PER_SEAT = 1200
MEMBERS = ("ana@meridian.test", "ben@meridian.test", "cleo@meridian.test", "dana@meridian.test",
           "raj@meridian.test", "kim@meridian.test", "lee@meridian.test")
STARTER_SEATS = 3
LOSING_ACCESS = MEMBERS[STARTER_SEATS:]
DAYS_REMAINING = 12
CYCLE_DAYS = 30
# Unused days on the seats that are going away, refunded as credit: 4 seats x USD 12 x 12/30.
CREDIT_CENTS = round(len(LOSING_ACCESS) * PRO_CENTS_PER_SEAT * DAYS_REMAINING / CYCLE_DAYS)

WORKSPACES = [
    {"id": "atlas", "name": "Atlas", "projects": 3, "documents": 412, "members": 12,
     "status": "active"},
    {"id": "beacon", "name": "Beacon", "projects": 1, "documents": 38, "members": 4,
     "status": "active"},
]

REGIONS = [
    {"code": "AMER", "revenue_cents": 4_182_000},
    {"code": "EMEA", "revenue_cents": 2_640_000},
    {"code": "APAC", "revenue_cents": 1_905_000},
    {"code": "LATAM", "revenue_cents": 612_000},
]


def _money(cents):
    return f"USD {cents / 100:,.2f}"


def _long_date(value):
    # Written out rather than formatted with %-d, which does not exist on Windows.
    return f"{value.day} {value:%B %Y}"


def _short_date(value):
    return f"{value.day} {value:%b}"


def _names(addresses):
    listed = list(addresses)
    return ", ".join(listed[:-1]) + " and " + listed[-1] if len(listed) > 1 else listed[0]


# ---------------------------------------------------------------------------------------------
# Notices. The server owns every word, so a scenario changes what the administrator is told without
# the page being able to compensate. "full" and "terse" are both complete; "terse" exists to fail
# any evaluator that mistakes length for adequacy. "generic" and "partial" are the defects.
# ---------------------------------------------------------------------------------------------
DOWNGRADE_NOTICES = {
    "full": (
        f"Starter includes {STARTER_SEATS} members and this workspace has {len(MEMBERS)}. "
        f"On {_long_date(RENEWAL)}, {len(LOSING_ACCESS)} members lose access: "
        f"{_names(LOSING_ACCESS)}. Everyone keeps Pro until then. Nothing is charged today and a "
        f"credit of {_money(CREDIT_CENTS)} for their unused days is applied to your next invoice."
    ),
    "terse": (
        f"{_short_date(RENEWAL)}: Starter, {STARTER_SEATS} seats. Drops {_names(LOSING_ACCESS)}. "
        f"Credit {_money(CREDIT_CENTS)}, no charge today."
    ),
    "credit_only": (
        f"A credit of {_money(CREDIT_CENTS)} for unused days will be applied to your next invoice."
    ),
    "generic": "Your plan will be updated.",
}

DELETE_NOTICES = {
    "full": (
        "Deleting Atlas removes 3 projects and 412 documents, and ends access for 12 members. "
        "Atlas stays in Recently deleted for 30 days, where any administrator can restore it. "
        "After 30 days it cannot be recovered."
    ),
    "terse": "Atlas: 3 projects, 412 documents, 12 members. Restorable for 30 days.",
    "generic": "This action cannot be undone.",
}

FRESHNESS_NOTICES = {
    "full": (
        "Data is complete through 18 September, 23:00 UTC — 19 hours old. EMEA is missing because "
        "its feed failed at 04:10 UTC, so the totals below cover AMER, APAC and LATAM only."
    ),
    "terse": "Through 18 Sep 23:00 UTC. EMEA feed down, excluded from these totals.",
    "current": "Data is complete through 4 minutes ago. All four regions are included.",
    "generic": "",
}

# Behavioural parameters only. A scenario name is never sent to the browser.
DEFAULTS = {
    "downgrade_notice": "full",
    "delete_notice": "full",
    "freshness_notice": "full",
    "freshness_badge": "19 hours old",
    "effective": "renewal",       # or "today": the schedule the page promises is not the one applied
    "restore_fails": False,       # the page promises 30 days of recovery; the backend hard-deletes
    "refresh_delay_ms": 60,
    "refresh_progress": "Refreshing revenue…",
    "stale": True,                # EMEA missing and the extract 19 hours old
    "clip_delete": False,
    "preview_delay_ms": 40,
}
# The only flags the page is allowed to read. Everything else stays server-side, so page text
# describes the product rather than the experiment.
PRESENTATION = ("refresh_progress", "clip_delete")

SCENARIOS = {
    "none": {},
    # Defects the evaluators are expected to detect.
    "silent_downgrade": {"downgrade_notice": "generic"},
    "hidden_seat_loss": {"downgrade_notice": "credit_only"},
    "schedule_claim_false": {"effective": "today"},
    "generic_delete_warning": {"delete_notice": "generic"},
    "false_restore_promise": {"restore_fails": True},
    "clipped_delete_button": {"clip_delete": True},
    "stale_without_caveat": {"freshness_notice": "generic", "freshness_badge": "Updated just now"},
    "missing_refresh_feedback": {"refresh_delay_ms": 2600, "refresh_progress": ""},
    # Controls. Each one is adjacent to a defect above and must not be reported.
    "terse_downgrade_notice": {"downgrade_notice": "terse"},
    "terse_delete_warning": {"delete_notice": "terse"},
    "terse_freshness_caveat": {"freshness_notice": "terse"},
    "current_complete_data": {"stale": False, "freshness_notice": "current",
                              "freshness_badge": "Updated 4 minutes ago"},
    "slow_refresh_with_feedback": {"refresh_delay_ms": 2600},
}


class AdminApplication:
    """State and behaviour for one running copy of the administration console."""

    def __init__(self, fault="none"):
        if fault not in SCENARIOS:
            raise ValueError(f"Unknown scenario {fault!r}; choose from {sorted(SCENARIOS)}")
        self.fault = fault
        self.flags = {**DEFAULTS, **SCENARIOS[fault]}
        self.lock = threading.Lock()
        self.reset()

    # -- lifecycle ---------------------------------------------------------------------------
    def reset(self):
        with self.lock:
            self.workspaces = copy.deepcopy(WORKSPACES)
            self.plan_changes = []
            self.queries = []

    def presentation(self):
        return {name: self.flags[name] for name in PRESENTATION}

    # -- billing -----------------------------------------------------------------------------
    def preview(self):
        time.sleep(self.flags["preview_delay_ms"] / 1000)
        return {
            "current_plan": "Pro",
            "current_seat_cents": PRO_CENTS_PER_SEAT,
            "members": len(MEMBERS),
            "target_plan": "Starter",
            "target_seats": STARTER_SEATS,
            "effective_date": RENEWAL.isoformat(),
            "credit_cents": CREDIT_CENTS,
            "charge_today_cents": 0,
            "notice": DOWNGRADE_NOTICES[self.flags["downgrade_notice"]],
        }

    def change_plan(self):
        """Schedule the downgrade and record what was *actually* applied, not what was promised."""
        applied = RENEWAL if self.flags["effective"] == "renewal" else TODAY
        record = {
            "plan": "Starter",
            "effective_date": applied.isoformat(),
            "promised_effective_date": RENEWAL.isoformat(),
            "seats_removed": len(LOSING_ACCESS),
            "members_removed": list(LOSING_ACCESS),
            "credit_cents": CREDIT_CENTS,
            "currency": "USD",
        }
        with self.lock:
            record["id"] = f"CHG-{len(self.plan_changes) + 1}"
            self.plan_changes.append(record)
        return record

    # -- workspaces --------------------------------------------------------------------------
    def workspace(self, identifier):
        return next((w for w in self.workspaces if w["id"] == identifier), None)

    def listing(self):
        with self.lock:
            active = [copy.deepcopy(w) for w in self.workspaces if w["status"] == "active"]
            deleted = [copy.deepcopy(w) for w in self.workspaces if w["status"] == "deleted"]
        if self.flags["delete_notice"] == "generic":
            # A product that warns only "this cannot be undone" is one that never worked out what
            # would be lost. Leaving the counts in the payload would let the page disclose the blast
            # radius in its summary table while the notice stayed silent, which is not the defect
            # this scenario is meant to seed.
            for workspace in active + deleted:
                for field in ("projects", "documents", "members"):
                    workspace.pop(field, None)
        return {"active": active, "deleted": deleted,
                "notice": DELETE_NOTICES[self.flags["delete_notice"]]}

    def delete_workspace(self, identifier):
        with self.lock:
            target = self.workspace(identifier)
            if target is None or target["status"] != "active":
                return 404, {"error": "No such active workspace"}
            target["status"] = "deleted"
            # The page says Recently deleted holds it for 30 days, and it is listed there either
            # way. Under this scenario the record behind it is already gone, so the promise only
            # breaks for someone who acts on it.
            target["purged"] = bool(self.flags["restore_fails"])
            return 200, {"id": identifier, "status": "deleted", "recoverable": True}

    def restore_workspace(self, identifier):
        with self.lock:
            target = self.workspace(identifier)
            if target is None or target["status"] != "deleted":
                return 410, {"error": "That workspace is no longer recoverable"}
            if target.get("purged"):
                return 410, {"error": "That workspace is no longer recoverable"}
            target["status"] = "active"
            target["purged"] = False
            return 200, copy.deepcopy(target)

    # -- insights ----------------------------------------------------------------------------
    def revenue(self, window):
        started = time.perf_counter()
        time.sleep(self.flags["refresh_delay_ms"] / 1000)
        included = [r for r in REGIONS if not (self.flags["stale"] and r["code"] == "EMEA")]
        with self.lock:
            # One record per window rather than one per click. Refreshing twice is ordinary
            # behaviour, and an acceptance contract that refuses leftovers would otherwise read the
            # second refresh as state from a previous run.
            record = next((q for q in self.queries if q["window"] == window), None)
            if record is None:
                record = {"id": f"QRY-{len(self.queries) + 1}", "window": window, "requests": 0}
                self.queries.append(record)
            record.update({
                "regions_included": [r["code"] for r in included],
                "regions_expected": [r["code"] for r in REGIONS],
                "complete": len(included) == len(REGIONS),
                "extract_age_minutes": 19 * 60 if self.flags["stale"] else 4,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "requests": record["requests"] + 1,
            })
        return {
            "window": window,
            "regions": included,
            "total_cents": sum(r["revenue_cents"] for r in included),
            "badge": self.flags["freshness_badge"],
            "notice": FRESHNESS_NOTICES[self.flags["freshness_notice"]],
        }

    # -- verifier snapshots ------------------------------------------------------------------
    def snapshot(self, name):
        with self.lock:
            if name == "plan-changes":
                return copy.deepcopy(self.plan_changes)
            if name == "queries":
                return copy.deepcopy(self.queries)
            if name.startswith("queries:"):
                # A verifier for one window only. A journey that declares "the last seven days"
                # should be certified on whether that query really ran, not on whether the actor
                # also looked at another window on the way, which is not a failure of the journey.
                window = name.split(":", 1)[1]
                return [copy.deepcopy(q) for q in self.queries if q["window"] == window]
            return [copy.deepcopy(w) for w in self.workspaces]


def build_handler(application, assets):
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
            path = self.path.split("?")[0]
            if not self.permitted():
                self.reply(403, {"error": "Loopback fixture only"})
            elif path == "/favicon.ico":
                self.reply(200, b"", "image/x-icon")
            elif path == "/admin.js":
                self.reply(200, assets["js"], "application/javascript; charset=utf-8")
            elif path == "/api/flags":
                self.reply(200, application.presentation())
            elif path == "/api/workspaces":
                self.reply(200, application.listing())
            elif path.startswith("/__test__/queries/"):
                self.reply(200, application.snapshot("queries:" + path.rsplit("/", 1)[-1]))
            elif path.startswith("/__test__/"):
                self.reply(200, application.snapshot(path.rsplit("/", 1)[-1]))
            elif path in ("/", "/billing", "/billing/review", "/billing/scheduled", "/workspaces",
                          "/workspaces/deleted", "/insights"):
                # One document for every route: the page is a persistent dispatcher, so a control
                # that vanishes between two states is a real transient and not a re-render.
                self.reply(200, assets["html"], "text/html; charset=utf-8")
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
            if path == "/api/plan/preview":
                self.reply(200, application.preview())
            elif path == "/api/plan/change":
                self.reply(200, application.change_plan())
            elif path == "/api/workspace/delete":
                status, body = application.delete_workspace(payload.get("id", ""))
                self.reply(status, body)
            elif path == "/api/workspace/restore":
                status, body = application.restore_workspace(payload.get("id", ""))
                self.reply(status, body)
            elif path == "/api/insights/revenue":
                self.reply(200, application.revenue(payload.get("window", "last_30_days")))
            elif path == "/__test__/reset":
                application.reset()
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "Unknown fixture route"})

    return Handler


def build(*, port=0, fault="none"):
    application = AdminApplication(fault)
    assets = {"html": (APP / "admin.html").read_bytes(), "js": (APP / "admin.js").read_bytes()}
    flight_app.refuse_taken_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", port), build_handler(application, assets))
    server.application = application
    return server
