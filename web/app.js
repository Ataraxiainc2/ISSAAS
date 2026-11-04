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

function isMissing(v) {
  if (v === null || v === undefined) return true;
  if (typeof v === "string" && v.trim() === "") return true;
  return false;
}

function kv(pairs) {
  const dl = document.createElement("dl");
  dl.className = "kv";
  for (const [k, v] of pairs) {
    if (isMissing(v)) continue;
    dl.appendChild(el("dt", k));
    const dd = document.createElement("dd");
    if (v instanceof Node) dd.appendChild(v);
    else dd.textContent = String(v);
    dl.appendChild(dd);
  }
  return dl;
}

function chip(text, cls) {
  const s = el("span", text);
  s.className = cls ? "chip " + cls : "chip";
  return s;
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

/* Brief language: English default, Spanish/Portuguese on request. */
const BRIEF_LANGS = ["en", "es", "pt"];

function getBriefLang() {
  try {
    const v = window.localStorage.getItem("issa-lang");
    if (BRIEF_LANGS.includes(v)) return v;
  } catch (err) { /* storage unavailable: fall back to English */ }
  return "en";
}

function setBriefLang(lang) {
  if (!BRIEF_LANGS.includes(lang)) lang = "en";
  try {
    window.localStorage.setItem("issa-lang", lang);
  } catch (err) { /* storage unavailable: session-only */ }
  document.querySelectorAll(".langswitch button").forEach((b) => {
    b.classList.toggle("active", b.dataset.lang === lang);
  });
}

function briefText(entry, field, lang) {
  if (!entry) return "";
  const map = entry[field];
  if (!map || typeof map !== "object") return "";
  return map[lang] || map.en || "";
}

/* Plain-language brief in the chosen language; all text via textContent. */
function renderBrief(entry, originalTitle, showTitle) {
  const lang = getBriefLang();
  const wrap = document.createElement("div");
  wrap.className = "brief";
  if (showTitle) {
    const t = briefText(entry, "title", lang);
    if (t) {
      const h = el("p", t);
      h.className = "brief-title";
      wrap.appendChild(h);
    }
  }
  const p = briefText(entry, "problem", lang);
  if (p) {
    const node = el("p", p);
    node.className = "brief-text";
    wrap.appendChild(node);
  }
  const f = briefText(entry, "fix_idea", lang);
  if (f) {
    const node = el("p", "Fix idea: " + f);
    node.className = "brief-text";
    wrap.appendChild(node);
  }
  if (originalTitle) {
    const code = entry && entry.source_language ? String(entry.source_language) : "unknown";
    const o = el("p", "Original (" + code + "): " + originalTitle);
    o.className = "meta-line original";
    wrap.appendChild(o);
  }
  return wrap;
}

function initBriefLang() {
  setBriefLang(getBriefLang());
  document.querySelectorAll(".langswitch button").forEach((b) => {
    b.addEventListener("click", () => {
      setBriefLang(b.dataset.lang);
      const scoutSel = document.getElementById("scout-report");
      if (scoutSel && scoutSel.value) loadScoutReport(scoutSel.value);
      const triageSel = document.getElementById("triage-report");
      if (triageSel && triageSel.value) loadTriageReport(triageSel.value);
      renderNowLast();
    });
  });
}

/* Live "Now working on" card: stage, selected brief, solver, verdict. */
const LIVE_FALLBACK_STAGE = {
  scout: "scouting",
  brief: "briefing",
  triage: "triaging",
  solve: "solving",
  run: "scouting",
  recover: "solving",
  reverify: "verifying",
};

let lastNowJob = null;
let lastNowLive = null;

function renderNowLast() {
  if (lastNowJob) renderNow(lastNowJob, lastNowLive);
}

function renderNow(job, live) {
  lastNowJob = job;
  lastNowLive = live || null;
  const card = document.getElementById("now-card");
  if (!card) return;
  card.hidden = false;
  const pill = document.getElementById("now-pill");
  pill.replaceChildren();
  pill.appendChild(el("span", job.status || "idle"));
  pill.className = "status-pill " + (
    job.status === "running" || job.status === "queued" ? "running"
    : job.status === "done" ? "done"
    : job.status === "error" ? "error" : "");
  document.getElementById("now-stage").textContent =
    ((live && live.stage) || LIVE_FALLBACK_STAGE[job.kind] || job.kind || "");
  const body = document.getElementById("now-body");
  body.replaceChildren();
  const entry = live && live.brief;
  const url = live && live.issue_url;
  if (entry) {
    body.appendChild(renderBrief(entry, "", true));
  } else if (job.status === "running" || job.status === "queued") {
    const p = el("p", "Working on " + (job.kind || "job") + ": waiting for the first trace events.");
    p.className = "meta-line";
    body.appendChild(p);
  }
  if (url) {
    const p = el("p");
    p.className = "meta-line";
    p.appendChild(link(url));
    body.appendChild(p);
  }
  const modelLine = document.getElementById("now-model");
  modelLine.textContent = (live && (live.model || live.effort))
    ? ("solver " + (live.model || "-") + " / effort " + (live.effort || "-")) : "";
  const verdictLine = document.getElementById("now-verdict");
  const verdict = (live && live.verdict) || (job.result && job.result.verdict) || "";
  if (verdict) {
    verdictLine.replaceChildren();
    verdictLine.appendChild(el("span", "verdict: "));
    verdictLine.appendChild(verdictBadge(verdict, false));
  } else if (job.status === "error") {
    verdictLine.textContent = "failed: " + (job.error || "unknown error");
  } else {
    verdictLine.textContent = "";
  }
}

async function refreshLive(job) {
  const runId = job.run_id || (job.result && job.result.run_id);
  let live = null;
  if (runId) {
    try {
      live = await getJSON("/api/live/" + encodeURIComponent(runId));
    } catch (err) { live = null; }
  }
  try {
    renderNow(job, live);
  } catch (err) { /* never break job polling for the live card */ }
}

function setStatus(msg) {
  document.getElementById("status").textContent = msg || "";
}

function verdictBadge(verdict, hero) {
  const span = el("span", verdict);
  span.className = hero ? "badge hero" : "badge";
  if (verdict === "VERIFIED") span.classList.add("verified");
  else if (verdict === "ABSTAIN") span.classList.add("abstain");
  else if (verdict === "ERROR") span.classList.add("error");
  return span;
}

function fmtUSD(v) {
  if (v === null || v === undefined || v === "") return "unknown";
  const n = Number(v);
  if (!Number.isFinite(n)) return "unknown";
  if (n === 0) return "$0";
  if (Math.abs(n) < 0.01) return "$" + n.toFixed(4);
  return "$" + n.toFixed(2);
}

function fmtSpent(v) {
  return fmtUSD(v);
}

/* Scores and signal values always show 2 decimals. */
function fmtScore(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

/* Token counts: thousands separators, "54.3k" from 10k, "4.65M" from 1M. */
function fmtTokens(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1000000) return (n / 1000000).toFixed(2) + "M";
  if (a >= 10000) return (n / 1000).toFixed(1) + "k";
  return Math.round(n).toLocaleString("en-US");
}

/* Cost with the optional "incl. reasoning" correction chip: the corrected
   figure first, the logged ledger value as muted secondary text. */
function costNode(d) {
  const wrap = el("span");
  wrap.appendChild(el("span", fmtUSD(d.cost_usd)));
  if (d.cost_corrected) {
    const tag = el("span", "incl. reasoning");
    tag.className = "chip reason";
    if (d.cost_basis) tag.title = String(d.cost_basis);
    wrap.appendChild(document.createTextNode(" "));
    wrap.appendChild(tag);
    wrap.appendChild(document.createTextNode(" "));
    const logged = el("span", "logged " + fmtUSD(d.cost_logged_usd));
    logged.className = "muted";
    wrap.appendChild(logged);
  }
  return wrap;
}

/* Parse "<date>-<time>-<owner>-<repo...>-<n>" into a readable name.
   Falls back to the raw run id when the shape does not match. */
function parseRunName(runId) {
  const m = /^(\d{8})-(\d{6})-(.+)-(\d+)$/.exec(runId || "");
  if (!m) return { name: runId, date: "" };
  const rest = m[3].split("-");
  if (rest.length < 2) return { name: runId, date: "" };
  const owner = rest[0];
  const repo = rest.slice(1).join("-");
  const num = m[4];
  const ds = m[1];
  const date = ds.slice(0, 4) + "-" + ds.slice(4, 6) + "-" + ds.slice(6, 8);
  return { name: owner + "/" + repo + " #" + num, date };
}

/* -- tabs + hash routing -------------------------------------------------
   #runs, #scout/<id>, #triage/<id>, #detail/<run_id>, #actions.
   The select boxes and the hash stay in sync. */

const TABS = ["runs", "scout", "triage", "detail", "actions"];
let activeTab = "runs";
let routing = false;
/* Deep-link target remembered until the run list populates the detail
   select: on a fresh tab the selects are empty when the hash is read, so
   the initial route would otherwise lose to the default selection. */
let pendingDetailId = "";

/* Pure route parser (no DOM): split "#tab/id" into a known tab plus
   the decoded id. Unknown or empty tabs fall back to "runs". */
function parseRoute(rawHash) {
  const raw = String(rawHash || "").replace(/^#/, "");
  const slash = raw.indexOf("/");
  const tab = slash < 0 ? raw : raw.slice(0, slash);
  const id = slash < 0 ? "" : decodeURIComponent(raw.slice(slash + 1));
  const name = TABS.includes(tab) && tab ? tab : "runs";
  return { tab: name, id };
}

/* Pure call-count label: "1 call", otherwise "N calls". */
function formatCallCount(n) {
  const count = Number(n) || 0;
  return String(count) + (count === 1 ? " call" : " calls");
}

function openTab(name, reportId) {
  if (!TABS.includes(name)) name = "runs";
  activeTab = name;
  document.querySelectorAll(".tabs button").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  for (const sec of document.querySelectorAll("main > section")) sec.hidden = true;
  document.getElementById("tab-" + name).hidden = false;
  if (reportId !== undefined) {
    const selId = name === "scout" ? "scout-report"
      : name === "triage" ? "triage-report"
      : name === "detail" ? "detail-run" : null;
    if (selId) {
      const sel = document.getElementById(selId);
      if ([...sel.options].some((o) => o.value === reportId)) {
        sel.value = reportId;
        if (name === "scout") loadScoutReport(reportId);
        else if (name === "triage") loadTriageReport(reportId);
        else loadRunDetail(reportId);
      }
    }
  }
}

function hashFor(tab, id) {
  if (tab === "runs") return "#runs";
  if (tab === "actions") return "#actions";
  return "#" + tab + (id ? "/" + encodeURIComponent(id) : "");
}

function go(tab, id) {
  pendingDetailId = tab === "detail" ? (id || "") : "";
  openTab(tab, id);
  const want = hashFor(tab, id);
  if (window.location.hash !== want) {
    routing = true;
    window.location.hash = want;
  }
}

function routeFromHash() {
  const { tab, id } = parseRoute(window.location.hash || "");
  pendingDetailId = tab === "detail" ? id : "";
  openTab(tab, id || undefined);
}

document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    const selId = tab === "scout" ? "scout-report"
      : tab === "triage" ? "triage-report"
      : tab === "detail" ? "detail-run" : null;
    const id = selId ? document.getElementById(selId).value : "";
    go(tab, id || undefined);
  });
});

window.addEventListener("hashchange", () => {
  if (routing) {
    routing = false;
    return;
  }
  routeFromHash();
});

/* -- runs: KPI cards, filter chips, readable table ---------------------- */

let allRuns = [];
let runFilter = "fix";

function setFilter(f) {
  runFilter = f;
  document.querySelectorAll("#run-filters .filter-chip").forEach((c) => {
    c.setAttribute("aria-pressed", c.dataset.filter === f ? "true" : "false");
  });
  renderRuns();
}

document.querySelectorAll("#run-filters .filter-chip").forEach((c) => {
  c.addEventListener("click", () => setFilter(c.dataset.filter));
});

function kpi(label, value) {
  const d = document.createElement("div");
  d.className = "kpi";
  const v = el("div", value);
  v.className = "value";
  d.appendChild(v);
  const l = el("div", label);
  l.className = "label";
  d.appendChild(l);
  return d;
}

function runMatches(run, f) {
  if (f === "all") return true;
  if (f === "fix") return run.kind === "solve";
  return run.kind === f;
}

function ciCell(run, td) {
  const linux = run.linux;
  if (!linux) {
    td.appendChild(el("span", "—"));
    return;
  }
  const ci = linux.ci_run;
  if (typeof ci === "string" && /^https?:\/\//i.test(ci)) {
    const a = document.createElement("a");
    a.href = ci;
    a.rel = "noopener";
    a.textContent = "CI";
    td.appendChild(a);
  } else {
    td.appendChild(el("span", "—"));
  }
}

function renderRuns() {
  const tbody = document.querySelector("#runs-table tbody");
  tbody.replaceChildren();
  let rows = allRuns.filter((r) => runMatches(r, runFilter));
  if (runFilter === "fix") {
    rows = [...rows].sort((a, b) =>
      Number(b.verdict === "VERIFIED") - Number(a.verdict === "VERIFIED") ||
      (b.run_id < a.run_id ? -1 : b.run_id > a.run_id ? 1 : 0));
  }
  for (const run of rows) {
    const parsed = parseRunName(run.run_id);
    const tr = document.createElement("tr");
    const nameTd = document.createElement("td");
    nameTd.appendChild(el("div", parsed.name)).className = "run-name";
    const raw = el("div", run.run_id);
    raw.className = "run-id";
    nameTd.appendChild(raw);
    tr.appendChild(nameTd);
    tr.appendChild(el("td", parsed.date || "—"));
    const vtd = document.createElement("td");
    vtd.appendChild(verdictBadge(run.verdict));
    if (run.kind === "solve" && run.local_verdict && run.local_verdict !== run.verdict) {
      const linuxOk = run.linux && run.linux.verdict === "VERIFIED";
      const tag = el("span", (linuxOk ? "Windows run: " : "local ") + run.local_verdict);
      tag.className = "tag";
      vtd.appendChild(document.createTextNode(" "));
      vtd.appendChild(tag);
    }
    tr.appendChild(vtd);
    const linuxTd = document.createElement("td");
    if (run.linux) {
      const tag = el("span", String(run.linux.verdict || "Linux"));
      tag.className = "tag " + (run.linux.verdict === "VERIFIED" ? "ok"
        : run.linux.verdict === "ERROR" ? "bad" : "");
      linuxTd.appendChild(tag);
    } else {
      linuxTd.appendChild(el("span", "—"));
    }
    tr.appendChild(linuxTd);
    const ciTd = document.createElement("td");
    ciCell(run, ciTd);
    tr.appendChild(ciTd);
    const costTd = document.createElement("td");
    costTd.appendChild(costNode(run));
    tr.appendChild(costTd);
    const chainTd = document.createElement("td");
    if (run.chain_ok === true) {
      const tag = el("span", "✓ ok");
      tag.className = "tag ok";
      chainTd.appendChild(tag);
    } else if (run.chain_ok === false) {
      const tag = el("span", "BROKEN");
      tag.className = "tag bad";
      chainTd.appendChild(tag);
    } else {
      chainTd.appendChild(el("span", "n/a"));
    }
    tr.appendChild(chainTd);
    tbody.appendChild(tr);
  }
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = el("td", "No runs in this view.");
    td.colSpan = 7;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

async function loadRuns() {
  try {
    const data = await getJSON("/api/overview");
    const spent = Number(data.total_spend_usd || 0);
    const total = Number(data.budget_total_usd || 150);
    const remaining = Number(data.remaining_usd || 0);
    document.getElementById("budget-line").textContent =
      "Spent " + fmtSpent(spent) + " of $" + total.toFixed(0);
    document.getElementById("budget-sub").textContent =
      "remaining " + fmtSpent(remaining);
    const pct = total > 0 ? Math.min(100, (spent / total) * 100) : 0;
    document.getElementById("budget-bar").style.width = pct.toFixed(1) + "%";
    allRuns = data.runs || [];
    const verified = allRuns.filter((r) => r.verdict === "VERIFIED").length;
    const linuxVerified = allRuns.filter(
      (r) => r.linux && r.linux.verdict === "VERIFIED").length;
    const triageCount = allRuns.filter((r) => r.kind === "triage").length;
    const box = document.getElementById("kpis");
    box.replaceChildren();
    box.appendChild(kpi("Verified fixes", String(verified)));
    box.appendChild(kpi("of which Linux-verified", String(linuxVerified)));
    box.appendChild(kpi("Triage runs", String(triageCount)));
    box.appendChild(kpi("Runtime spend (ledger)", fmtSpent(spent)));
    renderRuns();
    const detailSel = document.getElementById("detail-run");
    const keep = detailSel.value;
    detailSel.replaceChildren();
    for (const run of allRuns) {
      const opt = el("option", parseRunName(run.run_id).name);
      opt.value = run.run_id;
      detailSel.appendChild(opt);
    }
    if (pendingDetailId
      && [...detailSel.options].some((o) => o.value === pendingDetailId)) {
      detailSel.value = pendingDetailId;
      pendingDetailId = "";
    } else if (keep) detailSel.value = keep;
    if (detailSel.value && activeTab === "detail") loadRunDetail(detailSel.value);
  } catch (err) {
    setStatus("Runs: " + err.message);
  }
}

/* -- scout: candidate cards --------------------------------------------- */

async function loadScoutList() {
  try {
    const data = await getJSON("/api/scout");
    const sel = document.getElementById("scout-report");
    const keep = sel.value;
    sel.replaceChildren();
    for (const rep of data.reports || []) {
      const opt = el("option", rep.run_id + " (" + rep.n_candidates + " candidates)");
      opt.value = rep.run_id;
      sel.appendChild(opt);
    }
    if (keep && [...sel.options].some((o) => o.value === keep)) sel.value = keep;
    if (sel.value) loadScoutReport(sel.value);
  } catch (err) {
    setStatus("Scout: " + err.message);
  }
}

/* Guided proposal record of one scout report: accepted queries with their
   "why", rejected ones with the reason. All text via textContent (el). */
function renderGuidedQueries(doc) {
  const wrap = document.createElement("div");
  wrap.className = "card";
  wrap.appendChild(el("h3", "Guided search proposal"));
  if (doc.guided_rationale) {
    const r = el("p", doc.guided_rationale);
    r.className = "meta-line";
    wrap.appendChild(r);
  }
  for (const q of doc.proposed_queries || []) {
    const line = (q.keywords || "(empty)") +
      (q.class ? " [" + q.class + "]" : "") +
      (q.why ? " — " + q.why : "");
    const p = el("p", "accepted: " + line);
    p.className = "meta-line";
    wrap.appendChild(p);
  }
  for (const q of doc.rejected_queries || []) {
    const line = (q.keywords || "(empty)") +
      (q.reason ? " — rejected: " + q.reason : " — rejected");
    const p = el("p", line);
    p.className = "meta-line";
    wrap.appendChild(p);
  }
  return wrap;
}

function scoutCard(c, brief) {
  const card = document.createElement("div");
  card.className = "card";
  const head = el("p", (c.repo || "unknown repo") + " #" + (c.number || "?"));
  head.className = "cand-head";
  card.appendChild(head);
  const briefTitle = brief ? briefText(brief, "title", getBriefLang()) : "";
  const title = el("h3", briefTitle || c.title || "(no title)");
  title.className = "cand-title";
  card.appendChild(title);
  if (brief) {
    card.appendChild(renderBrief(brief, c.title || "", false));
  }
  const chips = document.createElement("div");
  chips.className = "chips";
  chips.appendChild(chip("★ " + (c.stars ?? "—")));
  chips.appendChild(chip(String(c.runner || "—")));
  chips.appendChild(chip("security: " + (c.security_class || "—")));
  if (c.score !== undefined && c.score !== null && c.score !== "") {
    chips.appendChild(chip("score " + fmtScore(c.score)));
  }
  card.appendChild(chips);
  if (c.matched_query) {
    const m = el("p", "matched query: " + c.matched_query);
    m.className = "meta-line";
    card.appendChild(m);
  }
  if (c.issue_url) {
    const p = el("p");
    p.className = "meta-line";
    p.appendChild(link(c.issue_url));
    card.appendChild(p);
  }
  const sig = c.signals || {};
  const keys = Object.keys(sig);
  if (keys.length) {
    const wrap = document.createElement("div");
    wrap.className = "sig-chips";
    for (const k of keys) {
      const raw = sig[k];
      const shown = (typeof raw === "number" && Number.isFinite(raw))
        ? raw.toFixed(2) : String(raw);
      const s = el("span", k + ": " + shown);
      s.className = "sig";
      wrap.appendChild(s);
    }
    card.appendChild(wrap);
  }
  return card;
}

async function loadScoutReport(runId) {
  const box = document.getElementById("scout-detail");
  box.replaceChildren();
  try {
    const doc = await getJSON("/api/scout/" + encodeURIComponent(runId));
    box.appendChild(kv([
      ["report", doc.run_id],
      ["candidates", (doc.candidates || []).length],
      ["rate limited", doc.rate_limited ? "yes" : "no"],
      ["guided", doc.guided === undefined ? undefined : (doc.guided ? "yes" : "no")],
    ]));
    if (doc.guided) {
      box.appendChild(renderGuidedQueries(doc));
    }
    if ((doc.candidates || []).length) {
      const explain = el("button", "Explain candidates");
      explain.type = "button";
      explain.className = "explain";
      explain.addEventListener("click", () => {
        openTab("actions");
        const input = document.querySelector('#form-brief input[name="scout_run_id"]');
        if (input) { input.value = doc.run_id || ""; input.focus(); }
      });
      box.appendChild(explain);
    }
    for (const c of doc.candidates || []) {
      box.appendChild(scoutCard(c, (doc.briefs || {})[c.issue_url]));
    }
  } catch (err) {
    box.appendChild(el("p", "Scout: " + err.message));
  }
}

/* -- triage: scored cards, picked first ---------------------------------- */

function scoreBar(label, value) {
  const row = document.createElement("div");
  row.className = "bar-row";
  row.appendChild(el("span", label)).className = "bar-label";
  const track = document.createElement("div");
  track.className = "bar-track";
  const num = Number(value);
  const known = value !== undefined && value !== null && value !== "" && Number.isFinite(num);
  const max = num > 1 ? 10 : 1;
  const fill = document.createElement("span");
  if (known) {
    const pct = Math.max(0, Math.min(100, (num / max) * 100));
    fill.style.width = pct.toFixed(1) + "%";
    track.classList.add(pct >= 66 ? "good" : pct >= 33 ? "mid" : "low");
  } else {
    fill.style.width = "0%";
  }
  track.appendChild(fill);
  row.appendChild(track);
  row.appendChild(el("span", fmtScore(value)));
  return row;
}

function closedDetails(summaryText, bodyText) {
  const d = document.createElement("details");
  d.appendChild(el("summary", summaryText));
  d.appendChild(el("pre", bodyText));
  return d;
}

function triageCard(item, picked, brief) {
  const card = document.createElement("div");
  card.className = "card" + (picked ? " picked" : "");
  const briefTitle = brief ? briefText(brief, "title", getBriefLang()) : "";
  const title = el("h3", briefTitle || item.title || item.issue_url || "(no title)");
  title.className = "cand-title";
  if (picked) {
    const badge = el("span", "PICKED");
    badge.className = "picked-badge";
    title.appendChild(badge);
  }
  card.appendChild(title);
  if (item.issue_url) {
    const p = el("p");
    p.className = "meta-line";
    p.appendChild(link(item.issue_url));
    card.appendChild(p);
  }
  if (brief) {
    card.appendChild(renderBrief(brief, item.title || "", false));
  }
  const critic = item.critic || {};
  const bars = document.createElement("div");
  bars.className = "bars";
  bars.appendChild(scoreBar("Signals score", item.signals_score));
  bars.appendChild(scoreBar("Spark solvability", item.solvability));
  bars.appendChild(scoreBar("Critic-adjusted", critic.solvability_v2));
  bars.appendChild(scoreBar("Final score", item.final_score));
  card.appendChild(bars);
  const pills = document.createElement("div");
  pills.className = "chips";
  const real = item.is_real_bug;
  pills.appendChild(chip("real bug: " + (real === true ? "yes" : real === false ? "no" : "—")));
  pills.appendChild(chip("critic: " + (critic.flag || item.critic_flag || "—")));
  card.appendChild(pills);
  const meta = el("p", "triage " + (item.triage_model || "—") +
    " via " + (item.triage_provider || "—") +
    " · critic " + (item.critic_model || "—") +
    " via " + (item.critic_provider || "—"));
  meta.className = "meta-line";
  card.appendChild(meta);
  if (item.rationale) {
    const r = el("p", item.rationale);
    r.className = "rationale";
    card.appendChild(r);
  }
  if (critic.rationale_v2) {
    const r = el("p", "Critic: " + critic.rationale_v2);
    r.className = "rationale";
    card.appendChild(r);
  }
  if (item.triage_reasoning_excerpt) {
    card.appendChild(closedDetails("Triage reasoning", item.triage_reasoning_excerpt));
  }
  if (item.critic_reasoning_excerpt) {
    card.appendChild(closedDetails("Critic reasoning", item.critic_reasoning_excerpt));
  }
  return card;
}

async function loadTriageList() {
  try {
    const data = await getJSON("/api/triage");
    const sel = document.getElementById("triage-report");
    const keep = sel.value;
    sel.replaceChildren();
    for (const rep of data.reports || []) {
      const opt = el("option", rep.run_id + " (" + rep.n_scored + " scored)");
      opt.value = rep.run_id;
      sel.appendChild(opt);
    }
    if (keep && [...sel.options].some((o) => o.value === keep)) sel.value = keep;
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
    const pickedUrl = doc.selection && doc.selection.picked
      ? doc.selection.picked.issue_url : null;
    box.appendChild(kv([
      ["triage run", doc.run_id],
      ["scout report", doc.scout_run_id],
      ["picked", pickedUrl || "none"],
    ]));
    const scored = [...(doc.scored || [])].sort((a, b) => {
      if (pickedUrl) {
        if (a.issue_url === pickedUrl) return -1;
        if (b.issue_url === pickedUrl) return 1;
      }
      return (Number(b.final_score) || 0) - (Number(a.final_score) || 0);
    });
    for (const item of scored) {
      box.appendChild(triageCard(item, pickedUrl && item.issue_url === pickedUrl, (doc.briefs || {})[item.issue_url]));
    }
  } catch (err) {
    box.appendChild(el("p", "Triage: " + err.message));
  }
}

/* -- run detail: verdict hero, f2p strip, calls, proof ------------------- */

function renderDiff(pre, patch) {
  pre.replaceChildren();
  const code = document.createElement("code");
  code.className = "code-lines";
  for (const line of String(patch).split("\n")) {
    const s = el("span", line || " ");
    if (line.startsWith("+") && !line.startsWith("+++")) s.className = "diff-add";
    else if (line.startsWith("-") && !line.startsWith("---")) s.className = "diff-del";
    code.appendChild(s);
  }
  pre.appendChild(code);
}

async function loadRunDetail(runId) {
  const box = document.getElementById("run-detail");
  box.replaceChildren();
  try {
    const d = await getJSON("/api/runs/" + encodeURIComponent(runId));
    const hero = document.createElement("div");
    hero.className = "hero";
    hero.appendChild(verdictBadge(d.verdict, true));
    if (d.linux && d.linux.verdict) {
      const pill = document.createElement("span");
      const ciRun = typeof d.linux.ci_run === "string" ? d.linux.ci_run : "";
      const useLink = /^https?:\/\//i.test(ciRun);
      const pillEl = useLink ? document.createElement("a") : pill;
      if (useLink) {
        pillEl.href = ciRun;
        pillEl.rel = "noopener";
      }
      pillEl.textContent = "Linux CI · " + d.linux.verdict;
      pillEl.className = "linux-pill" + (
        d.linux.verdict === "VERIFIED" ? " verified" : "");
      hero.appendChild(pillEl);
    }
    const meta = document.createElement("div");
    meta.className = "hero-meta";
    meta.appendChild(kv([
      ["run", d.run_id],
      ["kind", d.kind || "solve"],
      ["local verdict", d.local_verdict || d.verdict],
      ["cost", costNode(d)],
      ["chain", d.chain.ok ? "✓ ok"
        : ("BROKEN line " + d.chain.bad_line + ": " + d.chain.reason)],
    ]));
    hero.appendChild(meta);
    box.appendChild(hero);

    const work = d.work || {};
    const verify = work.verify_verdict || {};
    const f2p = (d.f2p && d.f2p.f2p_pass !== undefined && d.f2p.f2p_pass !== null)
      ? d.f2p : { source: "local", f2p_pass: verify.f2p_pass };
    if (f2p.f2p_pass === true || f2p.f2p_pass === false) {
      box.appendChild(el("h3", "Fail-to-pass"));
      const strip = document.createElement("div");
      strip.className = "f2p";
      const base = el("span", f2p.f2p_pass ? "FAIL on base" : "no failure on base");
      base.className = "end base";
      const arrow = el("span", "→");
      arrow.className = "arrow";
      const fixed = el("span", f2p.f2p_pass ? "PASS with fix" : "NOT demonstrated");
      fixed.className = "end fixed";
      strip.appendChild(base);
      strip.appendChild(arrow);
      strip.appendChild(fixed);
      const testPath = f2p.test_path
        || (work.repro_result && work.repro_result.test_path) || "";
      if (testPath) {
        const p = el("span", testPath);
        p.className = "path";
        strip.appendChild(p);
      }
      if (f2p.source === "linux") {
        const tag = el("span", "Linux CI");
        tag.className = "chip ok";
        strip.appendChild(tag);
        if (f2p.suite_scope) {
          const scope = el("span", "scope " + f2p.suite_scope);
          scope.className = "path";
          strip.appendChild(scope);
        }
        if (f2p.regressions !== null && f2p.regressions !== undefined) {
          const reg = el("span", f2p.regressions + " regressions");
          reg.className = "path";
          strip.appendChild(reg);
        }
      }
      box.appendChild(strip);
      if (f2p.source === "linux" && d.local_summary) {
        const local = el("p", d.local_summary);
        local.className = "meta-line";
        box.appendChild(local);
      }
    }

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
    } else if (d.issue_url) {
      const p = document.createElement("p");
      p.className = "meta-line";
      p.appendChild(el("span", "Solved directly from "));
      p.appendChild(link(d.issue_url));
      p.appendChild(el("span", " (no triage pick)"));
      box.appendChild(p);
    } else {
      box.appendChild(el("p", "No pick recorded."));
    }
    for (const s of (d.decision && d.decision.skipped) || []) {
      box.appendChild(kv([["skipped " + (s.issue_id || ""), s.reason]]));
    }

    box.appendChild(el("h3", "Work and verification"));
    const repro = work.repro_result || {};
    const fix = work.fix_attempt || {};
    box.appendChild(kv([
      ["repro test", repro.test_path],
      ["failed on base", repro.failed_on_base],
      ["failure reason", repro.failure_reason],
      ["fix effort", fix.effort],
      ["patch hash", fix.patch_hash],
      ["fix explanation", fix.explanation],
      ["regressions", verify.regressions],
    ]));

    box.appendChild(el("h4", "Model calls"));
    const calls = document.createElement("table");
    const chead = document.createElement("tr");
    for (const h of ["Stage", "Model", "In", "Out", "Reasoning", "Cost"]) {
      chead.appendChild(el("th", h));
    }
    calls.appendChild(chead);
    let tin = 0;
    let tout = 0;
    let trsn = 0;
    let tcost = 0;
    let costKnown = false;
    for (const c of work.model_calls || []) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", c.stage));
      tr.appendChild(el("td", (c.model || "") + " via " + (c.provider || "")));
      tr.appendChild(el("td", fmtTokens(c.in_tokens)));
      tr.appendChild(el("td", fmtTokens(c.out_tokens)));
      tr.appendChild(el("td", fmtTokens(c.reasoning_tokens)));
      tr.appendChild(el("td", fmtUSD(c.cost_usd)));
      calls.appendChild(tr);
      tin += Number(c.in_tokens) || 0;
      tout += Number(c.out_tokens) || 0;
      trsn += Number(c.reasoning_tokens) || 0;
      if (typeof c.cost_usd === "number") {
        tcost += c.cost_usd;
        costKnown = true;
      }
    }
    const foot = document.createElement("tfoot");
    const total = document.createElement("tr");
    total.appendChild(el("td", "Total"));
    total.appendChild(el("td", formatCallCount((work.model_calls || []).length)));
    total.appendChild(el("td", fmtTokens(tin)));
    total.appendChild(el("td", fmtTokens(tout)));
    total.appendChild(el("td", fmtTokens(trsn)));
    total.appendChild(el("td", costKnown ? fmtUSD(Math.round(tcost * 1e6) / 1e6) : "unknown"));
    foot.appendChild(total);
    calls.appendChild(foot);
    box.appendChild(calls);

    box.appendChild(el("h3", "Result"));
    const proof = d.proof || {};
    box.appendChild(kv([
      ["verdict", d.verdict],
      ["bundle files", (proof.files || []).join(", ")],
    ]));
    if (proof.verdict) {
      box.appendChild(closedDetails("verdict.json", JSON.stringify(proof.verdict, null, 2)));
    }
    if (proof.cost) {
      box.appendChild(closedDetails("cost.json", JSON.stringify(proof.cost, null, 2)));
    }
    if (proof.fix_patch) {
      box.appendChild(el("h4", "fix.patch"));
      const pre = document.createElement("pre");
      renderDiff(pre, proof.fix_patch);
      box.appendChild(pre);
    }
  } catch (err) {
    box.appendChild(el("p", "Run detail: " + err.message));
  }
}

/* -- actions: estimate summaries, job progress --------------------------- */

let pollTimer = null;
let jobStartedAt = 0;
let elapsedTimer = null;

function setPill(status) {
  const pill = document.getElementById("job-pill");
  pill.replaceChildren();
  pill.appendChild(el("span", status));
  pill.className = "status-pill " + (
    status === "running" || status === "queued" ? "running"
    : status === "done" ? "done"
    : status === "error" ? "error" : "");
}

function tickElapsed() {
  if (!jobStartedAt) return;
  const secs = Math.floor((Date.now() - jobStartedAt) / 1000);
  document.getElementById("job-elapsed").textContent = "elapsed " + secs + "s";
}

function flatPairs(obj) {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v !== null && typeof v === "object") {
      for (const [sk, sv] of Object.entries(v)) {
        out.push([k + " " + sk, sv === null || typeof sv !== "object" ? sv : JSON.stringify(sv)]);
      }
    } else {
      out.push([k, v]);
    }
  }
  return out;
}

function showJob(job) {
  document.getElementById("job-status").textContent =
    "Job " + job.job_id + " (" + job.kind + "): " + job.status +
    (job.error ? " — " + job.error : "");
  setPill(job.status || "idle");
  const log = document.getElementById("job-log");
  log.textContent = job.log || "";
  log.scrollTop = log.scrollHeight;
  const res = document.getElementById("job-result");
  res.replaceChildren();
  if (job.result) {
    res.appendChild(el("h4", "Result"));
    res.appendChild(kv(flatPairs(job.result)));
  }
  refreshLive(job);
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await getJSON("/api/jobs/" + encodeURIComponent(jobId));
      showJob(job);
      await refreshLive(job);
      if (job.status === "done" || job.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
        if (elapsedTimer) {
          clearInterval(elapsedTimer);
          elapsedTimer = null;
        }
        tickElapsed();
        loadRuns();
      }
    } catch (err) {
      document.getElementById("job-status").textContent = "Job poll: " + err.message;
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 3000);
}

function startTimers() {
  jobStartedAt = Date.now();
  tickElapsed();
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(tickElapsed, 1000);
}

/* Short human summary of an estimate reply; the confirm token is never
   printed (it travels only in the confirm request body). */
function renderEstimate(box, kind, reply) {
  box.replaceChildren();
  if (kind === "triage") {
    const t = reply.triage || {};
    const c = reply.critic || {};
    const unpriced = t.price_known === false;
    box.appendChild(kv([
      ["model", reply.model],
      ["provider", reply.provider],
      ["candidates", reply.n_candidates],
      ["triage tokens (est)", (t.est_in_tokens ?? "—") + " in / " + (t.est_out_tokens ?? "—") + " out"],
      ["triage cost (est)", unpriced ? "unpriced (Baseten credits)" : fmtUSD(t.est_cost_usd)],
      ["critic tokens (est)", (c.est_in_tokens ?? "—") + " in / " + (c.est_out_tokens ?? "—") + " out"],
      ["critic cost (est)", fmtUSD(c.est_cost_usd)],
    ]));
  } else if (kind === "solve") {
    box.appendChild(kv([
      ["issue", reply.issue_url],
      ["model", reply.model],
      ["provider", reply.provider || "—"],
      ["effort", reply.effort],
      ["steps", reply.steps],
      ["estimated cost", fmtUSD(reply.est_cost_usd)],
      ["recommended max USD", fmtUSD(reply.recommended_max_usd)],
    ]));
  } else if (kind === "brief") {
    const b = reply.brief || {};
    const unpriced = b.price_known === false;
    box.appendChild(kv([
      ["model", reply.model],
      ["provider", reply.provider],
      ["candidates", reply.n_candidates],
      ["brief tokens (est)", (b.est_in_tokens ?? "\u2014") + " in / "
        + (b.est_out_tokens ?? "\u2014") + " out"],
      ["brief cost (est)", unpriced ? "unpriced (Baseten credits)" : fmtUSD(b.est_cost_usd)],
    ]));
  } else if (kind === "scout") {
    box.appendChild(kv([
      ["languages", reply.languages],
      ["limit", reply.limit],
      ["guided", reply.guided ? "yes" : "no"],
      ["guided query proposal (est)", reply.guided ? fmtUSD(reply.guided_est_usd) : "$0"],
      ["model calls (est)", reply.est_calls],
    ]));
    if (reply.note) {
      const n = el("p", reply.note);
      n.className = "note";
      box.appendChild(n);
    }
  } else if (kind === "run") {
    box.appendChild(kv([
      ["languages", reply.languages],
      ["limit", reply.limit],
      ["max USD", reply.max_usd],
      ["triage (est)", fmtUSD(reply.triage_est_usd)],
      ["critic (est)", fmtUSD(reply.critic_est_usd)],
      ["solver allowance", fmtUSD(reply.solver_allowance_usd)],
      ["guided", reply.guided ? "yes" : "no"],
      ["guided query proposal (est)", reply.guided ? fmtUSD(reply.guided_est_usd) : "$0"],
    ]));
    if (reply.note) {
      const n = el("p", reply.note);
      n.className = "note";
      box.appendChild(n);
    }
  } else {
    box.appendChild(kv(flatPairs(reply)));
  }
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
      renderEstimate(estimateBox, estimateKind, reply);
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
      startTimers();
      showJob({ job_id: job.job_id, kind: job.kind, status: job.status, log: "", error: "" });
      pollJob(job.job_id);
    } catch (err) {
      setStatus(formId + " action: " + err.message);
    }
  });
}

/* Scout goes through estimate + Confirm when guided (one paid model call)
   and posts directly when unguided (free, fixed queries only). */
function wireScout(formId, pickParams) {
  const form = document.getElementById(formId);
  const estimateBox = form.querySelector(".estimate");
  const confirmBtn = form.querySelector(".confirm");
  let pending = null;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    confirmBtn.hidden = true;
    pending = null;
    estimateBox.replaceChildren();
    try {
      const params = pickParams(form);
      const box = form.querySelector('input[name="guided"]');
      params.guided = box ? box.checked : true;
      if (!params.guided) {
        const job = await postJSON("/api/actions/scout", params);
        startTimers();
        showJob({ job_id: job.job_id, kind: job.kind, status: job.status, log: "", error: "" });
        pollJob(job.job_id);
        setStatus("");
        return;
      }
      const reply = await postJSON("/api/estimate/scout", params);
      pending = { params, token: reply.confirm_token };
      renderEstimate(estimateBox, "scout", reply);
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
      const job = await postJSON("/api/actions/scout",
        Object.assign({}, pending.params, { confirm_token: pending.token }));
      pending = null;
      confirmBtn.hidden = true;
      startTimers();
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
      startTimers();
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

document.getElementById("scout-report").addEventListener("change", (e) => {
  go("scout", e.target.value || undefined);
});
document.getElementById("triage-report").addEventListener("change", (e) => {
  go("triage", e.target.value || undefined);
});
document.getElementById("detail-run").addEventListener("change", (e) => {
  go("detail", e.target.value || undefined);
});

wireScout("form-scout", (form) => {
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
  const guidedBox = form.querySelector('input[name="guided"]');
  out.guided = !guidedBox || guidedBox.checked;
  return out;
});

setFilter("fix");
routeFromHash();
loadRuns();
initBriefLang();
wireEstimate("form-brief", "brief", "brief", (form) => {
  const v = formValues(form, ["scout_run_id", "run_id"]);
  return { scout_run_id: v.scout_run_id, ...(v.run_id ? { run_id: v.run_id } : {}) };
});
loadScoutList();
loadTriageList();
