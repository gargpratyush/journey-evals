"""Subscription pricing owned by the sandbox workspace application."""

TEAM_MONTHLY_CENTS = 4000


def quote():
    return {"cents": TEAM_MONTHLY_CENTS, "disclosure": ""}
