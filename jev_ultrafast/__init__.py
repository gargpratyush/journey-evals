"""Jev Ultrafast: the imported browser agent. Jev chooses an observed action; code executes it.

Imported from browser-use/jev-ultrafast and kept under its own name. journey-evals is the
evaluation product built around this agent, not a rename of it, so this package keeps the
module paths, symbol names and behaviour it arrived with.
"""

__all__ = ["Agent", "Browser", "load_environment"]


def __getattr__(name):
    # Configure an owned harness before importing modules that capture its environment.
    if name == "Agent":
        from .agent import Agent
        return Agent
    if name == "Browser":
        from .browser import Browser
        return Browser
    if name == "load_environment":
        from .demo import load_environment
        return load_environment
    raise AttributeError(name)
