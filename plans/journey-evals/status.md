# Journey Evals implementation status

Each phase of [plan.md](plan.md) §14 mapped to the artifact that demonstrates its exit gate. Every
number here is reproducible from a committed script against the bundled loopback fixtures. Nothing
below was rerun to replace an outcome.

Source fingerprint of the calibration state: see `source_fingerprint` in
`artifacts/eval/receipts/calibration.json`.

## Phase status

| Phase | Exit gate | Status | Evidence |
| --- | --- | --- | --- |
| 0. Prove the foundations | Owned browser, telemetry before load, fresh final checks, no personal profile | **Done** | `scripts/check_guards.py` — 27/27 guards, no model calls. Isolation and loopback-only binding proven in `tests/test_fixtures.py`. |
| 1. Walking skeleton | One clean journey yields a verifiable result; errors and missing oracles cannot pass | **Done** | `journey_evals/{contracts,runner,report,cli}.py`; `examples/journeys/flight-booking.json`. `PASS` requires `goal_status == "verified"` from `verify_goal`. A dead worker writes an `ERROR` report (`cli._write_error_report`). |
| 2. Internal three-family demo | Clean vs broken evidence yields a useful non-handwritten finding without leaking the fault label | **Done** | `evaluation.py` five families; `scripts/calibrate.py` scenario table. Fault labels never enter a prompt; loading feedback is judged from the continuous timeline, not endpoint snapshots. |
| 3. V0 local public alpha | Five families, evaluation projection, narrow viewport, Back/persistence, redaction and caps | **Done** | Four suites, 220 journeys (below). Narrow viewport = `cal-narrow-v3` (820x700). Back/persistence = `examples/journeys/flight-booking-back.json`, `cal-back-v3`. Redaction: 19 tests in `tests/test_report.py`. |
| 4. V0.1 CI and feedback | A failure feeds a coding agent, a fix reruns the unchanged oracle, negative controls still work | **Done** | `.github/workflows/qa.yml` (two trust levels); `report.render_agent_feedback`; `scripts/smoke_e2e.py` runs the whole loop and passes. |
| 5. V0.2 pilot and calibration | Useful precision/coverage outside the demo; thresholds have provenance | **Partial — second application done, external pilot not done** | Second application (`examples/subscription_app.py`) at parity: 50/50 detection, 0/40 false findings. `artifacts/eval/receipts/calibration.json` gives every published threshold its provenance. No external developers have reviewed findings; that gate is not claimed. |

## Measured results, campaign v3

220 journeys across four suites, 10 repeats per scenario.

| | Flight | Subscription | Flight 820x700 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 90 | 90 | 20 | 20 | **220** |
| Seeded defects detected | 50/50 | 50/50 | 10/10 | 10/10 | **120/120** |
| Controls with a spurious finding | 0/40 | 0/40 | 0/10 | 0/10 | **0/100** |
| Clean journeys independently verified | 20/20 | 20/20 | 10/10 | 10/10 | **60/60** |
| False passes | 0 | 0 | 0 | 0 | **0** |
| Execution errors | 0 | 0 | 0 | 0 | **0** |
| Median wall clock | 15.3 s | 18.9 s | 44.0 s | 17.5 s | 18.0 s |
| Cost per journey | USD 0.00139 | USD 0.00216 | USD 0.00323 | USD 0.00096 | **USD 0.00183** |

Declared-check resolution: 1216 resolved, 104 unresolved (92.1%). Unresolved is reported as
unresolved, never as a pass. Status breakdown: 160 `completed`, 49 `budget_exhausted`, 11
`actor_blocked`; the latter two are all fault scenarios, where running out of steps or refusing to
proceed on a deliberately broken page is the correct terminal state, and the defect was reported in
every one of them.

Instrumentation overhead (`artifacts/eval/receipts/overhead.json`): 1.12 ms per scripted step,
median slowdown -3.0%, inside measurement noise.

## Retained earlier sweeps

- `cal-*-v1` — 90 journeys, 35 of 40 controls failed. The harness never reset the fixture backend
  between repeats, so repeat N found repeat N-1's booking and the acceptance contract correctly
  refused to treat a leftover record as proof of this run. The fix was `/__test__/reset` before
  every run, not a weaker contract. A single repeat per scenario would have hidden this entirely.
- `cal-*-v2` — 200 journeys, 109/110 detection. The one miss was a Windows `DevToolsActivePort`
  read race that produced no report. The read is now retried in `isolation.py` and a dead worker
  now writes an `ERROR` report. Counted as a miss, because it detected nothing.

v3 is a full re-run on one consistent source state, because `flight.html`, `contracts.py`,
`evaluation.py`, and `runner.py` all changed after v2. v1 and v2 were not deleted or amended.

## Redesign regression, campaign v4-spot

The two demo applications were restyled for demonstration (`examples/flight.html`,
`examples/subscription.html`), so **the v3 numbers above were measured on the previous markup**.
They are not restated here as if they had been measured on the redesigned pages. What was measured
on the redesigned markup is a one-repeat pass over all four suites, 22 journeys:

| | Flight | Subscription | Flight 820x700 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 9 | 9 | 2 | 2 | **22** |
| Seeded defects detected | 5/5 | 5/5 | 1/1 | 1/1 | **12/12** |
| Controls with a spurious finding | 0/4 | 0/4 | 0/1 | 0/1 | **0/10** |
| Clean journeys independently verified | 2/2 | 2/2 | 1/1 | 1/1 | **6/6** |
| False passes | 0 | 0 | 0 | 0 | **0** |

One repeat per scenario is a smoke-level denominator. It shows the redesign did not break
detection; it does not re-establish the v3 rates.

Two failures happened on the way there and are kept in the record:

- `artifacts/eval/redesign-flight` — 9 runs, 4 of 4 controls produced a spurious finding and every
  scenario failed identically. A demo server left listening on port 8111 by an earlier session was
  answering instead of the app under test, so the suite evaluated a different application than it
  believed. Windows accepts the second bind silently. The sweep is retained, not amended.
- `artifacts/eval/redesign-subscription` — 9 runs, 1 of 4 controls produced a spurious finding.
  This one was caused by the redesign. The restyled page was 1125 px tall, so in the first
  observations after the checkout total appeared, the price-change disclosure sat below the visible
  window; the evaluator saw a raised total with no explanation and said so, correctly. The fix was
  to compact the layout until the total, the disclosure, and the subscribe control share one
  window (measured: page height 900 px, disclosure bottom at 754 px). It was not to weaken the
  check. The re-measured sweep is `artifacts/eval/redesign2-subscription`.

The second of those is a genuine property of the product worth stating plainly: a disclosure the
user has to scroll to find is treated as a disclosure they were not shown.

## Second redesign, campaign v5-spot

The two demo applications were rebuilt again, this time as two deliberately unlike design systems
(*Meridian*, an airline booking flow; *Ledger*, a provisioning console) so they read as real
products rather than as test pages. **Every number above was measured on markup that no longer
exists.** What was measured on the current markup is, again, a one-repeat pass over all four
suites, 22 journeys:

| | Flight | Subscription | Flight 390x844 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 9 | 9 | 2 | 2 | **22** |
| Seeded defects detected | 5/5 | 5/5 | 1/1 | 1/1 | **12/12** |
| Controls with a spurious finding | 0/4 | 0/4 | 0/1 | 0/1 | **0/10** |
| Clean journeys independently verified | 2/2 | 2/2 | 1/1 | 1/1 | **6/6** |
| False passes | 0 | 0 | 0 | 0 | **0** |

One failure on the way there, kept in the record:

- The first flight sweep detected **4 of 5** seeded defects. `clipped_confirm_button` failed the
  journey (`goal=violated`, the actor could not reach the control) but produced **no finding**, so
  the run said the journey failed without saying why. The cause was in the fixture, not the
  evaluator: the redesigned `.actions` row had `padding-top:20px` and the fault's clip is
  `max-height:12px`, so the visible 12 px was entirely padding and the button was pushed
  *completely* outside its clipping ancestor. A control that is fully outside is not collected at
  all, so `confirm-action-usable` had nothing to evaluate. The original markup had left a sliver
  of the button showing, which reads as `partially_outside_clipping_ancestor` and is a finding.
  `.actions.constrained` now zeroes the padding, and the case detects again in all three
  viewports.

That regression is the argument for the sweep. Lint, 331 unit tests and 27 browser guards were all
green while a seeded defect had quietly stopped being detectable, because the failure was in the
geometry of a page rather than in any code path a test covered. It is also the argument for
`tests/test_fixture_markup.py`, added with this redesign: it holds the fixture invariants that
*can* be checked without a browser — journey literals present and unsplit, control names readable
as a single text node, only the two declared clipping rules, no sticky or fixed positioning, no
page-level `<form>` in the flight fixture, no built-in loading affordance. It would not have caught
this particular regression, and it says so in its own docstring.

The live console was rebuilt again in this pass. Its design system is now a monochrome near-black
panel: one neutral ramp from `#0a0a0a` to white, white as the only interactive colour, a 4px
spacing scale, a real radius scale (6px controls, 12px rows, 16px panels, pills for state), and
bordered translucent panels over a ruled ground instead of the earlier square ruled gutters and
single lime signal. Semantic colour is now a narrow, separate vocabulary used only at small sizes —
a verdict pill, a check's left spine, a viewport tab's dot — so colour on this screen always means
something the journal recorded. The imitation browser chrome that an earlier version drew around
the screenshot stays removed for the same reason it was removed then: in a recording it made the
page under test read as part of our own UI, which is exactly the confusion this console exists to
avoid. One layout bug was found by measuring rather than by reading: the viewport pane is a flex
column, so the frame was being shrunk to fit the window and a 390x844 phone run rendered 390x623.
The frame is now `flex: 0 0 auto` and measures 390x902 around an 839 px capture, which scrolls —
correctly, since the shape a run ran at is evidence and not a layout preference. The check and
finding rows still take their spine colour from the verdict via `:has()`, so no JavaScript change
was needed. This is a presentation change with no measured claim attached; it was verified by
driving real two-viewport runs of the flight fixture and reading the screenshots, live and
recorded.

## Evaluator batching, campaign v6-spot

A run of the narrow journey settled `checkout-total-explained` as `UNKNOWN` with the reason
`Semantic evaluation unavailable: ValueError: Invalid TypeSafe response; no action executed.`,
which made the whole run `INCONCLUSIVE` under `required_coverage_v1` even though the goal was
verified independently and the execution completed. Two defects sat behind it.

**One malformed head discarded the verdicts beside it.** `batch_semantic` validated every subject
in a loop, so the first bad head raised out of the entire function and *all* pending subjects in
that request became `UNKNOWN`. In the observed run `journey-advancing` was collateral: the model
had answered it, and the answer was thrown away. Heads are now validated independently, and a
subject the model never answered validly is the only one that becomes unknown.

**A malformed HTTP 200 was never retried.** `_post_json` retries transport failures and 429/503,
but a well-formed response carrying a garbled verdict fell straight through to `UNKNOWN`. A
garbled head is not evidence about the page, and re-asking an evaluator is read-only: nothing
about the run changes. One bounded re-ask of only the failed subjects was added, billed to the
ledger like any other request. A head that fails twice still becomes `UNKNOWN` — the re-ask
removes a transport artefact, it does not hunt for a verdict.

The shared `validate_choice` message was also fixed; it said "no action executed" on the
evaluation path, which is an actor message and misdescribed what had happened.

**The sweep that exposed a much worse bug.** The first re-measurement after this fix reported
1/5 faults detected, 4/4 controls producing false findings and 4 false passes — every scenario,
including the clean control, failing `checkout-total-explained` with an identical USD 20
unexplained increase. The evaluator was right and the fixture was wrong: a leftover
`watch --serve-app flight --fault silent_fare_increase` process from an earlier session still held
port 8111, and `calibrate.serve()` bound the port anyway because Windows honours `SO_REUSEADDR`.
Every run in that sweep measured the leftover's permanently faulted application.

`console.port_is_taken` already existed and its docstring describes this exact hazard, but the
calibration harness called `flight_app.build()` directly and never consulted it. The guard now
lives in `flight_app.refuse_taken_port`, called by both example applications' `build()`, so every
caller is covered rather than only the two CLI paths that remembered to ask. Stale listeners had
corrupted work four times before this.

That sweep is recorded here because it happened; it is not a measurement of the evaluator and is
not counted in any rate. The measurement below was taken afterwards on a clean machine.

| Suite | Faults detected | False findings on controls | Clean journeys verified | False passes |
| --- | --- | --- | --- | --- |
| flight | 5/5 | 0/4 | 2/2 | 0 |
| flight-narrow | 1/1 | 0/1 | 1/1 | 0 |
| flight-back | 1/1 | 0/1 | 1/1 | 0 |
| subscription | 5/5 | 0/4 | 2/2 | 0 |
| **total** | **12/12** | **0/10** | **6/6** | **0** |

This matches the v4-spot and v5-spot sweeps exactly.

## Viewport matrices, campaign v7-spot

A journey may now declare several viewports, and `run` measures each of them independently. The
first live matrix run found something immediately: the desktop cell passed and the mobile cell
reported `goal=violated` on a clean application. The cause was not the application and not the
size. The desktop cell's booking was still in the fixture when the mobile cell ran, and
`_records_match` correctly refuses leftover records as proof — that rule is what stops one run's
success from being credited to the next.

Two changes came out of it. The failure message now distinguishes the two ways a backend
expectation can fail: a missing record is the journey not doing what it claimed, while extra
records alongside a complete match means the application did not start the run from a known state.
Those have different causes and only one of them is a defect in the application. And
`acceptance.backend.reset_path` was added: an opt-in endpoint, POSTed from the journey's own
origin before the run, which is exactly what `scripts/calibrate.py` has always done by hand. It is
opt-in because only a journey's author knows whether clearing that state is safe, and a failed
reset is recorded as a setup error rather than as evidence about the application.

A matrix never merges its cells. Passing on a desktop and failing on a phone is a finding, and
averaging it into one verdict would hide the thing the matrix was declared to find. Selecting a
single size writes straight to `--out` with no subdirectory, so everything that reads
`<out>/report.json` — including the calibration harness — is unaffected.

Measured after the change, all four suites:

| Suite | Faults detected | False findings on controls | Clean journeys verified | False passes |
| --- | --- | --- | --- | --- |
| flight | 5/5 | 0/4 | 2/2 | 0 |
| flight-narrow | 1/1 | 0/1 | 1/1 | 0 |
| flight-back | 1/1 | 0/1 | 1/1 | 0 |
| subscription | 5/5 | 0/4 | 2/2 | 0 |
| **total** | **12/12** | **0/10** | **6/6** | **0** |

`examples/journeys/flight-booking-matrix.json` demonstrates the feature: the booking journey
declared at `["standard", "mobile"]`. Against a clean application both cells pass; against
`silent_fare_increase` both cells independently detect `checkout-total-explained`.

A third stale `watch --fault silent_fare_increase` server was found squatting port 8111 during
this work. The guard added in v6-spot refused to serve over it, which is what it is for, but the
run itself still targeted the URL its journey names and measured the squatter. Nothing in the
product can fix that — a run must go to the address it was told to — so it stays an operational
hazard to check for before any live measurement.

## Defects this work surfaced in the product itself

1. `Page.captureScreenshot` stalled 15 s because owned headless sessions created their CDP target
   in the background. Overhead fell from 4.79 ms/step to 1.12 ms/step after the fix.
2. `_settle()` used a blind sleep, so every measured latency equalled the deadline.
3. `expect.ready_when` was never validated, so a malformed predicate crashed mid-run with a
   `KeyError`. Now validated through `validate_predicate`; `holds()` raises `ContractError`.
4. `_settle()` skipped the telemetry watermark on poll observations, so a control-based readiness
   predicate could never hold. Polls now take a watermark when a declared condition reads the
   element table (`contracts.reads_controls`).
5. The model was asked whether a form field still held a value it cannot see in page text.
   `build_interaction_correctness` now measures control readiness directly and `code_decision`
   settles it deterministically. A model will answer such a question confidently and wrongly.
6. The console rendered a timeline row for every status-region frame, including frames where every
   region was empty or hidden, producing repeated blank `FEEDBACK` entries. Silence is now dropped
   from the timeline; its absence is still reported on the watermark, where it is evidence.
7. `model._post_json` retried a provider that answered 429/503 three times but gave a dropped
   connection no retry at all, so a single transport blip ended an otherwise healthy run with
   `actor_stopped` and three checks left unresolved. Transport failures are now retried on the
   same backoff, with the transport cause named in the message. This does not weaken the
   never-retry rule: the retry is of the HTTP request, which by definition executed no action;
   browser mutations are still never retried, and a 4xx is still the provider's final answer.
8. `journey-evals serve` bound an already-occupied port without complaint. Windows honours
   `SO_REUSEADDR` on a listening socket, so two fixture servers split the traffic and a run reads
   the other process's records — a correct acceptance contract then fails a journey that actually
   succeeded, or passes one that did not. This cost a corrupted calibration sweep during the
   redesign and, later, a bogus `violated` on a clean run whose records came from a stale faulted
   server left over from a demo. `watch` already refused an occupied port; `serve` now applies the
   same guard and exits `4`.
9. The guard from (8) was applied to the two CLI paths but not to the port the calibration harness
   binds itself, so a leftover faulted server silently replaced the application under test for a
   whole sweep. It now lives in `flight_app.refuse_taken_port` and runs inside both example
   applications' `build()`, which is the one place every caller passes through.
10. `evaluation.batch_semantic` let a single malformed head discard every other verdict in the
    same request, and never re-asked a garbled one. A transient model artefact could therefore
    turn a detected fault into `UNKNOWN` and take the checks beside it down too. Heads are now
    validated independently, with one bounded, ledger-billed re-ask of only the failed subjects.

## Not claimed

- Semantic checks are **advisory**, not blocking. Making them blocking needs a materially larger
  representative holdout and an agreed false-positive tolerance.
- Ten repeats on one machine does not characterise a flakiness tail.
- Costs are published-rate estimates from reported token usage, not a provider invoice. The text
  helper has no configured rate; its requests are counted, not valued.
- No external pilot has happened. Phase 5's developer-review gate is open.
- These are small denominators on applications we wrote. They do not transfer to an arbitrary
  application.

## Note on the CP7 receipt

The CP7 framework-admission receipt refers to the journey contract by its pre-implementation
wording. The implemented type is `contracts.JourneySpec`, with `CheckSpec`, `ProbeSpec`, and
`Coverage` alongside it. The receipt was not edited, because it records what was decided at the
time it was written.

## Reproducing

```powershell
ruff check .
.\.venv\Scripts\python.exe -m pytest -q          # 311 passing
node --check journey_evals\telemetry.js
node --check journey_evals\snapshot.js
node --check journey_evals\static\app.js
node --check journey_evals\static\console.js
node --check examples\flight.js
.\.venv\Scripts\python.exe scripts\check_guards.py   # 27/27, no model calls
.\.venv\Scripts\python.exe scripts\smoke_e2e.py      # the full agent loop, needs keys
```

## Watching a run

`journey-evals watch` renders the run's journal live: pages observed, actor decisions,
each declared check as it settles, and the final verdict read from `report.json`. It is a viewer
over the same artifacts, not a second runner, and it cannot influence a result. Tests in
`tests/test_console.py` hold it to that, including that it never ships full page text or a model
request to the browser, that evidence cannot be requested from outside the run directory, and that
a hidden section of a single-page app is not reported as a clipped control.

A journey that declares a viewport matrix is watched the same way, one size after another. Each
cell is an ordinary, independent run with its own worker, directory and report; the console only
decides which one is in front of you. When a cell finishes the next starts on the following poll,
and the viewports stand as tabs above the frame, the way a browser's tabs do. The tabs are a
selector, not an expander: they
change which run the console's existing columns are describing. Selecting a finished viewport
repaints navigation and evidence, checks as they settled, findings and the final verdict with that
cell's, and reframes the viewport to its size showing the last screen it recorded; selecting the
one being measured hands the columns back to the live run. All of it is read back from that cell's
journal and `report.json`, never from the DOM that happened to be on screen when it finished, and
its screenshots remain reachable at `/cell/<i>/evidence/<name>`. Displaying a recorded cell cannot
disturb the run in flight: the live journal keeps arriving and keeps being recorded, and the
console simply does not paint it. Stopping stops the matrix rather than only the cell on screen, so
nothing starts moments after being stopped. Nothing is merged and a failing cell does not cancel
the ones after it: a journey that passes on a desktop and fails on a phone is the result.

Verified live on `examples/journeys/flight-booking-matrix.json` against the clean flight fixture:
both cells advanced automatically and reported PASS independently with the goal verified by the
acceptance check. While the second cell was still measuring, selecting the first replayed all 52 of
its recorded events and all six of its settled checks into the same columns and reframed to
1120x780 with its own last screenshot, and the live cell went on accumulating events underneath
(26 to 51 across the replay) before the columns were handed back to it. Cell-scoped evidence served
the first cell's screenshots while still refusing paths outside its own directory.

## The name

The product is **journey-evals**: the Python package is `journey_evals`, the console script is
`journey-evals`, and the plan lives in `plans/journey-evals/`. What did *not* change is the vendor:
`JEV_API_KEY`, the `jev-1.13.0` model id, and `plans/jev-feasibility/` all name the model this
product calls, not the product itself, and renaming them would have quietly broken a contract that
belongs to somebody else. The upstream attribution to `browser-use/jev-ultrafast` is likewise
somebody else's repository and is left as it was.

The rename is a rename and nothing else, and it was measured rather than assumed. After it, the
flight calibration suite was re-run in full against the live model: **5/5 faults detected, 0/4
false findings on controls, 2/2 clean journeys goal-verified, 0 false passes** — the same numbers
as the pre-rename baseline. Guards (27), the end-to-end smoke, the packaged build and 405 tests all
pass under the new name.

### What the rename should not have touched

A find-and-replace over a repository cannot tell a product from its ingredients, and this one did
not. Three kinds of damage were found afterwards and undone:

- **The imported agent.** The browser agent this product drives was imported from
  `browser-use/jev-ultrafast`. It is not journey-evals and must not be renamed as if it were. It
  now lives in its own package again — `jev_ultrafast/` holding `agent.py`, `browser.py`,
  `model.py`, `questions.py`, `demo.py`, `snapshot.js` and the demo's own static assets — with its
  original module paths, its original `jev` console script and its original `JEV_OWNED_SESSION`
  variable. `journey_evals` imports *from* it; a test asserts the dependency never runs the other
  way. The only addition is `jev_ultrafast/limits.py`, which holds `VIEWPORT` and
  `SLOW_MACHINE_TIMEOUT` so that reading a number does not connect to a browser harness.
- **Recorded evidence.** `docs/measurement.json` and the three flight measurement files record
  file hashes under their real paths, and `plans/jev-feasibility/evidence/upstream-smoke.json`
  records which checkout and distribution were smoke-tested. Rewriting a receipt to match a later
  naming decision is falsifying it; all five were restored byte-for-byte.
- **Someone else's prose and bytes.** `docs/design.md`, `docs/performance*.md`,
  `docs/launch-draft.md`, `docs/banner.svg`, the upstream measurement scripts and the feasibility
  plan describe the imported agent, and several files had additionally been rewritten from LF to
  CRLF. All were restored, and every source file in the repository is LF again.

## Watching starts the run

`watch` used to open a console with a **Start run** button. Watching means watching something
happen, so the run now starts when the console starts serving and the button is gone: the page is a
viewer over a run in progress and never a launcher for one. `--start` is still accepted so existing
commands keep working, and it does nothing. `Stop` still ends a run early, and the report still
describes exactly what had happened by then.

The viewports are tabs now rather than stacked rows, laid out across the top of the pane the way a
browser's are, with the lit tab joined to the frame beneath it. The frame also keeps the shape the
journey declared: the screenshot box is floored at the declared aspect ratio, so a capture taken
before the page settled is letterboxed inside a 390x844 phone instead of collapsing the frame into
a wide strip and making the console appear to change size between screenshots. A capture taller
than the viewport still shows in full, because that is evidence rather than overflow.

Verified live on the two-viewport flight matrix with nothing clicked to begin: six seconds after
the console bound its port the run was already `running` with 23 events recorded. Both viewports
finished PASS with the goal verified independently, the tabs sat on one row, the phone frame
measured 388x840 around a genuine 390x844 capture, and selecting the finished desktop tab while the
phone was still measuring repainted every column with the desktop run and its own last screen.

## Packaging, documentation media, and the site

Journey Evals now installs outside a checkout. Every path decision that used to assume a repository
lives in `journey_evals/paths.py`: runs land under the user's own directory, scratch and profiles
under a work root, `.env` is read from where the user is standing, and the example applications and
journeys ship inside the wheel as `journey_evals/_examples`. `journey_evals/browsers.py` fetches one
pinned Chrome for Testing build into a user cache, so a consumer is not asked to have Chrome in the
right place. Three commands follow from that: `init`, `demo`, and `install-browser`.

The npm package in `npm/` is a launcher, not a reimplementation. It finds a Python interpreter,
creates a private virtual environment, installs the wheel into it once, and then passes arguments
through to `python -m journey_evals.cli`, returning its exit code verbatim. A signal is reported as
`3` rather than converted into a verdict. `scripts/build_npm.py` stages the wheel and keeps the two
version numbers identical, and `tests/test_npm_package.py` pins that parity along with the shipped
file list and the exit-code contract.

It was verified the only way that means anything: `npm pack`, install into a fresh temporary
project outside this repository, `doctor`, `init`, `install-browser` (a real 150 MB download), then
a real run against a served fixture with a real key. It produced a report in that project's own
directory and exited `1` — correctly, because the flight journey declares no `reset_path` and the
second run found the first run's booking still recorded.

Two product defects surfaced while photographing the documentation. The console's viewport pane is
a flex column, so the frame was being compressed by its siblings: a declared 390x844 phone rendered
390x623, which would have made every screenshot in the guide a lie about the size measured. And
every HTML report headlined itself `Journey: journey`, because the renderer read a top-level
`journey_id` the worker never writes. Both are fixed, the second with a regression test.

`docs/media/` holds six photographs of real pages and a real run, produced by
`scripts/render_docs_media.py` rather than by hand. The guide gained an npm-first installation
section, a quickstart built on `npx journey-evals demo`, framework integration for React, Angular,
Vue, Svelte and plain static builds — the integration is identical everywhere, because nothing is
imported and only a URL is driven — and two prompts written to be pasted into a coding agent, one
to author journeys and one to run the fix-and-re-run loop. Both prompts state what the agent may
not do, because the cheapest way to make a journey pass is to weaken it.

`docs/demo-video.md` designs the demonstration film: a flawed-implementer system prompt that
produces authentic defects by removing the agent's verification channels rather than by asking it
to write bugs, a deterministic fallback built on the seeded faults, a shot list, and the recording
rules. The marketing site lives outside this repository at `journey-evals-site` as two static HTML
files, one stylesheet and one script, with the video as a labelled placeholder and no claim on it
that this project has not measured.

## A third example application, and the eight defects it found

The flight and subscription fixtures both end in success, and success is the easy case. A
requirement a product manager actually writes is about the *degraded* path: a downgrade that is
accepted but only partially, a destructive action whose blast radius must be stated first, a
dashboard whose numbers are stale. So a third synthetic application was built — Meridian, `--app
admin` on port 8113 — where every declared journey ends in a degraded, partial, rejected, stale or
destructive state.

The selection rule it encodes is narrower than "use a model for hard assertions". A property
belongs here only when its passing set cannot be enumerated **and** there is a legitimate lookalike
a string assertion cannot separate from the defect. Every seeded defect is therefore paired with an
adjacent control — a terse-but-honest notice beside a reassuring-but-empty one — and the control
must not be reported. `test_every_defect_has_an_adjacent_control` refuses a defect shipped without
one. Two defects are invisible on the rendered page entirely: the page promises 1 October while the
backend applies today, and "Recently deleted" still offers a restore that returns `410`. Only the
backend acceptance contract catches those, which is the point of having one.

Building the examples found eight more defects — seven in the product, one in the example itself —
listed here because every one was found by measurement and none by reading the code.

11. `Run.assess` judged transition checks against the after-state only. A pre-action requirement —
    "before you confirm, the page states what you lose" — is gone the instant the action lands, so
    whenever the actor moved in one step the check was silently dropped and reported as never
    resolved. `Run.applicable_states` now pairs each check with the state it should be judged on:
    the after-state is preferred, and the before-state is consulted only for checks the after-state
    cannot answer. This is the same class of defect as the `checkout-total-explained` check that
    sat at `UNKNOWN` in the first application; it now resolves.
12. The textual `unexpected_state` rubric had no specification. With no measured amount, "this
    specific change" has no anchor, and the model accepted any reassuring sentence as an
    explanation. `rubric_for` now switches to `TEXTUAL_DISCLOSURE_RUBRIC` in textual mode, where
    the requirement names the facts that must be disclosed and coverage of those facts is the
    question. Both partial-disclosure defects were detected afterwards and neither terse control
    produced a finding.
13. `_settle` returned early when an action had no watched checks, leaving the most recent
    observation dated *before* that action. A progress check then read an unchanged page and called
    a working step a stall. The observation is now taken on both paths, via `_observe_settled`.
14. The actor's "the page did not change" claim was trusted. The actor samples at the instant it
    acts, so a page that re-renders 40 ms later is journalled as a no-op. `Run.reconcile` corrects
    a false no-op against the run's own settled observation. Only false no-ops are corrected: an
    action already called a change is never re-examined, because nothing available here can prove a
    change did *not* happen.
15. `Journal` opens `events.jsonl` in append mode and restarts its sequence at 0, so a second run
    in the same directory interleaves two clocks. This produced a measured interval of 294 s inside
    a 4.6 s run — a latency check would have failed a page that responded instantly. Both `execute()`
    and the CLI now refuse a directory that already holds a non-empty journal.

A sixth and a seventh were found after those, by sweeps rather than by tests.

On a **clean** `plan-downgrade` run the advisory progress check returned `STALLED` at `0.87` after
one step, with `consecutive_unchanged_actions: 1` against a declared threshold of `3`. The code
rule had not fired; the model had simply undercut the journey's own definition of a stall, on one
step's worth of trajectory. A journey that declares `stall_after_unchanged_actions: N` has stated
what a stall *is* in that journey, so `available_heads` now withholds `STALLED` below `N`. This
does not make the check pass — `PROGRESSING`, `BLOCKED`, `COMPLETION_CANDIDATE` and `UNKNOWN` all
remain available, including the verdicts that fail the run. It only stops judgement from overruling
a declared measurement.

That fix revealed the real defect underneath it. With `STALLED` withheld the same clean run
answered `BLOCKED` at `0.53`, still on `consecutive_unchanged_actions: 1` — but the step in
question had navigated from `/billing` to `/billing/review`. The page had plainly changed, and
defect 14's reconciliation should have said so. It never fired in any real run. `action_executed`
is journalled *before* the actor knows the outcome, so the frozen event carries
`page_changed: None`, and `reconcile` was reading that copy and testing it for `False`. The unit
tests built the entry by hand with `False` already set and passed throughout. Reconciliation now
reads the live history entry and skips only an action already known to be a change;
`test_the_journalled_copy_is_not_what_reconciliation_reads` pins the distinction. This is worth
stating plainly: a fix can be correct, tested, merged, and inert.

An eighth was a fault in the example rather than the product, and is recorded because it is the
same class of mistake a user will make. `revenue-freshness` asks for the last seven days while the
window selector already *defaulted* to the last seven days. The actor is biased toward using the
control in front of it, so the only change available to it was the wrong one: it selected the
thirty-day window and the journey failed on a correctly served application, twice. The default is
now the thirty-day window, so the declared task requires the action it names rather than requiring
the actor not to act. `test_the_declared_window_is_never_the_one_the_page_already_shows` refuses
the arrangement. **A journey whose success depends on the actor declining to act is not a journey;
it is a trap.**

### What was measured

Each sweep is its own denominator. Nothing was re-run to improve a number, and the earlier sweeps
are retained under `artifacts/admin-sweep-*` and `artifacts/sweep-meridian/`.

| Sweep | Code under test | Result |
| --- | --- | --- |
| 1 | before defect 11 | 3/5 on `plan-downgrade` |
| 2 | after 11 | 4/5 |
| 3-4 | after 12-14 | 11/14, then 13/14 |
| 5-6 | final defect set, ports 8112 then 8113 | 14/14, twice |
| 7 | stall threshold raised 2 -> 3 in the three Meridian journeys | 13/14 — one advisory false `STALLED` |
| 8 | after withholding `STALLED` below the declared threshold | 12/14 — the advisory became a false `BLOCKED`, and the fixture trap above cost two clean `revenue-freshness` runs |
| 9 | after the window default was fixed | 13/14 — the advisory false `BLOCKED` remained |
| 10 | after reconciliation was made to actually fire | **14/14** |

First-application non-regression on that same final code: flight **7/7**, subscription **1/1**.
Every defect detected, every control clean, no false pass anywhere in either suite.

The suite is 465 tests. `scripts/sweep.py` runs either suite (`meridian`, `first-apps`) and writes
every row to `artifacts/sweep-<suite>/`.

### Two judgements recorded rather than fixed

- `stall_after_unchanged_actions` was raised from `2` to `3` in the three Meridian journeys, which
  are two-step confirmation flows where a legitimate intermediate step can read as unchanged. The
  flight and subscription journeys were left at `2`, because they carry calibrated campaign
  evidence and changing them would invalidate it.
- `restore-promise-kept` never reaches `CONTRADICTED` under `false_restore_promise`. The run fails
  correctly — the acceptance contract catches the `410` — but the check that names the defect stays
  unresolved. The evidence the check would need is the failed restore *attempt*, which the actor
  makes but the page does not report. This is recorded, not worked around.

## A fault that was served for a whole run and reported PASS

An operator served `--fault janky_render` and got `PASS`, `7/7 declared checks resolved`, no
findings, on both viewports of the matrix journey. The report was true and the impression it gave
was false.

`janky_render` burns 240 ms of main thread on every render. The flight journey declared six checks
— results appear, progress is announced, the total is explained, the confirm control is usable, the
confirmation appears, the journey advances — and not one of them asks whether the page stays
responsive. The evaluation was honest about its own scope and silent about everything outside it.

The evidence had been collected all along. `telemetry.js` has always observed
`long-animation-frame`, and the operator's own `events.jsonl` held five frames at roughly 200 ms of
`blockingDuration` each. Nothing on the Python side ever read them.

Two changes followed.

16. A sixth evaluator family, `input_responsiveness`, measures the worst `blockingDuration` in the
    window following a declared action against a `blocking_budget_ms` the journey states. It is
    settled entirely by `code_decision` and never reaches the model: there is no adequacy question
    here, only a measurement against a budget, so it costs nothing and cannot drift between runs.
    An empty window is `RESPONSIVE`, not `UNKNOWN` — the observer is installed before the action and
    the browser reports every frame over its own threshold, so silence is evidence. Only a window
    with no telemetry at all abstains. `flight-booking` and `flight-booking-matrix` now declare
    `search-stays-responsive` with a 120 ms budget, and `brief_render_work` (40 ms) was added as the
    adjacent control the check must not report.
17. `test_every_seeded_fault_is_swept_or_recorded_as_uncovered` compares every fixture's
    `SCENARIOS` against the sweep tables and requires each unswept fault to be listed with a reason.
    Nine were silently unmeasured. A fault nobody measures is worse than no fault at all, because it
    looks like coverage.

Measured afterwards, each its own denominator: first apps **10/10** with `janky_render` detected
and `brief_render_work` clean, Meridian **14/14** unchanged, suite 466 tests. The operator's exact
command now fails on both viewports and names the number: `worst_blocking_ms: 204` against
`blocking_budget_ms: 120`.

The general lesson is now in the guide next to the family table, because it is the failure mode
most likely to mislead a real user: **a journey reports only on what it declares.** `Coverage:` is
the scope of the claim, not a statement about the application.
