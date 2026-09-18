# Jev Eval (experimental feasibility fork)

This workspace extends `browser-use/jev-ultrafast` at
`452c1ad2dd628008f1d5608f28158d76e49e6cc0`. The reference checkout at
`C:\source\projects\jev-ultrafast` remains unchanged; execution uses this fork.
All Git operations for this workspace use WSL. Upstream attribution and usage
documentation remain below.

**Current gate: CP5 AMBER.** Checkpoints 0 through 4 are GREEN and their
invariants were rechecked on the final CP5 source revision. CP5 measured
reliability and economics on that same revision and did **not** clear the bar:
quality passed, the cost advantage did not, and live journeys exposed a real
limitation in the browser actor. The two arms that passed and the two that did
not are described below. No general reliability, economic advantage, or
framework readiness is claimed.
See the [checkpoint plan](plans/jev-feasibility/plan.html).

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

The application under test is now real source: `jev_ultrafast/app/booking.html`
and `jev_ultrafast/app/pricing.py`. A defect is materialised by copying that
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

## Run the scoped experiment

Only Windows, the pinned Chrome for Testing binary, and the built-in synthetic
loopback booking fixture are supported by `jev-eval` today. It does not accept
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
.\.venv\Scripts\jev-eval.exe preflight --jev-budget-usd 0.40 --prior-receipt plans\jev-feasibility\evidence\upstream-smoke.json
.\.venv\Scripts\jev-eval.exe verify-browser
.\.venv\Scripts\jev-eval.exe verify-clean --jev-budget-usd 0.40
# Only after CP2 passes on the same source, for an unexposed screening corpus:
.\.venv\Scripts\jev-eval.exe screen-detection --jev-budget-usd 0.40
# CP4, one target at a time, with a coding agent doing the repair in between:
.\.venv\Scripts\jev-eval.exe repair-prepare --defect fare-surcharge
.\.venv\Scripts\jev-eval.exe repair-verify --defect fare-surcharge --attempt 1
.\.venv\Scripts\jev-eval.exe verify-repair-loop
# CP5, four independent arms on one source revision:
.\.venv\Scripts\jev-eval.exe screen-reliability   # arm A, held-out quality, then economics
.\.venv\Scripts\jev-eval.exe live-runs --seed 20260918   # arm B, 60 live journeys, ~1 hour
.\.venv\Scripts\jev-eval.exe measure-overhead     # arm D, no model calls
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
and HTTP transport. Added modules provide ownership/cleanup, pre-navigation
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
from jev_ultrafast import Agent

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
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
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
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
