"""Jev chooses an observed action. Code owns execution."""

__all__ = [
    "Agent", "AgentEvalSpec", "Browser", "Coverage", "Evaluation", "Finding", "JourneySpec",
    "Observation", "RunResult", "WindowBinding", "run_agent_eval", "run_journey",
]

_CONTRACTS = {"Coverage", "Finding", "JourneySpec"}
_FRAMEWORK = {"Evaluation", "Observation", "RunResult", "WindowBinding"}


def __getattr__(name):
    # Configure an owned harness before importing modules that capture its environment.
    if name == "Agent":
        from jev_ultrafast.agent import Agent
        return Agent
    if name == "Browser":
        from jev_ultrafast.browser import Browser
        return Browser
    if name == "run_journey":
        from .runner import run_journey
        return run_journey
    if name in {"AgentEvalSpec", "run_agent_eval"}:
        from . import agent_evals
        return getattr(agent_evals, name)
    if name in _CONTRACTS:
        from . import contracts
        return getattr(contracts, name)
    if name in _FRAMEWORK:
        from . import framework
        return getattr(framework, name)
    raise AttributeError(name)
