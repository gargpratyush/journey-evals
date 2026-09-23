# How Journey Evals works, in plain language

This document explains the project without assuming you have read the code. It
answers four questions:

1. [How does the browser agent actually drive a web page?](#1-how-the-browser-agent-drives-a-page)
2. [What have we built on top of it?](#2-what-we-built)
3. [What is this framework for, and what tech stacks does it work with?](#3-what-the-framework-is-and-what-it-works-with)
4. [What else could we do with it?](#4-what-else-this-could-do)

---

## 1. How the browser agent drives a page

The browser agent lives in the `jev_ultrafast` package. It came from
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) and we
kept it under its own name. Its job is narrow: given one plain-language goal and a
live web page, decide the single next thing to do, do it, and look again.

### The loop

Every step is the same four beats:

```
  observe  →  choose  →  execute  →  observe again
```

That is the whole agent. There is no plan, no script, no list of steps written in
advance. It decides one action at a time from what is currently on screen.

### Beat 1: Observe — turning a page into a numbered list

The agent connects to a real Chrome through the **Chrome DevTools Protocol** (CDP),
the same channel your browser's own developer tools use. It is not a plugin and it
does not import your application's code. It just drives a browser the way a person's
hands would.

On each observation it asks the page for every element a person could actually
interact with, and gives each one **a number**:

```
[1] Where from?          (textbox, current value: "")
[2] Where to?            (textbox, current value: "")
[3] Departure date       (textbox)
[4] Passengers           (combobox, current value: "1 adult")
[5] Search flights       (button)
```

It also grabs the page URL, the title, the visible text, and the geometry of each
element — where it sits, how big it is, whether something covers it.

Two details matter later:

- **Only observed elements get numbers.** If a control is not on the page, it has no
  number, so it cannot be chosen. The agent cannot act on something it never saw.
- **Every observation is fingerprinted** — a hash of the URL, text, elements and
  scroll position. This is how the agent knows whether the page moved underneath it.

### Beat 2: Choose — a multiple-choice question, not free text

Here is the part that surprises people.

The model is **never asked to write code, CSS selectors, or instructions**. It is
asked multiple-choice questions, and it can only answer with one of the options
offered.

In a single request, the agent asks:

> **Question A — which operation?**
> `CLICK` · `TYPE_TEXT` · `SELECT` · `SCROLL_DOWN` · `SCROLL_UP` · `WAIT` · `DONE` · `BLOCKED`
>
> **Question B — if `CLICK`, which element?** `1` · `2` · `3` · `4` · `5`
> **Question C — if `TYPE_TEXT`, which element?** `1` · `2` · `3`
> **Question D — if `SELECT`, which option?** `4:1` · `4:2` · `4:3`

The menu is built fresh from what is on screen. `SCROLL_DOWN` is only offered when
there is actually more page below; a `TYPE_TEXT` target list only contains fields
that are really editable right now.

All of those questions are answered in **one network round trip**. The answers to
the operations that were *not* chosen are simply thrown away. This is the
"speculative fan-out" pattern: ask everything at once, use only the branch that
turned out to matter.

The model returns a choice plus a probability for every option and a confidence
number. The code then **validates the answer before trusting it**: the choice must
be one of the offered options, the probabilities must cover exactly the offered
options, they must sum to 1, and the chosen option must be the highest. An answer
that fails any of those checks is rejected rather than executed.

This is the safety property of the whole design: **the model picks from a menu the
code wrote.** It cannot invent a selector, cannot emit a script, cannot reach an
element that was not observed. The worst a wrong answer can do is click the wrong
button on the page in front of it.

### Beat 3: Execute — synthetic input, not JavaScript

Once an option is chosen, the code translates it into real input events over CDP:

- **Click** — dispatch `mousePressed` then `mouseReleased` at the element's centre
  coordinates.
- **Scroll** — dispatch a `mouseWheel` event.
- **Select** — set the dropdown to the observed option.
- **Type** — see below.

These are genuine browser input events, which is why the application's own event
handlers, validation and framework state updates all fire exactly as they would for a
human.

### "But how does it type, if the model only picks from a menu?"

This is the question the design is most often asked, and the answer is that **two
different models do two different jobs**.

The decision model can only choose `TYPE_TEXT` and *which field* to type into. It
cannot produce the words. So when `TYPE_TEXT` is chosen, a **second, small
language model** is called with a tightly bounded context:

- the goal ("Book the cheapest one-way flight from Zurich to London for one adult")
- the field being filled (its label, role and current value)
- the page title and its visible text
- the last few actions taken

It returns **one value for one field** — `"Zurich"` — as JSON. That string is then
typed into the focused element using CDP's `Input.insertText`.

So the division of labour is:

| Job | Who does it | What it may produce |
| --- | --- | --- |
| What to do next, and where | Decision model | One option from a fixed menu |
| What words to put in this one field | Small text model | A short string, for that field only |
| Everything else | Ordinary Python code | Clicks, scrolls, waits, verification |

Neither model ever writes executable anything. One picks a number; the other writes a
word. The code does the rest.

There is one more subtlety worth knowing: if a step has to be retried, the generated
text is **reused only when the entire input to the text model was identical**. That
stops a retry from quietly typing something different into the same box.

### Beat 4: Observe again — and the rules that keep it honest

After the action, the agent observes again and compares fingerprints. Several rules
stop the loop from lying to itself or from doing damage:

- **A decision is consumed before it is executed.** It is cleared from state *first*,
  so a retry can never double-click.
- **Browser mutations are never retried.** If a click's outcome is uncertain, the run
  stops as uncertain rather than clicking again. A duplicate booking is worse than an
  unfinished one.
- **Freshness is checked immediately before input.** If the page changed between the
  decision and the keystroke — which happens constantly on live sites — the action is
  rejected and the agent observes again. This is checked *after* text generation too,
  because that call takes time.
- **Execution is logged before the result is observed.** If the page navigates away
  mid-observation, the record still shows the action happened.
- **Bounded by budget.** At most 60 actions and a capped number of model calls. If
  nothing has changed for three actions in a row, it stops as blocked rather than
  looping.
- **`DONE` is just a choice, not proof.** The agent saying it finished is recorded as
  an opinion. Something else has to verify it — which is the whole point of section 2.

---

## 2. What we built

The agent above can drive a page. On its own it cannot tell you whether your
application is *correct*. That is what we added, in the `journey_evals` package.

### Part one: browser journey evaluations

You write a **journey**: a starting URL, a task in plain language, the checks you
care about, and — kept deliberately separate — a contract that proves the work
really happened.

```json
{
  "id": "flight-booking",
  "url": "http://127.0.0.1:8111/",
  "task": "Book the cheapest one-way flight from Zurich to London for one adult.",
  "acceptance": {
    "backend": {"url": "http://127.0.0.1:8111/__test__/bookings", "min_items": 1}
  },
  "checks": [
    {"id": "search-shows-results",
     "when": {"action_executed": "Search flights"},
     "expect": {"ready_when": [{"text_contains": "CHF"}]},
     "deadline_ms": 4000}
  ]
}
```

Note what is *not* there: no selectors, no click steps, no waits. You describe the
goal and the evidence, never the route. That is why the journey survives a redesign
and still fails when the experience breaks.

**The single most important idea in the project:**

> A page that says "Success" is not proof that anything succeeded.

So goal completion is established on a path the agent **cannot influence** — reading
the backend, checking the URL, inspecting persisted values — and is reported
separately from every other check. Missing evidence resolves to `unknown`, never to a
quiet pass.

What we implemented around that:

- **Four check families** — layout integrity (clipping, occlusion, offscreen
  controls, measured from DOM geometry rather than by comparing pixels), unexpected
  state, interaction correctness, and task progress.
- **A fixed predicate vocabulary** — `text_contains`, `url_path_is`,
  `control_present`, `control_disabled`, `control_value_is`, `action_executed`,
  `any_of`, `all_of`, `not`. Not a general programming language, so a journey cannot
  smuggle in executable behaviour.
- **A viewport matrix** — the same checks measured independently at several screen
  sizes. Results are never averaged: passing on desktop and failing on a phone is a
  finding.
- **Five results with distinct exit codes** — `PASS` (0), `WARN` (0), `FAIL` (1),
  `INCONCLUSIVE` (2), `ERROR` (3). `INCONCLUSIVE` failing your build is deliberate:
  "the tool could not establish the answer" and "your app is fine" are different
  statements and must not share an exit code.
- **Evidence artifacts** — `report.json`, a self-contained `report.html`, `junit.xml`
  for CI, `agent-feedback.json` for a coding agent to read, and `events.jsonl`, the
  append-only journal every finding cites, redacted before it touches disk.
- **A live console** — `journey-evals watch` streams the run into a browser page as
  it happens. It is a viewer, not a second runner: it shells out to the same command
  and cannot change a verdict.
- **Three synthetic applications** with deliberately seeded faults, where the defect
  lives on the server or in the served script — never in the URL, the DOM or the page
  text. Otherwise a model could pass by reading a label instead of observing
  behaviour.

### Part two: AI agent evaluations (LangChain / LangGraph)

The same evidence pipeline can evaluate an **AI agent** instead of a web page.

Point it at a compiled LangGraph graph, exported as `module:attribute`. It drives the
graph over one or many conversation turns and records state updates, messages, tool
calls, tool results and the final output. It does not collect hidden
chain-of-thought.

Some things about an agent can be checked in code: did it call `lookup_order`, did
any tool error, does the final answer contain the expected verdict token.

But many things cannot. Was the reply *warm*? Did it *take ownership*? Did it honour a
constraint the user mentioned four turns ago? Did it disclose a fee **at the moment
it mattered**, rather than only when asked later? No string match settles those, and
an agent's wording changes on every run.

So we added **LLM-as-a-judge**, with guardrails:

```json
{
  "id": "discloses-fees-when-booking",
  "enforcement": "blocking",
  "requirement": "When the agent confirms the booking, that same reply must tell the guest about the cancellation fee that book_table returned. Disclosing it only later, after the guest asks about cancelling, fails."
}
```

A `blocking` judge really does decide the run — its `FAIL` becomes exit code 1 and a
real JUnit failure, not a skipped advisory. Two rules keep that honest:

1. **A judge may never decide whether the task succeeded.** Declaring a blocking judge
   on `task_outcome` is a contract error that refuses to load, not a warning. Whether
   the work happened stays deterministic code's job, permanently.
2. **Delegation must be explicit.** To say a dimension has no code oracle, the
   evaluation has to declare `"acceptance": {"basis": "model_judgment"}`, which in
   turn requires at least one blocking judge.

Every report records `evidence_basis` — `code`, `code_and_model_judgment`, or
`model_judgment` — so a model-graded pass is never mistaken for a proof.

Also implemented here:

- **Multi-turn conversations.** Replies are carried forward as history, so you test
  what the agent *remembers*, not just what it says once.
- **Judging happens once, after the last turn.** It has to: whether a booking
  confirmation disclosed a fee cannot be decided while that turn is still the newest
  thing in the trace, because a later disclosure is exactly what separates a pass from
  a fail. It is also about six times cheaper than judging per turn.
- **`judge-exchange.json`** — exactly what was sent to the judge and exactly what came
  back. If the provider fails, the request is still recorded with the error, so a
  verdict you cannot reproduce is never left unexplained.
- **`agent chat`** to talk to an agent before writing any criteria, `agent run
  --watch` to stream an evaluation in the terminal, and `agent console` for the
  browser view.
- **Cost reporting** with `--show-cost`, priced from tokens actually reported and
  compared against the declared budget. The agent under test runs on its owner's
  provider, which we have no rate for, so those tokens are **named as excluded rather
  than counted as zero**.

A worked example ships with the project: a five-turn Lisbon dinner conversation
against a deliberately flawed travel agent, with six blocking judges. The observed
result is **four pass, two fail** — which is the point. A useful evaluation
discriminates rather than condemning.

---

## 3. What the framework is, and what it works with

### What it is for

It answers one question: **did a real person's journey through this software actually
work, and can you prove it?**

It is not a replacement for unit tests, which check that a function returns the right
value. It sits where unit tests are blind — on the difference between "the code ran"
and "the user got what they came for". The most valuable bug it finds is the one
where the page says *Booked* and the server recorded nothing, because every
screen-asserting test in your suite is green on that screen.

### What tech stacks it works with

**For web applications: all of them.** This is not a diplomatic answer, it is a
consequence of the design.

Journey Evals **opens a URL**. It does not import your code, install a plugin, inject
a test harness, or need a build hook. It sees what a browser sees. So:

| Stack | Works? |
| --- | --- |
| React, Angular, Vue, Svelte, Solid, Next.js, Nuxt | Yes |
| Rails, Django, Laravel, Spring, ASP.NET | Yes |
| Plain HTML, static sites, server-rendered anything | Yes |
| Anything behind a URL your machine can reach | Yes |

The integration is *identical* in every case, because there is no integration. The
only thing your application needs is the one thing worth building anyway: **a way to
ask the server what it actually recorded**, for the acceptance contract. A test-only
read endpoint, a database query, or a URL that only exists in a truthful state is
enough.

**The tool itself** is Python 3.12+ and installs with `pip install journey-evals`.
There is also an npm shim so a JavaScript team can run it with `npx` without thinking
about Python — that shim is built and tested but not yet published.

**For AI agents: LangGraph today.** The adapter deliberately depends on only one
thing — a compiled graph's stable `stream(input, stream_mode="values")` surface — so
your agent brings its own framework version. LangChain's `create_agent` produces
exactly that, so LangChain agents work out of the box. Other frameworks would need a
new adapter, but not a new evaluation model.

### What it costs and how fast it is

Measured on this project's own fixtures:

| | Cost | Time |
| --- | --- | --- |
| One browser journey | ~ USD 0.002 | ~18 s |
| Agent eval, single turn, 3 judges | USD 0.000174 | 11.5 s |
| Agent eval, 5 turns, 6 judges | USD 0.000306 | 23.8 s |

Fractions of a cent. The honest comparison is not against a cheaper tool — it is that
a single downgrade screen that hides four seat removals costs more in one afternoon
of support than a year of running these.

### What it deliberately does not do

- Real purchases, messages, deletions or production writes. Owned fixtures only.
- General visual QA. Layout is checked by measuring DOM geometry, not by comparing
  rendered pixels. Screenshots are evidence for humans; they are never sent to a
  model.
- Shadow roots, iframes, canvas, file uploads, pop-up tabs, arbitrary keyboard
  widgets.
- Flakiness detection. One run of one journey cannot establish that.
- Agent evaluation beyond LangGraph, and a long enough conversation can still exceed
  the judge provider's input limit.

---

## 4. What else this could do

Ordered roughly by how much work each would take.

### Close at hand

- **More agent frameworks.** CrewAI, AutoGen, OpenAI's Agents SDK, or a plain
  function that returns a trace. The evaluation model — deterministic acceptance plus
  bounded judges — does not change; only the adapter does.
- **Accessibility journeys.** The agent already reads roles, labels and geometry.
  "Every control is reachable and labelled" is a checkable journey, and it would catch
  real regressions that automated scanners miss because they never complete a task.
- **Regression suites for prompts.** Run the same journey against two versions of a
  system prompt and report which criteria changed. Today you would compare two report
  files by hand.
- **Flakiness measurement.** Run one journey N times and report the distribution
  rather than a single verdict. The budget and reporting machinery already exists.
- **A GitHub Action.** Post the findings as a PR comment with the screenshots
  attached.

### A step further

- **Judge calibration.** This is the most valuable open item. Our judges keep
  returning `confidence: 1.0` on qualitative calls, which is suspiciously absolute,
  and we have never run the same persona twenty times to measure how stable a verdict
  actually is. Until that is done, blocking judges should be read as useful, not
  authoritative.
- **Record and replay.** The journal is append-only and complete. Replaying a run
  against a recorded trace would make judge changes testable without spending money or
  waiting for a browser.
- **Journeys from analytics.** Real funnels describe what users actually do. Turning
  the top ten into journeys would aim the tool at the paths that carry revenue.
- **Cross-browser.** The CDP dependency means Chrome today. Firefox and WebKit would
  need a different adapter behind the same `observe`/`act` interface.
- **Multi-agent and hand-off evaluation.** When one agent passes work to another, the
  interesting failures are at the seam — what was dropped, what was assumed. The
  trace model can already express that; nothing consumes it yet.

### The bigger idea

The pattern underneath this project is not really about browsers, and not really
about agents. It is:

> **Let a model choose from options that code wrote. Let code decide what actually
> happened. Never let those two swap jobs.**

That applies anywhere you want to point a model at a messy, changing system and still
get an answer you can defend — desktop applications, CLIs, mobile apps, API
workflows, data pipelines. The observation format and the action menu change. The
invariant does not.

---

## Where to go next

- [README](../README.md) — what it is and how to install it
- [Developer guide](guide.md) — writing journeys for your own application, connecting
  a LangGraph agent, reading a report, CI, full command reference
- [Release checklist](release-checklist.md) — the measured results and their caveats
- [NOTICE](../NOTICE) — what came from upstream and what is ours
