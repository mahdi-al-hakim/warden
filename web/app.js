"use strict";

// Warden demo UI. Plain JavaScript, no build step. It starts a run, streams the events over SSE,
// and lets the operator approve a held step or pull the kill switch. All state comes from the server.

const api = {
  config: () => fetch("/api/config").then((r) => r.json()),
  benchmark: () => fetch("/api/benchmark").then((r) => r.json()),
  evaluation: () => fetch("/api/eval").then((r) => r.json()),
  start: () =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    }).then((r) => r.json()),
  approve: (runId, actionId, approved) =>
    fetch(`/api/sessions/${runId}/approve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action_id: actionId, approved, approver: "operator (you)" }),
    }).then((r) => r.json()),
  revoke: (runId) =>
    fetch(`/api/sessions/${runId}/revoke`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ actor: "operator (you)" }),
    }).then((r) => r.json()),
};

const el = (id) => document.getElementById(id);
const dom = {
  modeChip: el("mode-chip"),
  repoChip: el("repo-chip"),
  rulesetChip: el("ruleset-chip"),
  taskText: el("task-text"),
  timeline: el("timeline"),
  activityEmpty: el("activity-empty"),
  runStatus: el("run-status"),
  startBtn: el("start-btn"),
  revokeBtn: el("revoke-btn"),
  killNote: el("kill-note"),
  approvalCard: el("approval-card"),
  approvalDetail: el("approval-detail"),
  approveBtn: el("approve-btn"),
  denyBtn: el("deny-btn"),
  resourceList: el("resource-list"),
  evalBtn: el("eval-btn"),
  evalSummary: el("eval-summary"),
  bars: el("bars"),
  benchNote: el("bench-note"),
  factSource: el("fact-source"),
  footModel: el("foot-model"),
};

const RESOURCES = [
  { id: "crm", name: "CRM access", detail: "directory grant" },
  { id: "analytics", name: "Analytics access", detail: "directory grant" },
  { id: "wiki", name: "Wiki access", detail: "directory grant" },
  { id: "vpn", name: "VPN", detail: "network access" },
  { id: "repo", name: "payments-api", detail: "repository member" },
  { id: "account", name: "Directory account", detail: "jordan.lee" },
  { id: "breakglass", name: "Break-glass admin", detail: "security.breakglass" },
];

const state = {
  runId: null,
  source: null,
  stream: null,
  steps: new Map(),
  pendingActionId: null,
  finished: false,
};

function fmtArgs(args) {
  if (!args) return "";
  return Object.entries(args)
    .filter(([k]) => k !== "account_tier")
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
}

function callLine(tool, operation, args) {
  const a = fmtArgs(args);
  return `${tool}.${operation}` + (a ? ` · ${a}` : "");
}

function setStatus(stateName, label) {
  dom.runStatus.dataset.state = stateName;
  dom.runStatus.textContent = label;
}

// Resources ---------------------------------------------------------------

function resetResources() {
  dom.resourceList.innerHTML = "";
  for (const r of RESOURCES) {
    const li = document.createElement("li");
    li.className = "resource";
    const protectedOne = r.id === "breakglass";
    li.innerHTML = `
      <span class="resource-name">${r.name}<small>${r.detail}</small></span>
      <span class="state ${protectedOne ? "protected" : "active"}" id="res-${r.id}">
        ${protectedOne ? "Protected" : "Active"}
      </span>`;
    dom.resourceList.appendChild(li);
  }
}

function markResource(id, label, cls) {
  const node = el(`res-${id}`);
  if (!node) return;
  node.className = `state ${cls} flash`;
  node.textContent = label;
  setTimeout(() => node.classList.remove("flash"), 500);
}

function applyEffect(step) {
  const { operation, args, outcome } = step;
  if (outcome === "executed") {
    if (operation === "revoke_access" && args.app === "crm") markResource("crm", "Revoked", "revoked");
    if (operation === "revoke_access" && args.app === "analytics")
      markResource("analytics", "Revoked", "revoked");
    if (operation === "revoke_access" && args.app === "wiki") markResource("wiki", "Revoked", "revoked");
    if (operation === "disable" && step.tool === "vpn") markResource("vpn", "Off", "revoked");
    if (operation === "remove_member") markResource("repo", "Removed", "revoked");
    if (operation === "disable_account" && args.user_id === "jordan.lee")
      markResource("account", "Disabled", "revoked");
  }
  if (outcome === "denied" && operation === "disable_account" && args.user_id === "security.breakglass") {
    markResource("breakglass", "Blocked", "protected");
  }
}

// Timeline ----------------------------------------------------------------

function upsertStep(actionId) {
  let step = state.steps.get(actionId);
  if (step) return step;
  const li = document.createElement("li");
  li.className = "step";
  li.innerHTML = `
    <div class="step-top"><span class="step-msg"></span><span class="tags"></span></div>
    <div class="step-call"></div>
    <div class="step-reason" hidden></div>`;
  dom.timeline.appendChild(li);
  step = { el: li, args: {}, tool: "", operation: "" };
  state.steps.set(actionId, step);
  dom.timeline.scrollTop = dom.timeline.scrollHeight;
  return step;
}

function addTag(step, cls, text) {
  const span = document.createElement("span");
  span.className = `tag ${cls}`;
  span.textContent = text;
  step.el.querySelector(".tags").appendChild(span);
}

function onProposed(d) {
  const step = upsertStep(d.action_id);
  step.tool = d.tool;
  step.operation = d.operation;
  step.args = d.arguments || {};
  step.el.querySelector(".step-msg").textContent = d.summary || `${d.tool}.${d.operation}`;
  step.el.querySelector(".step-call").textContent = callLine(d.tool, d.operation, d.arguments);
  addTag(step, `tag-risk-${(d.risk || "low").toLowerCase()}`, (d.risk || "low").toLowerCase());
}

function onDecision(d) {
  const step = upsertStep(d.action_id);
  const map = { allow: "tag-allow", deny: "tag-deny", require_approval: "tag-approval" };
  const labels = { allow: "allowed", deny: "denied", require_approval: "needs approval" };
  addTag(step, map[d.decision] || "tag-allow", labels[d.decision] || d.decision);
  if (d.decision === "allow") step.el.dataset.decision = "allow";
  if (d.decision === "deny") step.el.dataset.decision = "deny";
  if (d.decision === "require_approval") step.el.dataset.decision = "approval";
  if (d.reason) {
    const reason = step.el.querySelector(".step-reason");
    reason.hidden = false;
    reason.textContent = d.reason;
  }
}

function onExecuted(d) {
  const step = upsertStep(d.action_id);
  if (d.ok) addTag(step, "tag-done", "done");
  else addTag(step, "tag-failed", "tool failed");
  applyEffect({ ...step, outcome: "executed" });
  clearApprovalIf(d.action_id);
}

const BLOCK_LABELS = {
  denied: "blocked",
  timed_out: "timed out",
  blocked_revoked: "stopped: revoked",
};

function onBlocked(d) {
  const step = upsertStep(d.action_id);
  step.el.dataset.decision = "blocked";
  addTag(step, "tag-blocked", BLOCK_LABELS[d.outcome] || "blocked");
  if (d.reason) {
    const reason = step.el.querySelector(".step-reason");
    reason.hidden = false;
    reason.textContent = d.reason;
  }
  applyEffect({ ...step, outcome: d.outcome });
  clearApprovalIf(d.action_id);
}

// Approval ----------------------------------------------------------------

function showApproval(d) {
  state.pendingActionId = d.action_id;
  // Build with textContent, never innerHTML: the summary and arguments are model-controlled in live
  // mode, so injecting them as HTML would let a steered agent script the operator's own console.
  dom.approvalDetail.textContent = "";
  const summary = document.createElement("div");
  summary.textContent = d.summary || "A high-risk step needs a human.";
  const call = document.createElement("div");
  call.className = "step-call";
  call.textContent = callLine(d.tool, d.operation, d.arguments);
  dom.approvalDetail.append(summary, call);
  dom.approvalCard.hidden = false;
  setStatus("waiting", "Waiting for approval");
}

function clearApprovalIf(actionId) {
  if (state.pendingActionId === actionId) {
    state.pendingActionId = null;
    dom.approvalCard.hidden = true;
  }
}

// Stream ------------------------------------------------------------------

function resetRun() {
  if (state.stream) state.stream.close();
  state.steps.clear();
  state.pendingActionId = null;
  state.finished = false;
  dom.timeline.innerHTML = "";
  dom.approvalCard.hidden = true;
  dom.killNote.hidden = true;
  dom.activityEmpty.hidden = true;
  resetResources();
}

function handleEvent(event) {
  if (state.finished) return;
  const d = event.data || {};
  switch (event.type) {
    case "session.started":
      dom.factSource.textContent = d.source || "synthetic";
      setStatus("running", "Running");
      break;
    case "action.proposed": onProposed(d); break;
    case "policy.decision": onDecision(d); break;
    case "approval.requested": showApproval(d); break;
    // The commit marker records, for the audit and the reliability invariant, the moment an action
    // passed the final revocation check. The visible outcome is the executed step, so nothing to do.
    case "action.committed": break;
    case "action.executed": onExecuted(d); break;
    case "action.blocked": onBlocked(d); break;
    case "revocation.triggered":
      dom.killNote.hidden = false;
      // A revoke makes any pending approval moot: the re-check will block the action, so take the
      // approve and deny controls away rather than leave them clickable on a revoked run.
      state.pendingActionId = null;
      dom.approvalCard.hidden = true;
      break;
    case "session.completed":
      finishRun(d.revoked ? "revoked" : "completed", d.revoked ? "Revoked" : "Completed");
      break;
    case "error":
      finishRun("revoked", "Error");
      break;
    default: break;
  }
}

function finishRun(stateName, label) {
  state.finished = true;
  setStatus(stateName, label);
  dom.startBtn.disabled = false;
  dom.revokeBtn.disabled = true;
  dom.approvalCard.hidden = true;
  if (state.stream) state.stream.close();
}

async function startRun() {
  resetRun();
  dom.startBtn.disabled = true;
  dom.revokeBtn.disabled = false;
  setStatus("running", "Starting");
  const res = await api.start();
  state.runId = res.run_id;
  state.source = res.source;
  dom.factSource.textContent = res.source;
  state.stream = new EventSource(`/api/sessions/${res.run_id}/events`);
  state.stream.onmessage = (e) => handleEvent(JSON.parse(e.data));
  state.stream.onerror = () => {
    // The stream closes when the run ends; only treat it as an error while still running.
    if (!state.finished) setStatus("running", "Running");
  };
}

async function revokeRun() {
  if (!state.runId) return;
  dom.revokeBtn.disabled = true;
  await api.revoke(state.runId);
}

async function resolveApproval(approved) {
  if (!state.runId || !state.pendingActionId) return;
  const actionId = state.pendingActionId;
  dom.approvalCard.hidden = true;
  await api.approve(state.runId, actionId, approved);
}

// Reliability -------------------------------------------------------------

function renderBars(bench) {
  const ttl = bench.cache_ttl_seconds || 15;
  dom.bars.textContent = "";
  for (const row of bench.rows) {
    const pct = Math.max(1.5, Math.min(100, (row.median_seconds / ttl) * 100));
    const cls = row.median_seconds <= 0.5 ? "good" : row.median_seconds >= 3 ? "bad" : "mid";
    const div = document.createElement("div");
    div.className = "bar-row";

    const label = document.createElement("div");
    label.className = "bar-label";
    const name = document.createElement("b");
    name.textContent = row.label;
    const nums = document.createElement("span");
    nums.textContent =
      `${row.median_seconds.toFixed(2)}s median · ${row.max_seconds.toFixed(2)}s worst`;
    label.append(name, nums);

    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = `bar-fill ${cls}`;
    fill.style.width = `${pct}%`;
    track.appendChild(fill);

    div.append(label, track);
    dom.bars.appendChild(div);
  }
  const points = bench.rows[0]?.trials || 25;
  dom.benchNote.textContent =
    `Seconds from revoke to the first denied action, over ${points} revoke times with a ${ttl}s ` +
    `cached credential, on a deterministic clock. The gateway checks live. The CAEP signal ` +
    `invalidates the cached grant on revoke.`;
}

async function runEval() {
  dom.evalBtn.disabled = true;
  dom.evalSummary.textContent = "Running scenarios and the benchmark...";
  try {
    const report = await api.evaluation();
    dom.evalSummary.textContent =
      `${report.scenarios_passed}/${report.scenarios_total} scenarios passed, ` +
      `${report.invariants_passed}/${report.invariants_total} invariants held.`;
    renderBars(report.benchmark);
  } finally {
    dom.evalBtn.disabled = false;
  }
}

// Command-first controls and the live clock ------------------------------

function clickable(btn) {
  return btn && !btn.disabled && !btn.hidden && !btn.closest("[hidden]");
}

function handleKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea") return;
  const key = e.key.toLowerCase();
  const map = {
    r: dom.startBtn,
    k: dom.revokeBtn,
    a: dom.approveBtn,
    d: dom.denyBtn,
    e: dom.evalBtn,
  };
  const btn = map[key];
  if (btn && clickable(btn)) {
    e.preventDefault();
    btn.click();
  }
}

function startClock() {
  const clock = el("clock");
  if (!clock) return;
  const tick = () => {
    const now = new Date();
    const hh = String(now.getUTCHours()).padStart(2, "0");
    const mm = String(now.getUTCMinutes()).padStart(2, "0");
    const ss = String(now.getUTCSeconds()).padStart(2, "0");
    clock.textContent = `${hh}:${mm}:${ss}Z`;
  };
  tick();
  setInterval(tick, 1000);
}

// Boot --------------------------------------------------------------------

function renderRepoBackend(backend) {
  if (!backend || backend.backend !== "github") return;
  // A real GitHub org is driving the repository tool, so say so and show the write state.
  const mode = backend.writes_enabled ? "live writes" : "dry run";
  dom.repoChip.textContent = `GitHub: ${backend.org} · ${mode}`;
  dom.repoChip.classList.add(backend.writes_enabled ? "live" : "chip-quiet");
  dom.repoChip.hidden = false;
}

async function boot() {
  resetResources();
  dom.startBtn.addEventListener("click", startRun);
  dom.revokeBtn.addEventListener("click", revokeRun);
  dom.approveBtn.addEventListener("click", () => resolveApproval(true));
  dom.denyBtn.addEventListener("click", () => resolveApproval(false));
  dom.evalBtn.addEventListener("click", runEval);
  document.addEventListener("keydown", handleKey);
  startClock();

  const [config, bench] = await Promise.all([api.config(), api.benchmark()]);
  dom.taskText.textContent = config.task;
  dom.rulesetChip.textContent = `ruleset ${config.ruleset_version}`;
  renderRepoBackend(config.repo_backend);
  if (config.live_ready) {
    dom.modeChip.textContent = "live: " + (config.model || "claude");
    dom.modeChip.classList.add("live");
    dom.footModel.textContent = "Live model: " + config.model;
  } else {
    dom.modeChip.textContent = "replay: synthetic trajectory";
    dom.footModel.textContent = "Replay mode. Set a key and WARDEN_MODE=live to drive a real model.";
  }
  renderBars(bench);
}

boot();
