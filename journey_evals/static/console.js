/* Live console for one journey-evals run.
 *
 * Everything rendered here comes from the run's journal. Page-derived text and model output are
 * written with textContent, never innerHTML: the console shows evidence from a page that may be
 * broken or hostile, so it must not execute any of it.
 */
"use strict";

const TOKEN = "__TOKEN__";
const POLL_MS = 600;

const el = (id) => document.getElementById(id);
const state = { after: 0, checks: new Map(), started: false, done: false, lastShot: null,
                viewport: null, cell: null, cellSignature: "", selected: null, cells: [],
                journals: new Map(), fetching: new Set() };

function post(action) {
  return fetch("/api/" + action, {
    method: "POST",
    headers: { "X-Console-Token": TOKEN, "Content-Type": "application/json" },
    body: "{}",
  }).then((r) => r.json());
}

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null && text !== "") n.textContent = String(text);
  return n;
}

function row(tag, what) {
  const r = node("div", "row");
  r.append(node("span", "tag", tag), node("span", "what", what));
  return r;
}

function ms(value) {
  if (value === null || value === undefined) return "";
  return value >= 1000 ? (value / 1000).toFixed(1) + " s" : Math.round(value) + " ms";
}

/* ---------- timeline ---------- */

function renderEvent(item) {
  const li = node("li");
  switch (item.kind) {
    case "observation":
    case "settled_observation": {
      li.className = "observation";
      li.append(row("look", item.title || item.url || "observed the page"));
      const bits = [];
      if (item.phase) bits.push(item.phase);
      if (item.action_count) bits.push(item.action_count + " actionable control(s)");
      li.append(node("div", "detail", bits.join(" · ")));
      if (item.url) li.append(node("div", "detail mono", item.url));
      break;
    }
    case "watermark": {
      li.className = "observation";
      li.append(row("state", "telemetry watermark"));
      const bits = [item.visible_count + " of " + item.control_count + " control(s) visible"];
      if (item.clipped) bits.push(item.clipped + " clipped or offscreen");
      li.append(node("div", "detail", bits.join(" · ")));
      if (item.feedback && item.feedback.length) {
        li.append(node("div", "detail", "feedback: " + item.feedback.join(" | ")));
      }
      break;
    }
    case "decision_consumed": {
      li.className = "decision";
      li.append(row("decide", item.operation || "decision"));
      const bits = [];
      if (item.confidence !== null && item.confidence !== undefined) {
        bits.push("confidence " + Number(item.confidence).toFixed(2));
      }
      if (item.latency_ms) bits.push(ms(item.latency_ms));
      if (item.model) bits.push(item.model);
      if (item.input_tokens) bits.push(item.input_tokens + " in / " + (item.output_tokens || 0) + " out");
      li.append(node("div", "detail", bits.join(" · ")));
      break;
    }
    case "action_attempt": {
      li.className = "action";
      li.append(row("attempt", (item.action_kind || "act") + ": " + (item.label || "")));
      break;
    }
    case "action_executed": {
      li.className = "action";
      li.append(row("step " + (item.step ?? "?"), (item.action_kind || "act") + ": " + (item.label || "")));
      if (item.text) li.append(node("div", "detail", "typed: " + item.text));
      if (item.url) li.append(node("div", "detail mono", item.url));
      break;
    }
    case "evaluation": {
      const settled = item.state === "passed" ? "pass" : item.state === "failed" ? "fail" : "unknown";
      li.className = "evaluation-" + settled;
      li.append(row("check", item.check_id || ""));
      const bits = [item.verdict];
      if (item.family) bits.push(item.family.replace(/_/g, " "));
      if (item.source) bits.push(item.source === "code" ? "decided in code" : "model");
      li.append(node("div", "detail", bits.filter(Boolean).join(" · ")));
      if (item.reason) li.append(node("div", "detail", item.reason));
      break;
    }
    case "terminal_decision": {
      li.className = "terminal";
      li.append(row("actor", "stopped: " + (item.status || "")));
      break;
    }
    case "browser_event": {
      li.className = "observation";
      li.append(row(item.event, item.text || item.url || ""));
      break;
    }
    case "action_rejected_stale": {
      li.className = "problem";
      li.append(row("rejected", item.reason || "stale action"));
      break;
    }
    default: {
      li.className = "problem";
      li.append(row(item.kind.replace(/_/g, " "), "the page reported a problem"));
      if (item.detail) li.append(node("div", "detail mono", item.detail));
    }
  }
  return li;
}

/* ---------- side panels ---------- */

function paintChecks(checks, list) {
  checks = checks || state.checks;
  list = list || el("checks");
  list.replaceChildren();
  if (!checks.size) {
    list.append(node("li", "empty", "Nothing evaluated yet."));
    return;
  }
  for (const [id, info] of checks) {
    const li = node("li");
    const head = node("div", "row");
    head.append(node("span", "what", id));
    head.append(node("span", "verdict v-" + info.state, info.state.toUpperCase()));
    li.append(head);
    if (info.requirement) li.append(node("div", "detail", info.requirement));
    const bits = [];
    if (info.verdict) bits.push(info.verdict);
    if (info.source) bits.push(info.source === "code" ? "decided in code" : "model");
    if (info.observation_ids && info.observation_ids.length) {
      bits.push("evidence " + info.observation_ids.join(", "));
    }
    if (bits.length) li.append(node("div", "detail mono", bits.join(" · ")));
    list.append(li);
  }
}

function paintFindings(findings, list) {
  list = list || el("findings");
  list.replaceChildren();
  if (!findings || !findings.length) {
    list.append(node("li", "empty", "No findings."));
    return;
  }
  for (const finding of findings) {
    const li = node("li");
    const head = node("div", "row");
    head.append(node("span", "sev sev-" + (finding.severity || "low"), finding.severity || ""));
    head.append(node("span", "what", finding.title || ""));
    li.append(head);
    if (finding.requirement) li.append(node("div", "detail", finding.requirement));
    if (finding.observed) {
      li.append(node("div", "detail mono", JSON.stringify(finding.observed)));
    }
    const bits = [];
    if (finding.provenance) bits.push(finding.provenance);
    if (finding.review_state) bits.push(finding.review_state);
    if (finding.evidence_ids && finding.evidence_ids.length) {
      bits.push("evidence " + finding.evidence_ids.join(", "));
    }
    if (bits.length) li.append(node("div", "detail mono", bits.join(" · ")));
    list.append(li);
  }
}

function describeBasis(basis) {
  if (!basis) return "";
  if (typeof basis === "string") return basis;
  if (!Array.isArray(basis)) return JSON.stringify(basis);
  return basis
    .map((b) => (b.check_id || b.policy || "") + (b.effect ? " → " + b.effect : ""))
    .filter(Boolean)
    .join(" · ");
}

function paintFinal(final, exitCode, box) {
  box = box || el("final");
  box.replaceChildren();
  box.classList.remove("hidden");
  box.append(node("div", "result " + (final.result || ""), final.result || "NO RESULT"));
  const basis = describeBasis(final.result_basis);
  box.append(node("div", "detail", basis || "no declared check decided this result"));

  const dl = document.createElement("dl");
  const pair = (term, value) => {
    dl.append(node("dt", null, term), node("dd", null, value));
  };
  pair("Goal", final.goal_status === "verified"
    ? "verified by an independent check"
    : String(final.goal_status || "unknown"));
  pair("Execution", final.execution_status || "");
  const passed = Object.values(final.checks).filter((v) => v === "passed").length;
  pair("Checks", passed + " passed of " + Object.keys(final.checks).length + " declared");
  if (final.failed_required_checks.length) pair("Failed", final.failed_required_checks.join(", "));
  if (final.missing_required_checks.length) pair("Never resolved", final.missing_required_checks.join(", "));
  if (final.usage && final.usage.usd) {
    pair("Cost", "USD " + final.usage.usd + " · " + (final.usage.model_requests || 0) + " model request(s)");
  }
  if (final.timings) {
    const wall = final.timings.total_ms || final.timings.wall_ms || final.timings.cli_wall_ms;
    if (wall) pair("Wall clock", ms(wall));
  }
  pair("Exit code", String(exitCode));
  if (final.errors && final.errors.length) pair("Errors", final.errors.length + " recorded");
  box.append(dl);
}

/* ---------- the shape of the run ----------
 * A screenshot is stretched to the width of its frame, so a 390px phone run rendered edge to edge
 * in a wide pane is misleading: it shows the page at three times the size the journey measured it
 * at, which hides exactly the cramping a narrow journey exists to find. The frame is therefore
 * capped at a width that suits the device class the size falls into. The class is decided in
 * contracts.py, not here, so the console and the report cannot disagree about what "phone" means.
 */
function applyViewport(source) {
  const size = source.viewport;
  if (!size || state.viewport === String(size)) return;
  state.viewport = String(size);
  const frame = el("frame");
  frame.className = "viewframe " + (source.viewport_class || "desktop");
  // The frame keeps the declared shape whatever a single capture happens to be: the screenshot box
  // is floored at the declared aspect, so an early or short capture is letterboxed instead of
  // squashing a 390x844 phone into a wide strip. A taller capture still shows in full.
  frame.style.setProperty("--shot-aspect", (size[1] / size[0] * 100).toFixed(3) + "%");
  el("size").textContent = size[0] + " x " + size[1]
    + (source.viewport_class ? " \u00b7 " + source.viewport_class : "");
}

/* ---------- the viewport matrix ----------
 * A journey may declare several sizes. They run one after another, so the panes on the right hold
 * one run at a time. The rows are a selector for those panes rather than a second place to read a
 * run: choosing a finished viewport replays it into the same timeline, checks, findings, verdict
 * and frame the live run uses, read back from that cell's own journal and report. Choosing the row
 * that is still running returns the panes to the live journal.
 */

function checkTally(final) {
  const values = Object.values(final.checks || {});
  const passed = values.filter((v) => v === "passed").length;
  return passed + "/" + values.length + " checks";
}

function checksFromJournal(journal) {
  // Rebuilt from the cell's own evaluation events, then settled with the verdicts the report
  // recorded, so a replayed cell shows the same panel it showed while it was live.
  const checks = new Map();
  for (const item of journal.events || []) {
    if (item.kind !== "evaluation") continue;
    checks.set(item.check_id, {
      state: item.state || "unknown",
      verdict: item.verdict,
      source: item.source,
      requirement: item.requirement,
      observation_ids: item.observation_ids,
    });
  }
  const final = journal.final;
  if (final) {
    for (const [id, verdict] of Object.entries(final.checks || {})) {
      checks.set(id, { ...(checks.get(id) || {}), state: verdict });
    }
  }
  return checks;
}

function badgeFor(cell) {
  if (cell.final && cell.final.result) return cell.final.result;
  if (cell.status === "running") return "RUNNING";
  if (cell.status === "finished") return "NO RESULT";
  return "QUEUED";
}

function metaFor(cell) {
  if (!cell.final) {
    return cell.status === "running" ? "measuring now" : "not measured yet";
  }
  const bits = [checkTally(cell.final)];
  const findings = (cell.final.findings || []).length;
  bits.push(findings === 1 ? "1 finding" : findings + " findings");
  if (cell.final.goal_status) bits.push("goal " + cell.final.goal_status);
  return bits.join(" \u00b7 ");
}

function cellRow(cell, index, live) {
  // A viewport is a tab: the strip reads like a browser's, and the tab that is lit is the run the
  // console's columns are describing.
  const item = node("li", "cell");
  const head = document.createElement("button");
  head.type = "button";
  head.className = "cellhead";
  head.setAttribute("role", "tab");
  const showing = state.selected === null ? live : state.selected;
  if (showing === index) head.classList.add("showing");
  head.setAttribute("aria-selected", showing === index ? "true" : "false");
  const following = index === live && state.selected === null && cell.status !== "finished";
  head.append(node("span", "celldot d-" + badgeFor(cell), ""));
  head.append(node("span", "celllabel", cell.label));
  head.append(node("span", "cellsize", cell.viewport[0] + "\u00d7" + cell.viewport[1]));
  head.append(node("span", "cellresult r-" + badgeFor(cell), badgeFor(cell)));
  const selectable = Boolean(cell.final) || index === live;
  head.title = cell.label + " \u00b7 " + cell.viewport[0] + " x " + cell.viewport[1]
    + " \u00b7 " + metaFor(cell);
  if (following) head.classList.add("following");
  if (!selectable) head.disabled = true;
  head.addEventListener("click", () => {
    // Selecting the cell that is still running means "follow the live journal again", not "replay
    // a run that has not finished".
    state.selected = index === live ? null : index;
    state.cellSignature = "";
    if (state.selected === null) resumeLive();
    else showRecorded(state.selected);
  });
  item.append(head);
  return item;
}

function paintCells(data) {
  const box = el("matrix");
  const cells = data.cells || [];
  if (cells.length < 2) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const signature = JSON.stringify([
    cells.map((cell) => [cell.label, cell.status, cell.final ? cell.final.result : null]),
    state.selected, data.cell,
  ]);
  if (signature === state.cellSignature) return;
  state.cellSignature = signature;
  const done = cells.filter((cell) => cell.final);
  const active = cells[data.cell];
  const where = done.length === cells.length
    ? "all viewports measured"
    : (active ? "now running " + active.label : "waiting to start");
  const shown = cells[state.selected === null ? data.cell : state.selected];
  el("matrixprogress").textContent =
    done.length + " of " + cells.length + " complete \u00b7 " + where
    + (shown ? " \u00b7 showing " + shown.label + ": " + metaFor(shown) : "");
  const list = el("cells");
  list.setAttribute("role", "tablist");
  list.replaceChildren();
  cells.forEach((cell, index) => list.append(cellRow(cell, index, data.cell)));
}

/* ---------- showing a viewport that has already been measured ---------- */

function clearPanes() {
  el("events").replaceChildren();
  el("count").textContent = "0";
  el("final").classList.add("hidden");
  el("final").replaceChildren();
  el("shot").replaceChildren(node("p", "empty", "No screenshot yet."));
  el("url").textContent = "\u2014";
  el("title").textContent = "\u2014";
  el("controls").textContent = "\u2014";
  el("feedback").textContent = "\u2014";
  el("checks").replaceChildren(node("li", "empty", "Nothing evaluated yet."));
  el("findings").replaceChildren(node("li", "empty", "No findings."));
}

function resumeLive() {
  // The live journal is re-requested from the beginning, so the panes are rebuilt from the run's
  // own events rather than from whatever the replay left behind.
  state.after = 0;
  state.checks = new Map();
  state.done = false;
  state.lastShot = null;
  state.viewport = null;
  el("replay").hidden = true;
  clearPanes();
}

function replayNotice(index) {
  // Repainted on every poll: which viewport is live changes underneath a replay.
  const cell = state.cells[index] || {};
  const live = state.cells[state.cell];
  const back = live && live.status === "running" ? "the viewport measuring now"
             : live ? live.label : "it";
  el("replay").hidden = false;
  el("replay").textContent = "Showing the recorded " + (cell.label || "")
    + " run, read back from its own journal. Select " + back + " to return.";
}

function paintRecorded(index, journal) {
  clearPanes();
  replayNotice(index);

  const list = el("events");
  let lastShot = null;
  let lastWatermark = null;
  for (const item of journal.events || []) {
    list.append(renderEvent(item));
    if (item.screenshot) lastShot = item.screenshot;
    if (item.url) el("url").textContent = item.url;
    if (item.title) el("title").textContent = item.title;
    if (item.kind === "watermark") lastWatermark = item;
  }
  if (!list.childElementCount) list.append(node("li", "empty", "No events recorded."));
  el("count").textContent = String(journal.total || (journal.events || []).length);

  if (lastWatermark) {
    el("controls").textContent = lastWatermark.visible_count + " visible of "
      + lastWatermark.control_count + " observed"
      + (lastWatermark.clipped ? ", " + lastWatermark.clipped + " clipped or offscreen" : "");
    el("feedback").textContent = lastWatermark.feedback && lastWatermark.feedback.length
      ? lastWatermark.feedback.join(" | ") : "none visible";
  }
  if (lastShot) {
    // Evidence is served per cell, so the last screen this viewport reached stays reachable long
    // after the console has moved on to the next one.
    const img = document.createElement("img");
    img.alt = "last screenshot recorded at this viewport";
    img.src = "/cell/" + index + "/evidence/" + lastShot.split("/").pop();
    el("shot").replaceChildren(img);
  }

  state.viewport = null;
  applyViewport({ viewport: journal.viewport || cell.viewport,
                  viewport_class: journal.viewport_class || cell.viewport_class });

  paintChecks(checksFromJournal(journal), el("checks"));
  const final = journal.final;
  if (final) {
    paintFindings(final.findings, el("findings"));
    paintFinal(final, journal.exit_code, el("final"));
  }
}

function showRecorded(index) {
  const journal = state.journals.get(index);
  if (journal) return paintRecorded(index, journal);
  clearPanes();
  el("replay").hidden = false;
  el("replay").textContent = "Reading this viewport's journal\u2026";
  if (state.fetching.has(index)) return;
  state.fetching.add(index);
  fetch("/cell/" + index + "/journal")
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      state.fetching.delete(index);
      if (!data) return;
      state.journals.set(index, data);
      if (state.selected === index) paintRecorded(index, data);
    })
    .catch(() => state.fetching.delete(index));
}

/* ---------- polling ---------- */

function apply(data) {
  state.cells = data.cells || [];
  if (data.cell !== undefined && data.cell !== state.cell) {
    // The matrix has moved to the next viewport. The panes follow one run at a time, so they start
    // empty rather than inheriting the previous cell's evidence.
    if (state.cell !== null && state.selected === null) resumeLive();
    state.cell = data.cell;
    if (state.selected === null) data.events = [];
  }
  paintCells(data);
  el("status").textContent = data.status + (data.dropped ? " · " + data.dropped + " event(s) not shown" : "");
  const dot = el("dot");
  dot.className = "dot " + (data.status === "running" ? "running"
    : data.status === "finished" ? (data.exit_code === 0 ? "finished" : "failed") : "");
  if (data.journey) {
    el("journey").textContent = data.journey.id
      ? data.journey.id + " — " + (data.journey.task || "")
      : (data.journey.task || "");
  }
  el("stop").disabled = data.status !== "running";

  // A recorded viewport is on screen. The live journal keeps arriving and keeps being recorded;
  // the panes simply are not showing it, and nothing about the run depends on what is displayed.
  if (state.selected !== null) {
    if (state.journals.has(state.selected)) replayNotice(state.selected);
    return;
  }

  if (data.journey) {
    const cell = (data.cells || [])[data.cell];
    applyViewport(cell ? cell : data.journey);
  }
  const list = el("events");
  for (const item of data.events) {
    state.after = Math.max(state.after, item.sequence || 0);
    list.append(renderEvent(item));

    if (item.screenshot && item.screenshot !== state.lastShot) {
      state.lastShot = item.screenshot;
      const shot = el("shot");
      const img = document.createElement("img");
      img.alt = "screenshot of the page under test";
      img.src = "/" + item.screenshot;
      shot.replaceChildren(img);
    }
    if (item.url) el("url").textContent = item.url;
    if (item.title) el("title").textContent = item.title;
    if (item.kind === "watermark") {
      el("controls").textContent = item.visible_count + " visible of " + item.control_count
        + " observed" + (item.clipped ? ", " + item.clipped + " clipped or offscreen" : "");
      el("feedback").textContent = item.feedback && item.feedback.length
        ? item.feedback.join(" | ") : "none visible";
    }
    if (item.kind === "evaluation") {
      state.checks.set(item.check_id, {
        state: item.state || "unknown",
        verdict: item.verdict,
        source: item.source,
        requirement: item.requirement,
        observation_ids: item.observation_ids,
      });
      paintChecks();
    }
  }
  if (data.events.length) {
    el("count").textContent = data.total;
    list.lastElementChild.scrollIntoView({ block: "nearest" });
  }

  if (data.final && !state.done) {
    state.done = true;
    for (const [id, verdict] of Object.entries(data.final.checks)) {
      const existing = state.checks.get(id) || {};
      state.checks.set(id, { ...existing, state: verdict });
    }
    paintChecks();
    paintFindings(data.final.findings);
    paintFinal(data.final, data.exit_code);
  }
}

async function poll() {
  try {
    const response = await fetch("/api/state?after=" + state.after);
    if (response.ok) apply(await response.json());
  } catch (error) {
    el("status").textContent = "console disconnected";
  }
  setTimeout(poll, POLL_MS);
}

el("stop").addEventListener("click", () => post("stop"));

poll();
