# Journey Evals

**Drive a real browser — or a real AI agent — through one declared user journey, and report what actually happened.**

A page that says "Success" is never accepted as proof that anything succeeded.

https://github.com/user-attachments/assets/594e52fb-de06-4074-98ed-3414d8b89002

Journey Evals takes a *journey*: a starting URL, a plain-language task, the checks
you care about, and — separately — a contract that proves the work really happened.
It drives an owned browser through the task, then writes a versioned report backed
by retained evidence.

> **Status: experimental.** This is a working prototype with real measurements
> behind it, not a stable release. The measured numbers below come from synthetic
> applications written for the purpose. They do not transfer to an arbitrary
> application. See [Limits](#limits).

---

## The one idea

Most end-to-end tests assert against the same screen the user is looking at. If the
screen lies — the confirmation renders but the booking was never recorded — the
test is green and the bug ships.

So Journey Evals establishes goal completion on a path the actor **cannot
influence**: a backend read, a URL, persisted control values. That is reported
separately from every check verdict, and missing or unreadable evidence resolves to
`unknown` — never to a quiet pass.

**A model may choose what to do next and may flag a suspicion. It is never allowed
to decide whether the task succeeded.** Every report records which basis its verdict
rests on, so a model-graded pass is never mistaken for a proof.

---

## Install

```bash
pip install journey-evals
```

Or with [pipx](https://pipx.pypa.io/) or [uv](https://docs.astral.sh/uv/), which keep
it out of your project environment:

```bash
pipx install journey-evals
uv tool install journey-evals
```

The npm distribution is not published yet. To work on Journey Evals itself, install
from a checkout:

```bash
git clone https://github.com/gargpratyush/journey-evals.git
cd journey-evals
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .\.venv\Scripts\pip3.exe install -e .
```

Requires **Python 3.12+**. A pinned Chrome for Testing build is downloaded into a
user cache on first use:

```bash
journey-evals install-browser
```

Copy `.env.example` to `.env` and set `JEV_API_KEY` (or `TYPESAFE_API_KEY`).
Credentials stay server-side and are never passed to browser child processes.

## Quickstart

```bash
journey-evals demo           # bundled app + bundled journey + live console
journey-evals demo --list    # every bundled journey and the app it drives
journey-evals init           # copy the example journeys into ./journeys
```

Three synthetic applications ship with the package: `--app flight` (port 8111),
`--app subscription` (8112) and `--app admin` (8113). Each has **declared, hidden
faults** — the defect lives on the server or in the served script, never in the URL,
the DOM or the page text, so a model cannot pass by reading a label instead of
observing behaviour.

Run one yourself:

```bash
# Validate the journey. Free: no browser, no model call.
journey-evals validate --journey examples/journeys/flight-booking.json

# Serve the app, then run the journey against it.
journey-evals serve --app flight --port 8111
journey-evals run --journey examples/journeys/flight-booking.json --out artifacts/demo

# Re-read a finished run without re-running it.
journey-evals show artifacts/demo
```

`--url` plus `--task` runs in exploration mode. Exploration can report findings but
**cannot produce `PASS`**: with no declared success contract, nothing proves the
journey finished, and an actor's own claim of `DONE` is not evidence.

## Writing a journey

A journey names its checks and, separately, how success is proved:

```json
{
  "id": "flight-booking",
  "url": "http://127.0.0.1:8111/",
  "task": "Book the cheapest one-way flight from Zurich to London for one adult.",
  "mode": "verify",
  "acceptance": {
    "backend": {"url": "http://127.0.0.1:8111/__test__/bookings", "min_items": 1}
  },
  "checks": [
    {
      "id": "search-shows-results",
      "scope": "transition",
      "required": true,
      "when": {"action_executed": "Search flights"},
      "expect": {"ready_when": [{"text_contains": "CHF"}]},
      "deadline_ms": 4000
    }
  ]
}
```

Three things are deliberately kept separate: the **actor instruction** (`task`), the
**evaluation expectation** (`checks`), and the **acceptance assertion**
(`acceptance`). Predicates come from a fixed vocabulary — `text_contains`,
`url_path_is`, `control_present`, `control_disabled`, `control_value_is`,
`action_executed`, `any_of`, `all_of`, `not` — not a general workflow language, so a
journey cannot smuggle in executable behaviour.

Three rules that matter:

1. **Write the goal, not the route.** No selectors, no click steps. The journey keeps
   working when the UI is redesigned, and it fails when the *experience* breaks.
2. **Write checks a person could notice.** "The page states what the downgrade takes
   away" is falsifiable by looking at the screen. `assert text === "…"` is a
   different claim, and it passes the day the disclosure silently disappears.
3. **Prove the outcome somewhere else.** `acceptance.backend` asks the server what it
   recorded. If the page says "Booked" and the server never recorded it, that is
   reported as a failure — the most expensive bug this catches.

### Several viewport sizes

```jsonc
"viewport": ["desktop", "mobile"]
```

```bash
journey-evals run --journey journeys/checkout.json                    # every declared size
journey-evals run --journey journeys/checkout.json --viewport mobile  # just one
```

Each size gets its own `report.json` plus a `matrix.json`. Results are never merged —
passing on desktop and failing on a phone is a finding, not an average — and a size
the journey does not declare is refused rather than silently measured.

## Watching a run

```bash
journey-evals watch --journey examples/journeys/flight-booking.json \
    --serve-app flight --fault silent_fare_increase
```

One command serves the app with a seeded defect, runs the journey, opens a console
and shows the browser working. Pages, actor decisions and each check settling stream
live, then the verdict. Drop `--fault` for a clean run; add `--headless` to watch
only the console. Watching starts the run immediately — there is no button to press,
and it runs until you press Ctrl-C.

The console is a **viewer, not a second runner**. It shells out to the same
`journey-evals run`, renders the same append-only journal the report is built from,
and reads its final panel out of `report.json`. It cannot change a verdict. Page text
and model output are written as text, never as markup: the console displays evidence
from a page that may be hostile or broken.

## What a run produces

| Artifact | Contents |
| --- | --- |
| `report.json` | The versioned result: goal status and its evidence, per-check coverage, findings, usage, timings, environment |
| `report.html` | A static self-contained page. Page text and model output are escaped, never rendered as markup |
| `junit.xml` | CI-facing. Unknown and pending checks are **skipped**, never reported as passed |
| `agent-feedback.json` | Machine-readable input for a coding agent; confirmed findings, advisory findings and unresolved coverage kept in separate lists |
| `events.jsonl` | The append-only journal every finding cites, redacted before it reaches disk |
| `usage.json` | Model requests and the measured USD estimate for this run |

### Results and exit codes

| Result | Exit | Meaning |
| --- | --- | --- |
| `PASS` | 0 | The goal was independently verified and no required check failed |
| `WARN` | 0 | Verified, with advisory findings not promoted to blocking |
| `FAIL` | 1 | The goal was violated, a required check failed, or a finding matched a blocking policy |
| `INCONCLUSIVE` | 2 | Required evidence was missing; the run proves nothing either way |
| `ERROR` | 3 | The tool itself failed. This is never advisory |

`INCONCLUSIVE` failing your build is deliberate. "The tool could not establish the
answer" and "the application is fine" are different statements.

`--warn-as-error` turns `WARN` into exit 1. `--blocking-check` promotes a named check
for one run. Semantic evaluators stay advisory by default: they are not calibrated on
a corpus large enough to gate a merge.

---

## Evaluating an AI agent

The same evidence and reporting pipeline evaluates an AI agent from its observable
execution trace. The first adapter supports synchronous **LangGraph** compiled graphs
through their `values` stream, recording state updates, messages, tool calls, tool
results and the final output. It does not collect hidden chain-of-thought.

An evaluation points at a graph exported as `module:attribute`:

```json
{
  "schema_version": 1,
  "id": "support-agent-refund",
  "task": "Determine whether the order is eligible for a refund.",
  "runtime": {
    "framework": "langgraph",
    "entrypoint": "my_agent.graph:compiled_graph"
  },
  "input": {
    "messages": [{"role": "user", "content": "Can this order be refunded?"}]
  },
  "acceptance": {
    "output_contains": ["eligible"],
    "tools_called": ["lookup_order"],
    "no_tool_errors": true
  }
}
```

```bash
journey-evals agent validate --eval examples/agent-eval.json
journey-evals agent run --eval examples/agent-eval.json --out artifacts/agent
```

LangGraph is an *application* dependency, not a package dependency — install the
version your agent uses. The adapter relies only on the compiled graph's stable
`stream(input, stream_mode="values")` surface, so your agent brings its own framework
version.

### Judging intent when no code oracle exists

Some qualities have no deterministic test. Whether a reply to a distressed customer is
warm, takes ownership, or leaves a real choice cannot be settled by string matching,
and an agent's wording changes every run. For these, a judge may be declared
`blocking`:

```json
{
  "id": "acknowledges-the-guest",
  "family": "communication_quality",
  "enforcement": "blocking",
  "requirement": "The reply acknowledges what losing the booked room means for this guest and takes ownership before moving to logistics."
}
```

A blocking judge decides the run: its `FAIL` becomes an overall `FAIL`, exit code 1,
and a real JUnit failure — not a skipped advisory. Two rules keep this honest:

- **A judge may never block on `task_outcome`.** Whether the work actually happened
  stays the job of deterministic acceptance. Declaring `enforcement: blocking` on that
  family is a contract error, not a warning.
- **Delegation is explicit.** To say a dimension has no code oracle, the evaluation
  must declare `"acceptance": {"basis": "model_judgment"}`, which requires at least
  one blocking judge.

Every report records `evidence_basis`: `code`, `code_and_model_judgment`, or
`model_judgment`.

### Multi-turn conversations

A `conversation` is a list of user turns sent in order, with the agent's replies
carried forward as history — so the evaluation tests what the agent *remembers* as
well as what it says. Budgets span the whole conversation rather than resetting each
turn.

```bash
journey-evals agent run --eval examples/agent-eval-travel-multiturn.json \
    --out artifacts/travel --watch --show-cost
```

That example drives five turns of a Lisbon dinner plan against a deliberately flawed
travel agent, with six blocking judges. **The observed run is a partial failure, which
is the point** — a useful evaluation discriminates rather than condemning. Four
criteria pass: it honours the accessibility constraint stated in turn 1, invents no
venues, answers every question and stays courteous. Two fail: it confirms the booking
without disclosing the cancellation fee its own tool returned, and it answers in
markdown despite a declared plain-prose requirement. Exit `1`.

Judging happens **once, after the last turn**, in a single request. It has to: whether
a booking confirmation disclosed a fee cannot be decided while that turn is still the
newest thing in the trace, because a later disclosure is exactly what separates a pass
from a fail.

### Exploring and watching

```bash
# Chat with an agent before writing any criteria. Nothing is judged or recorded.
journey-evals agent chat --entrypoint examples.langchain_concierge_agent:graph

# Watch an evaluation stream in the terminal.
journey-evals agent run --eval examples/agent-eval-concierge.json --watch

# Or in a browser: conversation on the left, judges on the right, phase strip on top.
journey-evals agent console --eval examples/agent-eval-travel-multiturn.json
```

Both viewers are fed from the journal *after* redaction, so watching a run cannot
reveal a secret the artifacts would have masked. The console is served on loopback
only and renders every value as text, never as markup — an agent under evaluation must
not be able to execute anything in the page watching it.

### What a run costs

```
Cost: USD 0.000174 for 1 judge request(s) (4075 in / 75 out tokens at 4.2E-8 per token)
      within the declared budget of USD 0.200000
      not included: agent under test (the agent's own provider has no rate configured here)
```

Pass `--show-cost`. The price comes from tokens actually reported by the judge, at the
authorized rate — not an advertised list ratio — and is always compared against the
evaluation's `budgets.usd`, which is a hard stop rather than a suggestion. The agent
under test runs on whatever provider its owner configured, which this tool has no rate
for, so those tokens are **named as excluded rather than counted as zero**.

### Reading what happened afterwards

| File | What it answers |
| --- | --- |
| `report.json` | `history` is the conversation, `tool_trace` the tool calls, `evaluations` the verdicts with confidences, `turns` pairs each user turn with the reply it drew |
| `judge-exchange.json` | Exactly what was sent to the judge and exactly what came back |
| `events.jsonl` | The append-only journal every other artifact derives from |
| `report.html` | The same run as a readable page |
| `junit.xml` | CI result; blocking judge failures appear as real failures |

`judge-exchange.json` is written whenever a judge ran. If the provider fails, the
request is still recorded with the error, so a verdict you cannot reproduce is never
left unexplained.

---

## What it actually detects

220 journeys across four suites on the bundled fixtures, 10 repeats per scenario,
every run retained in its original denominator:

| | Flight | Subscription | Flight, 820x700 | Back/persistence | Combined |
| --- | --- | --- | --- | --- | --- |
| Journeys executed | 90 | 90 | 20 | 20 | **220** |
| Seeded defects detected | 50/50 | 50/50 | 10/10 | 10/10 | **120/120** |
| Controls with a spurious finding | 0/40 | 0/40 | 0/10 | 0/10 | **0/100** |
| Clean journeys independently verified | 20/20 | 20/20 | 10/10 | 10/10 | **60/60** |
| False passes | 0 | 0 | 0 | 0 | **0** |
| Median wall clock | 15.3 s | 18.9 s | 44.0 s | 17.5 s | 18.0 s |
| Cost per journey | USD 0.00139 | USD 0.00216 | USD 0.00323 | USD 0.00096 | **USD 0.00183** |

Collector overhead is **1.12 ms per scripted step**, with a median slowdown inside
measurement noise.

**Read these honestly.** They were measured on the demo applications' original markup.
Both demo pages have since been restyled twice into deliberately unlike design
systems, and the current pages were re-measured at only one repeat per scenario (22
journeys, 12/12 defects detected, 0/10 spurious findings, 6/6 clean journeys
verified) — which shows detection survived each redesign without re-establishing the
rates above. The second redesign *did* break one case before it was caught: a fixture
padding change hid a clipped control completely instead of partly, so it stopped being
reportable.

Earlier sweeps are retained as recorded, including a first one where a missing backend
reset between repeats made a correct acceptance contract fail 35 of 40 controls. These
are small denominators on applications we wrote. They do not transfer to an arbitrary
application, and they are not enough to make semantic checks blocking.

Full numbers and caveats: [docs/release-checklist.md](docs/release-checklist.md).

## Limits

- **Owned local fixtures and synthetic data.** Real purchases, messages, deletions and
  production writes are out of scope.
- **No general visual QA.** Layout problems are found by measuring DOM geometry —
  clipping, occlusion, offscreen controls — not by comparing rendered pixels.
  Screenshots are human-readable evidence; they are not sent to a model.
- Shadow roots, frames, canvas, uploads, pop-up tabs and arbitrary keyboard widgets
  are outside this MVP.
- **One run of one journey cannot establish flakiness.**
- Agent evaluation currently supports LangGraph only, and a long enough conversation
  can still exceed the judge provider's input limit.
- **What leaves the machine:** the structured element table and page text sent to the
  evaluation model, plus the field-level prompt sent to the text helper. Screenshots,
  the journal and credentials do not leave. Browser child processes receive no
  inference credentials.
- **Redaction** removes configured secrets, declared patterns and URL credentials
  before anything is written. Screenshots are not redacted.

## Continuous integration

`.github/workflows/qa.yml` runs two jobs at two trust levels. `contracts` runs on every
push and pull request including forks: lint, offline tests, `node --check` and the
browser guards, with no secrets and no model calls. `live` runs a real journey in
**advisory** mode, only on manual dispatch from a non-fork, gated on an approval
environment. It does not use `pull_request_target` and does not consume artifacts
produced by untrusted code.

## Using it with a coding agent

1. The agent builds the feature and runs the ordinary unit, lint and build checks.
2. `journey-evals run` emits `report.json` and `agent-feedback.json`.
3. The agent reads **confirmed findings** and **unresolved coverage** as separate
   things. An unresolved check is not a defect and not a pass.
4. It fixes **the application** — not the journey, the evaluators or the acceptance
   checks.
5. The unchanged journey and the seeded controls run again.

A passing rerun alone does not prove a defect was fixed unless the same scenario and
the unchanged oracle actually ran. There is no `--fix` command.

## Documentation

- **[docs/guide.md](docs/guide.md)** — the developer guide: writing journeys for your
  own application, connecting a LangGraph agent, reading a report, CI, full command
  reference.
- [docs/release-checklist.md](docs/release-checklist.md) — measured results and caveats.

## Development

```bash
ruff check .
pytest
node --check journey_evals/static/console.js
python -m hatchling build
```

## Provenance and licence

This project began as a fork of
**[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** (MIT).
The `jev_ultrafast` package is that upstream work — the actor loop, the CDP adapter,
the model transport and the prompts — kept under its own name rather than absorbed, so
its provenance stays visible in the source tree. The `journey_evals` package and
everything built around it are this project's own contribution.

Chrome connects through
[Browser Harness](https://github.com/browser-use/browser-harness). Operation and
element selection use [TypeSafe's Jev](https://docs.typesafe.ai/introduction).

MIT licensed. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
