// Relay front end: turns the orchestrator's events into a live conversation.
"use strict";

const ORDER = ["planner", "coder", "tester", "reviewer"];
const state = { kind: "create", sample: null, titles: {}, source: null, maxRounds: 3, requirements: [], bubble: null };
const LONG_NOTES = 500;  // agent notes longer than this are collapsed
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const title = (role) => state.titles[role] || { planner: "Planner", coder: "Builder", tester: "Tester", reviewer: "Reviewer" }[role] || "Relay";
const parseArgs = (raw) => { try { return JSON.parse(raw || "{}"); } catch { return null; } };

// ---------- setup ----------

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return response.json();
}

async function init() {
  try {
    const status = await getJSON("/api/status");
    const line = $("connection");
    line.textContent = "";
    if (status.api_key_configured) {
      line.append("Connected to ", el("strong", { text: status.model }));
      if (status.sandbox === "docker") line.append(", code runs in a sandbox");
    } else {
      line.append(el("strong", { text: "No API key set." }), " You can still watch recorded runs.");
      $("start").disabled = true;
      $("form-note").textContent = "Add an API key to your .env file to start live runs.";
    }
  } catch {
    $("connection").textContent = "The Relay server is not answering. Is it still running?";
  }

  try {
    const examples = await getJSON("/api/examples");
    for (const example of examples.create) {
      const chip = el("button", { type: "button", class: "example", text: example.title });
      chip.addEventListener("click", () => { $("prompt").value = example.prompt; $("prompt").focus(); });
      $("create-examples").append(chip);
    }
    for (const sample of examples.fix) {
      const button = el("button", { type: "button", class: "sample", role: "radio", "aria-checked": "false", "data-name": sample.name },
        el("span", { class: "sample-name", text: sample.name.replace(/^(easy|medium|hard)_/, "").replaceAll("_", " ") }),
        el("span", { class: "sample-meta", text: `${sample.difficulty}, ${sample.files.join(", ")}` }));
      button.addEventListener("click", () => selectSample(sample));
      $("fix-samples").append(button);
    }
  } catch { /* the form still works without examples */ }

  try {
    const replays = await getJSON("/api/replays");
    if (!replays.length) $("show-replays").hidden = true;
    for (const replay of replays) {
      const button = el("button", { type: "button", class: "replay" },
        el("span", { class: "replay-title", text: replay.title }),
        el("span", { class: "replay-task", text: replay.task }));
      button.addEventListener("click", () => startReplay(replay));
      $("replay-list").append(el("li", {}, button));
    }
  } catch { $("show-replays").hidden = true; }
}

function selectSample(sample) {
  state.sample = sample;
  for (const button of $("fix-samples").children) {
    button.setAttribute("aria-checked", String(button.dataset.name === sample.name));
  }
}

for (const tab of document.querySelectorAll(".mode")) {
  tab.addEventListener("click", () => {
    state.kind = tab.dataset.kind;
    for (const other of document.querySelectorAll(".mode")) other.setAttribute("aria-selected", String(other === tab));
    $("panel-create").hidden = state.kind !== "create";
    $("panel-fix").hidden = state.kind !== "fix";
    $("form-note").textContent = "";
  });
}

$("show-replays").addEventListener("click", () => {
  $("replays").hidden = !$("replays").hidden;
  $("show-replays").textContent = $("replays").hidden ? "Watch a recorded run" : "Hide recorded runs";
});

$("request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const note = $("form-note");
  note.textContent = "";
  let body;
  if (state.kind === "fix") {
    if (!state.sample) { note.textContent = "Pick a sample project first."; return; }
    body = { kind: "fix", sample: state.sample.name, prompt: state.sample.prompt };
  } else {
    const prompt = $("prompt").value.trim();
    if (!prompt) { note.textContent = "Write a request first, or pick an example."; $("prompt").focus(); return; }
    body = { kind: "create", prompt };
  }
  $("start").disabled = true;
  try {
    const response = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The run could not start.");
    watch(data.id, body.prompt);
  } catch (error) {
    note.textContent = typeof error.message === "string" ? error.message : "The run could not start.";
    $("start").disabled = false;
  }
});

async function startReplay(replay) {
  const response = await fetch(`/api/replays/${encodeURIComponent(replay.name)}`, { method: "POST" });
  if (!response.ok) { $("form-note").textContent = "That recording could not be loaded."; return; }
  const data = await response.json();
  watch(data.id, replay.task, true);
}

// ---------- watching a run ----------

function watch(runId, task, isReplay = false) {
  if (state.source) state.source.close();
  state.requirements = [];
  $("stream").replaceChildren();
  $("checklist").replaceChildren();
  $("checklist-empty").hidden = false;
  $("loops").replaceChildren();
  $("deliverable-panel").hidden = true;
  $("stats-panel").hidden = true;
  $("round-label").textContent = isReplay ? "Recorded run" : "";
  $("task-echo").textContent = task;
  $("line-section").hidden = false;
  $("workspace").hidden = false;
  setStations(-1);
  $("line-section").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });

  const source = new EventSource(`/api/runs/${runId}/events`);
  state.source = source;
  source.onmessage = (message) => handle(JSON.parse(message.data));
  source.addEventListener("end", () => { source.close(); $("start").disabled = false; });
  source.onerror = () => { if (source.readyState === EventSource.CLOSED) $("start").disabled = false; };
}

function setStations(activeIndex, allDone = false) {
  for (const station of document.querySelectorAll(".station")) {
    const index = ORDER.indexOf(station.dataset.role);
    station.dataset.state = allDone || index < activeIndex ? "done" : index === activeIndex ? "active" : "idle";
  }
}

function append(node) {
  const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 240;
  $("stream").append(node);
  if (nearBottom) node.scrollIntoView({ block: "end", behavior: "auto" });
}

function removeThinking() {
  for (const node of document.querySelectorAll(".stream .thinking")) node.remove();
}

function handle(event) {
  switch (event.type) {
    case "run_started": return onStarted(event);
    case "agent_started": return onAgentStarted(event);
    case "agent_step": return onStep(event);
    case "agent_submitted": return onSubmitted(event);
    case "agent_finished": return onAgentFinished(event);
    case "gate": return onGate(event);
    case "decision": return onDecision(event);
    case "phase": return onPhase(event);
    case "run_finished": return onFinished(event);
  }
}

function onStarted(event) {
  state.maxRounds = event.max_rounds || 3;
  state.runKind = event.kind || "fix";
  for (const role of event.roles || []) state.titles[role.name] = role.title;
  for (const name of document.querySelectorAll(".station-name")) {
    const role = name.closest(".station").dataset.role;
    name.textContent = title(role);
  }
}

function onAgentStarted(event) {
  removeThinking();
  state.bubble = null;
  setStations(ORDER.indexOf(event.role));
  if (event.round) $("round-label").textContent = `Round ${event.round} of ${state.maxRounds}`;
  const verb = { planner: "is reading the request", coder: event.round > 1 ? "is revising the work" : "is doing the work", tester: "is checking each item", reviewer: "is reviewing the result" }[event.role] || "is working";
  append(el("li", { class: `arrival role-${event.role}`, text: `${title(event.role)} ${verb}` }));
  append(thinking(event.role));
}

function message(role, ...content) {
  return el("li", { class: `msg role-${role}` },
    el("span", { class: "avatar", "aria-hidden": "true", text: title(role).charAt(0) }),
    el("div", { class: "bubble" }, el("p", { class: "who", text: title(role) }), ...content));
}

function thinking(role) {
  return el("li", { class: `thinking role-${role}` },
    el("span", { class: "dots", "aria-hidden": "true" }, el("span"), el("span"), el("span")),
    `${title(role)} is thinking`);
}

// Markdown from a model, sanitized. Falls back to plain text if the libraries did not load.
function notes(text) {
  const box = el("div", { class: "notes" });
  if (window.marked && window.DOMPurify) box.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text));
  else box.append(el("p", { class: "said", text }));
  if (text.length <= LONG_NOTES) return box;
  return el("details", { class: "long-notes" }, el("summary", { text: "Show the agent's notes" }), box);
}

// Consecutive steps by the same agent share one message: actions accumulate in its list.
function onStep(event) {
  const actions = (event.tools || []).map(describeAction);
  if (!event.text && !actions.length) return;
  removeThinking();
  if (!state.bubble || state.bubble.role !== event.role) {
    const list = el("ul", { class: "actions-list" });
    const body = el("div", { class: "bubble-body" }, list);
    append(message(event.role, body));
    state.bubble = { role: event.role, body, list };
  }
  if (event.text) state.bubble.body.insertBefore(notes(event.text), state.bubble.list);
  state.bubble.list.append(...actions);
  append(thinking(event.role));
}

function detailBlock(label, text) {
  return [el("p", { class: "pre-label", text: label }), el("pre", { text })];
}

function describeAction(tool) {
  const args = parseArgs(tool.arguments) || {};
  const output = tool.output || "";
  let summary, note = "", details = [];
  switch (tool.name) {
    case "list_files":
      summary = "Looked through the files"; details = detailBlock("Files", output); break;
    case "read_file":
      summary = `Read ${args.path || "a file"}`;
      if (output.startsWith("Error:")) note = output.replace("Error: ", "");
      details = detailBlock("Contents", output); break;
    case "write_file":
      summary = `Wrote ${args.path || "a file"}`;
      if (args.content) note = `${args.content.length.toLocaleString()} characters`;
      if (output.startsWith("Error:")) note = output.replace("Error: ", "");
      details = args.content ? detailBlock("New contents", args.content) : detailBlock("Result", output); break;
    case "run_tests": {
      const first = output.split("\n")[0];
      summary = first.startsWith("Tests PASSED") ? "Ran the tests: all passed" : first.startsWith("Tests FAILED") ? "Ran the tests: some failed" : "Ran the tests";
      details = detailBlock("Test output", output); break;
    }
    case "run_python":
      summary = "Ran a quick check";
      details = [...detailBlock("Code", args.code || tool.arguments), ...detailBlock("Printed", output.replace(/^exit code 0\n/, ""))]; break;
    case "show_diff":
      summary = "Looked at the changes"; details = detailBlock("Changes", output); break;
    default:
      summary = `Used ${tool.name}`; details = detailBlock("Result", output);
  }
  return el("li", { class: "action" },
    el("details", {}, el("summary", {}, el("span", { text: summary }), note ? el("span", { class: "action-note", text: note }) : null), ...details));
}

function onSubmitted(event) {
  removeThinking();
  state.bubble = null;
  const out = event.output || {};
  let card;
  if (event.role === "planner") {
    state.requirements = out.requirements || [];
    renderChecklist();
    const count = state.requirements.length;
    card = el("div", { class: "card" },
      el("h3", { text: event.fallback ? "No plan came back, so the team works from the request" : "The plan" }),
      out.summary ? el("p", { class: "said", text: out.summary }) : null,
      el("p", { class: "action-note", text: `${count} item${count === 1 ? "" : "s"} to get right, listed in the checklist.` }));
  } else if (event.role === "coder") {
    card = el("div", { class: "card" }, el("h3", { text: "Done, handing over" }),
      el("p", { class: "said", text: out.summary || "" }),
      out.files_changed && out.files_changed.length ? el("p", { class: "action-note", text: `Changed: ${out.files_changed.join(", ")}` }) : null);
  } else if (event.role === "tester") {
    const checks = out.checks || [];
    applyChecks(checks);
    const failed = checks.filter((c) => !c.passed).length;
    card = el("div", { class: "card" },
      el("h3", { text: failed ? `${failed} of ${checks.length} items failed` : `All ${checks.length} items passed` }),
      el("ul", {}, checks.map((c) => el("li", {}, el("strong", { text: `${c.passed ? "Passed" : "Failed"} ${c.requirement_id}: ` }), c.evidence))));
  } else if (event.role === "reviewer") {
    const approve = out.verdict === "approve";
    card = el("div", { class: "card" },
      el("h3", {}, el("span", { class: `verdict verdict-${out.verdict}`, text: approve ? "Approved" : "Sent back" })),
      out.issues && out.issues.length
        ? el("ul", {}, out.issues.map((i) => el("li", {}, el("strong", { text: i.severity === "blocker" ? "Must fix: " : "Suggestion: " }), i.description)))
        : el("p", { class: "said", text: "No issues found." }));
  }
  if (card) append(message(event.role, card));
}

function onAgentFinished(event) {
  removeThinking();
  state.bubble = null;
  if (event.status === "max_steps") {
    const after = { planner: "The team continues from your request alone.", tester: "The checklist was not verified this round.", reviewer: "The work is accepted because the checks passed." }[event.role] || "";
    append(el("li", { class: "system bad" }, el("strong", { text: `${title(event.role)} ran out of steps before reporting.` }), after));
  }
  if (event.status === "error") {
    append(el("li", { class: "system bad" }, el("strong", { text: `${title(event.role)} stopped.` }), friendlyError(event.error)));
  }
}

function onGate(event) {
  state.bubble = null;
  append(el("li", { class: `system ${event.passed ? "good" : "bad"}` },
    el("strong", { text: event.passed ? "The project's own tests pass." : "The project's own tests still fail." }),
    event.passed ? "On to the Tester." : `Back to the ${title("coder")} without asking the Tester.`));
}

function onDecision(event) {
  state.bubble = null;
  const decision = event.decision || "";
  if (decision.startsWith("revise")) {
    const reason = decision.replace(/^revise: /, "");
    const sender = reason.includes("reviewer") ? "reviewer" : reason.includes("tester") ? "tester" : "coder";
    $("loops").append(el("p", { class: `loop role-${sender}`, text: `Round ${event.round}: ${reason}` }));
    append(el("li", { class: "system bad" }, el("strong", { text: `Sent back to the ${title("coder")}.` }), capitalize(reason) + "."));
  } else if (decision.startsWith("accept")) {
    append(el("li", { class: "system good" }, el("strong", { text: "Accepted." }), capitalize(decision.replace(/^accept: /, "")) + "."));
  }
}

function onPhase(event) {
  if (event.phase === "budget_exhausted") {
    append(el("li", { class: "system bad" }, el("strong", { text: "Stopped to save tokens." }), "This run reached its token budget."));
  }
}

function onFinished(event) {
  removeThinking();
  const ok = event.status === "completed";
  setStations(ok ? -1 : 4, ok);
  const heading = { completed: "Finished and approved", unresolved: "Finished without approval", budget_exhausted: "Stopped at the token budget", error: "The run stopped" }[event.status] || "Finished";
  const detail = event.status === "error" ? friendlyError(event.error)
    : `${event.rounds} round${event.rounds === 1 ? "" : "s"}, ${(event.total_tokens || 0).toLocaleString()} tokens, ${Math.round(event.duration_s || 0)} seconds.`;
  append(el("li", { class: "finish" }, el("h3", { text: heading }), el("p", { text: detail })));
  renderDeliverables(event.files || {}, event.diff || "");
  renderStats(event);
  $("start").disabled = false;
}

function friendlyError(error) {
  const text = String(error || "");
  if (/api key/i.test(text)) return "No API key was found. Add one to the .env file and restart Relay.";
  if (/DailyLimitReached|per day/i.test(text)) return "The AI provider's daily limit is used up. Try again later, or watch a recorded run.";
  if (/RateLimit|429/.test(text)) return "The AI provider is limiting requests right now. Try again in a minute.";
  if (/503|high demand|unavailable/i.test(text)) return "The AI provider is overloaded right now. Try again in a few minutes.";
  return text.slice(0, 300) || "Something went wrong.";
}

const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

// ---------- result column ----------

function renderChecklist() {
  $("checklist").replaceChildren(...state.requirements.map((r) =>
    el("li", { class: "check", "data-id": r.id, "data-status": "pending" },
      el("span", { class: "mark", "aria-hidden": "true" }),
      el("span", {}, el("span", { class: "check-id", text: r.id }), r.description, el("span", { class: "check-evidence" })))));
  $("checklist-empty").hidden = state.requirements.length > 0;
}

function applyChecks(checks) {
  for (const check of checks) {
    const item = $("checklist").querySelector(`[data-id="${CSS.escape(check.requirement_id)}"]`);
    if (!item) continue;
    item.dataset.status = check.passed ? "pass" : "fail";
    item.querySelector(".mark").textContent = check.passed ? "✓" : "✕";
    item.querySelector(".mark").setAttribute("aria-label", check.passed ? "passed" : "failed");
    item.querySelector(".check-evidence").textContent = check.evidence || "";
  }
}

function renderDeliverables(files, diff) {
  const names = Object.keys(files);
  if (!names.length && !diff) return;
  const views = names.map((name) => ({ label: name, render: () => renderFile(name, files[name]) }));
  if (state.runKind === "fix" && diff && diff !== "(no changes)") {
    views.unshift({ label: "Changes", render: () => renderDiff(diff) });  // the fix itself first
  }
  const tabs = $("file-tabs");
  tabs.replaceChildren();
  views.forEach((view, index) => {
    const tab = el("button", { type: "button", class: "file-tab", role: "tab", "aria-selected": String(index === 0), text: view.label });
    tab.addEventListener("click", () => {
      for (const other of tabs.children) other.setAttribute("aria-selected", String(other === tab));
      $("file-view").replaceChildren(view.render());
    });
    tabs.append(tab);
  });
  $("file-view").replaceChildren(views[0].render());
  $("deliverable-panel").hidden = false;
}

function renderFile(name, content) {
  if (/\.html?$/i.test(name)) {
    const frame = el("iframe", { title: `Preview of ${name}`, sandbox: "allow-scripts" });
    frame.srcdoc = content;
    return frame;
  }
  if (/\.(md|markdown|txt)$/i.test(name) && window.marked && window.DOMPurify) {
    const div = el("div", { class: "rendered" });
    div.innerHTML = window.DOMPurify.sanitize(window.marked.parse(content));
    return div;
  }
  return el("pre", { text: content });
}

function renderDiff(diff) {
  return el("pre", { class: "diff" }, diff.split("\n").map((line) => {
    const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : null;
    return el("span", { class: cls }, line + "\n");
  }));
}

function renderStats(event) {
  const byRole = event.tokens_by_role || {};
  const total = Object.values(byRole).reduce((a, b) => a + b, 0);
  if (!total) return;
  $("token-bar").replaceChildren(...ORDER.filter((r) => byRole[r]).map((r) =>
    el("span", { class: `role-${r}`, style: `flex-grow: ${byRole[r]}`, title: `${title(r)}: ${byRole[r].toLocaleString()} tokens` })));
  $("token-bar").setAttribute("aria-label", ORDER.filter((r) => byRole[r]).map((r) => `${title(r)} ${byRole[r]} tokens`).join(", "));
  $("stats").replaceChildren(...ORDER.filter((r) => byRole[r]).flatMap((r) => [
    el("dt", { class: `role-${r}` }, el("span", { class: "legend-swatch" }), title(r)),
    el("dd", { text: `${byRole[r].toLocaleString()} tokens` })]),
    el("dt", { text: "Time" }), el("dd", { text: `${Math.round(event.duration_s || 0)} seconds` }));
  $("stats-panel").hidden = false;
}

init();
