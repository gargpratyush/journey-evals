# journey-evals

Drive a real browser through a declared user journey, and get back a report that says what actually
happened — including "I could not tell", which is the answer most test tools are missing.

```bash
npm install --save-dev journey-evals
npx journey-evals demo
```

`demo` serves a bundled synthetic application, drives it in a real Chrome window, and opens a live
console showing the page, the evidence timeline and each declared check as it settles.

## What it needs

- **Node 18+** — this package is a shim.
- **Python 3.12+ on PATH** — the browser agent is Python, shipped here as a wheel and installed into
  a private environment inside this package. Nothing is added to your own interpreter.
- **Google Chrome**.
- **Your own Jev API key.** Put `JEV_API_KEY=...` in a `.env` file in your project, or in the
  environment. The key is read by the worker process only, is never printed, and never reaches the
  browser's environment.

Check all of that at once:

```bash
npx journey-evals doctor
```

## Using it

```bash
npx journey-evals init                                   # copy example journeys into ./journeys
npx journey-evals validate --journey journeys/checkout.json
npx journey-evals run      --journey journeys/checkout.json      # CI
npx journey-evals watch    --journey journeys/checkout.json      # live console
```

Exit codes are the contract, and this shim passes them through untouched:

| Code | Meaning |
| --- | --- |
| 0 | pass |
| 1 | fail — a declared check failed, or the goal was not verified |
| 2 | inconclusive — the run could not establish the answer |
| 3 | error — the tool itself fell over |
| 4 | configuration refused before anything was spent |

Inconclusive is deliberately not a pass.

## Documentation

Full guide, journey format, CI recipes and framework integration:
<https://github.com/gargpratyush/jev-test/blob/feasibility/docs/guide.md>

MIT licensed.
