"""A real LangChain agent for the agent-evaluation demo.

It runs against whatever OpenAI-compatible endpoint the local ``.env`` configures, and answers
from its tools rather than from the model's own memory. The order records are synthetic.
"""

import json
import os

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

ORDERS = {
    "A-1001": {"order_id": "A-1001", "item": "Noise-cancelling headphones",
               "amount_usd": 249.00, "age_days": 12, "category": "electronics"},
    "A-2002": {"order_id": "A-2002", "item": "Espresso beans, 1kg",
               "amount_usd": 32.50, "age_days": 61, "category": "grocery"},
}

POLICIES = {
    "electronics": "Electronics may be refunded within 30 days of purchase.",
    "grocery": "Perishable grocery items are final sale and cannot be refunded.",
}


@tool
def lookup_order(order_id: str) -> str:
    """Look up one synthetic order by its identifier."""
    order = ORDERS.get(order_id.strip().upper())
    if not order:
        return json.dumps({"error": "no such order"})
    return json.dumps(order)


@tool
def refund_policy(category: str) -> str:
    """Return the refund policy text for one product category."""
    return POLICIES.get(category.strip().lower(), "No specific policy; escalate to a human agent.")


SYSTEM_PROMPT = (
    "You are a support agent. Always call lookup_order first to get the order's category and age, "
    "then call refund_policy for that category. Decide strictly from the tool results, never from "
    "memory. End your reply with a final line that is exactly 'VERDICT: REFUNDABLE' or "
    "'VERDICT: NOT_REFUNDABLE', preceded by one short sentence citing the order age and the "
    "policy window."
)


def build():
    """Build the compiled agent. Credentials are read from the environment at call time."""
    model = ChatOpenAI(
        model=os.environ.get("TEXT_MODEL", "gpt-5.6-luna"),
        base_url=os.environ["TEXT_MODEL_BASE_URL"],
        api_key=os.environ["TEXT_MODEL_API_KEY"],
        timeout=60,
    )
    return create_agent(model=model, tools=[lookup_order, refund_policy],
                        system_prompt=SYSTEM_PROMPT)


graph = build
