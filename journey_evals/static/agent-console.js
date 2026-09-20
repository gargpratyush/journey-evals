"use strict";

// Agent replies and tool output are untrusted evidence, so every value from the run is placed
// with textContent and never parsed as markup. The console is a viewer; it must not become a
// way for an agent under evaluation to execute anything in the page watching it.

const chat = document.getElementById("chat");
const judgesEl = document.getElementById("judges");
const verdictEl = document.getElementById("verdict");
const phaseEls = Array.from(document.querySelectorAll(".phase"));

let after = 0;
let announced = null;
let judges = [];
const verdicts = new Map();
let started = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function atBottom() {
  const pane = chat.parentElement;
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
}

function add(node) {
  const stick = atBottom();
  chat.appendChild(node);
  // Only follow along if the viewer was already at the live edge, so reading back through
  // earlier turns is not yanked away by the next one arriving.
  if (stick) chat.parentElement.scrollTop = chat.parentElement.scrollHeight;
}

function clip(value, limit) {
  const text = String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : text.slice(0, limit - 1) + "\u2026";
}

function renderEvent(event) {
  const data = event.data || {};
  if (event.kind === "agent_turn_started") {
    announced = data.user || "";
    add(el("div", "turnmark", "Turn " + data.turn));
    add(el("div", "bubble user", data.user || ""));
    return;
  }
  if (event.kind === "agent_message") {
    const message = data.message || {};
    const role = message.role || "";
    const content = message.content || "";
    if (role === "tool" || !String(content).trim()) return;
    if ((role === "user" || role === "human") && announced !== null
        && String(content).trim() === String(announced).trim()) {
      return; // the turn bubble above already showed this; the graph is echoing it back
    }
    const mine = role === "user" || role === "human";
    add(el("div", "bubble " + (mine ? "user" : "agent"), content));
    return;
  }
  if (event.kind === "agent_tool_called") {
    const tool = data.tool || {};
    add(el("div", "tool", "\u2192 " + (tool.name || "?") + "(" + clip(JSON.stringify(tool.args || {}), 120) + ")"));
    return;
  }
  if (event.kind === "agent_tool_result") {
    const tool = data.tool || {};
    const failed = ["error", "failed"].indexOf(String(tool.status || "").toLowerCase()) >= 0;
    add(el("div", "tool" + (failed ? " error" : ""), "\u2190 " + (tool.name || "?") + ": " + clip(tool.output, 160)));
    return;
  }
  if (event.kind === "agent_evaluation") {
    verdicts.set(data.check_id, data.verdict || "UNKNOWN");
    return;
  }
  if (event.kind === "agent_failed") {
    add(el("div", "tool error", "run failed: " + clip(data.detail, 200)));
  }
}

function renderJudges(phase) {
  judgesEl.textContent = "";
  judges.forEach(function (judge) {
    const card = el("div", "judge");
    const head = el("header");
    head.appendChild(el("span", "id", judge.id));
    const verdict = verdicts.get(judge.id);
    const waiting = phase === "judging" ? "judging" : "not yet judged";
    const tag = el("span", "tag " + (verdict || "waiting"), verdict || waiting);
    if (!verdict && phase === "judging") tag.classList.add("dots");
    head.appendChild(tag);
    card.appendChild(head);
    card.appendChild(el("div", "blocking", judge.enforcement));
    card.appendChild(el("div", "req", judge.requirement));
    judgesEl.appendChild(card);
  });
}

function renderPhase(phase) {
  const order = ["conversation", "judging", "done"];
  const index = order.indexOf(phase);
  phaseEls.forEach(function (node) {
    const position = order.indexOf(node.dataset.phase);
    node.classList.toggle("active", position === index);
    node.classList.toggle("complete", index >= 0 && position < index);
  });
  const note = document.getElementById("judgenote");
  if (phase === "done") {
    note.textContent = "The whole conversation was judged in a single request after the last turn.";
  } else if (phase === "judging") {
    note.textContent = "Conversation finished. Judging the whole conversation in one request\u2026";
  } else {
    note.textContent = "Judging starts when the conversation ends, not turn by turn: some "
      + "requirements can only be decided once later turns exist.";
  }
}

function renderSummary(state) {
  if (state.error) {
    verdictEl.className = "verdict ERROR";
    verdictEl.textContent = "ERROR";
    return;
  }
  if (!state.summary) {
    verdictEl.className = "verdict pending";
    verdictEl.textContent = state.phase === "judging" ? "judging" : "running";
    return;
  }
  verdictEl.className = "verdict " + state.summary.result;
  verdictEl.textContent = state.summary.result;
  const cost = state.summary.cost || {};
  const costEl = document.getElementById("cost");
  costEl.textContent = "";
  costEl.appendChild(el("div", null, "Basis: " + (state.summary.evidence_basis || "")));
  if (cost.usd !== undefined) {
    costEl.appendChild(el("div", null, "Judging cost: USD " + cost.usd + " ("
      + (cost.input_tokens || 0) + " in / " + (cost.output_tokens || 0) + " out tokens)"));
  }
}

function poll() {
  fetch("/api/state?after=" + after, { headers: { "X-Console-Token": window.CONSOLE_TOKEN } })
    .then(function (response) { return response.json(); })
    .then(function (state) {
      if (!started) {
        started = true;
        document.getElementById("title").textContent = state.evaluation_id || "Agent evaluation";
        document.getElementById("task").textContent = state.task || "";
        judges = state.judges || [];
      }
      (state.events || []).forEach(function (event) {
        after = Math.max(after, event.sequence || 0);
        renderEvent(event);
      });
      const turns = chat.querySelectorAll(".turnmark").length;
      document.getElementById("turncount").textContent =
        state.turns_expected ? turns + " of " + state.turns_expected + " turns" : "";
      renderPhase(state.phase);
      renderJudges(state.phase);
      renderSummary(state);
      if (state.status === "running" || state.status === "idle") setTimeout(poll, 500);
    })
    .catch(function () { setTimeout(poll, 1500); });
}

poll();
