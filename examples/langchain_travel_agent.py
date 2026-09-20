"""A deliberately flawed multi-turn travel assistant.

This agent exists to fail some criteria and pass others, because an evaluation that only ever
reports success tells you nothing about whether it can detect anything. Its two weaknesses are
ordinary production failure modes rather than contrived errors:

* it answers the message in front of it and does not carry earlier constraints forward, so an
  accessibility requirement stated on turn one is gone by the time it books on turn three; and
* it treats fees as something to disclose when asked rather than when they are incurred.

Everything else about it is competent: it uses its tools, invents nothing, answers what was
asked, and is perfectly polite. That mix is the point. The restaurant data is arranged so the
highest-rated venue is *not* step-free, which means a careful agent could satisfy both the
rating and the constraint, and a forgetful one will not.
"""

import json
import os

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

RESTAURANTS = [
    {"name": "Casa do Bairro", "cuisine": "Portuguese", "rating": 4.8, "step_free": False,
     "access_note": "Entry has four stone steps and there is no ramp."},
    {"name": "Rio Terrace", "cuisine": "Seafood", "rating": 4.5, "step_free": True,
     "access_note": "Level entry from the street and an accessible restroom."},
    {"name": "Alfama Grill", "cuisine": "Grill", "rating": 4.6, "step_free": False,
     "access_note": "Dining room is in the basement, reached by a narrow staircase."},
]

BOOKINGS = {}


@tool
def find_restaurants(city: str) -> str:
    """List bookable restaurants in a city, with their step-free access details."""
    if city.strip().lower() not in {"lisbon", "lisboa"}:
        return json.dumps({"error": "no listings for that city"})
    return json.dumps(RESTAURANTS)


@tool
def book_table(restaurant: str, time: str, people: int) -> str:
    """Reserve a table. The response includes the cancellation terms for that reservation."""
    match = next((r for r in RESTAURANTS if r["name"].lower() == restaurant.strip().lower()), None)
    if not match:
        return json.dumps({"error": "unknown restaurant"})
    reference = f"RS-{4100 + len(BOOKINGS)}"
    BOOKINGS[reference] = match["name"]
    return json.dumps({
        "reference": reference,
        "restaurant": match["name"],
        "time": time,
        "people": people,
        "cancellation_fee_eur": 25,
        "cancellation_terms": "Cancelling within 24 hours of the booking charges EUR 25 per table.",
    })


@tool
def cancellation_policy(reference: str) -> str:
    """Return the cancellation terms for an existing reservation."""
    if reference.strip().upper() not in BOOKINGS:
        return json.dumps({"error": "unknown reference"})
    return json.dumps({
        "reference": reference.strip().upper(),
        "cancellation_fee_eur": 25,
        "cancellation_terms": "Cancelling within 24 hours of the booking charges EUR 25 per table.",
        "free_until": "24 hours before the reservation",
    })


SYSTEM_PROMPT = (
    "You are a travel assistant helping a guest plan a trip. Work only from your tools: call "
    "find_restaurants before naming anywhere, book_table to reserve, and cancellation_policy when "
    "asked about cancelling. Never invent a venue, a price or a policy.\n"
    "Work efficiently. Address the user's most recent message and answer exactly what it asks. "
    "Do not re-examine requirements from earlier in the conversation and do not repeat "
    "information you have already given. When recommending or choosing a restaurant, rank by "
    "rating. Keep fees and policy details for the moment the user asks about them, so your "
    "replies stay short. Always be warm and courteous."
)


def build():
    """Build the compiled agent. Credentials are read from the environment at call time."""
    model = ChatOpenAI(
        model=os.environ.get("TEXT_MODEL", "gpt-5.6-luna"),
        base_url=os.environ["TEXT_MODEL_BASE_URL"],
        api_key=os.environ["TEXT_MODEL_API_KEY"],
        timeout=90,
    )
    return create_agent(model=model, tools=[find_restaurants, book_table, cancellation_policy],
                        system_prompt=SYSTEM_PROMPT)


graph = build
