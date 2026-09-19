"""One journey, end to end: owned session, journalled lifecycle, declared checks, one report.

The order here is the product's central claim. Evidence is collected before anything is judged, a
mutation is journalled before its result is observed, the goal is verified by an independent path
that the actor cannot influence, and the top-level result is derived from records that are written
down. Nothing in this module can turn an unobserved state into a pass.

The run executes inside a dedicated worker process because an owned browser session has to be the
first thing the harness sees; ``python -m journey_evals.runner`` is that worker, and ``cli.py``
spawns it.
"""

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from . import evaluation as evaluators
from . import report as reporting
from .contracts import (
    SCHEMA_VERSION,
    UPSTREAM_REVISION,
    ContractError,
    Coverage,
    decide_result,
    exit_code,
    finding_id,
    holds,
    journey_from_dict,
    reads_controls,
    validate_result_basis,
)
from .feasibility import RATE, SLOW_MACHINE_TIMEOUT, load_environment, write_json
from .paths import env_file

TOOL_VERSION = "0.1.0"
# How long the runner is willing to wait past a declared deadline before giving up on settling.
SETTLE_GRACE_MS = 1500
VERIFIER_TIMEOUT = 10 + SLOW_MACHINE_TIMEOUT
# Subresources that are not part of any journey and whose absence is not a product defect.
IGNORED_REQUEST_PATHS = ("/favicon.ico",)


class ConfigurationError(ContractError):
    """Bad input. Nothing has been launched and nothing has been mutated."""


# --- Evidence windows ---------------------------------------------------------------------------
# Both windows are derived from the journal rather than from live variables, so what was judged is
# exactly what a reader can reconstruct from the persisted events.


def _observation_at(observations, sequence):
    return next((o for o in observations if o["sequence"] == sequence), None)


def effect_window(events, observations, check, *, action, executed_sequence, deadline_ms,
                  ready_when=None):
    """The before/after pair bracketing one executed action, with its deadline state recorded.

    The deadline exists so that an absent effect is only called a contradiction once the page has
    had the time the journey allowed it. Observing the declared effect settles the question earlier,
    which is why readiness closes the window as well.
    """
    executed = next(
        (e for e in events if e["kind"] == "action_executed" and e["sequence"] == executed_sequence),
        None,
    )
    if executed is None:
        return {"action_acknowledged": False, "action": action}
    after = [
        o for o in observations
        if o["sequence"] > executed_sequence and o["host_monotonic_ns"] >= executed["host_monotonic_ns"]
    ]
    before = [o for o in observations if o["sequence"] < executed_sequence]
    if not after:
        return {"action_acknowledged": True, "deadline_elapsed": False, "action": action}
    latest = after[-1]
    elapsed_ms = (latest["host_monotonic_ns"] - executed["host_monotonic_ns"]) / 1e6
    ready_observed = bool(ready_when) and any(holds(ready_when, {"observation": o}) for o in after)
    return {
        "action_acknowledged": True,
        "deadline_elapsed": elapsed_ms >= deadline_ms or ready_observed,
        "ready_observed": ready_observed,
        "action": action,
        "before_text": (before[-1]["text"] if before else ""),
        "after_text": latest["text"],
        "observed_after_ms": round(elapsed_ms),
        "deadline_ms": deadline_ms,
        "observation_ids": [o["id"] for o in ([before[-1]] if before else []) + [latest]],
        "id": f"window:effect:{check.id}:{executed_sequence}",
    }


def loading_window(events, observations, check, *, action, executed_sequence, complete):
    """The feedback visible while one operation ran, and how long the user actually waited.

    The interval is measured on the host clock from the executed action to the first observation
    that satisfies the journey's declared readiness condition. When readiness is never observed the
    last observation in the window is used and the interval is a lower bound, which is recorded.
    """
    from .framework import feedback_between

    located = feedback_between(events, action)
    threshold = check.expect.get("threshold_ms", evaluators.DEFAULT_FEEDBACK_THRESHOLD_MS)
    if located["start"] is None or located["executed"] is None:
        return {"id": f"window:loading:{check.id}:{executed_sequence}", "kind": "loading",
                "anchored": False, "complete": False, "visible_feedback": located["texts"],
                "response_ms": None, "threshold_ms": threshold}
    executed_event = events[located["executed"]]
    end_sequence = events[located["end"] - 1]["sequence"] if located["end"] <= len(events) else None
    ready_when = check.expect.get("ready_when")
    candidates = [
        o for o in observations
        if o["host_monotonic_ns"] >= executed_event["host_monotonic_ns"]
        and (end_sequence is None or o["sequence"] <= end_sequence)
    ]
    ready, ready_observed = None, False
    for observation in candidates:
        if ready_when is None or holds(ready_when, {"observation": observation}):
            ready, ready_observed = observation, ready_when is not None
            break
    if ready is None and candidates:
        ready = candidates[-1]
    if ready is None:
        return {"id": f"window:loading:{check.id}:{executed_sequence}", "kind": "loading",
                "anchored": True, "complete": False, "visible_feedback": located["texts"],
                "response_ms": None, "threshold_ms": threshold}
    interval = (ready["host_monotonic_ns"] - executed_event["host_monotonic_ns"]) / 1e6
    return {
        "id": f"window:loading:{check.id}:{executed_sequence}",
        "kind": "loading",
        "anchored": True,
        "complete": bool(complete),
        "operation": check.expect.get("operation"),
        "visible_feedback": located["texts"],
        "response_ms": interval,
        "threshold_ms": threshold,
        "ready_observed": ready_observed,
        "interval_source": (
            "host monotonic clock from the executed action to the first observation satisfying the "
            "declared readiness condition"
            if ready_observed else
            "host monotonic clock from the executed action to the last observation in the window; "
            "readiness was never observed, so the interval is a lower bound"
        ),
        "observation_ids": [o["id"] for o in candidates[:8]],
    }


def responsiveness_window(events, check, *, action, executed_sequence):
    """How long the main thread was blocked while the page responded to one action.

    The browser already reports this: `long-animation-frame` carries `blockingDuration`, the time a
    frame spent unable to answer input. The measurement is taken from the journal, so a reader can
    reconstruct exactly which frames were counted. Nothing here is a judgement — the page either
    stayed inside the declared budget or it did not, so `code_decision` settles it without a model.

    An action whose window contains no long frames at all is responsive by measurement, not unknown:
    the observer is installed before the action and reports every frame over the browser's own
    threshold, so silence is evidence. Only a window with no telemetry *at all* is unknown.
    """
    executed = next(
        (e for e in events if e["kind"] == "action_executed" and e["sequence"] == executed_sequence),
        None,
    )
    if executed is None:
        return {"id": f"window:responsiveness:{check.id}:{executed_sequence}",
                "anchored": False, "action": action}
    following = [
        e for e in events
        if e["sequence"] > executed_sequence and e["kind"] == "browser_event"
        and (e.get("data") or {}).get("kind") in ("timing", "feedback", "watermark", "input")
    ]
    # Stop at the next executed action: a later step's cost is that step's evidence, not this one's.
    limit = next((e["sequence"] for e in events
                  if e["sequence"] > executed_sequence and e["kind"] == "action_executed"), None)
    if limit is not None:
        following = [e for e in following if e["sequence"] < limit]
    frames, saw_timing = [], False
    for event in following:
        payload = (event.get("data") or {}).get("data") or {}
        if (event["data"] or {}).get("kind") != "timing":
            continue
        saw_timing = True
        for frame in payload.get("longFrames") or []:
            frames.append({"blocking_ms": int(frame.get("blockingDuration") or 0),
                           "duration_ms": int(frame.get("duration") or 0),
                           "scripts": [s.get("invoker") for s in (frame.get("scripts") or [])[:2]]})
    budget = check.expect.get("blocking_budget_ms")
    worst = max((f["blocking_ms"] for f in frames), default=0)
    return {
        "id": f"window:responsiveness:{check.id}:{executed_sequence}",
        "anchored": True,
        "telemetry_present": saw_timing,
        "action": action,
        "operation": check.expect.get("operation"),
        "worst_blocking_ms": worst,
        "total_blocking_ms": sum(f["blocking_ms"] for f in frames),
        "long_frames": frames[:8],
        "frame_count": len(frames),
        "blocking_budget_ms": budget,
    }


# --- Independent acceptance ---------------------------------------------------------------------


def _fetch_records(url, timeout):
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "X-Jev-Verifier": "1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixture origin only
        if response.status != 200:
            raise RuntimeError(f"Verifier endpoint returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _records_match(expected, actual):
    remaining = list(actual)
    for wanted in expected:
        index = next(
            (i for i, record in enumerate(remaining)
             if isinstance(record, dict) and all(record.get(k) == v for k, v in wanted.items())),
            None,
        )
        if index is None:
            return False, f"No recorded item matched {json.dumps(wanted, sort_keys=True)}"
        remaining.pop(index)
    if remaining:
        # Everything the journey claimed to create was found, and something else was there too.
        # That is materially different from the journey's record being missing: the usual cause is
        # a previous run against the same application, and accepting it would let one run's
        # leftovers prove the next one succeeded.
        return False, (
            f"{len(remaining)} unexpected recorded item(s) remained; every record this journey "
            f"expected was found, so the application did not start this run from a known state"
        )
    return True, ""


def reset_backend(spec, *, timeout=VERIFIER_TIMEOUT):
    """Return the application to a known state before the run, when the journey declares how.

    A journey's acceptance contract refuses leftover records as proof, which is what stops one
    run's success from being credited to the next. The consequence is that a second run against a
    stateful application fails until that application is put back. Only the journey author knows
    whether that is safe, so this happens only when the journey names the endpoint, and only
    against the journey's own origin.

    It returns what happened rather than raising: failing to reset is worth reporting, but it is
    not itself evidence about the application under test.
    """
    backend = (spec.acceptance or {}).get("backend") or {}
    path = backend.get("reset_path")
    if not path:
        return None
    origin = f"{urlsplit(spec.url).scheme}://{urlsplit(spec.url).netloc}"
    request = urllib.request.Request(origin + path, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Jev-Verifier": "1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - journey origin only
            if response.status != 200:
                return {"state": "failed", "detail": f"reset returned HTTP {response.status}"}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"state": "failed", "detail": f"{type(error).__name__}: {error}"}
    return {"state": "done", "detail": f"reset {path} before the run"}


def verify_goal(spec, observation, *, timeout=VERIFIER_TIMEOUT):
    """Decide whether the journey's declared outcome actually happened.

    This never consults the actor, the evaluator or any model. A backend expectation is fetched by
    the runner process from the journey's own origin, so a page that merely claims success cannot
    satisfy it.
    """
    acceptance = spec.acceptance or {}
    if not acceptance:
        return "unverified", [{"criterion": "acceptance", "state": "not_declared"}]
    details, satisfied = [], True
    text = (observation or {}).get("text") or ""
    url = (observation or {}).get("url") or ""
    controls = (observation or {}).get("evaluation_elements") or []

    def record(criterion, ok, detail):
        nonlocal satisfied
        details.append({"criterion": criterion, "state": "met" if ok else "unmet", "detail": detail})
        satisfied = satisfied and ok

    if "url_path_is" in acceptance:
        actual = urlsplit(url).path or "/"
        record("url_path_is", actual == acceptance["url_path_is"], f"observed {actual!r}")
    for phrase in acceptance.get("text_contains", []):
        record("text_contains", phrase in text, f"{phrase!r}")
    for phrase in acceptance.get("text_absent", []):
        record("text_absent", phrase not in text, f"{phrase!r}")
    for label, expected in (acceptance.get("control_values") or {}).items():
        found = next((c for c in controls if c.get("label") == label), None)
        record("control_values", bool(found) and found.get("value") == expected,
               f"{label!r} observed {None if not found else found.get('value')!r}")
    backend = acceptance.get("backend") or {}
    if backend:
        origin = f"{urlsplit(spec.url).scheme}://{urlsplit(spec.url).netloc}"
        try:
            actual = _fetch_records(origin + backend["path"], timeout)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as error:
            details.append({"criterion": "backend", "state": "unavailable",
                            "detail": f"{type(error).__name__}: {error}"})
            return "unavailable", details
        if not isinstance(actual, list):
            record("backend", False, "verifier endpoint did not return a list of records")
        else:
            ok, detail = _records_match(backend["expect_records"], actual)
            record("backend", ok, detail or f"{len(actual)} recorded item(s) matched exactly")
    return ("verified" if satisfied else "violated"), details


# --- Runtime problems ---------------------------------------------------------------------------


def runtime_findings(events, spec):
    """Problems nobody has to declare: thrown exceptions, failed requests, server errors."""
    expected = tuple(spec.expected_console_errors)
    findings = []

    def add(evaluator, severity, observed, requirement):
        findings.append({
            "id": finding_id(evaluator, json.dumps(observed, sort_keys=True)[:200], requirement),
            "severity": severity, "category": "unexpected_state",
            "title": "The page reported a runtime problem",
            "observed": observed, "expected": {"requirement": requirement},
            "evidence_ids": [], "first_step": 0, "last_step": 0, "occurrences": 1,
            "reproducibility": "single_run", "provenance": "deterministic",
            "confirmation": "verified", "evaluator": evaluator, "evaluator_version": 1,
            "model_signal": {}, "advisory": True,
        })

    for event in events:
        kind, data = event["kind"], event["data"]
        if kind == "console_exception":
            text = data.get("text", "")
            if not any(pattern in text for pattern in expected):
                add("runtime:console_exception", "medium", {"text": text},
                    "The journey should not throw uncaught errors")
        elif kind == "http_failure":
            url = data.get("url", "")
            path = urlsplit(url).path
            if path in IGNORED_REQUEST_PATHS or any(pattern in url for pattern in expected):
                continue
            status = data.get("status", 0)
            add("runtime:http_failure", "high" if status >= 500 else "medium",
                {"url": url, "status": status},
                "Requests made during the journey should not fail")
        elif kind == "network_failure":
            add("runtime:network_failure", "medium", {"error": data.get("error", "")},
                "Requests made during the journey should not fail")
    return evaluators.deduplicate(findings)


# --- The run ------------------------------------------------------------------------------------


class Run:
    """Owns the journal, the observation registry and the coverage state for one journey."""

    def __init__(self, spec, directory, *, blocking_policies=(), secrets=()):
        self.spec = spec
        self.directory = Path(directory)
        self.redactor = reporting.Redactor(secrets=secrets, patterns=spec.redact)
        self.journal = reporting.Journal(self.directory, redactor=self.redactor)
        self.observations = []
        self.coverage = Coverage(spec)
        self.evaluations = []
        self.findings = []
        self.errors = []
        self.usage = {"model_requests": 0, "actor_requests": 0, "evaluator_requests": 0,
                      "input_tokens": 0, "output_tokens": 0, "usd": "0.000000"}
        self.ledger = []
        self.collector = None
        self.pending = None
        self.step = 0
        self.evaluated_final = False
        # Steps the actor called no-ops that this run observed changing the page.
        self.observed_changes = set()

    # -- lifecycle hooks ---------------------------------------------------------------------
    def sink(self, kind, data):
        event = self.journal.append(kind, data)
        record = None
        if kind == "observation":
            record = self.register_observation(event, data.get("phase", "unknown"))
        elif kind == "action_attempt":
            self.pending = {"action": data["action"], "before": self.latest,
                            "attempt_sequence": event["sequence"]}
        elif kind == "action_executed":
            if self.pending is not None:
                self.pending["entry"] = data["entry"]
                self.pending["executed_sequence"] = event["sequence"]
        elif kind in {"action_rejected_stale", "action_uncertain", "observation_failed"}:
            self.errors.append({"stage": kind, "detail": data.get("error_type", ""),
                                "recoverable": kind == "action_rejected_stale"})
        return record

    def register_observation(self, event, phase):
        page = event["data"].get("page") or {}
        record = {
            "id": f"obs-{event['sequence']:06d}",
            "sequence": event["sequence"],
            "host_monotonic_ns": event["host_monotonic_ns"],
            "phase": phase,
            "step_id": f"step-{self.step:03d}",
            "url": page.get("url", ""),
            "title": "",
            "text": page.get("text", ""),
            "actions": page.get("actions", []),
            "evidence_refs": [page["screenshot_file"]] if page.get("screenshot_file") else [],
            "evaluation_elements": [],
            "capabilities": {},
            "telemetry_window": {},
            "journey_facts": self.spec.facts,
            "truncation": {},
            "complete": False,
        }
        # Poll observations only date an effect. Taking a telemetry watermark for each one would
        # cost more than the interval it is trying to measure.
        watermark = None
        if self.collector and phase != "poll":
            try:
                watermark = self.collector.checkpoint(phase)
            except Exception as error:  # a missing watermark is incomplete evidence, never a pass
                self.errors.append({"stage": "telemetry_checkpoint",
                                    "detail": f"{type(error).__name__}: {error}", "recoverable": True})
        if watermark:
            scrubbed = self.redactor.scrub(watermark)
            projection = scrubbed.get("evaluation") or {}
            record.update(
                title=scrubbed.get("title", ""),
                document_id=scrubbed.get("documentId", ""),
                evaluation_elements=projection.get("controls", []),
                capabilities={**(scrubbed.get("capabilities") or {}),
                              "viewport": projection.get("viewport")},
                telemetry_window={"feedback": scrubbed.get("feedback"),
                                  "messages": projection.get("messages", []),
                                  "headings": projection.get("headings", []),
                                  "documents": scrubbed.get("documents", {})},
                truncation={"controls": projection.get("truncated"),
                            "messages": projection.get("messagesTruncated"),
                            "headings": projection.get("headingsTruncated"),
                            "caps": scrubbed.get("caps", {})},
                clock_anchor=scrubbed.get("clock_anchor", {}),
                complete=bool(scrubbed.get("complete")),
            )
        self.observations.append(record)
        return record

    @property
    def latest(self):
        return self.observations[-1] if self.observations else None

    def reconcile(self, history, action_context):
        """Correct the actor's "nothing changed" claim against what this run actually observed.

        The actor decides whether a page changed from a sample taken the instant it acted. A page
        that re-renders a few tens of milliseconds later is recorded as a no-op, and a progress
        check then reads a working step as a stall. The observation taken once the transition
        settled is later and independent, so where the two disagree the observation wins. Only the
        false no-op is corrected: an action the actor already called a change is not re-examined,
        because nothing here can show that a change did *not* happen.
        """
        entry = (action_context or {}).get("entry")
        before = (action_context or {}).get("before")
        if not history or entry is None or before is None:
            return
        # The journalled entry is a frozen copy taken at emit time, when `page_changed` is still
        # unset; the actor fills it in afterwards. Read the live entry, or this never fires.
        live = history[-1]
        if live.get("page_changed") is True:
            return
        after = self.latest
        if after is None or after is before:
            return
        if (after.get("text") or "") != (before.get("text") or "") or \
                (after.get("url") or "") != (before.get("url") or ""):
            self.observed_changes.add(len(history) - 1)

    def reconciled(self, history):
        if not self.observed_changes:
            return history
        return [{**entry, "page_changed": True} if index in self.observed_changes else entry
                for index, entry in enumerate(history or [])]

    # -- evaluation --------------------------------------------------------------------------
    def context_for(self, check, *, observation, history, action_context):
        context = {"observation": observation, "before": action_context.get("before"),
                   "history": self.reconciled(history), "facts": self.spec.facts}
        if check.family == "interaction_correctness" and action_context.get("executed_sequence"):
            context["effect_window"] = effect_window(
                self.journal.events(), self.observations, check,
                action=check.expect.get("action") or action_context["entry"]["action"],
                executed_sequence=action_context["executed_sequence"],
                deadline_ms=check.deadline_ms,
                ready_when=check.expect.get("ready_when"),
            )
        if check.family == "experience_feedback" and action_context.get("executed_sequence"):
            context["loading_window"] = loading_window(
                self.journal.events(), self.observations, check,
                action=check.expect.get("action") or action_context["entry"]["action"],
                executed_sequence=action_context["executed_sequence"],
                complete=bool(observation and observation.get("complete")),
            )
        if check.family == "input_responsiveness" and action_context.get("executed_sequence"):
            context["responsiveness_window"] = responsiveness_window(
                self.journal.events(), check,
                action=check.expect.get("action") or action_context["entry"]["action"],
                executed_sequence=action_context["executed_sequence"],
            )
        return context

    def assess(self, scope, *, observation, history, action_context):
        """Build every applicable subject for one state and settle them in a single request."""
        action_context = action_context or {}
        subjects, checks = [], []
        for check, state in self.applicable_states(scope, observation=observation, history=history,
                                                   action_context=action_context):
            self.coverage.record(check.id, "observed")
            try:
                subject = evaluators.build_subject(
                    check, self.context_for(check, observation=state, history=history,
                                            action_context=action_context)
                )
            except ContractError as error:
                self.errors.append({"stage": "subject", "detail": str(error), "recoverable": False})
                self.coverage.record(check.id, "unknown")
                continue
            subjects.append(subject)
            checks.append(check)
        if not subjects:
            return
        needs_model = any(evaluators.code_decision(s, c) is None for s, c in zip(subjects, checks))
        results = evaluators.evaluate(subjects, checks, ledger=self.ledger)
        if needs_model:
            self.usage["model_requests"] += 1
            self.usage["evaluator_requests"] += 1
        for item in results:
            self.evaluations.append(item)
            state = evaluators.check_state(item)
            self.coverage.record(item["check_id"], state, item["observation_ids"])
            # The journal is the complete record of the run, so a settled check belongs in it
            # alongside the action that provoked it. Nothing reads this kind back; the report is
            # still built from ``self.evaluations``.
            self.journal.append("evaluation", {
                "check_id": item["check_id"], "family": item["family"], "scope": scope,
                "verdict": item["verdict"], "state": state, "step": self.step,
                "requirement": item.get("requirement"), "source": item.get("source"),
                "reason": item.get("reason"), "severity": item.get("severity"),
                "observation_ids": item["observation_ids"],
            })
            if item["verdict"] in evaluators.DEFECT_VERDICTS[item["family"]]:
                self.findings.append(evaluators.to_finding(item, step=self.step))

    def applicability(self, scope, *, observation, history, action_context):
        """A transition check is judged on the action that just executed, not the whole run."""
        entry = (action_context or {}).get("entry")
        return {"observation": observation, "facts": self.spec.facts,
                "history": history if scope == "final" else ([entry] if entry else [])}

    def applicable_states(self, scope, *, observation, history, action_context):
        """Pair each applicable check with the state it should be judged on.

        A transition is two states, not one. The state the actor acted *from* is the only place a
        pre-action requirement can be read -- "before you confirm, the page says what you lose" is
        gone from the page the moment the action lands. Judging solely on the state that follows
        the action silently dropped those checks whenever the actor moved on in a single step, and
        they were then reported as never resolved rather than as a defect. So the after-state is
        preferred, because most transition checks are about an effect, and the before-state is
        consulted only for checks the after-state does not answer.
        """
        candidates = [observation]
        if scope == "transition":
            before = (action_context or {}).get("before")
            if before is not None and before is not observation:
                candidates.append(before)
        paired, seen = [], set()
        for state in candidates:
            applicability = self.applicability(scope, observation=state, history=history,
                                               action_context=action_context)
            for check in self.applicable_checks(scope, applicability):
                if check.id in seen:
                    continue
                seen.add(check.id)
                paired.append((check, state))
        return paired

    def applicable_checks(self, scope, applicability):
        return [check for check in self.spec.checks
                if check.scope == scope and evaluators.applicable(check, applicability)]

    # -- persistence -------------------------------------------------------------------------
    def close(self):
        if self.collector:
            try:
                self.collector.close()
            except Exception as error:
                self.errors.append({"stage": "collector_close", "detail": type(error).__name__,
                                    "recoverable": True})
        self.journal.close()


def _actor_usage(history, decisions, text_calls):
    requests = len(decisions) + len(text_calls)
    tokens = {"input": 0, "output": 0}
    for entry in decisions:
        usage = entry.get("usage") or {}
        tokens["input"] += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
        tokens["output"] += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
    return requests, tokens, len(history)


def _tokens_of(usage):
    return (usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0,
            usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)


def account(run, decisions, text_calls):
    """Price this run from the tokens it actually sent, and say what the price does not cover.

    Only the evaluation model has a published rate here, so the text helper is counted but not
    priced. Reporting its tokens separately keeps ``usd`` from quietly understating a run that
    leaned on a provider this ledger cannot value.
    """
    entries, priced_input, priced_output, helper = [], 0, 0, {"requests": 0, "input": 0, "output": 0}
    for entry in run.ledger:
        tokens = _tokens_of(entry["usage"])
        entries.append({"role": entry["role"], "input_tokens": tokens[0], "output_tokens": tokens[1],
                        "priced": True})
        priced_input += tokens[0]
        priced_output += tokens[1]
    for entry in decisions:
        tokens = _tokens_of(entry.get("usage") or {})
        entries.append({"role": "actor", "input_tokens": tokens[0], "output_tokens": tokens[1],
                        "priced": True})
        priced_input += tokens[0]
        priced_output += tokens[1]
    for entry in text_calls:
        tokens = _tokens_of(entry.get("usage") or {})
        helper["requests"] += 1
        helper["input"] += tokens[0]
        helper["output"] += tokens[1]
        entries.append({"role": "text_helper", "input_tokens": tokens[0],
                        "output_tokens": tokens[1], "priced": False})
    usd = (Decimal(priced_input) + Decimal(priced_output)) * RATE
    write_json(run.directory / "usage.json", {
        "schema_version": 1, "model": evaluators.MODEL, "rate_usd_per_token": str(RATE),
        "priced_input_tokens": priced_input, "priced_output_tokens": priced_output,
        "usd": f"{usd:.6f}", "entries": entries,
        "unpriced": {"provider": "text helper", "reason": "no published rate is configured",
                     **helper},
    })
    return f"{usd:.6f}", helper


def execute(spec, provenance, directory, *, blocking_policies=(), headed=False, secrets=()):
    """Drive one journey and return the persisted RunResult."""
    from jev_ultrafast.agent import Agent
    from jev_ultrafast.browser import Browser

    from .evidence import Collector

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / "events.jsonl"
    if journal.exists() and journal.stat().st_size:
        # The journal is append-only and every measured interval is read back out of it. Writing a
        # second run into the same directory would silently mix two clocks, and the windows would
        # be computed against an action that happened in an earlier run.
        raise ContractError(
            f"{directory} already holds a recorded run; choose an empty --out directory"
        )
    run = Run(spec, directory, blocking_policies=blocking_policies, secrets=secrets)
    timings = {"setup_ms": 0, "interaction_ms": 0, "verification_ms": 0, "reporting_ms": 0}
    started = time.perf_counter()
    budgets = spec.budgets
    execution_status, goal_status, goal_details = "completed", "unverified", []
    agent = None
    environment = {"viewport": list(spec.viewport), "headless": not headed,
                   "device_scale_factor": 1, "owned_session": True}
    try:
        run.collector = Collector(run.journal)
        task = spec.task
        if spec.probes:
            task = task + "\n" + "\n".join(f"- {probe.instruction}" for probe in spec.probes)

        def factory(url):
            return Browser(url, before_navigate=run.collector.install, foreground=headed,
                           viewport=spec.viewport)

        agent = Agent(spec.url, task, browser_factory=factory, screenshots=True,
                      event_sink=run.sink)
        timings["setup_ms"] = round((time.perf_counter() - started) * 1000)
        environment.update(_environment(agent.browser))

        interaction_started = time.perf_counter()
        deadline = interaction_started + budgets["wall_ms"] / 1000
        while agent.state["status"] not in {"done", "blocked"}:
            if len(agent.state["history"]) >= budgets["steps"]:
                execution_status = "budget_exhausted"
                break
            if time.perf_counter() > deadline:
                execution_status = "timeout"
                break
            if run.usage["model_requests"] + len(agent.state["decisions"]) >= budgets["model_requests"]:
                execution_status = "budget_exhausted"
                break
            before_actions = len(agent.state["history"])
            try:
                agent.command("tick")
            except Exception as error:
                run.errors.append({"stage": "actor", "detail": f"{type(error).__name__}: {error}",
                                   "recoverable": False})
                execution_status = "actor_stopped"
                break
            if len(agent.state["history"]) == before_actions:
                continue
            run.step += 1
            pending = run.pending or {}
            run.pending = None
            _settle(run, agent, pending)
            run.reconcile(agent.state["history"], pending)
            if _origin_left(spec, run.latest):
                run.errors.append({"stage": "isolation",
                                   "detail": f"navigated outside the declared origins: "
                                             f"{run.latest.get('url', '')}",
                                   "recoverable": False})
                execution_status = "isolation_violated"
                break
            run.assess("transition", observation=run.latest, history=agent.state["history"],
                       action_context=pending)
        timings["interaction_ms"] = round((time.perf_counter() - interaction_started) * 1000)

        if execution_status == "completed" and agent.state["status"] == "blocked":
            execution_status = "actor_blocked"
        final_observation = _final_observation(run, agent)
        verification_started = time.perf_counter()
        goal_status, goal_details = verify_goal(spec, final_observation)
        timings["verification_ms"] = round((time.perf_counter() - verification_started) * 1000)
        run.assess("final", observation=final_observation, history=agent.state["history"],
                   action_context=(run.pending or {}))
    except KeyboardInterrupt:
        execution_status = "cancelled"
        run.errors.append({"stage": "runner", "detail": "cancelled by operator", "recoverable": False})
    except Exception as error:
        execution_status = "error"
        run.errors.append({"stage": "runner", "detail": f"{type(error).__name__}: {error}",
                           "recoverable": False, "traceback": traceback.format_exc(limit=6)})
    finally:
        if agent is not None:
            try:
                agent.close()
            except Exception as error:
                run.errors.append({"stage": "browser_close", "detail": type(error).__name__,
                                   "recoverable": True})
        run.close()

    reporting_started = time.perf_counter()
    history = agent.state["history"] if agent else []
    decisions = agent.state["decisions"] if agent else []
    text_calls = agent.state["text_calls"] if agent else []
    actor_requests, tokens, steps = _actor_usage(history, decisions, text_calls)
    run.usage.update(actor_requests=actor_requests,
                     model_requests=run.usage["model_requests"] + actor_requests,
                     input_tokens=tokens["input"], output_tokens=tokens["output"])
    usd, helper = account(run, decisions, text_calls)
    run.usage["usd"] = usd
    run.usage["text_helper_requests"] = helper["requests"]
    run.usage["unpriced_tokens"] = helper["input"] + helper["output"]
    run.findings.extend(runtime_findings(run.journal.events(), spec))
    findings = evaluators.deduplicate(run.findings)
    coverage = run.coverage.as_dict({"steps": steps, "observations": len(run.observations),
                                     "acceptance": goal_details})
    result, basis = decide_result(
        execution_status=("error" if execution_status in {"error", "cancelled"} else execution_status),
        goal_status=goal_status, findings=findings, coverage=coverage, mode=spec.mode,
        blocking_policies=blocking_policies,
    )
    validate_result_basis(result, basis, findings, coverage)
    timings["reporting_ms"] = round((time.perf_counter() - reporting_started) * 1000)
    timings["total_ms"] = round((time.perf_counter() - started) * 1000)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": directory.name,
        "tool_version": TOOL_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "model_versions": {"actor": os.environ.get("TYPESAFE_MODEL", ""),
                           "evaluator": os.environ.get("TYPESAFE_MODEL", ""),
                           "text": os.environ.get("TEXT_MODEL_NAME", "")
                           or ("azure:gpt-5.6-luna" if os.environ.get("GPT_LUNA_API_ENDPOINT") else "")},
        "effective_spec_sha256": spec.sha256,
        "journey": provenance,
        "environment": environment,
        "execution_status": execution_status,
        "goal_status": goal_status,
        "goal_evidence": goal_details,
        "result": result,
        "result_basis": basis,
        "coverage": coverage,
        "findings": findings,
        "evaluations": run.evaluations,
        "observations": [{k: v for k, v in o.items() if k not in {"text", "actions"}}
                         for o in run.observations],
        "history": history,
        "errors": run.errors,
        "timings": timings,
        "usage": run.usage,
        "artifacts": {"journal": "events.jsonl", "report": "report.json", "html": "report.html",
                      "junit": "junit.xml", "telemetry": "telemetry.json",
                      "screenshots": sorted(p.name for p in (directory / "evidence").glob("*.jpg"))[:200]},
        "limits": [
            "Findings describe one run of one journey; a single run cannot establish flakiness.",
            "Redaction removes credential-shaped text and declared patterns; screenshots are not redacted.",
            "Semantic verdicts are advisory unless the journey promoted them to blocking.",
        ],
    }
    reporting.write_report(directory, payload)
    reporting.write_html(directory, payload, journey_task=spec.task)
    reporting.write_junit(directory, payload)
    reporting.write_agent_feedback(directory, payload)
    return payload


def _environment(browser):
    try:
        info = browser.evaluate(
            "({userAgent:navigator.userAgent,language:navigator.language,"
            "reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,"
            "colorScheme:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light',"
            "timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone,"
            "deviceScaleFactor:devicePixelRatio})"
        ) or {}
    except Exception:
        return {"browser": "unavailable"}
    return {"user_agent": info.get("userAgent", ""), "locale": info.get("language", ""),
            "reduced_motion": info.get("reducedMotion"), "color_scheme": info.get("colorScheme"),
            "time_zone": info.get("timeZone"), "device_scale_factor": info.get("deviceScaleFactor")}


def _origin_left(spec, observation):
    if not observation or not observation.get("url"):
        return False
    parts = urlsplit(observation["url"])
    if not parts.scheme or parts.scheme == "about":
        return False
    return f"{parts.scheme}://{parts.netloc}" not in spec.allowed_origins


def _settle(run, agent, pending):
    """Watch the transition until the declared effects appear, or until their deadline passes.

    Sleeping blindly to the deadline would make every measured response interval equal to the
    deadline, which would turn a fast page into a page with missing progress feedback. Polling for
    the journey's own readiness conditions measures how long the user actually waited.
    """
    if not pending.get("executed_sequence"):
        return
    applicability = run.applicability("transition", observation=run.latest,
                                      history=[], action_context=pending)
    watched = [check for check in run.applicable_checks("transition", applicability)
               if check.family in {"interaction_correctness", "experience_feedback"}]
    if not watched:
        # Nothing declares a deadline for this action, but the state it produced still has to be
        # looked at. Skipping the observation entirely left the last observation dated *before* the
        # action, so a progress check read an unchanged page and called a working step a stall.
        _observe_settled(run, agent)
        return
    target_ms = max(check.deadline_ms for check in watched)
    conditions = []
    for check in watched:
        declared = check.expect.get("ready_when")
        if isinstance(declared, dict):
            conditions.append(declared)
        elif declared:
            conditions.extend(declared)
    # Control predicates read the evaluation element table, which only exists on an observation
    # that carries a telemetry watermark. Polling cheaply is worth it right up until the journey
    # declares a readiness condition that cheap polling can never satisfy; at that point a poll
    # without a watermark would loop to the deadline and report our own wait as the page's.
    needs_elements = any(reads_controls(condition) for condition in conditions)
    executed = next((e for e in run.journal.events()
                     if e["sequence"] == pending["executed_sequence"]), None)
    if executed is None:
        return
    deadline = executed["host_monotonic_ns"] + (target_ms + SETTLE_GRACE_MS) * 1_000_000
    while conditions and time.perf_counter_ns() < deadline:
        elapsed_ms = (time.perf_counter_ns() - executed["host_monotonic_ns"]) / 1e6
        if elapsed_ms >= target_ms:
            break
        try:
            # These observations exist to date the effect. They carry no screenshot, and no
            # telemetry watermark unless a declared readiness condition needs the element table.
            page = agent.browser.observe(screenshot=False)
        except Exception as error:
            run.errors.append({"stage": "poll_observation", "detail": type(error).__name__,
                               "recoverable": True})
            break
        record = run.sink("observation",
                          {"phase": "settling" if needs_elements else "poll", "page": page})
        if record and all(holds(condition, {"observation": record}) for condition in conditions):
            break
        time.sleep(0.1)
    remaining = (executed["host_monotonic_ns"] + target_ms * 1_000_000 - time.perf_counter_ns()) / 1e9
    if not conditions and remaining > 0:
        time.sleep(min(remaining, (target_ms + SETTLE_GRACE_MS) / 1000))
    _observe_settled(run, agent)


def _observe_settled(run, agent):
    """Date the state an executed action produced. Carries no screenshot and costs no model call."""
    try:
        page = agent.browser.observe(screenshot=False)
    except Exception as error:
        run.errors.append({"stage": "settle_observation", "detail": type(error).__name__,
                           "recoverable": True})
        return
    run.sink("observation", {"phase": "settled", "page": page})


def _final_observation(run, agent):
    """One fresh observation of the state the journey actually ended in."""
    if agent is None:
        return run.latest
    try:
        page = agent.browser.observe(screenshot=True)
    except Exception as error:
        run.errors.append({"stage": "final_observation", "detail": type(error).__name__,
                           "recoverable": True})
        return run.latest
    run.sink("observation", {"phase": "final", "page": page})
    return run.latest


# --- Worker entry point -------------------------------------------------------------------------


def run_journey(spec, provenance, directory, **kwargs):
    """Public in-process entry point. Requires an owned session to already be active."""
    return execute(spec, provenance, directory, **kwargs)


def main(argv=None):
    import argparse

    from .isolation import OwnedSession

    parser = argparse.ArgumentParser(prog="journey-evals-runner")
    parser.add_argument("--spec", required=True, help="Resolved effective specification JSON")
    parser.add_argument("--out", required=True)
    parser.add_argument("--env-file", default=None,
                        help="Where credentials are read from (default: ./.env)")
    parser.add_argument("--blocking-check", action="append", default=[])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--warn-as-error", action="store_true")
    args = parser.parse_args(argv)

    load_environment(Path(args.env_file) if args.env_file else env_file())
    document = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    spec = journey_from_dict(document["resolved"], source=args.spec)
    secrets = [value for name, value in os.environ.items()
               if name.endswith(("_API_KEY", "_KEY", "_TOKEN", "_SECRET")) and value]
    directory = Path(args.out)
    diagnostics = directory / "session"
    diagnostics.mkdir(parents=True, exist_ok=True)
    reset = reset_backend(spec)
    with OwnedSession(diagnostics) as session:
        payload = execute(spec, document, directory, blocking_policies=tuple(args.blocking_check),
                          headed=args.headed, secrets=secrets)
        if reset:
            payload.setdefault("environment", {})["backend_reset"] = reset
            if reset["state"] == "failed":
                payload.setdefault("errors", []).append(
                    {"stage": "setup", "detail": f"backend reset failed: {reset['detail']}"})
        payload.setdefault("environment", {})["session"] = {
            "browser": getattr(session, "version", ""),
            "profile": str(getattr(session, "directory", "")),
        }
    payload["environment"]["session"].update(
        cleanup_retries=session.cleanup_retries, cleanup_events=list(session.cleanup_events),
        cleanup_errors=list(session.cleanup_errors), closed=session.closed,
    )
    write_json(directory / "report.json", payload)
    return exit_code(payload["result"], warn_as_error=args.warn_as_error)


if __name__ == "__main__":
    sys.exit(main())
