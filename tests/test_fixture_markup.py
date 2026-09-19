"""Markup guards for the two demo fixtures.

The fixtures in examples/ are what every calibration number is measured against. Their
visual design is free to change; the properties below are not, because the observer
depends on them:

  * journey literals must stay in the page, unsplit, or a declared check can never match
  * control labels must stay a single text node, because the accessible name joins child
    nodes with a space
  * only two clipping rules may exist, and both are scenario-driven faults
  * nothing may be sticky or fixed, because occlusion is measured by hit-testing
  * the flight page must not be wrapped in a form, because the staleness guard hashes the
    nearest form/li/row scope
  * no page may ship a loading affordance of its own, because a fault works by blanking
    the only progress message

These are cheap to check without a browser, and this file exists because a redesign broke
none of the tests that existed at the time.
"""

from __future__ import annotations

import pathlib
import re

import pytest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"
FLIGHT = EXAMPLES / "flight.html"
SUBSCRIPTION = EXAMPLES / "subscription.html"
FLIGHT_JS = EXAMPLES / "flight.js"
ADMIN = EXAMPLES / "admin.html"
ADMIN_JS = EXAMPLES / "admin.js"

FLIGHT_LITERALS = [
    "Available flights",
    "Passenger details",
    "Review and confirm",
    "Booking confirmed",
    "Selected fare",
    "Checkout total",
    "Reference:",
]

SUBSCRIPTION_LITERALS = [
    "Choose a plan",
    "Workspace details",
    "Review subscription",
    "Confirmation",
    "Selected plan:",
    "Checkout total:",
    "per month",
]

# Accessible names the journeys address controls by. Each must appear as the complete
# text of one element, or as an aria-label, with no interior markup.
FLIGHT_CONTROLS = [
    "Search flights",
    "Continue to review",
    "Back to results",
    "Back to passenger details",
    "Confirm booking",
    "Passenger full name",
]

SUBSCRIPTION_CONTROLS = [
    "Check workspace availability",
    "Start sandbox subscription",
    "Accept price change and start subscription",
    "Workspace name",
    "Admin email",
]

ADMIN_LITERALS = [
    "Plan and billing",
    "Review plan change",
    "Downgrade scheduled",
    "Change reference",
    "Delete workspace",
    "Recently deleted",
    "Revenue by region",
    "Total",
]

ADMIN_CONTROLS = [
    "Switch to Starter",
    "Confirm downgrade",
    "Keep Pro",
    "Delete workspace",
    "Refresh revenue",
]

ALLOWED_CLIPS = {
    FLIGHT: [".actions.constrained"],
    SUBSCRIPTION: ["#subscribe-frame.clipped"],
    ADMIN: ["#delete-frame.clipped"],
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def stylesheet(source: str) -> str:
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", source, re.S)
    assert blocks, "fixture has no stylesheet"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.S)


def rules(css: str) -> list[tuple[str, str]]:
    """Return (selector, body) for every rule, ignoring at-rule preludes."""
    out: list[tuple[str, str]] = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = " ".join(match.group(1).split())
        if selector.startswith("@"):
            selector = selector.split("{")[-1].strip()
        out.append((selector, match.group(2)))
    return out


def body_without_comments(source: str) -> str:
    """Markup and inline script, with the stylesheet and every comment removed.

    Several journey literals are written by the inline script rather than by static
    markup, so the script stays in; only comments go, because a design note that happens
    to quote a literal must not stand in for the literal itself.
    """
    body = re.sub(r"<style[^>]*>.*?</style>", "", source, flags=re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return re.sub(r"/\*.*?\*/", "", body, flags=re.S)


def rendered_sources(path: pathlib.Path) -> str:
    """Everything that can put text on the page: the markup, the inline script, and for
    the flight fixture the external script that renders most of its views."""
    parts = [body_without_comments(read(path))]
    if path == FLIGHT:
        parts.append(re.sub(r"/\*.*?\*/", "", read(FLIGHT_JS), flags=re.S))
    if path == ADMIN:
        parts.append(re.sub(r"/\*.*?\*/", "", read(ADMIN_JS), flags=re.S))
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("path", "literals"),
    [(FLIGHT, FLIGHT_LITERALS), (SUBSCRIPTION, SUBSCRIPTION_LITERALS), (ADMIN, ADMIN_LITERALS)],
    ids=["flight", "subscription", "admin"],
)
def test_journey_literals_present_and_unsplit(path, literals):
    body = rendered_sources(path)
    for literal in literals:
        assert literal in body, f"{path.name} lost the journey literal {literal!r}"


@pytest.mark.parametrize(
    ("path", "controls"),
    [(FLIGHT, FLIGHT_CONTROLS), (SUBSCRIPTION, SUBSCRIPTION_CONTROLS), (ADMIN, ADMIN_CONTROLS)],
    ids=["flight", "subscription", "admin"],
)
def test_control_names_are_single_text_nodes(path, controls):
    body = rendered_sources(path)
    for name in controls:
        candidates = (
            f'aria-label="{name}"',  # pinned name, wins over content
            f">{name}<",              # complete text of one element
            f"'{name}'",              # assigned as one string by the script
            f'"{name}"',
        )
        assert any(form in body for form in candidates), (
            f"{path.name}: control name {name!r} is missing or was split across elements; "
            "the accessible name joins child nodes with a space, so interior markup "
            "changes the name the journey addresses"
        )


def test_flight_select_button_label_is_built_as_one_text_node():
    source = read(FLIGHT_JS)
    assert "Select ${offer.cabin} fare" in source or "Select ' + offer.cabin" in source, (
        "the select-fare label must be produced as a single string assigned to one text "
        "node; splitting it across spans changes the accessible name"
    )
    assert "button.append(" not in source, (
        "appending children to the select button would split its accessible name"
    )


@pytest.mark.parametrize("path", [FLIGHT, SUBSCRIPTION, ADMIN], ids=["flight", "subscription", "admin"])
def test_only_declared_clipping_rules_exist(path):
    allowed = ALLOWED_CLIPS[path]
    found = []
    for selector, decls in rules(stylesheet(read(path))):
        if re.search(r"overflow(-x|-y)?\s*:\s*(hidden|scroll|auto|clip)", decls):
            found.append(selector)
    unexpected = [s for s in found if s not in allowed]
    assert not unexpected, (
        f"{path.name} introduced clipping outside the declared fault rules: {unexpected}. "
        "Clipping ancestors are reported as findings, so a decorative one is a false "
        "positive in every run."
    )
    for selector in allowed:
        assert selector in found, f"{path.name} lost the deliberate clipping rule {selector}"


@pytest.mark.parametrize("path", [FLIGHT, SUBSCRIPTION, ADMIN], ids=["flight", "subscription", "admin"])
def test_no_sticky_or_fixed_positioning(path):
    css = stylesheet(read(path))
    offenders = [
        selector
        for selector, decls in rules(css)
        if re.search(r"position\s*:\s*(sticky|fixed)", decls)
    ]
    assert not offenders, (
        f"{path.name} uses sticky or fixed positioning ({offenders}); occlusion is measured "
        "by hit-testing each control's centre, so a floating layer becomes a finding"
    )


def test_flight_page_is_not_wrapped_in_a_form():
    assert "<form" not in body_without_comments(read(FLIGHT)).lower(), (
        "the staleness guard hashes the nearest form/li/row scope; a page-level form makes "
        "every control's guard the whole page and every observation looks stale"
    )


@pytest.mark.parametrize("path", [FLIGHT, SUBSCRIPTION, ADMIN], ids=["flight", "subscription", "admin"])
def test_no_built_in_loading_affordances(path):
    source = body_without_comments(read(path)).lower() + stylesheet(read(path)).lower()
    for token in ("<progress", 'role="progressbar"', "spinner", "skeleton", "cursor:wait"):
        assert token not in source, (
            f"{path.name} ships a loading affordance ({token}); the missing-feedback fault "
            "works by blanking the page's only progress message"
        )


def test_flight_status_region_is_present_and_singular():
    source = read(FLIGHT)
    assert source.count('id="search-status"') == 1
    assert 'role="status"' in source and 'aria-live="polite"' in source


def test_admin_frame_is_styled_by_id_only():
    css = stylesheet(read(ADMIN))
    for selector, _ in rules(css):
        if "delete-frame" in selector:
            assert selector.startswith("#delete-frame"), (
                f"{selector!r} would be wiped: render() assigns className outright, so the "
                "frame may only be styled by id"
            )


def test_subscription_frame_is_styled_by_id_only():
    css = stylesheet(read(SUBSCRIPTION))
    for selector, _ in rules(css):
        if "subscribe-frame" in selector:
            assert selector.startswith("#subscribe-frame"), (
                f"{selector!r} would be wiped: render() assigns className outright, so the "
                "frame may only be styled by id"
            )


# -- the fixture must never be served onto a port somebody else already holds ------------------


def test_serving_a_fixture_onto_an_occupied_port_is_refused():
    """A leftover faulted server once turned a whole calibration sweep into measurements of it.

    Windows honours SO_REUSEADDR, so the bind succeeds and the requests silently go elsewhere.
    Every example application must refuse rather than measure another process's app.
    """
    import socket

    from examples import admin_app, flight_app, subscription_app

    for build in (flight_app.build, subscription_app.build, admin_app.build):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(8)
        port = holder.getsockname()[1]
        try:
            with pytest.raises(OSError) as raised:
                build(port=port)
            assert "already listening" in str(raised.value)
        finally:
            holder.close()


def test_an_unused_port_still_serves():
    from examples import flight_app

    server = flight_app.build(port=0)
    try:
        assert server.server_address[1] > 0
    finally:
        server.server_close()
