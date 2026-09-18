"""Jev chooses an observed action. Code owns execution."""

__all__ = ["Agent", "Browser"]


def __getattr__(name):
    # Configure an owned harness before importing modules that capture its environment.
    if name == "Agent":
        from .agent import Agent
        return Agent
    if name == "Browser":
        from .browser import Browser
        return Browser
    raise AttributeError(name)
