"""A deliberately non-deterministic agent for judging intent rather than output text.

Two personas share the same tools, the same synthetic facts and the same task. Both look up the
booking, both report the situation accurately, and both call exactly the same tools in the same
order. No string comparison can separate them, because they never emit a fixed token and their
wording changes on every run.

What separates them is intent: ``graph`` is a warm concierge who acknowledges the disruption and
offers the guest a real choice, while ``blunt_graph`` is accurate and useless to a distressed
person. Deciding which is acceptable is a judgement about communication quality, so the
evaluations that drive this module use blocking ``communication_quality`` judges.
"""

import json
import os

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

BOOKINGS = {
    "BK-7741": {
        "booking_id": "BK-7741",
        "guest": "R. Mehta",
        "room_type": "Garden suite, king bed",
        "nights": 3,
        "status": "overbooked",
        "note": "The property sold the last garden suite; the room is not available on arrival.",
        "occasion": "wedding anniversary",
    },
}

AVAILABILITY = {
    "BK-7741": [
        {"room_type": "Courtyard double, king bed", "rate_difference_usd": 0,
         "note": "Same bed, smaller window, available for all three nights."},
        {"room_type": "Garden suite", "rate_difference_usd": 0,
         "note": "Free from the second night onward; first night would be the courtyard double."},
    ],
}


@tool
def lookup_booking(booking_id: str) -> str:
    """Look up one synthetic booking and its current status."""
    booking = BOOKINGS.get(booking_id.strip().upper())
    if not booking:
        return json.dumps({"error": "no such booking"})
    return json.dumps(booking)


@tool
def room_availability(booking_id: str) -> str:
    """List the alternative rooms that can actually be offered for a booking."""
    options = AVAILABILITY.get(booking_id.strip().upper())
    if options is None:
        return json.dumps({"error": "no availability record"})
    return json.dumps(options)


_GROUNDING = (
    "Always call lookup_booking first, then room_availability for the same booking. Every fact you "
    "state must come from those tool results; never invent compensation, upgrades, refunds or "
    "policies that the tools did not return. Write in continuous prose. Do not use bullet points, "
    "headings, or any fixed closing formula, and vary your wording naturally."
)

WARM_PROMPT = (
    "You are a hotel concierge writing directly to a guest whose room is unavailable on arrival. "
    "Open by acknowledging what this disruption actually means for them before you discuss "
    "logistics. Be warm, specific and human rather than formulaic, take ownership on behalf of the "
    "property, and lay out the real alternatives so the guest can choose between them. Close by "
    "inviting their preference. " + _GROUNDING
)

BLUNT_PROMPT = (
    "You are a booking system operator. Report the booking status and the available alternatives "
    "as briefly as possible. Do not apologise, do not acknowledge any inconvenience, and do not "
    "ask the guest what they would prefer. State only the facts. " + _GROUNDING
)


def _model():
    return ChatOpenAI(
        model=os.environ.get("TEXT_MODEL", "gpt-5.6-luna"),
        base_url=os.environ["TEXT_MODEL_BASE_URL"],
        api_key=os.environ["TEXT_MODEL_API_KEY"],
        timeout=90,
    )


def build(system_prompt=WARM_PROMPT):
    """Build the compiled agent. Credentials are read from the environment at call time."""
    return create_agent(model=_model(), tools=[lookup_booking, room_availability],
                        system_prompt=system_prompt)


def build_blunt():
    """The same agent and the same facts, stripped of any care for the guest."""
    return build(BLUNT_PROMPT)


graph = build
blunt_graph = build_blunt
