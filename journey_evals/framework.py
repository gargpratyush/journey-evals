"""Shared contracts and evidence windows proven by both feasibility applications."""

from dataclasses import dataclass
from typing import Literal, TypedDict


class Observation(TypedDict):
    title: str
    text: str
    actions: list[dict]


class Evaluation(TypedDict):
    outcome: Literal["clean", "defect", "unknown"]
    source: str
    evidence_id: str
    category: str
    advisory: bool
    requires_review: bool


class RunResult(TypedDict, total=False):
    status: str
    evidence_complete: bool
    independent_verifier: dict
    findings: dict[str, Evaluation]
    text_entry: str
    text_calls: list[dict]


@dataclass(frozen=True)
class WindowBinding:
    """How one application's journey names the actions its evidence windows anchor on.

    This is the feasibility-era binding, not the product's journey contract. The versioned,
    user-authored specification lives in ``contracts.JourneySpec``.
    """

    goal: str
    loading_action: str
    loading_operation: str
    effect_actions: tuple[str, ...]
    effect_action: str
    expected_effect: str


def feedback_between(events, action):
    """Locate one action's evidence window and the feedback text visible during it.

    The window opens at the journalled *attempt*, not at the recorded execution: a page can write
    its in-progress text synchronously on click, which the collector emits before the executed
    action reaches the journal. Collection then runs until the next real action, ignoring waits,
    because a wait is the actor sitting out the response rather than the next step of the journey.
    """
    executed = next(
        (
            i
            for i, event in enumerate(events)
            if event["kind"] == "action_executed"
            and event["data"].get("entry", {}).get("action") == action
        ),
        None,
    )
    start = (
        next(
            (
                i
                for i, event in enumerate(events)
                if event["kind"] == "action_attempt"
                and event["data"].get("action", {}).get("label") == action
            ),
            None,
        )
        if executed is not None
        else None
    )
    texts = []
    end = len(events)
    if start is not None:
        for index, event in enumerate(events[start + 1 :], start + 1):
            closing = (
                event["kind"] == "action_executed"
                and index != executed
                and event["data"].get("entry", {}).get("kind") != "wait"
            )
            if closing:
                end = index
                break
            if event["kind"] == "browser_event" and event["data"].get("kind") == "feedback":
                texts.extend(
                    region["text"]
                    for region in event["data"]["data"]["regions"]
                    if region.get("visible") and (region.get("text") or "").strip()
                )
    return {"start": start, "executed": executed, "end": end,
            "texts": list(dict.fromkeys(texts))}


def loading_window(
    events,
    *,
    evidence_complete,
    response_ms,
    action,
    operation,
    threshold_ms,
    interval_source,
    evidence_id,
):
    located = feedback_between(events, action)
    start, texts = located["start"], located["texts"]
    return {
        "id": evidence_id,
        "kind": "loading",
        "complete": bool(evidence_complete and start is not None and response_ms is not None),
        "anchored": start is not None,
        "operation": operation,
        "visible_feedback": texts,
        "response_ms": round(response_ms) if response_ms is not None else 0,
        "threshold_ms": threshold_ms,
        "interval_source": interval_source,
    }


def effect_window(
    events,
    *,
    evidence_complete,
    actions,
    action,
    expected_effect,
    settle_ms,
    deadline_ms,
    evidence_id,
):
    index = next(
        (
            i
            for i, event in enumerate(events)
            if event["kind"] == "action_executed"
            and event["data"].get("entry", {}).get("action") in actions
        ),
        None,
    )
    before = after = None
    elapsed = 0.0
    if index is not None:
        before = next((event for event in reversed(events[:index]) if event["kind"] == "observation"), None)
        after = next((event for event in reversed(events) if event["kind"] == "settled_observation"), None)
        if after:
            elapsed = (after["host_monotonic_ns"] - events[index]["host_monotonic_ns"]) / 1e6
    deadline_elapsed = elapsed >= deadline_ms
    return {
        "id": evidence_id,
        "kind": "effect",
        "complete": bool(evidence_complete and before and after and deadline_elapsed),
        "action_acknowledged": index is not None,
        "deadline_elapsed": deadline_elapsed,
        "action": action,
        "expected_effect": expected_effect,
        "before_text": before["data"]["page"]["text"] if before else "",
        "after_text": after["data"]["page"]["text"] if after else "",
        "observed_after_ms": round(elapsed),
        "settle_ms": settle_ms,
    }
