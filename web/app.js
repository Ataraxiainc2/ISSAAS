"use strict";
/* Local control panel: reads JSON APIs, inserts all server text with
   textContent only (issue/model text is untrusted). */

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function link(url) {
  // Only link http(s) URLs; anything else renders as plain text.
  if (/^https?:\/\//i.test(url)) {
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    a.textContent = url;
    return a;
  }
  return el("span", url);
}

function kv(pairs) {
  const dl = document.createElement("dl");
  dl.className = "kv";
  for (const [k, v] of pairs) {
    dl.appendChild(el("dt", k));
    const dd = document.createElement("dd");
    if (v instanceof Node) dd.appendChild(v);
    else dd.textContent = v === null || v === undefined ? "" : String(v);
    dl.appendChild(dd);
  }
  return dl;
}

async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || ("request failed: " + res.status));
  return body;
}

async function postJSON(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || ("request failed: " + res.status));
  return body;
}

function setStatus(msg) {
  document.getElementById("status").textContent = msg || "";
}

function verdictBadge(verdict) {
  const span = el("span", verdict);
  span.className = "badge";
  if (verdict === "VERIFIED") span.classList.add("verified");
  else if (verdict === "ABSTAIN") span.classList.add("abstain");
  else if (verdict === "ERROR") span.classList.add("error");
  return span;
}

function fmtUSD(v) {
  if (v === null || v === undefined) return "unknown";
  return "$" + Number(v).toFixed(4);
}

/* -- tabs ------------------------------------------------------------ */

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    for (const sec of document.querySelectorAll("main > section")) sec.hidden = true;
    document.getElementById("tab-" + btn.dataset.tab).hidden = false;
  });
});

/* -- runs ------------------------------------------------------------ */

async function loadRuns() {
  try {
    const data = await getJSON("/api/overview");
    document.getElementById("budget").textContent =
      "Budget: spent " + fmtUSD(data.total_spend_usd) +
      " of $" + data.budget_total_usd +
      " (remaining " + fmtUSD(data.remaining_usd) + ")";
    const tbody = document.querySelector("#runs-table tbody");
    tbody.replaceChildren();
    for (const run of data.runs || []) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", run.run_id));
      const vtd = document.createElement("td");
      vtd.appendChild(verdictBadge(run.verdict));
      tr.appendChild(vtd);
      tr.appendChild(el("td", fmtUSD(run.cost_usd)));
      tr.appendChild(el("td", run.chain_ok === true ? "ok" : run.chain_ok === false ? "BROKEN" : "n/a"));
      tr.appendChild(el("td", run.n_events));
      tbody.appendChild(tr);
    }
    const detailSel = document.getElementById("detail-run");
    const keep = detailSel.value;
    detailSel.replaceChildren();
    for (const run of data.runs || []) {
      const opt = el("option", run.run_id);
      opt.value = run.run_id;
      detailSel.appendChild(opt);
    }
    if (keep) detailSel.value = keep;
    if (detailSel.value) loadRunDetail(detailSel.value);
  } catch (err) {
    setStatus("Runs: " + err.message);
  }
}

/* -- scout ----------------------------------------------------------- */

async function loadScoutList() {
  try {
    const data = await getJSON("/api/scout");
    const sel = document.getElementById("scout-report");
    sel.replaceChildren();
    for (const rep of data.reports || []) {
      const opt = el("option", rep.run_id + " (" + rep.n_candidates + " candidates)");
      opt.value = rep.run_id;
      sel.appendChild(opt);
    }
    if (sel.value) loadScoutReport(sel.value);
  } catch (err) {
    setStatus("Scout: " + err.message);
  }
}

async function loadScoutReport(runId) {
  const box = document.getElementById("scout-detail");
  box.replaceChildren();
  try {
    const doc = await getJSON("/api/scout/" + encodeURIComponent(runId));
    box.appendChild(kv([
      ["run", doc.run_id],
      ["candidates", (doc.candidates || []).length],
      ["rate limited", doc.rate_limited ? "yes" : "no"],
    ]));
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const h of ["Issue", "Stars", "Runner", "Security", "Score"]) head.appendChild(el("th", h));
    table.appendChild(head);
    for (const c of doc.candidates || []) {
      const tr = document.createElement("tr");
      const issue = document.createElement("td");
      issue.appendChild(link(c.issue_url || ""));
      issue.appendChild(el("div", c.title || ""));
      tr.appendChild(issue);
      tr.appendChild(el("td", c.stars));
      tr.appendChild(el("td", c.runner));
      tr.appendChild(el("td", c.security_class || "—"));
      tr.appendChild(el("td", c.score));
      table.appendChild(tr);
      const sigRow = document.createElement("tr");
      const sigCell = document.createElement("td");
      sigCell.colSpan = 5;
      sigCell.appendChild(el("small", "signals: " + JSON.stringify(c.signals || {})));
      sigRow.appendChild(sigCell);
      table.appendChild(sigRow);
    }
    box.appendChild(table);
  } catch (err) {
    box.appendChild(el("p", "Scout: " + err.message));
  }
}

/* -- triage ---------------------------------------------------------- */

async function loadTriageList() {
  try {
    const data = await getJSON("/api/triage");
    const sel = document.getElementById("triage-report");
    sel.replaceChildren();
    for (const rep of data.reports || []) {
      const opt = el("option", rep.run_id + " (" + rep.n_scored + " scored)");
      opt.value = rep.run_id;
      sel.appendChild(opt);
    }
    if (sel.value) loadTriageReport(sel.value);
  } catch (err) {
    setStatus("Triage: " + err.message);
  }
}

async function loadTriageReport(runId) {
  const box = document.getElementById("triage-detail");
  box.replaceChildren();
  try {
    const doc = await getJSON("/api/triage/" + encodeURIComponent(runId));
    box.appendChild(kv([
      ["run", doc.run_id],
      ["scout report", doc.scout_run_id],
      ["picked", (doc.selection && doc.selection.picked && doc.selection.picked.issue_url) || "none"],
    ]));
    for (const item of doc.scored || []) {
      const card = document.createElement("div");
      card.className = "card";
      const title = el("h3");
      title.appendChild(link(item.issue_url || ""));
      card.appendChild(title);
      const critic = item.critic || {};
      card.appendChild(kv([
        ["deterministic signals score", item.signals_score],
        ["Spark solvability", item.solvability + " (" + item.triage_model + " via " + item.triage_provider + ")"],
        ["critic adjustment", (critic.solvability_v2 ?? "—") + " delta " + (critic.delta ?? "—") +
          " flag " + (critic.flag || item.critic_flag || "—") +
          " (" + item.critic_model + " via " + item.critic_provider + ")"],
        ["final score", item.final_score],
        ["is_real_bug", item.is_real_bug],
        ["rationale", item.rationale],
        ["critic rationale", critic.rationale_v2 || "—"],
        ["triage reasoning", item.triage_reasoning_excerpt || "—"],
        ["critic reasoning", item.critic_reasoning_excerpt || "—"],
      ]));
      box.appendChild(card);
    }
  } catch (err) {
    box.appendChild(el("p", "Triage: " + err.message));
  }
}

/* -- run detail: decision, work/verification, result ------------------ */

async function loadRunDetail(runId) {
  const box = document.getElementById("run-detail");
  box.replaceChildren();
  try {
    const d = await getJSON("/api/runs/" + encodeURIComponent(runId));
    const head = el("h3");
    head.appendChild(el("span", "Verdict: "));
    head.appendChild(verdictBadge(d.verdict));
    box.appendChild(head);
    box.appendChild(kv([
      ["run", d.run_id],
      ["cost", fmtUSD(d.cost_usd)],
      ["chain", d.chain.ok ? "ok" : ("BROKEN line " + d.chain.bad_line + ": " + d.chain.reason)],
    ]));

    box.appendChild(el("h3", "Decision"));
    const sel = d.decision && d.decision.selected;
    if (sel) {
      box.appendChild(kv([
        ["picked", link(sel.issue_id || "")],
        ["why", sel.reason],
        ["solver model / effort", (sel.model || "") + " / " + (sel.effort || "")],
        ["model rationale", sel.model_rationale],
        ["final score", sel.final_score],
      ]));
    } else {
      box.appendChild(el("p", "No pick recorded."));
    }
    for (const s of (d.decision && d.decision.skipped) || []) {
      box.appendChild(kv([["skipped " + (s.issue_id || ""), s.reason]]));
    }

    box.appendChild(el("h3", "Work and verification"));
    const work = d.work || {};
    const repro = work.repro_result || {};
    const fix = work.fix_attempt || {};
    const verify = work.verify_verdict || {};
    box.appendChild(kv([
      ["repro test", repro.test_path || "—"],
      ["failed on base", repro.failed_on_base],
      ["failure reason", repro.failure_reason || "—"],
      ["fix effort", fix.effort || "—"],
      ["patch hash", fix.patch_hash || "—"],
      ["fix explanation", fix.explanation || "—"],
      ["Fail-to-Pass", verify.f2p_pass === true ? "FAIL on base → PASS with fix" :
        verify.f2p_pass === false ? "NOT demonstrated" : "—"],
      ["regressions", verify.regressions],
    ]));
    const calls = document.createElement("table");
    const chead = document.createElement("tr");
    for (const h of ["Stage", "Model", "Tokens in/out", "Cost"]) chead.appendChild(el("th", h));
    calls.appendChild(chead);
    for (const c of work.model_calls || []) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", c.stage));
      tr.appendChild(el("td", (c.model || "") + " via " + (c.provider || "")));
      tr.appendChild(el("td", c.in_tokens + "/" + c.out_tokens));
      tr.appendChild(el("td", fmtUSD(c.cost_usd)));
      calls.appendChild(tr);
    }
    box.appendChild(calls);

    box.appendChild(el("h3", "Result"));
    const proof = d.proof || {};
    box.appendChild(kv([
      ["verdict", d.verdict],
      ["bundle files", (proof.files || []).join(", ")],
    ]));
    if (proof.verdict) box.appendChild(kv([["verdict.json", JSON.stringify(proof.verdict)]]));
    if (proof.cost) box.appendChild(kv([["cost.json", JSON.stringify(proof.cost)]]));
    if (proof.fix_patch) {
      box.appendChild(el("h4", "fix.patch"));
      box.appendChild(el("pre", proof.fix_patch));
    }
  } catch (err) {
    box.appendChild(el("p", "Run detail: " + err.message));
  }
}

/* -- actions --------------------------------------------------------- */

let pollTimer = null;

function showJob(job) {
  document.getElementById("job-status").textContent =
    "Job " + job.job_id + " (" + job.kind + "): " + job.status +
    (job.error ? " — " + job.error : "");
  document.getElementById("job-log").textContent = job.log || "";
  if (job.result) {
    document.getElementById("job-log").textContent =
      (job.log ? job.log + "\n" : "") + "result: " + JSON.stringify(job.result);
  }
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await getJSON("/api/jobs/" + encodeURIComponent(jobId));
      showJob(job);
      if (job.status === "done" || job.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
        loadRuns();
      }
    } catch (err) {
      document.getElementById("job-status").textContent = "Job poll: " + err.message;
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 1000);
}

function formValues(form, names) {
  const data = new FormData(form);
  const out = {};
  for (const name of names) {
    const raw = (data.get(name) || "").toString().trim();
    if (raw !== "") out[name] = raw;
  }
  return out;
}

function wireEstimate(formId, estimateKind, actionKind, pickParams) {
  const form = document.getElementById(formId);
  const estimateBox = form.querySelector(".estimate");
  const confirmBtn = form.querySelector(".confirm");
  let pending = null;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    confirmBtn.hidden = true;
    pending = null;
    try {
      const params = pickParams(form);
      const reply = await postJSON("/api/estimate/" + estimateKind, params);
      pending = { params, token: reply.confirm_token };
      estimateBox.replaceChildren();
      estimateBox.appendChild(el("p", "Estimate: " + JSON.stringify(reply, null, 1)));
      confirmBtn.hidden = false;
      setStatus("");
    } catch (err) {
      estimateBox.replaceChildren();
      setStatus(formId + " estimate: " + err.message);
    }
  });
  confirmBtn.addEventListener("click", async () => {
    if (!pending) return;
    try {
      const job = await postJSON("/api/actions/" + actionKind,
        Object.assign({}, pending.params, { confirm_token: pending.token }));
      pending = null;
      confirmBtn.hidden = true;
      showJob({ job_id: job.job_id, kind: job.kind, status: job.status, log: "", error: "" });
      pollJob(job.job_id);
    } catch (err) {
      setStatus(formId + " action: " + err.message);
    }
  });
}

function wireDirect(formId, actionKind, pickParams) {
  const form = document.getElementById(formId);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const job = await postJSON("/api/actions/" + actionKind, pickParams(form));
      showJob({ job_id: job.job_id, kind: job.kind, status: job.status, log: "", error: "" });
      pollJob(job.job_id);
      setStatus("");
    } catch (err) {
      setStatus(formId + " action: " + err.message);
    }
  });
}

function numOrOmit(out, key, raw) {
  if (raw === undefined || raw === "") return;
  const n = Number(raw);
  if (Number.isFinite(n)) out[key] = n;
}

document.getElementById("scout-report").addEventListener("change", (e) => loadScoutReport(e.target.value));
document.getElementById("triage-report").addEventListener("change", (e) => loadTriageReport(e.target.value));
document.getElementById("detail-run").addEventListener("change", (e) => loadRunDetail(e.target.value));

wireDirect("form-scout", "scout", (form) => {
  const v = formValues(form, ["limit", "languages"]);
  const out = {};
  numOrOmit(out, "limit", v.limit);
  if (v.languages) out.languages = v.languages;
  return out;
});

wireEstimate("form-triage", "triage", "triage", (form) => {
  const v = formValues(form, ["scout_run_id", "run_id"]);
  return { scout_run_id: v.scout_run_id, ...(v.run_id ? { run_id: v.run_id } : {}) };
});

wireEstimate("form-solve", "solve", "solve", (form) => {
  const v = formValues(form, ["issue_url", "model", "effort", "max_steps", "max_usd", "run_id"]);
  const out = { issue_url: v.issue_url, model: v.model || "muse-spark-1.3-contributor", effort: v.effort || "medium" };
  numOrOmit(out, "max_steps", v.max_steps);
  if (v.max_usd !== undefined && v.max_usd !== "") {
    const n = Number(v.max_usd);
    if (Number.isFinite(n)) out.max_usd = n;
  }
  if (v.run_id) out.run_id = v.run_id;
  return out;
});

wireDirect("form-recover", "recover", (form) => formValues(form, ["run_id"]));
wireDirect("form-reverify", "reverify", (form) => formValues(form, ["run_id"]));

wireEstimate("form-run", "run", "run", (form) => {
  const v = formValues(form, ["languages", "limit", "max_usd"]);
  const out = { languages: v.languages || "python" };
  numOrOmit(out, "limit", v.limit);
  if (v.limit === undefined) out.limit = 30;
  numOrOmit(out, "max_usd", v.max_usd);
  if (out.max_usd === undefined) out.max_usd = 0.5;
  return out;
});

loadRuns();
loadScoutList();
loadTriageList();
