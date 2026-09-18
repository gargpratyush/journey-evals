"""Checkout pricing for the sandbox booking application.

This module is application source. Everything under this directory is the code
under test; the harness, oracle and evaluators live outside it.
"""

SELECTED_CENTS = 10000


def quote():
    """Return the checkout quote for the fare the traveller selected."""
    return {"cents": SELECTED_CENTS, "disclosure": ""}
