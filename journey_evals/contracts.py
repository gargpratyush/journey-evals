"""Versioned public data contracts for a journey evaluation run.

The hard-to-reverse interface of this tool is the persisted artifact, not an HTTP service, so
every structure a report exposes is defined and validated here. Three claims stay separate
throughout: a measured fact, a semantic interpretation, and an unverified hypothesis. Only the
first two are ever persisted; this version emits no guessed source locations at all.

Nothing in this module talks to a browser or a model. It is pure enough to test offline.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, TypedDict

SCHEMA_VERSION = 1
UPSTREAM_REVISION = "452c1ad2dd628008f1d5608f28158d76e49e6cc0"

# The six public evaluator families and their complete candidate spaces. A family is a bounded
# vocabulary, not a licence to ask whether the whole site is good.
FAMILIES = {
    "task_progress": ("PROGRESSING", "STALLED", "BLOCKED", "COMPLETION_CANDIDATE", "UNKNOWN"),
    "unexpected_state": ("EXPECTED", "EXPLAINED_CHANGE", "UNEXPLAINED_CHANGE", "UNKNOWN"),
    "interaction_correctness": ("EFFECT_OBSERVED", "STILL_PENDING", "CONTRADICTED", "UNKNOWN"),
    "layout_integrity": ("RELEVANT", "BENIGN", "UNKNOWN"),
    "experience_feedback": ("ADEQUATE", "MISSING", "CONTRADICTORY", "UNKNOWN"),
    "input_responsiveness": ("RESPONSIVE", "SLUGGISH", "UNKNOWN"),
}
# Which verdicts of each family describe a user-facing problem rather than an acceptable state.
DEFECT_VERDICTS = {
    "task_progress": {"STALLED", "BLOCKED"},
    "unexpected_state": {"UNEXPLAINED_CHANGE"},
    "interaction_correctness": {"STILL_PENDING", "CONTRADICTED"},
    "layout_integrity": {"RELEVANT"},
    "experience_feedback": {"MISSING", "CONTRADICTORY"},
    "input_responsiveness": {"SLUGGISH"},
}
ABSTENTIONS = {"UNKNOWN"}

SCOPES = ("transition", "final")
SEVERITIES = ("low", "medium", "high")
MODES = ("explore", "verify")
RESULTS = ("PASS", "WARN", "FAIL", "INCONCLUSIVE", "ERROR")
EXIT_CODES = {"PASS": 0, "WARN": 0, "FAIL": 1, "INCONCLUSIVE": 2, "ERROR": 3}
CONFIGURATION_EXIT = 4

# A required check moves through these states. Only a proven applicability condition may reach
# not_applicable; a state the journey never visited leaves the check pending and coverage
# incomplete, which is materially different from a pass.
CHECK_STATES = ("pending", "observed", "passed", "failed", "unknown", "not_applicable")

EVIDENCE_KINDS = ("observation", "telemetry_window", "screenshot", "network", "console", "verifier")

# Named viewports, so a journey or a command line can say "mobile" instead of restating pixels
# that then drift between the spec, the console and the report. A preset is only ever a shorthand
# for a size: it resolves to exact pixels before anything else sees it, and the resolved pixels are
# what the run records. Nothing infers a device, a user agent or a touch capability from the name.
#
# The sizes are CSS pixels for common device classes, chosen so each class is meaningfully
# different from its neighbours rather than to imitate a particular handset.
VIEWPORT_PRESETS = {
    "mobile-small": (360, 640),
    "mobile": (390, 844),
    "mobile-large": (430, 932),
    "tablet": (834, 1112),
    "tablet-landscape": (1112, 834),
    "standard": (1120, 780),
    "laptop": (1280, 800),
    "desktop": (1440, 900),
    "desktop-large": (1920, 1080),
}
# The lower bound of each class, used only to describe a viewport once it has been resolved. The
# console frames a phone-shaped run differently from a desktop one; this is where that decision is
# made, so the console and the report cannot disagree about it.
VIEWPORT_CLASSES = ((520, "phone"), (900, "tablet"), (10_000, "desktop"))

VIEWPORT_MIN = (320, 400)
VIEWPORT_MAX = (3840, 2160)

# The complete applicability vocabulary. This is deliberately a small fixed predicate set and not
# a general workflow language: a trusted journey file should be reviewable at a glance.
PREDICATES = (
    "always",
    "text_contains",
    "text_absent",
    "title_is",
    "url_path_is",
    "url_contains",
    "action_executed",
    "action_kind_executed",
    "control_present",
    "control_disabled",
    "control_value_is",
    "any_of",
    "all_of",
    "not",
)

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")


class ContractError(ValueError):
    """An invalid journey, configuration, or artifact. Raised before any browser mutation."""


class Observation(TypedDict, total=False):
    """One immutable, bounded view of the page. Persisted copies never change afterwards."""

    id: str
    step_id: str
    document_id: str
    sequence: int
    phase: str
    url: str
    title: str
    text: str
    actions: list[dict]
    evaluation_elements: list[dict]
    journey_facts: dict
    telemetry_window: dict
    evidence_refs: list[str]
    capabilities: dict
    truncation: dict
    host_monotonic_ns: int
    clock_anchor: dict


class Evaluation(TypedDict, total=False):
    """One evaluator's judgment of one subject, with its provenance kept separable."""

    id: str
    evaluator: str
    evaluator_version: int
    family: str
    check_id: str
    subject_id: str
    requirement: str
    observation_ids: list[str]
    window_id: str
    verdict: str
    measured: dict
    source: Literal["code", "jev"]
    reason: str
    model_signal: dict
    policy_version: str
    review_state: Literal["advisory", "review_required", "confirmed", "not_required"]


class Finding(TypedDict, total=False):
    """A reportable problem. Deterministic and semantic provenance stay distinguishable."""

    id: str
    severity: str
    category: str
    title: str
    observed: dict
    expected: dict
    evidence_ids: list[str]
    first_step: int
    last_step: int
    occurrences: int
    reproducibility: str
    provenance: Literal["deterministic", "semantic"]
    confirmation: Literal["review_required", "verified", "unconfirmed"]
    evaluator: str
    evaluator_version: int
    model_signal: dict
    advisory: bool


class RunResult(TypedDict, total=False):
    schema_version: int
    run_id: str
    tool_version: str
    upstream_revision: str
    model_versions: dict
    effective_spec_sha256: str
    environment: dict
    execution_status: str
    goal_status: str
    result: str
    result_basis: list[dict]
    coverage: dict
    findings: list[Finding]
    evaluations: list[Evaluation]
    errors: list[dict]
    timings: dict
    usage: dict
    artifacts: dict


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def _identifier(value, what):
    _require(isinstance(value, str) and IDENTIFIER.match(value), f"{what} must be a short lowercase identifier")
    return value


def viewport_names():
    """The preset names, widest last, for help text and error messages."""
    return sorted(VIEWPORT_PRESETS, key=lambda name: VIEWPORT_PRESETS[name])


def resolve_viewport(value):
    """Resolve a preset name, a "WxH" string, a mapping or a pair into exact pixels.

    A preset is shorthand and nothing more. It is expanded here, once, so that the specification,
    the worker command line, the console and the report all carry the same two integers; a name
    that survived into the artifacts would be a second source of truth for the size a run was
    actually measured at.
    """
    if isinstance(value, str):
        name = value.strip().lower()
        if name in VIEWPORT_PRESETS:
            return VIEWPORT_PRESETS[name]
        match = re.fullmatch(r"\s*(\d{2,4})\s*[x*]\s*(\d{2,4})\s*", name)
        _require(match, f"viewport {value!r} is neither a size like 1120x780 nor one of: "
                        f"{', '.join(viewport_names())}")
        pair = (int(match.group(1)), int(match.group(2)))
    elif isinstance(value, dict):
        _require(type(value.get("width")) is int and type(value.get("height")) is int,
                 "a viewport object needs integer width and height")
        pair = (value["width"], value["height"])
    else:
        _require(isinstance(value, (tuple, list)) and len(value) == 2
                 and all(type(part) is int for part in value),
                 f"viewport {value!r} must be a name, a size like 1120x780, or two integers")
        pair = (value[0], value[1])
    _require(VIEWPORT_MIN[0] <= pair[0] <= VIEWPORT_MAX[0]
             and VIEWPORT_MIN[1] <= pair[1] <= VIEWPORT_MAX[1],
             f"viewport {pair[0]}x{pair[1]} is outside {VIEWPORT_MIN[0]}x{VIEWPORT_MIN[1]}"
             f"..{VIEWPORT_MAX[0]}x{VIEWPORT_MAX[1]}")
    return pair


def viewport_label(viewport):
    """The preset name for a resolved size when one exists, else the size itself.

    Used for directory names and matrix headings only. The pixels remain the record of what was
    measured; this is a label on top of them, never a substitute for them.
    """
    pair = tuple(viewport)
    for name in viewport_names():
        if VIEWPORT_PRESETS[name] == pair:
            return name
    return f"{pair[0]}x{pair[1]}"


def resolve_viewports(value):
    """Resolve a declared viewport, or a list of them, into resolved sizes in declared order.

    A journey may declare one size or several. Several means the journey is a matrix: the same
    declared checks, measured independently at each size, because a layout that holds on a desktop
    says nothing about the same layout on a phone.

    ``[390, 844]`` stays a single size rather than two, so a journey written before this existed
    keeps its meaning.
    """
    pair_shaped = (isinstance(value, (tuple, list)) and len(value) == 2
                   and all(type(part) is int for part in value))
    if isinstance(value, (tuple, list)) and not pair_shaped:
        _require(value, "a viewport list must name at least one viewport")
        resolved = [resolve_viewport(item) for item in value]
    else:
        resolved = [resolve_viewport(value)]
    unique = list(dict.fromkeys(resolved))
    return tuple(unique)


def select_viewports(declared, requested):
    """Choose which of a journey's declared sizes to run.

    Nothing requested runs the whole declared matrix. A requested size must be one the journey
    declares: running the journey at a size it was not written for changes what its result means,
    so it is refused rather than silently honoured. This is the same rule as ``--task``, applied
    to the one other input that decides what a pass is evidence of.
    """
    declared = tuple(declared)
    if not requested:
        return declared
    chosen, unknown = [], []
    for pair in dict.fromkeys(tuple(item) for item in requested):
        (chosen if pair in declared else unknown).append(pair)
    if unknown:
        missing = ", ".join(f"{w}x{h}" for w, h in unknown)
        offered = ", ".join(f"{viewport_label(pair)} ({pair[0]}x{pair[1]})" for pair in declared)
        raise ContractError(
            f"--viewport {missing} is not declared by this journey; it declares {offered}. "
            f"Add the size to the journey rather than measuring it at a size it does not claim."
        )
    return tuple(chosen)


def viewport_class(viewport):
    """Describe a resolved viewport as phone, tablet or desktop.

    This is a presentation hint only. It is derived from the width that was actually used, never
    from the preset name, so a custom size is classified on the same rule as a named one.
    """
    width = viewport[0] if not isinstance(viewport, dict) else viewport["width"]
    for bound, name in VIEWPORT_CLASSES:
        if width < bound:
            return name
    return "desktop"


def canonical_json(value):
    """Stable bytes for hashing. Refuses NaN so a hash can never depend on an unorderable float."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def finding_id(evaluator, subject, requirement):
    """Stable across runs for the same evaluator/subject/requirement, so history can be compared."""
    return "finding-" + hashlib.sha256("\x1f".join((evaluator, subject, requirement)).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CheckSpec:
    """One declared acceptance or experience check. Trusted input, never inferred from a page."""

    id: str
    family: str
    scope: str
    applies_when: dict
    requirement: str
    deadline_ms: int = 1000
    required_evidence: tuple[str, ...] = ()
    required: bool = True
    severity: str = "medium"
    expect: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(raw):
        _require(isinstance(raw, dict), "Each check must be an object")
        unknown = set(raw) - {
            "id", "family", "scope", "applies_when", "requirement", "deadline_ms",
            "required_evidence", "required", "severity", "expect",
        }
        _require(not unknown, f"Unknown check fields: {sorted(unknown)}")
        identifier = _identifier(raw.get("id"), "check id")
        family = raw.get("family")
        _require(family in FAMILIES, f"Check {identifier} names an unknown family {family!r}")
        scope = raw.get("scope", "transition")
        _require(scope in SCOPES, f"Check {identifier} must have scope transition or final")
        deadline = raw.get("deadline_ms", 1000)
        _require(type(deadline) is int and 0 <= deadline <= 120_000, f"Check {identifier} has an invalid deadline")
        severity = raw.get("severity", "medium")
        _require(severity in SEVERITIES, f"Check {identifier} has an invalid severity")
        evidence = tuple(raw.get("required_evidence", ()))
        _require(all(kind in EVIDENCE_KINDS for kind in evidence),
                 f"Check {identifier} requires an unknown evidence kind")
        requirement = raw.get("requirement")
        _require(isinstance(requirement, str) and requirement.strip(),
                 f"Check {identifier} must state its requirement in words")
        required = raw.get("required", True)
        _require(type(required) is bool, f"Check {identifier} required flag must be boolean")
        expect = raw.get("expect", {})
        _require(isinstance(expect, dict), f"Check {identifier} expect must be an object")
        # ready_when is executed against live evidence exactly like applies_when, so it has to
        # clear the same gate. Leaving it unvalidated turns a typo in a journey into a crash
        # halfway through a paid run instead of a configuration error before anything is spent.
        if "ready_when" in expect:
            clauses = expect["ready_when"]
            if isinstance(clauses, dict):
                expect = {**expect, "ready_when": validate_predicate(clauses)}
            else:
                _require(isinstance(clauses, list) and clauses,
                         f"Check {identifier} ready_when must be a predicate or a non-empty list")
                expect = {**expect, "ready_when": [validate_predicate(c) for c in clauses]}
        applies = validate_predicate(raw.get("applies_when", {"predicate": "always"}))
        if family == "input_responsiveness":
            # The whole family is a threshold comparison, so a missing or nonsensical budget is a
            # configuration error rather than something to be discovered mid-run.
            budget = expect.get("blocking_budget_ms")
            _require(type(budget) is int and 0 < budget <= 60_000,
                     f"Check {identifier} must declare blocking_budget_ms as a positive integer")
            operation = expect.get("operation")
            _require(isinstance(operation, str) and operation.strip(),
                     f"Check {identifier} must name the operation it measures")
        return CheckSpec(identifier, family, scope, applies, requirement.strip(), deadline,
                         evidence, required, severity, expect)

    def canonical(self):
        return {
            "id": self.id, "family": self.family, "scope": self.scope, "applies_when": self.applies_when,
            "requirement": self.requirement, "deadline_ms": self.deadline_ms,
            "required_evidence": list(self.required_evidence), "required": self.required,
            "severity": self.severity, "expect": self.expect,
        }


@dataclass(frozen=True)
class ProbeSpec:
    """A state the journey must actually visit, with the checks that only exist there."""

    id: str
    instruction: str
    required_checks: tuple[str, ...]

    @staticmethod
    def from_dict(raw):
        _require(isinstance(raw, dict), "Each probe must be an object")
        identifier = _identifier(raw.get("id"), "probe id")
        instruction = raw.get("instruction")
        _require(isinstance(instruction, str) and instruction.strip(),
                 f"Probe {identifier} needs an actor-facing instruction")
        checks = tuple(raw.get("required_checks", ()))
        _require(checks and all(isinstance(c, str) for c in checks),
                 f"Probe {identifier} must name at least one required check")
        return ProbeSpec(identifier, instruction.strip(), checks)

    def canonical(self):
        return {"id": self.id, "instruction": self.instruction, "required_checks": list(self.required_checks)}


@dataclass(frozen=True)
class JourneySpec:
    """The complete trusted specification of one run, after CLI and file inputs are reconciled."""

    id: str
    mode: str
    task: str
    url: str
    allowed_origins: tuple[str, ...]
    facts: dict = field(default_factory=dict)
    viewport: tuple[int, int] = (1120, 780)
    viewports: tuple[tuple[int, int], ...] = ()
    probes: tuple[ProbeSpec, ...] = ()
    checks: tuple[CheckSpec, ...] = ()
    acceptance: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    redact: tuple[str, ...] = ()
    expected_console_errors: tuple[str, ...] = ()

    def canonical(self):
        payload = {
            "schema_version": SCHEMA_VERSION, "id": self.id, "mode": self.mode, "task": self.task,
            "url": self.url, "allowed_origins": list(self.allowed_origins), "facts": self.facts,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "probes": [p.canonical() for p in self.probes],
            "checks": [c.canonical() for c in self.checks], "acceptance": self.acceptance,
            "budgets": self.budgets, "redact": list(self.redact),
            "expected_console_errors": list(self.expected_console_errors),
        }
        if len(self.viewports) > 1:
            # Only present for a matrix journey, so a journey that declares one size keeps the
            # specification hash it had before matrices existed.
            payload["viewports"] = [{"width": w, "height": h} for w, h in self.viewports]
        return payload

    @property
    def sha256(self):
        return digest(self.canonical())

    def check(self, identifier):
        return next((c for c in self.checks if c.id == identifier), None)


DEFAULT_BUDGETS = {"wall_ms": 180_000, "model_requests": 120, "steps": 40, "usd": "0.40"}


def validate_predicate(raw):
    """Validate one applicability predicate and return it normalized."""
    _require(isinstance(raw, dict), "A predicate must be an object")
    name = raw.get("predicate")
    _require(name in PREDICATES, f"Unknown predicate {name!r}")
    if name == "always":
        _require(set(raw) == {"predicate"}, "always takes no arguments")
        return {"predicate": "always"}
    if name in {"any_of", "all_of"}:
        clauses = raw.get("clauses")
        _require(isinstance(clauses, list) and clauses, f"{name} needs a non-empty clause list")
        return {"predicate": name, "clauses": [validate_predicate(c) for c in clauses]}
    if name == "not":
        return {"predicate": "not", "clause": validate_predicate(raw.get("clause", {}))}
    value = raw.get("value")
    _require(isinstance(value, str) and value, f"{name} needs a string value")
    if name == "control_value_is":
        expected = raw.get("expected")
        _require(isinstance(expected, str), "control_value_is needs an expected string")
        return {"predicate": name, "value": value, "expected": expected}
    _require(set(raw) == {"predicate", "value"}, f"{name} takes only a value")
    return {"predicate": name, "value": value}


def _url_path(url):
    from urllib.parse import urlsplit

    return urlsplit(url).path or "/"


def _controls(context):
    observation = context.get("observation") or {}
    return observation.get("evaluation_elements") or []


def holds(predicate, context):
    """Evaluate one trusted predicate against observed evidence. Page text is data, never code."""
    name = predicate["predicate"]
    if name == "always":
        return True
    if name == "any_of":
        return any(holds(clause, context) for clause in predicate["clauses"])
    if name == "all_of":
        return all(holds(clause, context) for clause in predicate["clauses"])
    if name == "not":
        return not holds(predicate["clause"], context)
    observation = context.get("observation") or {}
    value = predicate.get("value")
    # Every remaining predicate is value-carrying. A missing value means the predicate never went
    # through validate_predicate, which is a programming error here rather than a false verdict.
    if not isinstance(value, str):
        raise ContractError(f"Predicate {name!r} reached evaluation without a validated value")
    if name == "text_contains":
        return value in (observation.get("text") or "")
    if name == "text_absent":
        return value not in (observation.get("text") or "")
    if name == "title_is":
        return (observation.get("title") or "") == value
    if name == "url_path_is":
        return _url_path(observation.get("url") or "") == value
    if name == "url_contains":
        return value in (observation.get("url") or "")
    if name == "action_executed":
        return any(entry.get("action") == value for entry in context.get("history") or ())
    if name == "action_kind_executed":
        return any(entry.get("kind") == value for entry in context.get("history") or ())
    if name == "control_present":
        return any(control.get("label") == value for control in _controls(context))
    if name == "control_disabled":
        return any(control.get("label") == value and control.get("disabled") for control in _controls(context))
    if name == "control_value_is":
        return any(control.get("label") == value and control.get("value") == predicate["expected"]
                   for control in _controls(context))
    raise ContractError(f"Unhandled predicate {name!r}")


def reads_controls(predicate):
    """Does this predicate need the evaluation element table to be answerable at all?

    Control predicates are answered from measured element state, not from page text. Anything that
    tries to satisfy one without that table will simply never see it hold.
    """
    name = predicate.get("predicate")
    if name in {"any_of", "all_of"}:
        return any(reads_controls(clause) for clause in predicate.get("clauses", ()))
    if name == "not":
        return reads_controls(predicate.get("clause", {}))
    return name in {"control_present", "control_disabled", "control_value_is"}


def _acceptance(raw, mode, identifier):
    _require(isinstance(raw, dict), "acceptance must be an object")
    unknown = set(raw) - {"url_path_is", "text_contains", "text_absent", "control_values", "backend"}
    _require(not unknown, f"Unknown acceptance fields: {sorted(unknown)}")
    for name in ("text_contains", "text_absent"):
        values = raw.get(name, [])
        _require(isinstance(values, list) and all(isinstance(v, str) and v for v in values),
                 f"acceptance.{name} must be a list of strings")
    _require(isinstance(raw.get("control_values", {}), dict), "acceptance.control_values must be an object")
    backend = raw.get("backend", {})
    _require(isinstance(backend, dict), "acceptance.backend must be an object")
    if backend:
        _require(isinstance(backend.get("path"), str) and backend["path"].startswith("/"),
                 "acceptance.backend.path must be an absolute fixture path")
        _require(isinstance(backend.get("expect_records"), list),
                 "acceptance.backend.expect_records must be a list of exact record objects")
        reset = backend.get("reset_path")
        _require(reset is None or (isinstance(reset, str) and reset.startswith("/")),
                 "acceptance.backend.reset_path must be an absolute fixture path")
    if mode == "verify":
        _require(any(raw.get(name) for name in ("url_path_is", "text_contains", "control_values", "backend")),
                 f"Journey {identifier} runs in verify mode but declares no independent acceptance check")
    return raw


def _origin(url):
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    _require(parts.scheme in {"http", "https"}, "Target URL must be http or https")
    _require(not parts.username and not parts.password, "Credentials must not appear in the target URL")
    _require(parts.hostname, "Target URL needs a host")
    return f"{parts.scheme}://{parts.netloc}"


def journey_from_dict(raw, *, source="<inline>", declared_viewports=None):
    _require(isinstance(raw, dict), f"{source} must contain a journey object")
    unknown = set(raw) - {
        "schema_version", "id", "mode", "task", "url", "allowed_origins", "facts", "viewport",
        "viewports", "probes", "checks", "acceptance", "budgets", "redact",
        "expected_console_errors",
    }
    _require(not unknown, f"Unknown journey fields: {sorted(unknown)}")
    _require(raw.get("schema_version") == SCHEMA_VERSION,
             f"{source} declares schema_version {raw.get('schema_version')!r}; this tool writes {SCHEMA_VERSION}")
    identifier = _identifier(raw.get("id"), "journey id")
    mode = raw.get("mode", "verify")
    _require(mode in MODES, f"Journey {identifier} mode must be explore or verify")
    task = raw.get("task")
    _require(isinstance(task, str) and task.strip(), f"Journey {identifier} needs a task")
    url = raw.get("url")
    _require(isinstance(url, str) and url, f"Journey {identifier} needs a url")
    origin = _origin(url)
    origins = tuple(raw.get("allowed_origins") or (origin,))
    _require(origin in origins, f"Journey {identifier} target origin is not in its own allowed_origins")
    for allowed in origins:
        _origin(allowed)
    viewports = resolve_viewports(raw.get("viewport", {"width": 1120, "height": 780}))
    viewport = viewports[0]
    if raw.get("viewports"):
        # A canonical specification round-tripping back in: it carries the cell it runs and the
        # whole matrix it belongs to, and the two have to keep agreeing.
        viewports = resolve_viewports(list(raw["viewports"]))
    if declared_viewports:
        # This spec is one cell of a matrix that was narrowed before it got here. Keep the
        # whole declaration so the run can say which cell of what it is.
        viewports = tuple(declared_viewports)
    _require(viewport in viewports, "the viewport being run is not one this journey declares")
    facts = raw.get("facts", {})
    _require(isinstance(facts, dict), "facts must be an object of trusted synthetic values")
    checks = tuple(CheckSpec.from_dict(item) for item in raw.get("checks", ()))
    ids = [c.id for c in checks]
    _require(len(ids) == len(set(ids)), f"Journey {identifier} repeats a check id")
    probes = tuple(ProbeSpec.from_dict(item) for item in raw.get("probes", ()))
    probe_ids = [p.id for p in probes]
    _require(len(probe_ids) == len(set(probe_ids)), f"Journey {identifier} repeats a probe id")
    for probe in probes:
        for name in probe.required_checks:
            _require(name in ids, f"Probe {probe.id} requires undeclared check {name!r}")
    budgets = {**DEFAULT_BUDGETS, **(raw.get("budgets") or {})}
    for name in ("wall_ms", "model_requests", "steps"):
        _require(type(budgets[name]) is int and budgets[name] > 0, f"budgets.{name} must be a positive integer")
    _require(isinstance(budgets["usd"], str), "budgets.usd must be a decimal string")
    redact = tuple(raw.get("redact") or ())
    _require(all(isinstance(value, str) and value for value in redact), "redact must be a list of strings")
    expected_errors = tuple(raw.get("expected_console_errors") or ())
    _require(all(isinstance(value, str) and value for value in expected_errors),
             "expected_console_errors must be a list of strings")
    acceptance = _acceptance(raw.get("acceptance", {}), mode, identifier)
    return JourneySpec(
        id=identifier, mode=mode, task=task.strip(), url=url, allowed_origins=origins, facts=facts,
        viewport=viewport, viewports=viewports, probes=probes, checks=checks,
        acceptance=acceptance, budgets=budgets, redact=redact, expected_console_errors=expected_errors,
    )


def declared_viewports(journey_file):
    """The sizes a journey file declares, without building or validating the whole specification.

    The CLI needs the matrix before it commits to anything, so that a journey declaring four sizes
    is planned as four runs rather than discovered one run at a time.
    """
    from pathlib import Path

    path = Path(journey_file)
    if not path.exists():
        hint = ""
        bundled = Path("examples/journeys") / path.name
        if bundled.exists():
            hint = f" Did you mean {bundled}?"
        raise ContractError(f"no journey file at {journey_file}.{hint}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ContractError(f"{journey_file} is not valid JSON: {error}") from None
    _require(isinstance(raw, dict), f"{journey_file} must contain a journey object")
    return resolve_viewports(raw.get("viewport", {"width": 1120, "height": 780}))


def effective_spec(*, journey_file=None, url=None, task=None, mode=None, viewport=None, source="<cli>"):
    """Reconcile CLI arguments and an optional journey file into one immutable specification.

    A supplied URL or task that contradicts the file is a configuration error, not a silent
    override: changing the actor's task without changing its oracle is how a run quietly stops
    testing what it claims to test.
    """
    supplied = {"url": url, "task": task, "mode": mode,
                "viewport": list(viewport) if viewport else None, "journey_file": journey_file}
    if journey_file is None:
        _require(url and task, "Without a journey file, both --url and --task are required")
        raw = {
            "schema_version": SCHEMA_VERSION, "id": "exploration", "mode": mode or "explore",
            "task": task, "url": url,
        }
        if viewport:
            raw["viewport"] = {"width": viewport[0], "height": viewport[1]}
        _require(raw["mode"] == "explore",
                 "A run without a journey file cannot certify success; use mode explore or supply --journey")
        spec = journey_from_dict(raw, source=source)
    else:
        from pathlib import Path

        text = Path(journey_file).read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise ContractError(f"{journey_file} is not valid JSON: {error}") from None
        for name, value in (("url", url), ("task", task), ("mode", mode)):
            if value is not None and raw.get(name, value) != value:
                raise ContractError(
                    f"--{name} conflicts with {journey_file}; reject rather than run a different journey"
                )
            if value is not None:
                raw[name] = value
        matrix = None
        if viewport:
            declared = raw.get("viewport")
            if declared is not None:
                matrix = resolve_viewports(declared)
                chosen = select_viewports(matrix, [viewport])
                raw["viewport"] = {"width": chosen[0][0], "height": chosen[0][1]}
            else:
                raw["viewport"] = {"width": viewport[0], "height": viewport[1]}
        spec = journey_from_dict(raw, source=str(journey_file), declared_viewports=matrix)
    provenance = {
        "supplied": {k: v for k, v in supplied.items() if v is not None},
        "resolved": spec.canonical(),
        "effective_spec_sha256": spec.sha256,
    }
    return spec, provenance


class Coverage:
    """The declared-check state machine. An unvisited state never becomes a pass."""

    def __init__(self, spec):
        self.spec = spec
        self.states = {check.id: "pending" for check in spec.checks}
        self.evidence = {check.id: [] for check in spec.checks}
        self.probes = {probe.id: "pending" for probe in spec.probes}

    def record(self, check_id, state, evidence_ids=()):
        _require(state in CHECK_STATES, f"Unknown check state {state!r}")
        _require(check_id in self.states, f"Undeclared check {check_id!r}")
        order = {name: index for index, name in enumerate(CHECK_STATES)}
        current = self.states[check_id]
        # passed/failed/unknown are terminal for one applicability episode; a later stronger
        # observation may only move a check away from pending/observed/not_applicable.
        if current in {"failed", "unknown"} and state == "passed":
            return
        if current == "passed" and state in {"failed", "unknown"}:
            self.states[check_id] = state
        elif order[state] >= order[current] or current in {"pending", "observed", "not_applicable"}:
            self.states[check_id] = state
        self.evidence[check_id].extend(evidence_ids)

    def mark_probe(self, probe_id, state):
        _require(probe_id in self.probes, f"Undeclared probe {probe_id!r}")
        self.probes[probe_id] = state

    def resolve_probes(self):
        for probe in self.spec.probes:
            states = {self.states[name] for name in probe.required_checks}
            if states <= {"passed", "not_applicable"} and "passed" in states:
                self.probes[probe.id] = "covered"
            elif "failed" in states:
                self.probes[probe.id] = "failed"
            elif states <= {"not_applicable"}:
                self.probes[probe.id] = "not_applicable"
            else:
                self.probes[probe.id] = "incomplete"

    @property
    def missing_required(self):
        return sorted(
            check.id for check in self.spec.checks
            if check.required and self.states[check.id] in {"pending", "observed", "unknown"}
        )

    @property
    def failed_required(self):
        return sorted(check.id for check in self.spec.checks
                      if check.required and self.states[check.id] == "failed")

    def as_dict(self, extra=None):
        self.resolve_probes()
        return {
            "checks": dict(self.states),
            "check_evidence": {k: list(dict.fromkeys(v)) for k, v in self.evidence.items()},
            "probes": dict(self.probes),
            "missing_required_checks": self.missing_required,
            "failed_required_checks": self.failed_required,
            "unvisited_probes": sorted(name for name, state in self.probes.items()
                                       if state in {"pending", "incomplete", "not_applicable"}),
            "complete": not self.missing_required and not any(
                state in {"pending", "incomplete", "not_applicable"} for state in self.probes.values()
            ),
            **(extra or {}),
        }


def decide_result(*, execution_status, goal_status, findings, coverage, mode, blocking_policies=()):
    """Deterministic precedence: runtime failure, verified failure, incomplete coverage, then warn/pass.

    A confident model answer never makes a report green on its own, and a provider outage never
    turns an already-observed defect into a pass.
    """
    basis = []
    if execution_status in {"error", "cancelled"}:
        basis.append({"effect": "error", "execution_status": execution_status})
        return "ERROR", basis
    failed = coverage["failed_required_checks"]
    if goal_status == "violated":
        basis.append({"check_id": "goal:acceptance", "effect": "fail", "policy": "independent_acceptance_v1"})
    for check_id in failed:
        basis.append({"check_id": check_id, "effect": "fail", "policy": "declared_required_check_v1"})
    blocking = [f for f in findings
                if not f.get("advisory", True) and f.get("evaluator") in set(blocking_policies)]
    for item in blocking:
        basis.append({"finding_id": item["id"], "effect": "fail", "policy": "promoted_semantic_v1"})
    if basis:
        return "FAIL", basis
    missing = coverage["missing_required_checks"]
    if missing:
        basis.append({"effect": "inconclusive", "missing_required_checks": missing,
                      "policy": "required_coverage_v1"})
        return "INCONCLUSIVE", basis
    unvisited = coverage.get("unvisited_probes") or []
    if unvisited:
        # A state the journey declared it must visit was never reached, so the checks that only
        # exist there were never exercised. That is unknown, not clean.
        basis.append({"effect": "inconclusive", "unvisited_probes": unvisited,
                      "policy": "declared_probe_coverage_v1"})
        return "INCONCLUSIVE", basis
    if mode == "verify" and goal_status != "verified":
        basis.append({"check_id": "goal:acceptance", "effect": "inconclusive", "goal_status": goal_status,
                      "policy": "independent_acceptance_v1"})
        return "INCONCLUSIVE", basis
    if mode == "explore" and goal_status != "verified":
        basis.append({"effect": "inconclusive", "goal_status": goal_status,
                      "policy": "exploration_cannot_certify_v1"})
        advisory = [f for f in findings if f.get("advisory", True)]
        for item in advisory:
            basis.append({"finding_id": item["id"], "effect": "warn", "policy": "semantic_advisory_v1"})
        return "INCONCLUSIVE", basis
    advisory = [f for f in findings if f.get("advisory", True)]
    for item in advisory:
        basis.append({"finding_id": item["id"], "effect": "warn", "policy": "semantic_advisory_v1"})
    if advisory:
        return "WARN", basis
    basis.append({"check_id": "goal:acceptance", "effect": "goal_verified"})
    return "PASS", basis


def validate_result_basis(result, basis, findings, coverage):
    """A top-level result without supporting records is invalid; catch it before persisting."""
    known_findings = {f["id"] for f in findings}
    for item in basis:
        if "finding_id" in item:
            _require(item["finding_id"] in known_findings,
                     f"result_basis references unknown finding {item['finding_id']}")
        if "check_id" in item and item["check_id"] not in {"goal:acceptance"}:
            _require(item["check_id"] in coverage["checks"],
                     f"result_basis references unknown check {item['check_id']}")
    _require(result in RESULTS, f"Unknown result {result!r}")
    if result in {"FAIL", "INCONCLUSIVE", "ERROR"}:
        _require(basis, f"{result} must identify what determined it")
    return True


def exit_code(result, *, warn_as_error=False):
    if result == "WARN" and warn_as_error:
        return 1
    return EXIT_CODES[result]
