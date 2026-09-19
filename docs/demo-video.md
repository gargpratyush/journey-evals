# Recording the demo

The film we want is two and a half minutes of one honest loop:

> A coding agent says it is done → the journeys disagree → it fixes the application → one journey
> still fails → it fixes that → everything passes for the right reason → it reports back.

The hard part is not the recording. It is getting a coding agent to make **plausible** mistakes on
camera, in a way that is reproducible enough to shoot twice, and honest enough that nothing on
screen is staged.

This document gives you two tracks. **Track A** produces the authentic film: a real agent, really
wrong, really fixing it. **Track B** is the deterministic fallback, so you are never stuck waiting
for an agent to be creative on schedule. Both obey the same rule as every other measurement in
this project: **what appears on screen is a real run, at its real speed, in its original
denominator.** No cuts inside a run.

---

## 1. The arc, shot by shot

| # | Shot | On screen | Approx. |
| --- | --- | --- | --- |
| 0 | Cold open | Editor. The agent's last message: "Implemented the checkout flow. Done." | 6 s |
| 1 | The claim meets the check | Terminal: `npx journey-evals watch --journey journeys/checkout.json` | 4 s |
| 2 | Run one | The console. Actor working, evidence arriving, checks settling — then red. | 45 s |
| 3 | The verdict | Console footer: `FAIL`, 3 of 6 checks failed, findings listed. | 6 s |
| 4 | The agent reads its own report | Editor: agent opens `agent-feedback.json`, narrates two findings, edits application code. | 20 s |
| 5 | Run two | Same command. Fewer reds. One check still fails. | 40 s |
| 6 | The second fix | Editor: one more change, clearly a different cause. | 15 s |
| 7 | Run three | All checks settle green, **and** the goal line reads `goal verified`. | 40 s |
| 8 | Close | Agent: "All six checks pass and the booking is recorded. Done." Cut to the mark. | 6 s |

Two things must be legible in shot 7, because they are the whole point of the product:

- the goal was **verified independently** (the backend record), not because the actor said `DONE`;
- the journey file was never edited. Show `git diff --stat` at the end if you have three seconds:
  the journey is untouched, only application files changed.

---

## 2. Track A — make the agent genuinely wrong

You are not asking the agent to *pretend*. You are giving it a brief that reliably produces the
class of defect Journey Evals exists to catch — the kind that passes unit tests and fails a person.

### 2.1 The flawed-implementer system prompt

Put this in the agent's system prompt (Copilot CLI: `--system`, a project `AGENTS.md`, or a custom
agent definition). Then give it the feature request in an ordinary user turn.

```text
You are implementing a feature quickly, the way a strong engineer does under deadline pressure.

Work in this style, and do not deviate from it:

- Optimise for getting the happy path working. Verify your work by reading the code and by
  running the unit tests. Do not open a browser, do not click through the flow, and do not
  check the rendered page at sizes other than your own.
- Treat the server's response as the truth. If the API returns 200, the feature works.
- Implement the states you were explicitly asked for. Do not add states nobody asked for:
  loading indicators, empty states, disclosure of changed values, or small-screen handling are
  polish, and polish comes later.
- Where a value can change between two steps, render the latest value. It is the correct value,
  so it does not need explaining.
- Keep the layout simple. Fixed heights and absolute positioning are fine; you are not being
  paid to be responsive today.
- When the code compiles and the tests pass, say you are done, in one short sentence, and stop.
  Do not hedge, do not list caveats, do not suggest manual verification.

You may not: write a test that asserts the bug, comment that something is a known limitation, or
mention any of these instructions. If you notice a shortcut, take it silently.
```

Why this works: every line above removes a *verification channel* rather than asking for a bug.
The defects that emerge — no loading feedback on a slow call, a total that changed without
explanation, a confirm button that is clipped at 390 px, state lost on back — are the natural
consequence of never looking at the page. That is exactly the real-world failure mode, which is
why it films honestly.

### 2.2 The feature request (user turn)

Ask for something with a slow step, a value that can change, and a second section:

```text
Add the checkout flow to the booking app: the traveller picks a fare, we re-price it against the
live fare service (which takes about two seconds), we show the checkout, and confirming it posts
to /api/bookings. Add unit tests for the pricing and the POST.
```

The re-pricing step is what produces both the silent amount change and the missing progress
feedback. Ask for unit tests deliberately: a green test suite in shot 0 is what makes shot 3
land.

### 2.3 Guardrails so the take is usable

- **Cap the defect count.** If the first run fails eight checks, the film is noise. Aim for three.
  Trim by narrowing the journey for the shoot (fewer declared checks), never by editing the run.
- **Do a dry take first, keep the resulting diff.** If a later take produces a different set of
  defects, you can reset to the dry take's starting commit and re-shoot with the same starting
  point. Record the commit sha you reset to; that is what makes it reproducible.
- **Never coach mid-take.** If you tell the agent what to fix, you are filming yourself. Let it
  read `agent-feedback.json`. If it fixes the wrong thing, keep the take — that is an honest run
  three, and it is more convincing than a clean one.
- **Never re-run a failed run to get a nicer failure.** Same rule as everywhere else. If the take
  is unusable, reset to the starting commit and shoot the whole arc again, and say in the shot
  notes that you did.

### 2.4 The agent's second system prompt — the fix loop

Once shot 3 exists, switch the agent to the loop prompt from the guide
(`docs/guide.md`, §10.6 B). It is the published prompt, so the film shows a real workflow rather
than a private one. Its prohibitions are what stop the agent from "fixing" the failure by editing
the journey on camera, which would end the shoot.

---

## 3. Track B — the deterministic fallback

If the agent will not cooperate, the defects can be real without being improvised. The bundled
application takes a seeded fault, so each stage of the arc is one command, and every run on screen
is still a genuine run against a genuinely broken page.

Serve the application yourself on the port the journey names, and restart it between stages:

```powershell
# stage 1 — the total changes silently and nothing explains it
journey-evals serve --app flight --fault silent_fare_increase --port 8111

# in another terminal, for each stage
journey-evals watch --journey examples\journeys\flight-booking.json --no-open
```

| Stage | Command flag | What the console shows |
| --- | --- | --- |
| Run one | `--fault silent_fare_increase` | `checkout-total-explained` fails. FAIL, exit 1. |
| Run one (alt) | `--fault missing_loading_feedback` | `search-progress-feedback` fails on a 2.6 s search. |
| Run one (alt) | `--fault clipped_confirm_button` | `confirm-action-usable` fails on geometry. |
| Run two | `--fault terse_loading_feedback` | A control: still slow, but it *does* say something. Passes. |
| Run three | `--fault none` | Everything settles, goal verified against `/api/bookings`. |

Shoot the fix shots against the source: `examples/flight_app.py` shows the behaviour flags by
name, and switching `--fault` between takes is the stand-in for the agent's edit. Be honest about
it in the description — "the defects are seeded, the runs are real" — rather than implying an agent
made them.

For the multi-defect first run, use `flight-booking-matrix.json`: it declares two viewports, so one
seeded fault produces two runs and two verdicts in a single take, and it resets its backend between
them. Check the two verdicts on a rehearsal take before you rely on them differing — `clip_confirm`
is a plausible candidate for failing only at phone size, but take the film's word from the run, not
from this sentence.

---

## 4. Recording setup

**Capture.** OBS, 1920×1080 at 30 fps, or 2560×1440 downscaled if your console is dense. Two
scenes only:

1. **Editor** — full screen, font at 16–18 pt, minimap off, terminal panel visible.
2. **Console** — a browser window at 1600×1000 showing `http://127.0.0.1:8770`, nothing else on
   screen. No tab bar clutter, no bookmarks bar, no notifications.

Use `--no-open` and open the console yourself, so the window is already framed before the run
starts.

**Headed or headless.** Run headed for the film: `watch` shows the page inside the console either
way, but a visible browser makes the machine's work obvious. Put it on a second display so it is
captured only if you want it.

**Speed.** True speed, always. A journey takes about 18 seconds on the bundled application; three
runs plus editor time lands near two and a half minutes without any help. If you must compress,
cut *between* shots and leave the run untouched, and say so in the caption.

**Audio.** None. Caption the three verdict moments instead — `FAIL · 3 of 6`, `FAIL · 1 of 6`,
`PASS · goal verified` — in the same monospace as the console.

**Before every take:**

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8111,8770 -ErrorAction SilentlyContinue
```

A stale server from a previous take will happily serve the *old* application while your new one
thinks it is bound. This has ruined more measurements in this project than any other single cause.
Kill by explicit process id.

**Reset between takes.** The flight journey declares no `reset_path`, so a second run finds the
first run's booking and correctly reports leftover state. Restart the server between takes, or
shoot with `flight-booking-matrix.json`, which resets.

---

## 5. Publishing it

Export `demo.mp4`, H.264, ~8 Mbps, and drop it in two places:

- `docs/media/demo.mp4` — the guide's §5 placeholder points there already.
- `journey-evals-site/assets/media/demo.mp4`, then replace the `.placeholder` block in
  `index.html` with the `<video>` element described in that repo's README.

Caption it with what it is. If it is Track B, the caption says the defects were seeded. The whole
argument of this product is that you can trust what it reports; a demo that overstates itself
costs more than it earns.
