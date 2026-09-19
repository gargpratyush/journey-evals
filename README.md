# Journey Evals (experimental feasibility fork)

This workspace extends `browser-use/jev-ultrafast` at
`452c1ad2dd628008f1d5608f28158d76e49e6cc0`. The reference checkout at
`C:\source\projects\jev-ultrafast` remains unchanged; execution uses this fork.
All Git operations for this workspace use WSL. Upstream attribution and usage
documentation remain below.

**Current gate: CP7 GREEN for a small local experimental framework.** CP6
transferred the frozen checks to a second subscription application with real
text entry: 9/10 defects detected, 0/10 false findings, 10/10 controls
definitive and independently completed, and zero false passes. A 60-case
first-app regression stayed GREEN. The original CP5 economic result remains
AMBER at 7.27x cheaper against the declared 10x gate; framework admission uses
the owner's explicitly restated latency/throughput value proposition, not a
claim that the cost gate passed.
See the [checkpoint plan](plans/jev-feasibility/plan.html).

The first extracted core is `journey_evals/framework.py`: typed observation,
evaluation, run-result and window-binding contracts plus loading/effect evidence
windows used by both applications. Domain verifiers and trusted bindings remain
application-owned.

> **Naming note.** The CP7 receipt describes a `JourneySpec` in `framework.py`.
> That type is now `framework.WindowBinding`, because a journey specification in
> the product sense is a different, larger contract and now lives in
> `journey_evals/contracts.py`. The rename is purely nominal; the CP7 evidence is
> unaffected, and receipts stay valid at the source fingerprint they recorded.

> **Measurement note.** After CP7, `Browser` was found to create its CDP target
> in the background whenever the session was not explicitly headed, which made
> `Page.captureScreenshot` stall for 15 s in owned headless Chrome. Owned
> sessions are now always foreground. Setup dropped from roughly 2.5 s to 0.4 s,
> so the CP5-D overhead figures below predate the fix and were re-measured; see
> `artifacts/eval/receipts/overhead.json`.

### What CP5 measured

| Arm | Question | Result |
| --- | --- | --- |
| A, held-out quality | Does Jev resolve an unexposed 90-case corpus? | **GREEN.** Jev 90/90, the `gpt-5.6-luna` baseline 90/90, deterministic code alone 10/90 |
| B, live repeatability | Does it hold over 60 real browser journeys? | **AMBER.** 59/59 usable journeys classified correctly with 0 spurious findings, but 7 journeys were reported passing while the user goal was never reached |
| C, economics | Is Jev at least 10x cheaper per correctly resolved case? | **AMBER.** 7.27x cheaper and 14.0x faster, so the declared 10x gate is not met |
| D, instrumentation overhead | Does the collector distort what it measures? | **GREEN.** 4.79 ms per step, 20.09% median slowdown, inside the declared tolerance |

Both model arms scored 100% in arm A, so that corpus bounds quality from below
but cannot rank the two models against each other. Arm C is an estimate from
reported usage at two published/authorised rates, not an invoice.

### The finding that matters most

All seven arm-B failures are the same case: the loading check correctly returned
`clean`, but the actor had abandoned the journey without booking anything. The
separation is total. Of the ten loading controls, the seven whose search took
longer than 1.2 s produced **0** bookings, and the three that answered in under
0.25 s produced **3**. The actor gives up on a page that takes about a second to
respond, even when that page is showing visible progress feedback.

This is a limitation of the browser actor, not of Jev and not of the evaluators:
Jev classified all seven of those journeys correctly. The consequence for any
framework built on this is concrete — **a green check does not mean the journey
succeeded**, so journey completion has to be gated and reported separately from
the check verdict. These runs were retained as measured; the delay range was not
lowered to make the gate pass.

### What this means for the value proposition

On this workload Jev's demonstrated advantage is **latency (14.0x)**, not cost
(7.27x, below the 10x gate) and not quality (tied at the ceiling). Committing to
CP6 and CP7 is therefore an explicit decision to either restate the value
proposition around latency and journey throughput, or to pivot. CP6 additionally
remains blocked on a `TEXT_MODEL_API_KEY` and a second synthetic application.

Measured results and retained evidence are rendered in
`artifacts/visuals/cp5-results.png`, with three videos built only from retained
run evidence: `journey-clean.mp4`, `defect-vs-repaired.mp4`, and
`live-fault-detected.mp4`.

### The repair loop (CP4)

CP3 screening detected 9/9 held-out defects with 0/9 false positives, and its
live trio was revalidated on the current runtime without rescoring the exposed
holdout.

The application under test is now real source: `journey_evals/app/booking.html`
and `journey_evals/app/pricing.py`. A defect is materialised by copying that
source and genuinely editing it, because a runtime fault flag cannot be repaired.
Two targets were run:

| Target | Injected defect | Repair the agent made |
| --- | --- | --- |
| `fare-surcharge` | an undisclosed peak surcharge in `pricing.py` | removed the surcharge so checkout charges the selected fare |
| `silent-confirmation` | `booking.html` built the confirmation markup but never rendered it | rendered it after the booking succeeds |

In both cases the booking itself succeeded and the independent verifier passed
**before** the repair, so the gate turns on the user-facing defect rather than on
booking success. In both cases the actor also reported `done`; the evaluator is
what caught the problem. Each target additionally required a paired legitimate
control to stay clean (for the fare target, a disclosed and acknowledged
increase), and the byte-identical original defect restored into a separate
disposable copy to be caught again by the unchanged evaluator. The evaluator,
oracle, journey, fixture controls and acceptance criterion are hashed before the
task is handed over and rechecked afterwards; repairs are capped at two attempts
and both targets passed on the first.

### Known environment behaviour: intermittent Chrome stalls

On this workstation a freshly created headless target occasionally never answers
a CDP command. A stack dump showed the harness daemon idle and correctly waiting,
and one stalled request was answered 9.5 s late, so the stall is inside Chrome
rather than in this code. Reordering the commands, launching at a fixed window
size, reusing the daemon's own tab, and Chrome's anti-throttling flags were each
measured and none fixed it; the anti-throttling arm crashed the browser.

What ships instead is bounded and honest: startup and shutdown use a generous
60 s bound, and a browser stall may be retried on a fresh session **only while
the budget ledger proves no inference has happened yet**, capped at three
attempts, with every abandoned attempt recorded in the receipt. A stall after
inference begins fails the run, and a worker killed for exceeding its wall clock
is never retried because its browser could not be cleaned up.

## Journey Evals: run one journey, get evidence back

> **New here? Read [docs/guide.md](docs/guide.md)** — the developer guide: what
> Journey Evals is, how to write a journey for your own application, how to read a
> report, and how to wire it into CI or a coding-agent loop.

`journey-evals run` is the product this campaign earned the right to build. It takes a
**journey** — a starting URL, a plain-language task, declared checks, and an
independent acceptance contract — drives an owned browser through it, and writes
a versioned report backed by retained evidence.

Outside this checkout it installs from npm, which fetches the Python package and
a pinned Chrome for Testing build on first use:

```bash
npm install --save-dev journey-evals
npx journey-evals install-browser
npx journey-evals demo          # bundled app + bundled journey + live console
npx journey-evals init          # copy the example journeys into ./journeys
```

Inside this checkout, use the venv entry point:

```powershell
# Validate a journey without spending anything.
.\.venv\Scripts\journey-evals.exe validate --journey examples\journeys\flight-booking.json

# Serve the bundled synthetic application, then run the journey against it.
.\.venv\Scripts\journey-evals.exe serve --app flight --port 8111
.\.venv\Scripts\journey-evals.exe run --journey examples\journeys\flight-booking.json --out artifacts\eval\demo

# Re-read a finished run without re-running it.
.\.venv\Scripts\journey-evals.exe show artifacts\eval\demo\<run-id>
```

Three synthetic applications ship with the package — `--app flight` (8111),
`--app subscription` (8112) and `--app admin` (8113). The third, Meridian, is
the one that matters for calibration: every journey it declares ends in a
degraded, partial, rejected, stale or destructive state rather than a clean
success, and every seeded defect is paired with a *legitimate lookalike* that
must not be reported. `journey-evals demo --list` prints each bundled journey
with the application it drives.

`--url` plus `--task` runs in exploration mode. Exploration can report findings
but **cannot produce `PASS`**: without a declared independent success contract
there is nothing that proves the journey finished, and this tool does not treat
an actor's own `DONE` as evidence.

### Watching a run happen

`journey-evals watch` starts the ordinary run and streams its journal to a local
console, so you can see the pages, the actor's decisions, and each declared
check settling while it happens — then the final verdict.

```powershell
# One command: serve the example app with a seeded defect, run the journey,
# open the console, and show the browser doing the work.
.\.venv\Scripts\journey-evals.exe watch --journey examples\journeys\flight-booking.json `
    --serve-app flight --fault silent_fare_increase
```

Drop `--fault` for a clean journey, add `--headless` to watch only the console,
and use `--port` to move the console off `8770`. Watching starts the run
immediately; there is no button to press.

The console is a **viewer, not a second runner**. It shells out to the same
`journey-evals run`, renders the same append-only journal the report is built from,
and reads the final panel out of `report.json`. It never evaluates anything and
cannot change a verdict. Page text and model output are written as text, never
as markup: the console displays evidence from a page that may be broken.

`--serve-app` refuses to start when something already answers on the journey's
port. Two servers sharing one port would let the run read another process's
records, which is exactly how a correct acceptance contract ends up failing a
journey that actually succeeded.

### The invariant everything else serves

**A green check is not proof of a successful journey.** CP5 measured seven live
journeys where the checks were right and the journey had still been abandoned.
So goal completion is established on a path the actor cannot influence — a
backend read, a URL, persisted control values — and is reported separately from
every check verdict. Missing or unreadable evidence resolves to `unknown`, never
to a quiet pass.

### What a run produces

| Artifact | Contents |
| --- | --- |
| `report.json` | The versioned result: goal status and its evidence, per-check coverage, findings, usage, timings, environment |
| `report.html` | A static, self-contained page. Page text and model output are escaped, never rendered as markup |
| `junit.xml` | CI-facing. Unknown and pending checks are **skipped**, never reported as passed |
| `agent-feedback.json` | Machine-readable input for a coding agent; confirmed findings, advisory findings, and unresolved coverage kept in separate lists |
| `events.jsonl` | The append-only journal every finding cites, redacted before it reaches disk |
| `usage.json` | Model requests and the measured USD estimate for this run |

### Results and exit codes

| Result | Exit | Meaning |
| --- | --- | --- |
| `PASS` | 0 | The goal was independently verified and no required check failed |
| `WARN` | 0 | Verified, with advisory findings that were not promoted to blocking |
| `FAIL` | 1 | The goal was violated, a required check failed, or a finding matched a declared blocking policy |
| `INCONCLUSIVE` | 2 | Required evidence was missing; the run proves nothing either way |
| `ERROR` | 3 | The tool itself failed. This is never advisory |

`--warn-as-error` turns `WARN` into exit 1. `--blocking-check` promotes a named
check for one run. Semantic evaluators stay advisory by default: they are not
calibrated on a corpus large enough to gate a merge.

### Declaring a journey

A journey names its checks and, separately, how success is proved:

```json
{
  "id": "flight-booking",
  "url": "http://127.0.0.1:8111/",
  "task": "Book the cheapest one-way flight from Zurich to London for one adult.",
  "mode": "verify",
  "acceptance": {"backend": {"url": "http://127.0.0.1:8111/__test__/bookings", "min_items": 1}},
  "checks": [
    {"id": "search-shows-results", "scope": "transition", "required": true,
     "when": {"action_executed": "Search flights"},
     "expect": {"ready_when": [{"text_contains": "CHF"}]},
     "deadline_ms": 4000}
  ]
}
```

The actor instruction, the evaluation expectation, and the acceptance assertion
are deliberately three separate things. Predicates come from a fixed vocabulary
(`text_contains`, `url_path_is`, `control_present`, `control_disabled`,
`control_value_is`, `action_executed`, `any_of`, `all_of`, `not`, …), not a
general workflow language, so a journey cannot smuggle in executable behaviour.

`journey-evals validate` checks the schema, the predicate vocabulary, and that every
probe's required checks exist, and warns when a journey declares no probes.

### One journey, several sizes

A journey may declare one viewport or several. Several makes it a matrix: the
same declared checks, measured independently at each size.

```jsonc
"viewport": ["desktop", "mobile"]
```

```bash
journey-evals run --journey journeys/checkout.json                     # every declared size
journey-evals run --journey journeys/checkout.json --viewport mobile   # just one
```

Each size gets its own `report.json`, plus a `matrix.json` listing the cells.
Results are never merged — passing on a desktop and failing on a phone is a
finding, not an average — and a size the journey does not declare is refused
rather than silently measured. Nine presets (`mobile`, `tablet`, `laptop`,
`desktop-large`, …) are shorthand for pixels; only the pixels are ever recorded.

### The coding-agent loop

1. The agent builds the feature and runs the ordinary unit, lint, and build checks.
2. `journey-evals run` emits `report.json` and `agent-feedback.json`.
3. The agent reads **confirmed findings** and **unresolved coverage** as separate things. An unresolved check is not a defect and not a pass.
4. It fixes the application — not the journey, the evaluators, or the acceptance checks.
5. The unchanged journey and the seeded controls run again.

A passing rerun alone does not prove a defect was fixed unless the same scenario
and the unchanged oracle actually ran. There is no `--fix` command in V0.

### What it actually detects

220 journeys across four suites on the bundled fixtures, 10 repeats per scenario, every run
retained in its original denominator:

| | Flight | Subscription | Flight, 820x700 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 90 | 90 | 20 | 20 | **220** |
| Seeded defects detected | 50/50 | 50/50 | 10/10 | 10/10 | **120/120** |
| Controls with a spurious finding | 0/40 | 0/40 | 0/10 | 0/10 | **0/100** |
| Clean journeys independently verified | 20/20 | 20/20 | 10/10 | 10/10 | **60/60** |
| False passes | 0 | 0 | 0 | 0 | **0** |
| Execution errors | 0 | 0 | 0 | 0 | **0** |
| Median wall clock | 15.3 s | 18.9 s | 44.0 s | 17.5 s | 18.0 s |
| Cost per journey | USD 0.00139 | USD 0.00216 | USD 0.00323 | USD 0.00096 | **USD 0.00183** |

Collector overhead is **1.12 ms per scripted step** and a median slowdown inside measurement
noise, re-measured after the screenshot fix described below.

These 220 journeys were measured on the demo applications' original markup. Both demo pages have
since been restyled twice, most recently into two deliberately unlike design systems. The current
pages were re-measured at one repeat per scenario (22 journeys, 12/12 defects detected, 0/10
spurious control findings, 6/6 clean journeys verified), which shows detection survived each
redesign without re-establishing the rates above. The second redesign did break one case before it
was caught — a fixture padding change hid a clipped control completely instead of partly, so it
stopped being reportable; `plans/journey-evals/status.md` records it.

Earlier sweeps are retained as recorded, including a first one in which a missing backend reset
between repeats made a correct acceptance contract fail 35 of 40 controls. These are small
denominators on applications we wrote; they do not transfer to an arbitrary application, and they
are not enough to make semantic checks blocking. Full numbers, caveats, and the release checklist:
[docs/release-checklist.md](docs/release-checklist.md).

### Bounds worth stating plainly
- Owned local fixtures and synthetic data. Real purchases, messages, deletions, and production writes are out of scope.
- No general visual QA. Layout problems are found by measuring DOM geometry — clipping, occlusion, offscreen controls — not by comparing rendered pixels. Screenshots are human-readable evidence; they are not sent to a model.
- Shadow roots, frames, canvas, uploads, pop-up tabs, and arbitrary keyboard widgets remain outside this MVP, exactly as upstream.
- One run of one journey cannot establish flakiness.
- Data leaving the machine is the structured element table and page text sent to the evaluation model, plus the field-level prompt sent to the text helper. Screenshots, the journal, and credentials do not leave. Browser child processes receive no inference credentials.
- Redaction removes configured secrets, declared patterns, and URL credentials before anything is written. Screenshots are not redacted.

### CI

`.github\workflows\qa.yml` runs two jobs at two trust levels. `contracts` runs on
every push and pull request including forks: lint, offline tests, `node --check`,
and the browser guards, with no secrets and no model calls. `live` runs the real
journey in **advisory** mode, only on manual dispatch from a non-fork, gated on
an approval environment. It does not use `pull_request_target` and does not
consume artifacts produced by untrusted code. A runner error still fails the job;
"the tool could not run" and "the application is fine" are different statements.

## Run the scoped experiment

Only Windows, the pinned Chrome for Testing binary, and the built-in synthetic
loopback booking fixture are supported by `journey-evals` today. It does not accept
an arbitrary target URL. Browser children receive no inference credentials.
Jev receives synthetic text/JSON; screenshots are local human-readable evidence,
not visual-model inputs.

```powershell
uv sync
# Keep the existing .env; never overwrite it.
# Configure JEV_API_KEY or TYPESAFE_API_KEY, and TYPESAFE_MODEL=jev-1.13.0.
```

### Configuring the text helper for CP6

`TYPE_TEXT` needs a separate small text model, and CP6 exercises ordinary text
entry. The `gpt-5.6-luna` deployment already configured for the CP5 baseline arm
can serve it, so no additional vendor is required. Azure's v1 surface speaks a
different dialect from the OpenRouter default: it rejects `max_tokens` and the
`reasoning` object, requiring `max_completion_tokens` and `reasoning_effort`.
`TEXT_MODEL_DIALECT` selects the spelling and is detected from the endpoint when
it is not set.

```powershell
# Reuse the Azure deployment already configured for the baseline arm.
# TEXT_MODEL_BASE_URL is GPT_LUNA_API_ENDPOINT with the trailing /responses removed.
TEXT_MODEL_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
TEXT_MODEL=gpt-5.6-luna
TEXT_MODEL_API_KEY=<the same key as GPT_LUNA_API_KEY>
# Optional; azure is detected automatically from the endpoint.
TEXT_MODEL_DIALECT=azure
```

Verified against the live deployment: the helper returned `Zurich` and `London`
for the departure and destination fields of the standard goal. It correctly
returns nothing for a field the goal does not determine, such as a passenger
name, because the prompt forbids inventing personal information.

For a fresh installation, install Chrome for Testing at the exact owned path:

```powershell
New-Item -ItemType Directory -Force .tools | Out-Null
Invoke-WebRequest -Uri "https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.52/win64/chrome-win64.zip" -OutFile .tools\chrome-153.0.8010.52.zip
Expand-Archive -LiteralPath .tools\chrome-153.0.8010.52.zip -DestinationPath .tools\chrome-153.0.8010.52
Remove-Item -LiteralPath .tools\chrome-153.0.8010.52.zip
```

Run checkpoints explicitly and stop on a nonzero exit:

```powershell
.\.venv\Scripts\journey-evals.exe preflight --jev-budget-usd 0.40 --prior-receipt plans\jev-feasibility\evidence\upstream-smoke.json
.\.venv\Scripts\journey-evals.exe verify-browser
.\.venv\Scripts\journey-evals.exe verify-clean --jev-budget-usd 0.40
# Only after CP2 passes on the same source, for an unexposed screening corpus:
.\.venv\Scripts\journey-evals.exe screen-detection --jev-budget-usd 0.40
# CP4, one target at a time, with a coding agent doing the repair in between:
.\.venv\Scripts\journey-evals.exe repair-prepare --defect fare-surcharge
.\.venv\Scripts\journey-evals.exe repair-verify --defect fare-surcharge --attempt 1
.\.venv\Scripts\journey-evals.exe verify-repair-loop
# CP5, four independent arms on one source revision:
.\.venv\Scripts\journey-evals.exe screen-reliability   # arm A, held-out quality, then economics
.\.venv\Scripts\journey-evals.exe live-runs --seed 20260918   # arm B, 60 live journeys, ~1 hour
.\.venv\Scripts\journey-evals.exe measure-overhead     # arm D, no model calls
```

`measure-overhead` makes no paid inference calls. `live-runs` is the expensive
arm: 60 journeys cost roughly USD 0.033 and take about an hour on this
workstation, because intermittent Chrome stalls add a retry to roughly one
journey in three.

`verify-browser` makes no paid inference calls. Other commands make explicit
Jev calls against a **cumulative USD 0.40 published-rate estimate cap**, including
the imported initial investigation. The cap was raised from USD 0.10 once CP0-CP5
had consumed USD 0.0597 and CP6/CP7 needed headroom; the ledger refuses implicit
cap changes, so that raise is recorded in
`artifacts\feasibility\cap-raise-0.10-to-0.40.json` with the pre-change ledger
preserved beside it. No attempt record was edited, so earlier receipts remain
valid at their original denominators. Each HTTP attempt reserves maximum usage
before sending; ambiguous billing retains its reservation. The ledger rejects
implicit cap changes and concurrent access. This is not a provider invoice.

Local evidence is under `artifacts\feasibility`: `budget.json`, latest checkpoint
receipts, immutable `checkpoints\attempts`, cohort manifests, worker journals,
screenshots, independent reports, and frozen screening reports. Failures remain
in their original denominators. A fresh UUID is not a fresh holdout: the CLI
refuses to rescore an exposed semantic corpus. Do not delete the budget or
screening history to bypass these safeguards.

The implementation reuses upstream's actor, guarded action execution, snapshot,
and HTTP transport. That agent keeps its own name and its own package: it is
imported as `jev_ultrafast`, and `journey_evals` is this product built around it.
Added modules provide ownership/cleanup, pre-navigation
telemetry, durable action records, independent booking checks, and three advisory
evaluators. Text-helper input, vision, and second-app transfer remain untested or
unimplemented. The model comparison covers one baseline on one workload, not a
matrix. Abrupt worker termination is not the cooperative-cancellation check.

## Upstream project

<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from journey_evals import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](journey_evals/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check journey_evals/telemetry.js
node --check journey_evals/snapshot.js
node --check journey_evals/static/app.js
node --check journey_evals/static/console.js
node --check examples/flight.js
uv run python scripts/check_guards.py
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls
and evidence completeness in a local browser without model calls.

The `journey-evals` console script is generated at install time, so a `.venv` created
before a command was added will reject it with `invalid choice`. Re-sync the
install (`uv sync`, or `pip install -e . --no-deps --no-build-isolation`) after
pulling, or just call the module: `python -m journey_evals.cli <command>`.
`scripts/calibrate.py --suite flight --repeats 10` runs the paid calibration
sweep against the bundled fixtures and prints detection, false-finding, and
latency figures; every failure and execution error stays in its original
denominator. Live examples and recording scripts make paid API calls.
`scripts/record_flights.py <new-folder>` captures original browser timestamps;
`scripts/render_demo.py <recording-folder>` renders that verified run at 1× and
crops out the Google account strip. Credentials and raw traces stay ignored.


---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
