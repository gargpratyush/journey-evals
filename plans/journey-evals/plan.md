
# Journey Evals: detailed build plan

**Execution follows the [feasibility checkpoint plan](..\jev-feasibility\plan.html).** Its eight checkpoints take precedence over this document's initial phase ordering: prove a thin report-driven repair loop, measure quality/economics, and demonstrate second-app transfer before extracting a reusable framework. An experimental fork exists and CP0 through CP4 are GREEN on one source revision, so the report-driven repair loop is demonstrated end to end on two defects in one synthetic application. **CP5 has now run and is AMBER**, so no framework-admission gate has passed and extraction is not yet justified. This document remains the target architecture and subsequent product roadmap.

**What CP5 changes about this plan.** Three measured results bear directly on the architecture below. First, quality is not the differentiator: Jev and a `gpt-5.6-luna` baseline both scored 90/90 on an unexposed corpus, so the semantic backend should be treated as replaceable rather than as the product. Second, the advantage is latency, 14.0x, not cost, 7.27x against a declared 10x gate, which means the value proposition has to be restated around journey throughput or the project pivots. Third, and most concretely for the design: in 7 of 60 live journeys the check correctly returned `clean` while the browser actor had silently abandoned the journey after a step took longer than about 1.2 seconds. **Journey completion must therefore be a first-class gate in the report, reported separately from every check verdict**, because a green check does not mean the journey succeeded.

**Recommendation:** build a small, open-source Python CLI that takes a URL and a user journey, drives an isolated browser using the existing `journey-evals` policy, evaluates intermediate states, and returns an evidence-backed report that a developer or coding agent can act on. Prove this on a synthetic flight-booking application before building a general evaluation framework.

The long-term product is **journey execution + observations + evaluators + independent verification + actionable feedback**. Jev is the inexpensive semantic decision backend, not the browser, a vision model, a performance profiler, or the final authority on correctness.

**Baseline and implementation:** 18 September 2026. Source inspected: `browser-use/jev-ultrafast` at `452c1ad2dd628008f1d5608f28158d76e49e6cc0`, preserved separately at `C:\source\projects\jev-ultrafast`. `jev-test` is now the narrow source fork, with `journey-evals`, owned Chrome lifecycle, continuous evidence, independent booking verification, three advisory evaluators, a separated application source under `journey_evals\app`, and persistent bounded-cost receipts. All Git operations use WSL; there are no new commits or pushes. The CP3 holdout detected 9/9 defects with 0/9 false positives, the clean cohort verifies 3/3 bookings, and CP4 repaired 2/2 injected source defects with paired legitimate controls and restored-defect reproduction. The intermittent pre-inference Chrome stall was traced to Chrome itself and is now bounded and recorded rather than eliminated; the declared transport contingency was not required. See the [feasibility plan](..\jev-feasibility\plan.html) for current admission states. No production interaction occurred; the repair experiment ran only against disposable copies of the synthetic application.

The shared conversation was recovered beyond its abbreviated rendered view and read through the final MVP recommendation. The supplied `C:\Users\gargpratyush\Downloads\plan-jev-test.txt` matches the final three substantive discussions: autonomous E2E evaluation, an open-source business, and the smallest useful first release.

## 1. The first thing a developer should experience

The developer has a local travel application. A coding agent has just changed checkout. They run the proposed command:

```text
journey-evals run --url http://127.0.0.1:3000 --task "Find HYD to LHR on 2026-10-15 for one adult, enter the supplied test passenger, and complete a sandbox booking" --journey journeys\flight.json --out runs\flight
```

In the proposed product, the URL and task are enough for an **exploratory** run. The optional journey file supplies test data and independent acceptance checks, so a run can honestly become a passing test. This `run` subcommand is not implemented yet: the current `journey-evals` exposes only the four synthetic checkpoint commands documented in the repository README.

The useful output is not a stream of model reasoning:

```text
Journey: sandbox flight booking
Goal: verified complete
Experience: WARN

HIGH  Potentially unexplained fare change - review required
      Selection: INR 52,431; checkout: INR 61,892
      Price difference: measured in code
      Disclosure: semantic finding, requires review until calibrated
      Evidence: steps 7-8, fare DOM spans, before/after screenshots

WARN  Search response had no observed loading feedback for 1,800 ms
      Evidence: click timestamp, loading-state observations, results timestamp

Coverage: desktop 1120x780; common DOM controls; no image understanding
Artifacts: report.json, report.html, events.jsonl, evidence\
```

This is illustrative output, not a measured result. A completed booking and experience findings can coexist. The shown semantic finding is advisory, so the result is `WARN`; a verified blocking violation would produce `FAIL`. A confident `DONE` answer must not turn the report green.

The next useful integration is simply: **coding agent reads `report.json`, changes code, reruns the same journey and independent checks**. A hosted dashboard or autonomous repair orchestrator is not required to prove that loop.


> **Decision:** Start with a narrow fork of the inspected runtime, the three feasibility checks, a synthetic flight fixture, local JSON/HTML reports, and explicit uncertainty. Expand to the five public evaluator families only after the feasibility gates. Retain Python, the existing HTTP client, native browser telemetry, and the existing tests. Do not start with a TypeScript rewrite, plugin system, cloud service, or universal agent runtime.


## 2. What the conversation gets right, and what must change

The final direction is sound: build the evaluation layer rather than another browser agent. Several earlier claims would otherwise lead to the wrong implementation.

| Topic | Verified reality | Design consequence |
| --- | --- | --- |
| Jev can judge screenshots, video, or audio | Current Jev 1.13 accepts **text only**, including structured JSON; no image, audio, or video input [S3]. | Extract DOM facts and browser telemetry. Keep screenshots as evidence. Use a separate vision backend only when that feature is explicitly added. |
| Upstream independently verifies every task | The generic `Agent` accepts `DONE`; task-specific verification lives in `examples\flights.py`, and the recording script takes a fresh final observation [S5-S7]. | Add a general verification contract. Reuse the example's principle, not its route-specific checks. |
| A few post-action snapshots notice animation lag | The current snapshot omits geometry from its semantic marker and intentionally tolerates animations. It has no event/performance collector [S8-S9]. | Collect time-series telemetry continuously inside the browser. A final screenshot cannot prove what happened between observations. |
| Five broad questions discover every defect | Jev is designed for narrow questions and is explicitly weak at arithmetic, dates, indirection, and irrelevant context [S4]. | Keep five public families but use atomic, evidence-specific checks within them. Compute money, timing, and geometry in code. |
| Confidence 0.97 means 97% bug accuracy | Provider confidence summarizes its answer distribution, not measured accuracy on this application [S10]. | Preserve probabilities and confidence separately. Calibrate per evaluator and model version; do not print a fabricated probability that a bug is real. |
| A frontier answer is ground truth | A second model can repeat the same mistake. | Store model adjudication as a weak label; distinguish it from human confirmation and deterministic outcomes. |
| The seven-second demo proves general reliability | The matched experiment is three runs per arm on one task/profile. Initial navigation and final verification are outside the timed loop [S11]. | Retain it as engineering evidence, not a product SLA or broad quality claim. |
| Hundreds of model calls per second are available | Current default limits are 1,200 requests/minute and 250,000 input tokens/second, subject to change [S3]. | Batch questions, cap request/token budgets, and distinguish questions from requests. Twenty requests/second is the published RPM average, not a guaranteed sustained rate. |
| Open-source means application data stays local | Jev and the text helper are hosted inference services. | Local execution and local artifacts do not mean offline inference. Use synthetic data first and require an explicit data policy before real staging use. |

Nothing here is fundamentally impossible with Playwright plus an LLM. The product hypothesis is **less custom assertion authoring, useful intermediate-state coverage, better evidence, and lower semantic-evaluation cost at comparable quality**. That must be demonstrated, not asserted.

## 3. Scope and release boundaries

### Internal demonstration: Phases 0-2

One supported environment: Chromium, one isolated run at a time, an owned local application, common HTML/ARIA controls, and synthetic accounts/payment data. Start with a desktop journey and the three evaluator families needed for the first three seeded defects.

Deliver one CLI, three reliable seeded defects, a clean control application, a fresh final verifier, and local reports. Include the continuous input/feedback/result evidence required by those detectors. No frontier escalation is necessary: ambiguous checks become `UNKNOWN`, not invented certainty.

### V0: a useful local open-source alpha, Phase 3

Complete all five evaluator families and planned defect scenarios, add broader timing/jank fixtures, document unsupported surfaces, make setup and isolation repeatable, and harden the report schema. Add separately declared narrow-viewport and Back/persistence journeys. A narrow browser viewport is not native mobile-app support. This five-family, evidence-backed local release is the first public MVP; the internal three-family demo is not its completion gate.

### V0.1: CI and agent feedback, Phase 4

Headless execution, readiness checks, resettable test data, stable exit codes, JUnit output, artifact retention, a shadow/advisory CI mode, and a documented coding-agent feedback recipe. Only promote calibrated checks to blocking gates.

### V0.2: external pilot and calibration, Phase 5

Demonstrate useful behavior against a second small application that was not used to tune the initial evaluators. This is the gate for broader reliability claims and ecosystem expansion, not a prerequisite for the narrow local demo.

### Later, after evidence justifies it

Selective vision review, a Playwright observer adapter for existing deterministic journeys, bounded repair automation, evaluator packs, replay-based calibration, additional providers, and hosted infrastructure.

**Explicitly deferred:** native mobile/desktop control, voice execution, games, model routing, automatic Figma matching, general pixel diffs, arbitrary browser widgets, distributed runs, databases, cloud dashboards, marketplaces, RBAC, and model training. These do not belong in the first implementation.

## 4. What to reuse from the actual repository

The core agent, browser, model, questions, and snapshot total approximately 699 lines. That is small enough to keep understandable rather than wrapping in a large architecture.

| Existing surface | What it already does | Planned change |
| --- | --- | --- |
| `journey_evals\agent.py:12-174` | Bounded observe/predict/act loop, text-helper handoff, stale-page recovery, action history. | Add optional evaluation lifecycle wiring and immutable step records; leave ordinary `Agent` use unchanged when disabled. |
| `journey_evals\model.py:30-149` | Validates finite choices; builds operation and operation-specific target heads in one request. | Reuse `post_json` and `validate_choice`; add a separate batched evaluation request rather than duplicating HTTP infrastructure. |
| `journey_evals\model.py:151-198` | Generates a field value from context with strict JSON validation. | Preserve exact-context retry reuse. Supply synthetic journey facts through trusted context; never infer missing identity/payment details. |
| `journey_evals\questions.py:3-26` | Treats page text as untrusted; limits actions to 60. | Keep navigation prompts separate from evaluator rubrics. Add evaluation/request/time budgets in the CLI configuration. |
| `journey_evals\snapshot.js:1-107` | Atomic visible-state snapshot, live node identities, compatible action candidates, freshness guards. | Keep the action projection intact. Add a separate evaluation projection for disabled, clipped, and non-interactive elements. |
| `journey_evals\browser.py:20-194` | CDP session, current geometry, hit testing, native input, guarded mutations. | Explicit isolated endpoint, telemetry installation before navigation, session-scoped event collection, viewport option, and clear unsupported outcomes. |
| `examples\flights.py:18-38` | Independently checks one flight-search result. | Use as prior art for task verification. Its hardcoded Google checks are not the new generic verifier. |
| `scripts\record_flights.py:36-75` | Timestamped screencast, event draining, fresh final observation. | Reuse collection concepts, not the production script as a library. One owner must drain and dispatch all browser events. |
| `tests\test_agent.py:49-320` | Offline response validation, stale retries, single-consumption actions, text validation, and route checks. | Preserve these tests and add compact evaluation contract/fault-injection coverage. |
| `scripts\check_guards.py:17-137` | Real local-browser guard checks without model calls. | Extend for isolated sessions, telemetry continuity, clipping, disabled controls, navigation, and cleanup. |
| `journey_evals\demo.py:44-145` | Loopback inspector with locking and request protections. | Keep as a debugging tool. Do not make its global singleton the CI runner or build a replacement dashboard. |
| `journey_evals\static\fixture.html` | Self-contained hotel/research fixture using ordinary controls. | Reuse the fixture style, but add a separate flight fixture so upstream guard/smoke examples remain useful. |

**Fork rather than an adjacent wrapper for the first release.** An outer `for state in agent.run()` wrapper misses important lifecycle boundaries: initial state, consumed-but-not-executed decisions, failed post-action observations, terminal decisions, and ambiguous mutation failures. Monkey-patching these is worse than a few explicit hook points in a small fork.

Keep the reference clone unchanged. The preliminary investigation used an editable installation of that clone; this was a diagnostic wrapper, not the product integration. At the start of feasibility Checkpoint 1, after implementation approval, seed the implementation workspace from the pinned upstream source using WSL Git, preserve the existing plans and `.env`, retain the MIT notice, and record upstream provenance. Keep the `journey_evals` module name initially to reduce churn; expose a new `journey-evals` console script when the runnable CLI exists. The product should execute its owned fork, not depend on the neighboring reference directory. Section 13 of the [feasibility plan](..\jev-feasibility\plan.html) maps each reused component to its checkpoint and new responsibilities. Confirm distribution-name availability before publishing. Upstream generic lifecycle/isolation improvements can be proposed separately; the QA product does not need to become part of the original demo.

## 5. Architecture: three kinds of truth


<style>.architecture { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
.architecture section { padding:16px; }
.architecture h3 { margin:0 0 14px; color:var(--wf-ink); }
.architecture .diagram-card { padding:14px; margin:10px 0; }
.architecture p { margin:8px 0 0; color:var(--wf-muted); }
@media (max-width:720px) { .architecture { grid-template-columns:1fr; } }</style>
<div class="architecture">
  <section class="diagram-panel" data-rough>
    <h3>Owned local environment</h3>
    <div class="diagram-card"><strong>Journey + test data + acceptance checks</strong><p>Trusted input; versioned with the application</p></div>
    <div class="diagram-card"><strong>Isolated Chromium</strong><p>Guarded actions, DOM facts, events, timing, screenshots</p></div>
    <div class="diagram-card"><strong>Independent verifier</strong><p>Outcome assertions and exact numeric comparisons</p></div>
  </section>
  <section class="diagram-panel" data-rough>
    <h3>Local evaluation runtime</h3>
    <div class="diagram-card"><strong>Observation builder</strong><p>Immutable before/action/after records; explicit missing evidence</p></div>
    <div class="diagram-card"><strong>Evaluator policy</strong><p>Deterministic facts + semantic judgments + abstention</p></div>
    <div class="diagram-card"><strong>Report and replay data</strong><p>JSONL events, JSON result, HTML evidence; no database</p></div>
  </section>
  <section class="diagram-panel" data-rough>
    <h3>Hosted inference boundary</h3>
    <div class="diagram-card"><strong>Jev</strong><p>Finite action choices and batched semantic checks; text only</p></div>
    <div class="diagram-card"><strong>Small text helper</strong><p>Field values from supplied facts; no browser commands</p></div>
    <div class="diagram-card"><strong>Later: selective vision review</strong><p>Opt-in, separately budgeted and separately attributed</p></div>
  </section>
</div>


Keep three different claims separate throughout the implementation:

**Measured fact:** a DOM value, HTTP status, observed state transition, target rectangle, elapsed time, or independent acceptance result.

**Semantic interpretation:** whether the available copy explained a fare change, whether a transition was inconsistent with the stated intent, or whether progress appears stalled.

**Unverified hypothesis:** a suspected cause or suggested source-code location. Do not report these as facts. V0 should omit guessed file names entirely; later diagnostics may use actual stack traces, source maps, and repository evidence.

The actor and evaluator may both use Jev, but evaluators receive observed actions and evidence, not the actor's conclusion that it succeeded. This reduces circular reasoning without pretending two calls to one model are independent oracles.

## 6. Observation and telemetry design

### Two projections, not one overloaded snapshot

The existing action table excludes disabled controls and targets whose centers are outside the viewport. It caps action candidates at 250 and visible text at 6,000 characters. A disabled search button or clipped payment CTA may therefore be absent from the very table the agent uses [S8].

Preserve those restrictions for **execution**. Build a separate bounded **evaluation** view of relevant controls, headings, validation messages, status regions, selected objects, prices, and rectangles, including non-actionable elements. Include clipping ancestors, disabled/read-only state, visibility, focus, and hit-test evidence when relevant. Do not make an unclickable element executable just to inspect it.

Surface `omitted_actions`, omitted evaluation elements/text, unsupported frames/widgets, and unavailable APIs in the report. Missing evidence is `UNKNOWN` or uncovered, never `PASS`.

### Install observers before the page loads

In the owned CDP session, install the telemetry script before initial navigation, enable the required `Page`, `Runtime`, and `Network` domains, and preserve collection across document replacements. Attach SPA route changes to the current document; assign a new document ID on actual navigation.

Use native APIs rather than inventing an animation profiler:

| Signal | Mechanism | Correct interpretation |
| --- | --- | --- |
| Runtime exceptions and console errors | CDP events | Concrete error evidence; expected negative-path errors can be declared in a journey. |
| Requests, failures, status, duration | CDP Network events | HTTP failure or duplicated request is observable; causality and business harm are separate judgments. Do not record bodies/headers by default. |
| Interaction responsiveness | `PerformanceObserver` with `event` entries | Native event timing, not time spent waiting for Jev. Minimum threshold is 16 ms; values are quantized [S13]. |
| Long animation frames | Feature-detected `long-animation-frame` entries | Frames beyond 50 ms; retain contributing script information if supplied [S12]. A slow animation duration alone does not prove jank. |
| Layout movement | `layout-shift`, relevant bounding-box changes | Preserve `hadRecentInput`. Raw interaction shifts are useful even when excluded from CLS [S14]. Do not call a simple sum the official CLS score. |
| Loading feedback | Time-stamped changes to busy/status/progress regions and visible response state | Detect an observed feedback gap. Absence of instrumentation does not prove no visual feedback existed. |
| Transient disappearance | Scoped mutation/visibility timeline | A state that vanished and reappeared can be seen even if both endpoint snapshots look fine. |
| Visual evidence | Initial, meaningful-step, failure, and final screenshots | Human evidence only in V0. Optional screencast is not a guaranteed 60-fps performance trace. |

Preserve default motion, focus, and rendering conditions in experience runs. Disabling animations to stabilize screenshots would hide the defect being tested. Record browser build, viewport, scale factor, locale, reduced-motion setting, headless/headed mode, and CPU/network settings. Background focus emulation and headless execution can affect timing; compare like with like.

### One event owner and explicit clocks

The pinned Browser Harness `drain_events()` empties a daemon-wide queue [S15]. A recorder and telemetry collector must not independently drain it: each would steal events from the other.

Use one collector for the dedicated run daemon, dispatch by CDP session/target, acknowledge screencast frames, and persist bounded event batches. Capture events during model calls, not only after them. Record dropped-event counts and collector errors. If required evidence is lost, affected checks are inconclusive.

Give each event a monotonic run sequence, run-relative timestamp, original browser timestamp, and document ID. Normalize `performance.timeOrigin + entry.startTime` through an explicit clock anchor; record uncertainty. Never subtract a host `perf_counter()` value directly from a browser timestamp.

Give every document a random ID and every exported observation a sequence number. Register a CDP runtime binding before navigation; the telemetry script emits compact event batches and periodic sequence watermarks through that binding to the single session collector. Flush pending observer records at action boundaries and on `pagehide` where available, but do not assume an unload callback is guaranteed.

Record each window's first/last sequence, last acknowledged watermark, document-destruction boundary, collector/drop errors, and `complete`, `partial`, or `unavailable` state. A final `Runtime.evaluate` or periodic drain alone cannot recover a destroyed document's buffer. Navigation without a confirmed closing boundary makes the outgoing tail partial, even if no dropped sequence is detectable. Positive facts already delivered remain usable; absence claims over that tail become `UNKNOWN`. The Phase 0 navigation test must prove retained evidence or explicit incompleteness, not just observer reinstallation.

### Scope and overhead limits

Start with the action target's form/dialog/row, important status regions, and a bounded visible-element scan. Do not compute every possible overlap pair on every frame. Attach persistent cross-step facts such as the selected flight and fare; a ten-action tail alone can forget them.

Record capture/serialization/model/wait time separately. Set explicit byte/event/screenshot caps and report truncation. Before calling this lightweight, measure instrumentation-on versus instrumentation-off on the same fixture. Performance findings should remain advisory until their measurement overhead is characterized.

## 7. The first five evaluator families

Five families are an understandable public surface, not five giant questions that ask whether the entire site is good. Use plain functions and a small data table; no class hierarchy or plugin loader.

| Family | Initial checks and candidate space | Evidence and behavior |
| --- | --- | --- |
| `task_progress` | `PROGRESSING`, `STALLED`, `BLOCKED`, `COMPLETION_CANDIDATE`, `UNKNOWN` | Compare requested milestones, actual visible state, and recent execution. Repeated/no-op actions are measured. Completion remains only a candidate until independently verified. |
| `unexpected_state` | `EXPECTED`, `EXPLAINED_CHANGE`, `UNEXPLAINED_CHANGE`, `UNKNOWN` | Compare before/after selections, route, messages, and stable journey facts. Fare arithmetic is code; whether an observed disclosure explains the change is semantic. |
| `interaction_correctness` | `EFFECT_OBSERVED`, `STILL_PENDING`, `CONTRADICTED`, `UNKNOWN` | Did the intended field retain its value, did the selected option apply, did an action produce the declared effect? Navigation, backend persistence, and response timing need their own evidence. |
| `layout_integrity` | Measured clipping, obstruction, overflow, missing/broken resource candidates; semantic `RELEVANT`, `BENIGN`, `UNKNOWN` | Geometry and hit testing first. Do not flag all offscreen content, nested rectangles, carousels, or intentional overlays. No claims about aesthetics, colors, image meaning, or Figma similarity in V0. |
| `experience_feedback` | `ADEQUATE`, `MISSING`, `CONTRADICTORY`, `UNKNOWN` for a specific action window | Loading acknowledgement, understandable validation/error copy, and observed transient instability. Actual latency/jank thresholds are computed, not guessed by Jev. |

Start the demo with unexpected-state, interaction, and loading-feedback checks. Add progress and bounded layout checks immediately afterward. Keep hard failures narrow: declared acceptance violations, reproducible measured contradictions, or a separately calibrated semantic check. New semantic rubrics begin in advisory mode.

**Fare example:** parse observed monetary candidates with explicit currency/locale, represent amounts in integer minor units, preserve fare basis/passenger count/taxes, and compare like with like. If matching amounts is ambiguous, abstain. Give Jev the bounded question: *Does this observed notice explain the already-measured increase and request acknowledgement?* Do not ask Jev to subtract two prices or assume any increase is a bug.

**Evidence selection:** assign IDs to relevant observations. An evaluator's question includes the exact candidate under review; the result inherits those IDs. If later needed, a second Choice head can select among enumerated evidence candidates. Jev does not generate arbitrary issue prose or source locations.

**Reporting:** render deterministic templates using actual measured values. Keep raw provider probabilities, provider confidence, evaluator version, model ID, and decision policy. Label a semantic confidence as a model signal, not "97% certain this bug exists."

## 8. Runtime integration and failure semantics

For V0, use a separate evaluation request after each meaningful post-action observation. Batch all applicable semantic checks into that request. This is easier to debug and avoids modifying navigation inputs before the evaluation hypothesis has been tested.

The lifecycle is:

1. Launch the isolated browser and collector; record setup and initial observation.
2. Evaluate the initial state for applicable checks.
3. Predict a supported operation and target using the existing policy.
4. Persist an action-attempt record, recheck freshness, and execute at most once.
5. Persist execution acknowledgement before observing the result, preserving the upstream invariant.
6. Capture the post-action state and event window; run deterministic checks and one batched semantic evaluation.
7. Continue, wait with a deadline, or stop for a policy/coverage/runtime condition.
8. On `DONE`, `BLOCKED`, budget exhaustion, exception, or cancellation: take the best available final observation, run eligible final checks, finalize artifacts, and release owned resources.

```python
before = freeze_observation(state["page"])
record_action_attempt(step_id, action, before)
browser.act(action, state["page"], text=text)
record_action_executed(step_id, action)
after = observe_and_freeze(browser)
record_transition(step_id, before, action, after)
evaluate_transition(step_id, before, action, after)
```

- Lines 1: Agent snapshots are shallow today; persisted evaluation state must not change as the run continues.
- Lines 3-4: Retain freshness guards and no automatic retry after input may have occurred. An uncertain mutation result stops the run.
- Lines 5-7: A failed post-action observation must preserve the executed action and leave the evaluation pending/inconclusive, not cause another click.


**Do not evaluate every stale retry as another user action.** Use a unique step ID and explicit attempt/executed/observed/evaluated states. Deduplicate findings by evaluator, subject, requirement, and contiguous episode; retain first/last occurrence and evidence rather than deleting repetitions.

**Observe settling, not arbitrary sleep.** Preserve upstream's brief interaction/combobox waits. Add a bounded task-specific response window with observed readiness, intermediate event capture, and a deadline. A model saying `WAIT` is not proof of loading. Avoid universal `networkidle`, especially for streaming applications.

**Do not hide provider failures.** Retry only read-only inference calls under bounded backoff and a total deadline, respecting `Retry-After`. Malformed/missing evaluation heads make those checks unknown and the evaluation incomplete. Never convert an unavailable model or invalid response into a passing result.

V0 runs serially. Later, after correctness and measurement, evaluation of transition N can share a request with navigation for N+1 when their evidence projection is compatible. Independent heads cannot read each other's answers. A terminal flush is still necessary. This optimization is a measured option, not a prerequisite.

## 9. Public data contracts and local persistence

Use Python dataclasses or typed dictionaries plus explicit boundary validation. `argparse`, `json`, `pathlib`, `hashlib`, `html.escape`, and the existing `httpx` dependency cover the initial implementation. Do not add Pydantic, a workflow engine, a database, or a frontend framework just to serialize a small report.

The hard-to-reverse interface is the **versioned artifact**, not an HTTP service.

| Contract | Required fields |
| --- | --- |
| `JourneySpec` | `schema_version`, `id`, `mode`, `task`, target URL, synthetic input facts, allowed origins, required probes/checks, independent acceptance checks, viewport, budgets. User-authored; not executable text from a webpage. |
| `Observation` | `id`, document/step IDs, time anchors, URL/title, bounded DOM facts, action candidates, evaluation elements, persistent journey facts, telemetry window, evidence refs, capability/truncation metadata. |
| `Evaluation` | evaluator ID/version, subject/requirement IDs, observation/window refs, verdict, measured facts, raw model answer/probabilities/confidence if used, policy version, review state. |
| `Finding` | stable finding ID, severity, category, concise title, observed/expected facts, evidence IDs, first/last step, reproducibility, deterministic/semantic provenance, confirmation status. |
| `RunResult` | run ID, schema/tool/upstream/model versions, effective-spec hash, environment, execution status, goal status, overall result, determining check/finding IDs and policy, coverage, findings, errors, timings, usage/cost provenance, artifact refs. |

**One effective specification:** normalize CLI-only exploration and journey-file runs into the same immutable specification before browser launch. With a file, supplied URL/task must match its values; reject conflicts rather than silently overriding the actor's task without changing the oracle. Omitted CLI values come from the file. Record the original inputs, resolved defaults, and a hash of the effective specification. A valid `explore` run may omit acceptance checks and ends inconclusive for success certification; a `verify` run missing them is a configuration error before any mutation.

**Minimal coverage contract:** each declared probe has an ID, actor-facing instruction, and required check IDs. Each check has an ID, scope (`transition` or `final`), a trusted applicability/trigger condition, an expected effect or evaluator rubric ID, a deadline, required evidence types, and a required/advisory flag. Use a small fixed set of existing DOM/URL/event predicates, not a general workflow language. Keep the actor instruction, evaluation expectation, and independent acceptance assertion separate.

Track each required check as `pending`, `observed`, `passed`, `failed`, `unknown`, or `not_applicable`, with linked evidence. Only a proven applicability condition can produce `not_applicable`; an unvisited state leaves a required check pending/unknown and coverage incomplete. A Back probe must record entry, the Back action, revisit, and persistence evidence. A final booking cannot substitute for that unexecuted probe. Expectations inferred by the model are advisory unless approved in the trusted specification.

Use a random UUID for `run_id`, monotonically increasing event/step numbers, and document-scoped node IDs. Upstream node identities do not survive navigation and must not become globally stable element IDs. A content hash can deduplicate evidence; it is not a secret-redaction mechanism.

```json
{
  "schema_version": 1,
  "execution_status": "completed",
  "goal_status": "verified",
  "result": "WARN",
  "result_basis": [
    {"check_id": "goal:booking_record", "effect": "goal_verified"},
    {"finding_id": "finding-001", "policy": "semantic_advisory_v1", "effect": "warn"}
  ],
  "coverage": {
    "viewport": {"width": 1120, "height": 780},
    "visual_mode": "dom_geometry",
    "missing_required_evidence": []
  },
  "findings": [
    {
      "id": "finding-001",
      "evaluator": "fare_change_disclosure",
      "evaluator_version": 1,
      "severity": "high",
      "confirmation": "review_required",
      "measured": {"currency": "INR", "before_minor": 5243100, "after_minor": 6189200},
      "evidence_ids": ["step-007-selection", "step-008-summary"],
      "model_signal": {"choice": "UNEXPLAINED_CHANGE", "confidence": 0.91}
    }
  ]
}
```

This verified, advisory-only example is `WARN`. `FAIL` requires a verified blocking violation or an explicitly promoted semantic policy, and `result_basis` must identify the determining check/finding plus its applied policy. Validate those references when finalizing the report; a top-level result without supporting records is invalid.

Store one `events.jsonl` append-only journal, one atomically replaced `report.json`, one static `report.html`, and an `evidence` directory per run. Derive the readable report from persisted events rather than separate hidden state. Flush action execution before further observation; on storage failure stop instead of taking unauditable actions. A crash leaves an incomplete journal, never an implied success.

Build the HTML with escaped untrusted text and local relative evidence links; do not inject page HTML or model content as executable markup. No remote scripts, analytics, or fonts in generated reports. "Replay" in V0 means inspecting the recorded timeline; it does not mean blindly re-executing recorded mutations.

## 10. Independent success and honest result states

`DONE` stops navigation; it does not satisfy an acceptance contract.

Start with a small declarative verifier: expected URL/path, required observed text/control values, and trusted fixture-specific confirmation/persistence checks. Acceptance checks are independent of the actor's answer, run against a fresh state, and are versioned with the journey. More complex application checks can later use a trusted Python callback or an existing Playwright test.

A visible "Booking complete" banner alone is insufficient for persistence. The synthetic flight fixture should expose an independent test-side check that exactly one booking exists with the intended route, date, passenger count, fare, and test payment state. The actor/evaluator do not receive the hidden ground-truth result.

| Result | Meaning | Proposed exit code |
| --- | --- | --- |
| `PASS` | Required outcome verified; required coverage complete; no blocking finding. Applies only to this declared journey and environment. | 0 |
| `WARN` | Verified outcome with advisory findings; no blocking violation. | 0, or 1 under an explicit warn-as-error policy |
| `FAIL` | A verified acceptance violation or approved blocking evaluation policy failed. | 1 |
| `INCONCLUSIVE` | Missing verifier/evidence, unsupported required interaction, uncertain mutation, budget exhaustion, or unresolved mandatory semantic check. | 2 |
| `ERROR` | Browser, provider, collector, artifact, or runner failure prevents evaluation. | 3 |
| Configuration error | Invalid journey, unsupported required configuration, missing credentials. No browser mutation. | 4 |

Preserve the full dimensions even when selecting one exit code. For example, an independently observed defect remains in the report if a later provider outage makes the run an `ERROR`. Define deterministic precedence for automation: configuration/runtime failure, then verified failure, then incomplete coverage, then warning/pass.

The default URL-plus-task exploration can report useful findings but cannot produce `PASS` without an explicit independent success contract. This is the necessary tradeoff between easy onboarding and trustworthy CI.

## 11. Browser isolation, safety, and privacy

Upstream creates a new tab in the existing Chrome profile; it does not isolate cookies or local storage [S9]. That is unsuitable as the default QA execution environment.

Use one owned Chrome for Testing process with a fresh, non-default profile for each run, a loopback CDP endpoint, and an isolated Browser Harness runtime/config directory. The pinned `browser-harness==0.1.13` supports `BU_CDP_URL`/`BU_CDP_WS` [S15]; this is not a speculative dependency upgrade. Set environment before importing harness modules, which capture configuration at import time.

A short Phase 0 spike must prove headless connection, explicit session events, fresh-profile separation, and cleanup against the pinned dependency. Prefer this reuse path. Only if it fails the bounded spike should we replace the transport with Playwright's mature Chromium/session lifecycle; do not maintain two browser backends in V0.

Modern Chrome requires a non-default data directory for remote-debugging flags; Chrome for Testing is recommended for automation [S16]. Never attach a QA run to the user's normal signed-in browser by default.

Safety requirements are part of the first slice:

- V0 runs against owned local fixtures and synthetic data. Real purchasing, sending messages, deleting accounts, and production write workflows are outside scope.
- Permit only configured origins and supported actions. Enforce navigation/request restrictions before side effects where supported; a post-navigation URL check is not an isolation boundary.
- Do not rely on a model or a button-label denylist to prevent real transactions. Use sandbox endpoints, test accounts, server-side restrictions, and no production payment credentials.
- Do not claim a browser origin allowlist is a complete network sandbox. A later arbitrary-staging mode needs tested browser egress restrictions, redirect/subresource/WebSocket handling, and OS/container isolation where required.
- Missing passenger/payment facts stop the journey; neither Jev nor the text helper may invent them.
- App-controlled text and network content are untrusted data. They cannot change goals, evaluator definitions, budgets, allowed origins, local paths, shell commands, or the success oracle.
- Keep provider keys in the runner, never in page state. Redact DOM text, URL query values, form values, logs, and telemetry before model submission and persistence. Existing password-field exclusion is not comprehensive PII protection.
- Do not capture response bodies or authentication headers by default. Screenshots can still contain sensitive data; limit initial examples to synthetic content and make raw screenshot/recording collection explicit.
- No automatic trace upload or community data contribution. Local execution can still send redacted state to the configured inference providers; document that plainly.
- On cancellation or failure, close only owned tabs/processes/daemon resources and remove only the explicitly created temporary profile. Persist useful evidence first.

## 12. Proving the product with a flight fixture

Keep the fixture small: static HTML/JavaScript plus a minimal local fake booking backend. Reuse the upstream fixture's ordinary controls and styling approach. Do not add React or a design system merely to demonstrate the evaluator.

The clean fixture must support search, selecting a flight, passenger entry, an application Back control, checkout, and a sandbox confirmation. Use fixed dates, seeded fares, and synthetic passenger data. Start with an application Back button, because the upstream action vocabulary does not provide browser-history navigation.

Inject one fault at a time through test-only server configuration. **Do not expose fault names in URL query strings, page text, DOM attributes, or model input.** Otherwise the evaluator can identify the fixture flag instead of discovering the defect. Keep ground truth and reset controls out of the actor's observation.

| Scenario | How it is exercised | What proves detection |
| --- | --- | --- |
| Clean journey | Complete a sandbox booking. | Fresh independent record check passes; no spurious blocking finding. |
| Search does nothing | Submit valid search criteria. | Acknowledged input, no expected effect by the declared deadline, and a relevant evidence window. |
| Silent fare increase | Select one fare, proceed to checkout. | Same fare basis/currency, exact measured increase, and calibrated/reviewed absence of an explanatory confirmation. Also test a correctly disclosed increase. |
| Missing loading feedback | Delay results while removing the loading state. | Native click/results timestamps plus continuous evidence that expected feedback was absent during the interval. Also test fast responses and alternate valid feedback. |
| Narrow-viewport clipped CTA | Run a separate 390x844 browser journey. | Relevant CTA intersects clipping/viewport boundaries and is unusable; do not infer mobile quality from a desktop screenshot. |
| Passenger details lost on Back | Enter supplied data, use the app Back control, revisit. | Explicit probe observes missing values; it is not detectable from a happy path that never goes back. |
| Timing/jank extension | Add a main-thread blocking task, moving target, and transient result disappearance. | Native event/LoAF/shift evidence plus observed transition timeline, not Jev estimating milliseconds. |

Each defect has a paired clean/legitimate variant. This matters as much as the broken demo: otherwise every surprising state can be labeled a bug.

A single run is not coverage of every scenario. The journey set explicitly includes normal booking, Back/persistence, and a narrow viewport. Add invalid-input and slow-network probes later; do not silently imply they ran.

### Initial evaluation experiment

Use two measurement layers:

**Frozen observation corpus:** manually label approximately 40-60 small evidence windows covering the initial families, clean controls, missing evidence, ambiguous fares, and legitimate loading/layout behavior. Split by scenario/application into development and holdout sets; do not scatter near-duplicate snapshots across both.

**Live journeys:** execute clean and fault-injected variants repeatedly from fresh browser and backend state. Begin with ten repeats per implemented scenario. Report observed counts and uncertainty; this is a debugging/reliability signal, not a statistically strong guarantee.

Compare: deterministic checks alone; deterministic checks plus Jev; and, when budget permits, a conventional structured-output LLM given the same evidence and questions. Measure bug-level precision/recall, clean-run false alarms, abstention/coverage, verified completion, reproducibility, provider cost, end-to-end latency, and instrumentation overhead.

Proposed local-alpha acceptance targets, to confirm in Phase 0: at least 9/10 clean completions, no blocking false positives on those clean runs, and at least 8/10 detections for each seeded in-scope defect. Count a detection only if its category, subject, and evidence match the hidden seeded defect. A missed defect caused by failure to reach the state is reported as an execution/coverage failure, not erased from the denominator.

Gate advisory quality too: no more than one clean/legitimate-control run in ten may receive a spurious actionable semantic finding, and independently reviewed actionable semantic findings must reach at least 80% precision across the paired initial corpus. Report the small denominator and uncertainty. An evaluator that flags every state fails these gates even if nothing is configured to block CI. Required unknowns and execution failures remain visible in coverage rather than being counted as accurate negatives.

Do not make semantic checks blocking solely because they meet these tiny-sample targets. Blocking promotion requires a materially larger, representative holdout set and an agreed false-positive tolerance. At thousands of checks per CI run, even a small per-check false-positive rate can make every PR fail.

## 13. Cost and latency: what "10x better" must mean

Jev's published input price is $0.042 per million tokens, with free output [S3]. At that rate:

```text
25 evaluation requests x 6,000 input tokens = 150,000 input tokens
Estimated Jev evaluation charge = $0.0063

Another 150,000 actor input tokens would add $0.0063.
Text-helper, browser execution, storage, reruns, and optional vision costs are extra.
```

This is an arithmetic illustration, not a run measurement or billing guarantee. The current recorded upstream run contains 90,558 Jev input tokens; at the published rate that implies about $0.0038 for Jev, but upstream itself did not report a billed Jev dollar total. Its $0.00006272 figure is **only the two text-helper calls** [S11].

Count actual `usage.input_tokens` for the whole request, including questions. Five checks batched into one request do not mean five complete state prefills; they also do not mean questions have no token cost. Respect both the total 64k context limit and the 32k state-plus-longest-question limit [S3]. Keep V0 payloads far below them and report truncation.

Adding a separate evaluation call after 20 steps could add approximately 3.6 seconds if latency matched upstream's 178 ms median. That is only a planning illustration; evaluation payloads and network conditions differ. Measure p50/p95 from before browser launch through final report persistence, and separately show setup, interaction, inference, wait, verification, and reporting.

The defensible 10x claim is **cost per correctly resolved semantic check at an agreed precision and coverage**, compared with an actual baseline. Do not compare a text-only Jev check with a vision model doing a richer job. Pure geometry, console, or arithmetic checks should be compared against deterministic code, which is cheaper than any model.

If Jev adds little beyond deterministic detectors, or requires frequent expensive vision interpretation, narrow the product or change the backend. Do not build a cloud business around an unproven cost advantage.

## 14. Implementation sequence and acceptance gates

**Use the [feasibility checkpoints](..\jev-feasibility\plan.html) for the next implementation work.** The phases below describe the broader release scope, not permission to postpone repair proof or second-app transfer until after framework construction. Checkpoint 7 in that plan is the framework admission decision; update this roadmap from the resulting evidence before proceeding.

Estimates assume one experienced engineer, API access, and a working supported browser. They are planning ranges, not promises. A three-day demonstration is plausible only for the narrow slice; a reliable public CI tool is not a three-day deliverable.

| Phase | Reuse first; new work second | Exit gate | Estimate |
| --- | --- | --- | --- |
| 0. Prove the foundations | Reuse pinned runtime and local guard fixture. Prove isolated headless CDP, telemetry events, fresh final checks, and a few labeled Jev questions. | No personal-profile attachment; immediate navigation during inference retains evidence or explicitly marks an incomplete window; sample evaluator results are useful; external-call/data policy understood. | 0.5-1.5 days |
| 1. Walking skeleton | Reuse `Agent`, HTTP helpers, existing tests. Add CLI, journey/result contracts, immutable transitions, durable JSONL, independent verifier, cleanup. | One clean synthetic journey yields a verifiable result and readable artifacts; errors and missing oracles cannot pass. | 1-2 days |
| 2. Internal three-family demo | Reuse browser state and fixture patterns. Add the minimal continuous input/feedback/result timeline and completeness accounting, three families, three hidden fault injections, batched evaluation, terminal/HTML output. | Clean versus broken evidence demonstrates at least one useful non-handwritten semantic finding without leaking the fault label. Loading-feedback absence is not judged from endpoint snapshots. | 1-2 days |
| 3. V0 local public alpha | Complete five families, separate evaluation projection, narrow viewport, Back/persistence, broader LoAF/layout/transient-state evidence, redaction and caps. | All declared scenarios have reproducible artifacts, explicit coverage, paired negatives, and both detection and advisory-quality alpha targets. | 3-5 days |
| 4. V0.1 CI and feedback | Reuse the exact local runner. Add headless readiness/reset, JUnit, artifact handling, advisory CI, machine-readable feedback recipe. | A preview build runs, failure feeds a coding agent, a fix reruns the unchanged oracle, and the negative controls still work. | 2-4 days |
| 5. V0.2 external pilot and calibration | Reuse persisted observations and evaluator versions. Try a second app and review findings with developers. | Useful precision/coverage outside the demo; thresholds have provenance; maintainers can reproduce a contribution. | 3-5 days |

A sensible first public alpha is roughly **two to four weeks**, depending on browser/telemetry issues and pilot feedback. The first demo can be earlier. Keep each phase independently useful and stop expanding if semantic quality fails the gate.

### First implementation patch

The first code patch should cover Phase 0 and the smallest Phase 1 path: owned-browser connection, lifecycle records, one declared final verifier, and a JSON report. Do not mix it with a full evaluator catalog or redesigned inspector.

### Proposed file map

All new names below are proposed, not existing implementation.

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Add `journey-evals` entry point; retain current dependency strategy and pinned harness. |
| `journey_evals\cli.py` | `run` command, journey/config validation, budgets, exit codes, resource ownership. |
| `journey_evals\evaluation.py` | Typed observation/results, five families, atomic rubrics, deterministic checks, one batched semantic call, aggregation policy. Split only when real growth warrants it. |
| `journey_evals\report.py` | Event writer, redaction boundary, JSON/HTML rendering, later JUnit. |
| `journey_evals\telemetry.js` | Bounded native observers and relevant DOM/geometry observations installed before navigation. |
| `journey_evals\agent.py` | Small optional lifecycle additions at initial/action/terminal/error boundaries. |
| `journey_evals\browser.py` | Owned endpoint, telemetry/session integration, viewport, and explicit capability/error reporting. |
| `journey_evals\model.py` | Reuse validators/transport; make request deadlines and retry accounting explicit for the new runner. |
| `examples\flight_app.py`, `examples\flight.html` | Minimal local fake backend and deterministic synthetic application with hidden test-controlled faults. |
| `examples\journeys\flight.json` | Trusted test facts, separate probes, allowed origins, and independent outcome checks. |
| `tests\test_evaluation.py` | Parameterized contract, aggregation, unknown/error, evidence, and redaction checks using existing pytest. |
| `scripts\check_guards.py` | Extend local browser checks without paid APIs. |
| `README.md`, `.env.example`, `.gitignore` | Accurate scope, data egress, limits, setup, ignored artifacts/secrets, contribution recipe. |
| `.github\workflows\qa.yml` | Add only in the CI phase; use the same CLI, not a second runner. |

The CLI and trusted journey definitions can validate inputs themselves initially. No plugin discovery, abstract provider factory, per-evaluator package tree, or configuration for speculative future platforms.

## 15. CI and the coding-agent loop

A real CI integration needs more than starting `npm run dev` in the background. Build the application, start its owned test process, wait on an explicit readiness endpoint with a deadline, seed/reset test data, run the journey, upload permitted artifacts even on failure, and stop owned processes in cleanup.

Pin tool/browser/model/evaluator versions. Use the same viewport, locale, dates, seeded data, and budgets locally and in CI. Retain original failures when rerunning for diagnosis; do not rerun until a pass and discard the evidence.

Begin with two modes:

**Advisory:** evaluate and publish findings without making uncalibrated semantic checks a merge gate. Actual runner failures remain visibly distinct from findings.

**Blocking:** require the declared independent outcome and approved deterministic/calibrated checks. The gate cannot pass when mandatory evidence is missing.

For untrusted fork PRs, run offline contracts and synthetic browser checks without inference secrets. Do not use `pull_request_target` or a privileged `workflow_run` to check out and execute untrusted code [S17]. Live model-backed runs require a trusted, approved workflow with separated credentials and infrastructure. Validate artifacts consumed by privileged follow-up jobs; HTML/JSON from a PR is not trusted code.

The first coding-agent integration is provider-neutral:

1. The agent builds the feature and runs ordinary unit/lint/build checks.
2. The journey runner emits a versioned report and evidence folder.
3. The agent reads confirmed findings and unresolved coverage separately.
4. It fixes the application, not the evaluator or the acceptance checks.
5. The unchanged journey, seeded controls, and ordinary regression tests run again.

A future `--fix` is an explicit opt-in local command, not part of V0. It should cap iterations, use a dedicated worktree when available, preserve user changes, treat page/report text as untrusted, require confirmation for consequential actions, and never auto-commit, push, or weaken tests. A successful rerun alone does not prove the bug was fixed unless the relevant scenario and unchanged oracle ran.

## 16. Covering the six framework pain points without building a framework first

| Pain point | Initial implementation | Evidence-driven extension |
| --- | --- | --- |
| State construction | Bounded before/action/after records, persistent journey facts, native telemetry, explicit missing evidence. | Add a new surface only when a concrete second adapter needs it. |
| Candidate generation | Deterministic action candidates and relevant observation/evaluator candidates with stable local IDs. | Hierarchical shortlisting only if real candidate sets exceed useful limits. |
| Question design | Atomic versioned rubrics, boundary examples, clean/fault fixtures, clear applicability. | Community evaluator packs with their own tests and documented evidence requirements. |
| Calibration | Preserve full distributions; conservative advisory defaults; holdout corpus. | Per-evaluator/model thresholds with measured precision/coverage and larger representative datasets. |
| Escalation and timing | `UNKNOWN`, bounded waits, manual review; no mandatory frontier call. | Selective text/vision adjudication from frozen evidence, with a cost cap and clear attribution. |
| Learning from outcomes | Local decisions, human corrections, independent outcomes, and model labels stored distinctly. | Offline replay to compare rubric/state changes; opt-in redacted contributions; no automatic retraining loop. |

Do not collect only disputed/escalated examples: that biases calibration. Sample some confident passes and failures for human review too. Re-evaluating a stored observation is not a browser rerun and cannot prove that a new policy would reach the same states.

## 17. Open-source and business design

Keep the core genuinely usable: local execution, evidence extraction, evaluator definitions, reports, offline replay, basic CI, and the feedback format should be open. Recommended initial license: retain MIT for the fork and modifications, preserve upstream copyright, and maintain clear provenance. Revisit commercial/legal needs before publishing rather than retrofitting a restrictive license after adoption.

The community contribution unit is a **reproducible evaluator improvement**: rubric or state change, positive and negative fixtures, evidence requirements, supported surfaces, and an accuracy/coverage comparison. A catalog of untested prompts is not an ecosystem.

Monetize later around hosted isolated browsers, concurrency, scheduled runs, artifact history/search, team review, managed CI/agent orchestration, private runners, governance, and support. Keep the local feedback loop open; sell the operational convenience of running it across a team.

Do not charge first for a thin Jev API proxy, and do not assume proprietary cross-customer traces will become a moat. Most useful test evidence can contain sensitive application data. Any shared evaluation corpus must be explicitly contributed and sanitized.

Expansion order: existing Playwright journeys as an observation source; selective vision checks; additional web/API evaluators; other surfaces only after the browser product works. For a later voice adapter, reuse timestamped transcripts/tool events and evaluation contracts, not DOM concepts. Text-only Jev would not replace STT, acoustic VAD, TTS, or audio-quality measurements, and any latency/cost advantage needs a separate experiment.

## 18. Validation and release criteria

Preserve the upstream commands and add focused coverage:

```text
uv run ruff check .
uv run pytest
node --check journey_evals\snapshot.js
node --check journey_evals\static\app.js
node --check journey_evals\telemetry.js
uv run python scripts\check_guards.py
uv build
```

`telemetry.js` is a proposed file. Tests remain offline unless explicitly selecting a paid live evaluation run. Do not add a new test framework.

Important regression cases: invalid evaluation heads; NaN/missing probabilities; no verifier; `DONE` with wrong final state; three stale retries without duplicate findings; mutation acknowledged before a failed observation; mutation result unknown; model outage; telemetry unavailable/dropped; navigation during collection; artifact write failure; cancellation; redaction; report escaping; clipped/disabled elements absent from the action table but present in evaluation state; text-helper cache invalidation; initial/final-state checks; and preservation of all upstream guard behavior.

The real end-to-end smoke is: fresh directory/profile and reset fake backend, start the flight fixture, wait for readiness, run a clean journey, verify the persisted booking and artifacts, enable one hidden defect, rerun, inspect the reported evidence, fix the app, rerun the unchanged journey, and confirm the defect disappeared without suppressing its evaluator.

### Release checklist

- [ ] Fresh owned browser and fake backend; no personal browser/profile access
- [ ] DONE cannot pass without fresh independent checks and declared coverage
- [ ] Clean controls and every supported seeded defect have reproducible evidence
- [ ] Provider, telemetry, browser, and artifact failures never become PASS
- [ ] Publish actual precision, coverage, cost, latency, and instrumentation overhead
- [ ] Document model data egress; verify redaction, HTML escaping, and explicit evidence capture
- [ ] A coding agent can fix one confirmed defect from the report and rerun unchanged checks
- [ ] README names unsupported browser surfaces and avoids broad visual/QA guarantees


## 19. Principal risks and stop conditions

| Risk | Mitigation / decision rule |
| --- | --- |
| Browser agent cannot reliably reach the interesting states | Use declared short journeys and report coverage failures. Later accept observations from existing Playwright tests rather than endlessly expanding the actor. |
| Model finds no useful bugs beyond exact rules | Run the deterministic-only ablation. If the semantic contribution is weak, narrow the product before investing in a platform. |
| Correlated actor/evaluator errors | Independent outcome checks, frozen evidence, actor conclusion excluded from evaluator input, and human-reviewed holdouts. |
| False-positive-heavy CI | Advisory first; per-evaluator calibration, negative cases, deduplication, documented suppressions, and meaningful run-level false-alarm measurement. |
| Timing changes caused by instrumentation or inference | Browser-native timestamps, collector overhead benchmarks, separate inference/interaction clocks, and matched environment comparisons. |
| Visual expectations exceed DOM evidence | Explicit `dom_geometry` scope; optional vision later, with separate accuracy and cost reporting. |
| Upstream is a fast-moving small demo | Pin the source/dependencies; keep a narrow diff and retained guard tests. Review upgrades deliberately. |
| Setup/CI isolation consumes the whole project | Bound the harness spike to 0.5-1.5 days; use Playwright transport only if the current pinned path demonstrably fails requirements. |
| "Self-improving" feedback rewards evaluator gaming | Keep evaluator/oracle changes separate from application repairs; run paired fault controls and holdout data. |

The success condition for the first release is narrow: **a developer gives the tool a local app and a declared journey; it completes or honestly reports why it could not, catches a useful intermediate-state defect, and gives enough evidence to fix it.**

## 20. Evidence and source index

Repository links are pinned to the inspected commit. External documentation was read during this planning session; pricing, limits, and model aliases can change.

- **S1 - Requirements:** [shared conversation](https://chatgpt.com/share/6aacf774-0690-83ee-bac3-b45282cbda0d); supplied local text, especially lines 1412-1935 for the final MVP.
- **S2 - Upstream overview:** [README](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/README.md), [design](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/docs/design.md), [MIT license](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/LICENSE), [dependencies](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/pyproject.toml).
- **S3 - Jev capability/pricing contract:** [Models](https://docs.typesafe.ai/models), [API](https://docs.typesafe.ai/api), [speculative fan-out](https://docs.typesafe.ai/patterns/fan-out).
- **S4 - Known model limitations:** [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13), reviewed upstream on 2026-09-17.
- **S5 - Generic terminal behavior:** [agent.py](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/jev_ultrafast/agent.py#L52-L161).
- **S6 - Task-specific verification:** [flights.py](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/examples/flights.py#L18-L38).
- **S7 - Fresh final observation / screencast:** [record_flights.py](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/scripts/record_flights.py#L36-L75).
- **S8 - Action filtering, caps, semantic marker:** [snapshot.js](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/journey_evals/snapshot.js#L55-L106).
- **S9 - Browser execution and profile behavior:** [browser.py](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/jev_ultrafast/browser.py#L20-L194).
- **S10 - Confidence semantics:** [TypeSafe confidence](https://docs.typesafe.ai/confidence).
- **S11 - Actual benchmark boundaries/costs:** [performance.md](https://github.com/browser-use/jev-ultrafast/blob/452c1ad2dd628008f1d5608f28158d76e49e6cc0/docs/performance.md).
- **S12 - Native long-frame measurements:** [MDN Long Animation Frame Timing](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceLongAnimationFrameTiming); [MDN source read](https://github.com/mdn/content/blob/main/files/en-us/web/api/performancelonganimationframetiming/index.md).
- **S13 - Event timing semantics:** [MDN PerformanceEventTiming](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceEventTiming); [MDN source read](https://github.com/mdn/content/blob/main/files/en-us/web/api/performanceeventtiming/index.md).
- **S14 - Shift versus CLS:** [MDN LayoutShift](https://developer.mozilla.org/en-US/docs/Web/API/LayoutShift); [MDN source read](https://github.com/mdn/content/blob/main/files/en-us/web/api/layoutshift/index.md).
- **S15 - Pinned harness contract:** [daemon.py v0.1.13](https://github.com/browser-use/browser-harness/blob/v0.1.13/src/browser_harness/daemon.py#L264-L290), [event draining](https://github.com/browser-use/browser-harness/blob/v0.1.13/src/browser_harness/daemon.py#L626-L680), [helpers](https://github.com/browser-use/browser-harness/blob/v0.1.13/src/browser_harness/helpers.py#L72-L80).
- **S16 - Browser isolation:** [Chrome remote-debugging changes](https://developer.chrome.com/blog/remote-debugging-port), [Playwright contexts](https://playwright.dev/python/docs/browser-contexts), [Playwright CI and trace sensitivity](https://playwright.dev/python/docs/ci-intro).
- **S17 - CI trust boundaries:** [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use).

### Open Questions

These are approval decisions, not blockers to the recommended technical direction. The defaults below keep the first implementation small and safe.

*Read-only planning choices; send decisions in chat.*

**Which application should follow the synthetic flight fixture as the first real pilot?**

Recommended: one owned local/staging app with resettable synthetic data and an existing acceptance test. Do not begin with production bookings.


**Is DOM/geometry-based layout coverage sufficient for the first public demo?**

- Yes: measured layout and timing first **(recommended)**
- Include actual screenshot understanding

**How should the first CI integration treat semantic findings?**

- Advisory until calibrated; independent checks can block **(recommended)**
- Block selected semantic checks after an agreed precision/coverage review

**Use journey-evals as the working CLI and retain MIT for the initial fork?**

- Use these defaults, subject to package-name availability **(recommended)**
- Keep working names local and decide branding/license before public release

