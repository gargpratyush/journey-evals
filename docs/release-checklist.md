# Journey Evals V0 release criteria

Every number here was measured on this workstation against the bundled loopback fixtures, on the
source fingerprint recorded in `artifacts/eval/receipts/calibration.json`. They are small
denominators on applications we wrote, and they do not transfer to an arbitrary application.

## Measured results

220 journeys across four suites, 10 repeats per scenario. No scenario was rerun to replace an
outcome; every run stays in the denominator it started in.

> These 220 journeys were measured on the demo applications' **original markup**. Both demo pages
> have since been restyled twice for demonstration. The current pages were re-measured at one
> repeat per scenario (22 journeys: 12/12 defects detected, 0/10 spurious control findings, 6/6
> clean journeys verified, 0 false passes), which shows detection survived each redesign but does
> not re-establish the rates below. See `plans/journey-evals/status.md`, "Redesign regression" and
> "Second redesign", for the two cases a redesign genuinely broke and why.

| | Flight | Subscription | Flight, 820x700 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 90 | 90 | 20 | 20 | **220** |
| Seeded defects detected | 50/50 | 50/50 | 10/10 | 10/10 | **120/120 (100%)** |
| Controls with a spurious finding | 0/40 | 0/40 | 0/10 | 0/10 | **0/100 (0.0%)** |
| Clean journeys independently verified | 20/20 | 20/20 | 10/10 | 10/10 | **60/60** |
| False passes | 0 | 0 | 0 | 0 | **0** |
| Execution errors | 0 | 0 | 0 | 0 | **0** |
| Median wall clock | 15.3 s | 18.9 s | 44.0 s | 17.5 s | 18.0 s |
| p90 wall clock | 44.2 s | 47.0 s | 45.7 s | 18.2 s | 45.4 s |
| Cost per journey | USD 0.00139 | USD 0.00216 | USD 0.00323 | USD 0.00096 | **USD 0.00183** |

Declared-check resolution across all runs: 1216 resolved, 104 unresolved (92.1%). Unresolved checks
are reported as unresolved. They are never counted as accurate negatives and never reported as
passes.

**Execution status breakdown:** 160 completed, 49 `budget_exhausted`, 11 `actor_blocked`. The 49
and the 11 are all fault scenarios where the actor correctly ran out of steps, or correctly refused
to proceed, on a deliberately broken page. Those are the right terminal states for those scenarios,
and the defect was reported in every one of them. They are published separately rather than folded
into "completed".

**Earlier sweeps are retained as recorded.** `cal-*-v1` contains a 90-journey sweep in which 35 of
40 controls failed. The cause was the harness, not the evaluator: the fixture backend was never
reset between repeats, so repeat N found repeat N-1's booking and the acceptance contract correctly
refused to treat a leftover record as proof of this run's success. The fix was a `/__test__/reset`
before every run, not a weaker contract. `cal-*-v2` (200 journeys) recorded 109/110 detection with
one `no_report` caused by a `DevToolsActivePort` read race on Windows; that race is now retried in
`isolation.py`, and the CLI writes a full `ERROR` report rather than an empty directory when a
worker dies. v3 is a full re-run on one consistent source state because `flight.html`,
`contracts.py`, `evaluation.py`, and `runner.py` all changed after v2; v1 and v2 were not deleted
or amended.

**Instrumentation overhead** (`artifacts/eval/receipts/overhead.json`, 10 matched alternating
pairs, 16 scripted steps each, no model calls): 1.12 ms per step, median slowdown -3.0%, which is
inside measurement noise. This is a re-measurement: the earlier CP5-D figure of 4.79 ms and 20.09%
predates the fix that stopped owned headless sessions creating their CDP target in the background,
which was making `Page.captureScreenshot` stall for 15 s.

## Checklist

- [x] **Fresh owned browser and fake backend; no personal browser/profile access.** Every run gets
      its own `OwnedSession` with its own profile directory under `artifacts/`, launched from the
      pinned Chrome for Testing binary. The fixtures bind `127.0.0.1` only and reject non-loopback
      callers. `scripts/smoke_e2e.py` asserts the backend starts empty for every phase.
- [x] **`DONE` cannot pass without fresh independent checks and declared coverage.** `PASS`
      requires `goal_status == "verified"`, which is decided by `verify_goal` from the acceptance
      contract - a backend read, a URL, page text, persisted control values - not by the actor's
      own report. Exploration mode has no acceptance contract and therefore cannot return `PASS`
      at all. Missing evidence yields `INCONCLUSIVE`.
- [x] **Clean controls and every supported seeded defect have reproducible evidence.** 12 seeded
      defects and 9 lookalike controls across two applications and four suites, 10 repeats each,
      with every run's journal, observations, and screenshots retained under `artifacts/eval/cal-*`.
      Each finding cites the observation IDs it was derived from; `smoke_e2e.py` asserts no finding
      is emitted without evidence.
- [x] **Provider, telemetry, browser, and artifact failures never become PASS.** A model outage
      resolves the affected subject to `unknown`; `Collector.checkpoint` raises rather than
      returning an incomplete window; a worker that dies writes an `ERROR` report with every check
      `pending`; JUnit emits `skipped`, never a pass, for unknown and pending checks. Covered by
      `tests/test_evaluation.py`, `tests/test_cli.py`, and `scripts/check_guards.py`.
- [x] **Publish actual precision, coverage, cost, latency, and instrumentation overhead.** Above,
      and in `artifacts/eval/receipts/calibration.json`.
- [x] **Document model data egress; verify redaction, HTML escaping, and explicit evidence
      capture.** Egress is documented in the README: the structured element table and page text go
      to the evaluation model, and a field-level prompt goes to the text helper. Screenshots, the
      journal, and credentials do not leave the machine. Redaction runs before anything reaches
      disk and covers configured secrets, declared patterns, and URL credentials; `render_html`
      escapes all page-derived text and model output. 19 tests in `tests/test_report.py`.
- [x] **A coding agent can fix one confirmed defect from the report and rerun unchanged checks.**
      `scripts/smoke_e2e.py` runs exactly that loop end to end: clean journey passes with a
      persisted booking, one hidden defect is caught by `checkout-total-explained` and carried into
      `agent-feedback.json` with evidence, the application is fixed, and the byte-identical journey
      passes again **with that evaluator still evaluated rather than suppressed**.
- [x] **README names unsupported browser surfaces and avoids broad visual/QA guarantees.** The
      README states that layout problems are found by measuring DOM geometry rather than comparing
      pixels, that screenshots are never sent to a model, and that shadow roots, frames, canvas,
      uploads, pop-up tabs, and arbitrary keyboard widgets are out of scope.

## What these numbers do not license

- They do not justify making semantic checks blocking. That needs a materially larger,
  representative holdout and an agreed false-positive tolerance. At thousands of checks per CI run
  a small per-check false-positive rate fails every PR.
- They do not establish flakiness behaviour. Ten repeats per scenario on one machine is enough to
  expose a harness defect - it did, see below - and not enough to characterise a tail.
- They are not a provider invoice. Cost is a published-rate estimate from reported token usage.
  The text helper has no configured rate; its requests are counted, not valued.

## A harness defect these repeats exposed

The first 10-repeat flight sweep reported 35 of 40 controls as failures. Every declared check
passed in all of them; the goal was `violated` because the fixture backend was never reset between
repeats, so run N found run N-1's booking still there and the acceptance contract correctly
refused to treat unexpected leftover records as proof of this journey.

The contract was right and the harness was wrong. The fixtures gained a `/__test__/reset` endpoint
and the sweep now resets before every run. That first sweep is retained at
`artifacts/eval/cal-flight-v1` exactly as it was recorded. A single repeat per scenario would never
have shown this, because run 0 of every scenario passed.
