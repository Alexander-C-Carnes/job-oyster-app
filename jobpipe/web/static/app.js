"use strict";

// ---- token & API ------------------------------------------------------------------
const TOKEN = (() => {
  const url = new URL(location.href);
  let t = url.searchParams.get("token");
  try {
    if (t) localStorage.setItem("jobpipe-token", t);
    else t = localStorage.getItem("jobpipe-token");
  } catch (_) { /* storage blocked: keep the token in the URL */ }
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search);
  }
  return t || "";
})();

async function request(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...opts,
      headers: { "X-Jobpipe-Token": TOKEN, ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) },
    });
  } catch (e) {
    if (!(e instanceof TypeError)) throw e;
    // The server isn't answering: say so in words, not the browser's "Failed to fetch". Still a TypeError for the checks below.
    $("#offline").classList.remove("hidden");
    throw new TypeError("Can't reach the app's server. Start it with oyster run.");
  }
  if (res.status === 401) { $("#auth-error").classList.remove("hidden"); throw new Error("Not authorized"); }
  return res;
}
async function api(path, opts = {}) {
  const res = await request(path, opts);
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}
const send = (method) => (path, body) => api(path, { method, body: JSON.stringify(body || {}) });
const post = send("POST"), put = send("PUT"), patch = send("PATCH");
const fileUrl = (path) => `${path}${path.includes("?") ? "&" : "?"}t=${encodeURIComponent(TOKEN)}`;
// A résumé version's PDF, by a link that ends in its download name ("<Role Title> - <Company>.pdf"), so the
// browser saves it under that whichever name it goes by.
const resumeUrl = (id, which, r) => fileUrl(`/api/jobs/${id}/resume/${which}/${encodeURIComponent(r.pdf_names?.[which] || r.pdf_name || "resume.pdf")}`);

// ---- tiny DOM helpers ------------------------------------------------------------------
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const ICONS = {
  star: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.4l2.35 4.76 5.25.77-3.8 3.7.9 5.23L10 14.39l-4.7 2.47.9-5.23-3.8-3.7 5.25-.77z"/></svg>',
  person: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7" r="3.1"/><path d="M3.8 16.8c.7-3 3.2-4.6 6.2-4.6s5.5 1.6 6.2 4.6"/></svg>',
  personAdd: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="7" r="3.1"/><path d="M2.4 16.8c.7-3 3.1-4.6 6.1-4.6 1.3 0 2.5.3 3.5.9M15.5 11.2v5.6M12.7 14h5.6"/></svg>',
  share: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.8v9.4M6.4 6.2L10 2.8l3.6 3.4M4.6 10.4v5.2c0 .9.7 1.6 1.6 1.6h7.6c.9 0 1.6-.7 1.6-1.6v-5.2"/></svg>',
  download: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.8v9.6M6.2 8.8L10 12.6l3.8-3.8M4 16.6h12"/></svg>',
  copy: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.6"/><path d="M13 7V4.6c0-.9-.7-1.6-1.6-1.6H4.6C3.7 3 3 3.7 3 4.6v6.8c0 .9.7 1.6 1.6 1.6H7"/></svg>',
  close: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg>',
  info: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.2"/><path d="M10 9v5M10 6.2v.1"/></svg>',
  up: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 12.5L10 8l4.5 4.5"/></svg>',
  down: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5.5 7.5L10 12l4.5-4.5"/></svg>',
  left: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 5.5L8 10l4.5 4.5"/></svg>',
  right: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 5.5L12 10l-4.5 4.5"/></svg>',
  resume: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 2.8h6.6L15.2 6.4v10.8H5z"/><path d="M11.4 2.8v3.8h3.8M7.6 10h4.8M7.6 13h4.8"/></svg>',
  palette: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5a7.5 7.5 0 1 0 0 15c1.3 0 1.9-.8 1.9-1.6 0-.9-.6-1.2-.6-2 0-.9.7-1.4 1.6-1.4h1.4c1.8 0 3.2-1.3 3.2-3.1C17.5 5.6 14.1 2.5 10 2.5z"/><circle cx="6.5" cy="9" r="1.1"/><circle cx="9" cy="5.8" r="1.1"/><circle cx="13" cy="6.2" r="1.1"/></svg>',
};
function icon(name) {
  const s = h("span", { class: "ico" });
  s.innerHTML = ICONS[name];
  return s;
}
const spinner = () => h("span", { class: "spinner" });
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), error ? 7000 : 3500);
}
const pct = (v) => (v == null ? "n/a" : `${Math.round(v)}%`);
const fitClass = (f) => (f == null ? "" : f >= 7 ? "hi" : f >= 5 ? "mid" : "lo");
const known = (v) => (v && !/^not stated$/i.test(v) ? v : "");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const safeHref = (url) => (/^https?:\/\//i.test(url || "") ? url : null);
const extLink = (url, label) => safeHref(url) && h("a", { href: url, target: "_blank", rel: "noopener" }, label, " ↗");
function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return "Referral"; }
}
const fmtDate = (v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" });
function formError(form, msg) {
  const p = $(".form-error", form);
  p.textContent = msg || "";
  p.classList.toggle("hidden", !msg);
}

// ---- app state -------------------------------------------------------------------------
const STAGES = ["Not started", "In progress", "Blocked", "Applied", "Interviewing", "Offer", "Denied", "Ghosted", "Done", "Not Applying"];
// The stages that mean the application was sent (APPLIED_STATUSES in jobpipe/notion.py).
const APPLIED_STAGES = ["Applied", "Interviewing", "Offer", "Denied", "Ghosted", "Done"];
// The main line, in order, and the exits off it: how the stage strip and a role's stage path draw them.
const PATH = ["Not started", "In progress", "Applied", "Interviewing", "Offer", "Done"];
const EXITS = ["Blocked", "Denied", "Ghosted", "Not Applying"];
const STAGE_BLURB = { "Not started": "Tracked, nothing done yet", "In progress": "Having its résumé made, or you're working on it", Blocked: "Waiting on something",
                      Applied: "Application sent", Interviewing: "In interviews",
                      Offer: "They made an offer", Denied: "They said no", Ghosted: "Never heard back", Done: "Interviews finished or closed out", "Not Applying": "Ruled out" };
// The two job lists: what's in the tracker, and what the searches found that isn't tracked yet.
// The tracker is kept on this Mac (tracker_id); Notion is its synced copy (notion_page_id, once the row is there).
const SCOPES = {
  tracker: { has: (j) => !!j.tracker_id },
  find: { has: (j) => j.local && !j.tracker_id },
};
const FIND_FILTERS = [["new", "New", (j) => !j.dismissed], ["fit7", "Fit 7+", (j) => !j.dismissed && (score(j) ?? 0) >= 7],
                      ["dismissed", "Dismissed", (j) => j.dismissed]];
const BOARD_CAP = 40;  // cards drawn per column until "Show more"
const prefs = (() => {
  try { return JSON.parse(localStorage.getItem("jobpipe-ui") || "{}"); } catch (_) { return {}; }
})();
function savePrefs() {
  try { localStorage.setItem("jobpipe-ui", JSON.stringify({ view: S.view, sort: S.sort, group: S.group, collapsed: [...S.collapsed], docViews: S.docViews, dashRange: S.dashRange, fold: S.fold, trend: S.trend, split: S.split, ghostSeen: [...S.ghostSeen] })); }
  catch (_) { /* optional */ }
}
const S = { jobs: [], byId: new Map(), regions: [], etag: null, notion: false, syncing: false, notionError: "",
            query: "", tokens: [], stages: new Set(), flags: new Set(), facets: { score: new Set(), company: new Set(), region: new Set() }, visible: [],
            sort: ["fit", "newest", "company", "pay"].includes(prefs.sort) ? prefs.sort : "fit",
            view: prefs.view === "board" ? "board" : "list", boardAll: new Set(),
            group: prefs.group === "company" ? "company" : "", collapsed: new Set(Array.isArray(prefs.collapsed) ? prefs.collapsed : []),
            scope: "tracker", sel: { tracker: null, find: null }, findFilter: "new",
            // Folded parts of the jobs tab, so the selected role (above all its résumé) gets the room.
            fold: { filters: !!prefs.fold?.filters, list: !!prefs.fold?.list, head: !!prefs.fold?.head },
            selected: null, drawer: false, detailTab: "resume", detailTabJob: null, details: new Map(), scoring: new Map(), scoreErrors: new Map(), scoreFinished: null, picks: new Set(),
            summary: null, searches: [], defaults: {}, picked: null, runId: null, runSince: 0, tab: "tracker",
            dashRange: ["14", "30", "all"].includes(prefs.dashRange) ? prefs.dashRange : "30",
            trend: ["ats", "keywords", "skills", "experience"].includes(prefs.trend) ? prefs.trend : "ats",
            split: prefs.split > 0.1 && prefs.split < 0.9 ? prefs.split : null,    // the PDF's share of the résumé workspace
            ghostSeen: new Set(Array.isArray(prefs.ghostSeen) ? prefs.ghostSeen : []),   // moves to Ghosted already announced
            docViews: Object.fromEntries(["impact", "prep"].map((k) => {
              const v = prefs.docViews?.[k] ?? (k === "impact" ? prefs.impactView : null);   // impactView: before Interview prep
              return [k, ["formatted", "edit", "split"].includes(v) ? v : "formatted"];
            })) };

// ---- tabs --------------------------------------------------------------------------------
// The impact record lives under Profile: the round Profile button stays lit on it, and the strip under the bar switches the two.
const PROFILE_TABS = ["profile", "impact"];
$$(".topbar [data-tab], #profile-nav [data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  if (S.tab in DOCS && name !== S.tab) DOCS[S.tab].save();
  S.tab = name;
  const lit = (b) => b.dataset.tab === name || (b.id === "profile-btn" && PROFILE_TABS.includes(name));
  $$(".topbar [data-tab], #profile-nav [data-tab]").forEach((b) => { b.classList.toggle("active", lit(b)); b.setAttribute("aria-selected", String(lit(b))); });
  $("#profile-nav").classList.toggle("hidden", !PROFILE_TABS.includes(name));
  const panel = name in SCOPES ? "jobs" : name;
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${panel}`));
  if (name in SCOPES) setScope(name);
  if (name === "searches") loadSearches();
  if (name === "startups") loadStartups();
  if (name === "runs") loadRuns();
  if (name === "dashboard") renderDashboard();
  if (name in DOCS) DOCS[name].load();
  // Profile's cards load at once; the promise settles when all have (the About button scrolls down after that)
  if (name === "profile") return Promise.allSettled([loadProfile(), loadAI(), loadFormats(), loadGhostDays(), loadAbout()]);
}

// ---- what each action is called ----------------------------------------------------------------
// One name per thing the app can do to a job, used on every button, tooltip, run and in the glossary
// (What each does). Times come from your own runs (progress.py) once there are some.
const ACTIONS = {
  signal: { name: "Signal score", calls: "one Claude call",
    what: "A quick read on fit: scores the posting against your impact record from 1 to 10, with the strongest matches, the hard requirements to check and the likely gaps. Use it to pick which roles are worth more." },
  full: { name: "Full score", calls: "about four Claude calls",
    what: "Rates every requirement in the posting against your impact record and each of your résumés: a 1 to 10 score, a heat map and the gaps to confirm. No résumé is written. Make résumé reuses it." },
  upload: { name: "Score résumé", calls: "three Claude calls, after a full score",
    what: "Scores a résumé you uploaded for a role the way Make résumé scores its own: the role's full score (reused if it has one), then your résumé rated on every requirement. You get its résumé score, ATS and keywords, the format check and the chat. Nothing in it is rewritten." },
  resume: { name: "Make résumé", calls: "about eleven Claude calls",
    what: "Writes a résumé for this posting: the full score (reused if it has one), three drafts, the best of them merged and checked, a PDF and a fit report. The job moves to In progress, and its badge fills as it goes. When the most runs are going, it waits its turn and starts by itself." },
};
const mins = (sec) => `~${Math.max(1, Math.round(sec / 60))} min`;
function actionTime(kind) {
  const d = S.summary?.durations;
  if (kind === "signal") return "~30 sec";
  if (kind === "full") return d ? mins(d.full_score_s) : "a few min";
  if (kind === "upload") return d ? mins(d.full_score_s + 90) : "a few min";
  return d ? mins(d.make_resume_s) : "~25 min";
}
const timeTag = (kind) => h("span", { class: "btn-time" }, actionTime(kind));
// A job with no posting text (a startup board's role often comes without it) can't be scored or given a résumé:
// Run full score and Make résumé are off until the posting is fetched from its link or pasted.
const NO_POSTING = "Needs the posting text first: use Fetch posting, or paste the description from its page.";
const noPosting = (j) => !!(j?.local && j.no_posting);
const makeResumeButton = (j, cls = "primary", label = ACTIONS.resume.name) => h("button", { class: cls,
  title: noPosting(j) ? NO_POSTING : ACTIONS.resume.what, disabled: noPosting(j),
  onclick: () => startRun({ kind: "tailor", job_id: j.id }) }, label, timeTag("resume"));
const helpLink = (label = "What each does") => h("button", { class: "link-btn help-link", type: "button", onclick: openActionsHelp }, icon("info"), label);
// The glossary: what each action does, how long it takes, and what the scores mean.
function openActionsHelp() {
  const row = (name, meta, what) => h("div", { class: "gl-row" }, h("dt", {}, name, meta && h("span", { class: "gl-meta" }, meta)), h("dd", {}, what));
  const dlg = $("#actions-help") || document.body.appendChild(h("dialog", { id: "actions-help", "aria-labelledby": "actions-help-title" }));
  dlg.replaceChildren(
    h("div", { class: "gl-head" }, h("h2", { id: "actions-help-title" }, "What each does"),
      h("button", { class: "icon-btn", title: "Close", "aria-label": "Close", onclick: () => dlg.close() }, icon("close"))),
    h("h3", {}, "On a job"),
    h("dl", {}, ...["signal", "full", "upload", "resume"].map((k) => row(ACTIONS[k].name, `${actionTime(k)} · ${ACTIONS[k].calls}`, ACTIONS[k].what))),
    h("p", { class: "muted" }, "Most roles only need a signal score. Run a full score on the ones worth a closer look, and make a résumé for the ones you'll apply to."),
    h("h3", {}, "Searches (Filters tab)"),
    h("dl", {},
      row("Check filters", "≤1 credit per filter", "Asks JobsPipe how many jobs each filter matches, with a one-job request. Nothing is stored or scored."),
      row("Search + signal score", "credits, then one call per new job", "Runs your filters, stores the new jobs in Search Jobs and gives each a signal score."),
      row("Search + make résumés for top N", "as above, then Make résumé N times", `The same, then Make résumé for the N best new jobs with a signal score of 7 or more.`),
      row("Search once", "credits", "In the New/Edit filter dialog: runs what's in the dialog without saving it.")),
    h("h3", {}, "Scores"),
    h("dl", {},
      row("Signal score", "1–10", "How well your impact record fits the posting, from one quick read."),
      row("Full score", "1–10", "The same question, answered requirement by requirement. It replaces the signal score once a job has one."),
      row("Résumé", "1–10", "How well the résumé you made covers the posting. Every edit is rated again."),
      row("ATS", "%", "How much of the posting's skills, experience and keywords an applicant tracking system would find in the résumé.")),
    h("h3", {}, "Logs"),
    h("p", {}, "The Logs tab lists searches and Make résumé, with each one's log. A signal or full score shows on the job instead. Runs carry on when the app restarts (oyster restart); quitting the app stops them, and it asks first."),
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: () => dlg.close() }, "Close")));
  dlg.showModal();
}
const isStartupRefresh = (r) => r.args?.[0] === "startups";
$$("[data-help]").forEach((b) => { b.prepend(icon("info")); b.addEventListener("click", openActionsHelp); });   // in index.html

// ---- summary ------------------------------------------------------------------------------
// Résumé tailoring runs' progress, by job (progress.py estimates it): their fit badges become rings that fill
// (ringTile), with the step and time left under the title, moved on every second between polls by tickRuns.
// A Make résumé waiting for a free slot (queued) shows an empty dashed ring and its place in line.
const RUNP = { jobs: new Map(), done: new Set(), timer: null };
const tailoredJob = (r) => (r.args?.[0] === "tailor" ? r.args[1] : null);
const placeInLine = (n) => (n === 1 ? "next in line" : `${n}${["", "st", "nd", "rd"][n] || "th"} in line`);
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function trackRunProgress(going) {
  const was = RUNP.jobs;
  RUNP.jobs = new Map([
    ...going.filter((r) => r.status === "queued" && tailoredJob(r)).map((r) => [tailoredJob(r), { queued: r.queue_position || 1 }]),
    ...going.filter((r) => r.progress).map((r) => [r.progress.job, { ...r.progress, got: Date.now() }])]);
  const isQueued = (m, id) => (m.has(id) ? !!m.get(id).queued : null);
  // started, finished, or a queued one starting (its dashed ring becomes a filling one)
  const changed = [...new Set([...was.keys(), ...RUNP.jobs.keys()])].filter((id) => isQueued(was, id) !== isQueued(RUNP.jobs, id));
  for (const id of changed) {
    if (!RUNP.jobs.has(id) && was.get(id) && !was.get(id).queued) { RUNP.done.add(id); setTimeout(() => RUNP.done.delete(id), 4000); }   // finished: its badge springs back
    const j = S.byId.get(id);
    if (!j) continue;
    j._el = j._card = null;
    $$(`#job-detail .fit[data-job="${CSS.escape(id)}"]`).forEach((el) => el.replaceWith(fitTile(j, el.dataset.cls)));
  }
  if (changed.length) renderJobs();
  clearInterval(RUNP.timer);
  if (RUNP.jobs.size) { tickRuns(); RUNP.timer = setInterval(tickRuns, 1000); }
}
function runNow(p) {   // the share done and seconds left now, moved on from the last poll but never past the step's end
  const dt = (Date.now() - p.got) / 1000;
  return { f: Math.min(p.fraction + dt / p.total_s, p.cap), left: Math.max(p.eta_s - dt, p.eta_min_s) };
}
const timeLeft = (s) => (s < 60 ? "under a minute" : `~${Math.round(s / 60)} min`);
function paintRing(el) {
  const p = RUNP.jobs.get(el.dataset.job);
  if (!p) return;
  el.classList.toggle("queued", !!p.queued);
  if (p.queued) {
    $(".arc", el).setAttribute("stroke-dasharray", "0 100");
    $(".ring-t", el).textContent = p.queued === 1 ? "next" : `#${p.queued}`;
    el.setAttribute("aria-valuenow", "0");
    el.title = `Make résumé is waiting its turn (${placeInLine(p.queued)}): the most runs are going. It starts by itself when one finishes.`;
    return;
  }
  const { f, left } = runNow(p);
  $(".arc", el).setAttribute("stroke-dasharray", `${(f * 100).toFixed(2)} 100`);
  $(".tip", el).style.transform = `rotate(${(f * 360).toFixed(1)}deg)`;
  // the percent, and every few seconds the minutes left
  $(".ring-t", el).textContent = !reducedMotion() && Math.floor(Date.now() / 3200) % 2 ? (left < 60 ? "<1m" : `${Math.round(left / 60)}m`) : `${Math.round(f * 100)}%`;
  el.setAttribute("aria-valuenow", String(Math.round(f * 100)));
  el.title = `Making the résumé: ${p.step} (step ${p.n} of ${p.of}). About ${timeLeft(left)} left.`;
}
function paintRunStep(el) {
  const p = RUNP.jobs.get(el.dataset.job);
  if (!p) return;
  el.classList.toggle("queued", !!p.queued);
  if (p.queued) {
    el.replaceChildren(h("span", { class: "dot", "aria-hidden": "true" }), h("b", {}, "Waiting its turn"),
      h("span", { class: "eta", title: "Starts by itself when one of the runs going finishes" }, `· ${placeInLine(p.queued)}`));
    return;
  }
  const { left } = runNow(p);
  el.replaceChildren(h("span", { class: "dot", "aria-hidden": "true" }), h("b", {}, p.step),
    h("span", { class: "eta" + (p.slow ? " slow" : ""), title: p.slow ? "This step is taking longer than usual" : null }, `· ${timeLeft(left)} left`));
}
function tickRuns() {
  $$(".run-ring[data-job]").forEach(paintRing);
  $$(".run-step[data-job]").forEach(paintRunStep);
  const eta = $("#run-indicator .run-eta");
  if (eta) { const p = RUNP.jobs.get(eta.dataset.job); if (p && !p.queued) eta.textContent = ` · ${timeLeft(runNow(p).left)} left`; }
}
const ringTile = (j, cls) => {
  const el = h("div", { class: `fit run-ring ${cls}`, "data-job": j.id, "data-cls": cls, role: "progressbar", "aria-label": "Make résumé progress",
    "aria-valuemin": "0", "aria-valuemax": "100",
    html: '<svg viewBox="0 0 40 40" aria-hidden="true"><circle class="trk" cx="20" cy="20" r="16.5"/>'
      + '<circle class="arc" cx="20" cy="20" r="16.5" pathLength="100" stroke-dasharray="0 100"/>'
      + '<g class="tip"><circle cx="36.5" cy="20" r="3.2"/></g></svg><span class="ring-t"></span>' });
  paintRing(el);
  return el;
};
const runStep = (j) => RUNP.jobs.has(j.id) && (() => { const el = h("div", { class: "run-step", "data-job": j.id }); paintRunStep(el); return el; })();

async function loadSummary() {
  const was = S.summary?.active_runs || [];
  try {
    S.summary = await api("/api/summary");
  } catch (e) {
    return;                                   // can't connect: request() has shown the banner
  }
  const off = $("#offline");
  if (!off.classList.contains("hidden")) {   // back after an outage: reload what failed while it was down
    off.classList.add("hidden");
    loadJobs().then(() => { if (S.selected && !S.details.has(S.selected) && S.byId.get(S.selected)?.local) loadDetail(S.selected); });
  }
  applyScoring(S.summary.scoring);
  const c = S.summary.credits;
  $("#credits").textContent = `${c.used} / ${c.allowance} credits`;
  $("#backend").textContent = "AI: " + (S.summary.backend_label || S.summary.backend);
  const ind = $("#run-indicator");
  const going = S.summary.active_runs;
  trackRunProgress(going);
  const live = going.filter((r) => r.status !== "queued"), waiting = going.length - live.length;
  ind.classList.toggle("hidden", !going.length);
  if (going.length) {
    ind.replaceChildren(spinner(), " ", live.length === 1 ? live[0].label : `${live.length} runs`,
      ...(live.length === 1 && live[0].progress && !waiting ? [h("span", { class: "run-eta", "data-job": live[0].progress.job })] : []),
      ...(waiting ? [h("span", { class: "run-wait" }, ` · ${waiting} waiting`)] : []));
    ind.title = going.map((r) => (r.status === "queued" ? `Waiting: ${r.label}` : r.label)).join("\n");
    ind.onclick = () => { showTab("runs"); selectRun(going[0].id); };
  }
  // A run this page wasn't following (started before a reload, or beside the one shown) just ended: pick up what it wrote.
  const ids = new Set(going.map((r) => r.id));
  const ended = was.filter((r) => !ids.has(r.id));
  if (ended.length && S.tab === "runs") loadRuns();
  $("#count-startups").textContent = S.summary.startups?.recent || "";
  renderStartupState();
  if (ended.some(isStartupRefresh)) loadStartups({ quiet: true });
  if (ended.some((r) => S.runId !== r.id)) { S.details.clear(); loadJobs({ refresh: true }); }
}

// ---- jobs: data ---------------------------------------------------------------------------
const stageOf = (j) => j.status || "Not started";
// How the search stands, for the feedback moments: applications out the door, roles still in play.
function stageCounts() {
  const tracked = S.jobs.filter((j) => SCOPES.tracker.has(j));
  const n = (...st) => tracked.filter((j) => st.includes(stageOf(j))).length;
  return { out: n(...APPLIED_STAGES), inPlay: n("Not started", "In progress", "Blocked", "Applied", "Interviewing", "Offer"),
           applied: n("Applied"), done: n("Done"), denied: n("Denied"), ghosted: n("Ghosted"), interviewing: n("Interviewing"), offer: n("Offer") };
}
function indexJob(j) {
  j._hay = [j.title, j.company, j.location, j.mode, j.status, j.one_line, j.referral_name, j.referral_url, j.referral_note, j.funding?.line, j.funding?.stage, j.source, ...j.searches].join(" ").toLowerCase();
  j._el = j._card = null;
}
async function loadJobs({ refresh = false, wait = false, announce = false } = {}) {
  try {
    const res = await request(`/api/jobs${refresh ? "?refresh=1" : wait ? "?wait=1" : ""}`,
      { headers: S.etag ? { "If-None-Match": S.etag } : {} });
    if (res.status === 304) {       // nothing changed since the copy on screen
      if (wait) S.syncing = false;
    } else {
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      S.etag = res.headers.get("etag");
      applyJobs(data);
    }
    renderSync();
    if (announce) toast(S.notion ? "Synced with Notion." : "Notion isn't connected, so the tracker is kept on this Mac only.");
    // The server answered from the local tracker and is syncing with Notion behind it: collect the result.
    if (S.syncing && !wait) loadJobs({ wait: true });
  } catch (e) {
    toast(e.message, true);
    if (refresh) loadJobs();     // a failed sync still leaves the last synced rows to show, with the warning
  }
}
function applyJobs(data) {
  const prev = S.byId.get(S.selected);
  S.jobs = data.jobs;
  S.loaded = true;
  S.byId = new Map(S.jobs.map((j) => [j.id, j]));
  S.jobs.forEach(indexJob);
  S.notion = data.notion; S.syncing = data.syncing; S.notionError = data.notion_error; S.notionSynced = data.notion_synced;
  S.pending = data.pending || 0;
  renderNotionLink();
  renderJobs();
  const cur = S.byId.get(S.selected);
  if (S.selected && !cur) { S.selected = null; renderDetail(); }
  else if (cur && prev && (prev.local !== cur.local || prev.tailored !== cur.tailored || prev.fit !== cur.fit
                           || prev.impact_score !== cur.impact_score)) {
    S.details.delete(cur.id);       // scored or tailored since: the panel below the header changed too
    renderDetail();
    if (cur.local) loadDetail(cur.id);
  } else if (cur && prev && (showsSent(prev) !== showsSent(cur) || prev.sent_resume !== cur.sent_resume)) {
    if (!showsSent(prev)) S.detailTabJob = null;   // just marked Applied: open the résumé sent
    renderDetail();
  } else if (cur) renderDetailHead();
  if (S.tab === "searches") renderSearches();
  if (S.tab === "dashboard") renderDashboard();
  announceGhosted();
}
// Roles the app moved to Ghosted on its own (tracker.ghost_after_days): said once, in a toast.
function announceGhosted() {
  const recent = dayKey(new Date(Date.now() - 7 * 864e5));
  const fresh = S.jobs.filter((j) => j.tracker_id && j.ghosted_on && j.ghosted_on >= recent && !S.ghostSeen.has(`${j.tracker_id}@${j.ghosted_on}`));
  if (!fresh.length) return;
  fresh.forEach((j) => S.ghostSeen.add(`${j.tracker_id}@${j.ghosted_on}`));
  S.ghostSeen = new Set([...S.ghostSeen].slice(-200));
  savePrefs();
  const names = fresh.slice(0, 3).map(jobLine).join(", ") + (fresh.length > 3 ? ` and ${fresh.length - 3} more` : "");
  toast(`Moved to Ghosted after ${ghostDays()} days with no change: ${names}. Heard back? Pick its status.`);
}
const ghostDays = () => S.summary?.ghost_after_days || 60;
// "Open in Notion" beside Sync: only on the Tracker, and only once Notion is set up and has said where the database is.
async function renderNotionLink() {
  const a = $("#open-notion");
  if (S.notion && S.notionUrl === undefined) {
    S.notionUrl = null;                       // asked once; a failed ask is tried again on the next load of the page
    try { S.notionUrl = (await api("/api/notion/link")).url || null; } catch { /* no link until it answers */ }
    if (!S.notionUrl) S.notionUrl = undefined;
  }
  const url = S.notion && safeHref(S.notionUrl);
  if (url) a.href = url;
  a.classList.toggle("hidden", !url || S.scope !== "tracker");
}
function renderSync() {
  const el = $("#sync-state");
  // While Notion isn't answering, ask again every minute so the list recovers without a click.
  clearTimeout(renderSync._t);
  if (S.notionError) renderSync._t = setTimeout(() => { if (!document.hidden) loadJobs(); else renderSync(); }, 60000);
  if (S.syncing) el.replaceChildren(spinner(), " Syncing with Notion…");
  else if (S.notionError) {
    const at = S.notionSynced && new Date(S.notionSynced * 1000);
    const when = at && (at.toDateString() === new Date().toDateString()
      ? at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : fmtDate(at));
    // The tracker itself is local, so everything still works; this only says the Notion copy is behind.
    const waiting = S.pending ? ` ${plural(S.pending, "change")} here will be sent when it's back.` : "";
    el.replaceChildren(h("span", { class: "warn", title: S.notionError },
      `Notion isn't answering${when ? ` (last synced ${when})` : ""}.${waiting}`));
  }
  else if (S.pending) el.replaceChildren(`${plural(S.pending, "change")} waiting to sync to Notion`);
  else el.replaceChildren();
}

// ---- jobs: filters --------------------------------------------------------------------------
const FLAGS = { starred: (j) => j.starred, referral: (j) => !!j.referral_url, remote: (j) => j.remote, tailored: (j) => j.tailored };
// Filters with a choice of values, where a role matches any value picked: the score it shows (the full score
// where there is one, else the signal score), its kind of company (a startup is one whose round is known), and
// where it is (the server reads its country and location; "London or New York" is in the US and the UK).
// A facet with `keys` puts a role under several values; one with `key`, under one.
const FACETS = {
  score: { label: "Score", key: (j) => (score(j) == null ? "none" : String(Math.round(score(j)))),
           // every score the list holds, best first, then Unscored
           values: (keys) => keys.sort((a, b) => (a === "none") - (b === "none") || b - a),
           name: (v) => (v === "none" ? "Unscored" : `${v}/10`),
           title: (v) => (v === "none" ? "Roles with no signal or full score yet" : `Roles scoring ${v}/10: the full score where there is one, else the signal score`) },
  company: { label: "Company", key: (j) => (j.funding ? "startup" : "corp"),
             values: () => ["startup", "corp"],
             name: (v) => (v === "startup" ? "Startup" : "Corporation"),
             title: (v) => (v === "startup" ? "Roles at a startup whose funding round is known (see Search Startups)"
                                            : "Roles at companies that aren't startups with a known round") },
  region: { label: "Region", keys: (j) => (j.regions?.length ? j.regions : ["none"]),
            values: (keys) => REGION_ORDER.filter((v) => keys.includes(v)),
            name: (v) => REGION_NAMES[v][0], title: (v) => REGION_NAMES[v][1] },
};
const REGION_ORDER = ["US", "UK", "EMEA", "Anywhere", "Other", "none"];
const REGION_NAMES = {
  US: ["US", "Roles in the United States"],
  UK: ["UK", "Roles in the United Kingdom"],
  EMEA: ["EMEA", "Roles in Europe, the Middle East or Africa outside the UK (a role open across EMEA is under UK too)"],
  Anywhere: ["Anywhere", "Roles whose location says they're open worldwide"],
  Other: ["Elsewhere", "Roles only somewhere outside the US and EMEA, such as Canada, India or Latin America"],
  none: ["Not stated", "Roles that name no place (often just \"Remote\")"],
};
const facetKeys = (f, j) => (f.keys ? f.keys(j) : [f.key(j)]);
function matches(j, { stages = true, flags = true, skip = "" } = {}) {
  if (!SCOPES[S.scope].has(j)) return false;
  for (const t of S.tokens) if (!j._hay.includes(t)) return false;
  if (flags) for (const f of S.flags) if (!FLAGS[f](j)) return false;
  for (const [k, f] of Object.entries(FACETS)) if (k !== skip && S.facets[k].size && !facetKeys(f, j).some((v) => S.facets[k].has(v))) return false;
  if (!stages) return true;
  if (S.scope === "find") return FIND_FILTERS.find(([k]) => k === S.findFilter)[2](j);
  return !(S.stages.size && !S.stages.has(stageOf(j)));
}
const SORTS = {
  newest: (a, b) => (b.posted || b.first_seen).localeCompare(a.posted || a.first_seen),
  company: (a, b) => a.company.localeCompare(b.company) || a.title.localeCompare(b.title),
  // The top of the stated range in USD, highest first; pay not stated or in another currency comes last. Ties keep the fit order.
  pay: (a, b) => (b.pay_usd || 0) - (a.pay_usd || 0),
};
function visibleJobs() {
  const list = S.jobs.filter((j) => matches(j));   // the server already sorts by fit
  if (SORTS[S.sort]) list.sort(SORTS[S.sort]);
  return S.group ? grouped(list) : list;
}

// ---- jobs: grouped by company ------------------------------------------------------------------
// "Acme.com" and "acme.com, Inc." are one company; a job with no company is grouped as such.
const companyKey = (j) => (j.company || "").toLowerCase().replace(/[,.]?\s+(inc|llc|ltd|corp|corporation|co)\.?$/, "").trim() || "~";
// Each company's jobs together, in the chosen sort; companies in the order of their first job
// (so by best fit, newest, or best paid, first), or A–Z when sorting by company.
function grouped(list) {
  const groups = new Map();
  for (const j of list) {
    const k = companyKey(j);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(j);
  }
  const keys = [...groups.keys()];
  if (S.sort === "company") keys.sort((a, b) => (a === "~") - (b === "~") || a.localeCompare(b));
  return keys.flatMap((k) => groups.get(k));
}
const collapsed = (j) => S.group === "company" && S.collapsed.has(companyKey(j));
function toggleGroup(key) {
  S.collapsed.has(key) ? S.collapsed.delete(key) : S.collapsed.add(key);
  savePrefs();
  renderJobs();
}
function groupHead(key, jobs) {
  const stages = STAGES.map((st) => [st, jobs.filter((j) => stageOf(j) === st).length]).filter(([, n]) => n);
  const open = !S.collapsed.has(key);
  return h("li", { class: "group-head", "data-group": key },
    h("button", { class: "group-toggle", "aria-expanded": String(open), onclick: () => toggleGroup(key) },
      h("span", { class: "caret" }, open ? "▾" : "▸"),
      h("span", { class: "group-name" }, key === "~" ? "No company" : jobs[0].company),
      h("span", { class: "n" }, jobs.length)),
    S.scope === "tracker" && h("span", { class: "group-stages" },
      ...stages.map(([st, n]) => h("span", { class: "tag stage-tag", "data-stage": st, title: st }, `${n} ${st}`))));
}
const facetsOn = () => Object.values(S.facets).some((v) => v.size);
const clearFacets = () => Object.values(S.facets).forEach((v) => v.clear());
const filtersOn = () => S.tokens.length || S.flags.size || facetsOn() || (S.scope === "find" ? S.findFilter !== "new" : S.stages.size);
function clearFilters() {
  S.query = ""; $("#job-filter").value = "";
  S.stages.clear(); S.flags.clear(); clearFacets(); S.findFilter = "new";
  renderJobs();
}
const boardMode = () => S.scope === "tracker" && S.view === "board";
function applyLayout() {
  const body = $("#jobs-body");
  body.classList.toggle("view-list", !boardMode());
  body.classList.toggle("view-board", boardMode());
  body.classList.toggle("detail-open", S.drawer);
}
function setScope(scope) {
  S.scope = scope; S.drawer = false;
  S.picks.clear();
  for (const [sel, on] of [["#stage-wrap", "tracker"], ["#view-seg", "tracker"], ["#refresh-jobs", "tracker"],
                           ["#find-seg", "find"], ["#run-searches", "find"]]) $(sel).classList.toggle("hidden", scope !== on);
  $("#job-filter").placeholder = scope === "find" ? "Search new roles, companies, locations" : "Search tracked roles, companies, locations";
  applyLayout();
  renderNotionLink();
  renderJobs();
  // Keep this list's own selection if it's still shown; otherwise open its first role.
  const keep = S.visible.find((j) => j.id === S.sel[scope]);
  const next = keep || (boardMode() ? null : S.visible.find((j) => j.tailored) || S.visible[0]);
  if (next) { selectJob(next.id); next._el?.scrollIntoView({ block: "nearest" }); }
  else { S.selected = null; renderDetail(); }
}
function renderCounts() {
  const open = S.jobs.filter((j) => SCOPES.tracker.has(j) && ["Not started", "In progress", "Blocked"].includes(stageOf(j))).length;
  const found = S.jobs.filter((j) => SCOPES.find.has(j) && !j.dismissed).length;
  $("#count-tracker").textContent = open || "";
  $("#count-find").textContent = found || "";
}
function renderFindFilters() {
  $("#find-seg").replaceChildren(...FIND_FILTERS.map(([k, label, test]) => {
    const n = S.jobs.filter((j) => matches(j, { stages: false }) && test(j)).length;
    return h("button", { class: S.findFilter === k ? "active" : "", onclick: () => { S.findFilter = k; renderJobs(); } },
      label, " ", h("b", {}, n, ofAll(n, S.jobs.filter((j) => SCOPES.find.has(j) && test(j)).length)));
  }));
}

// With a search or a chip on, the stage counts are of the roles showing; this says so: "1/10", 1 of 10 in all.
const facetNames = () => Object.entries(FACETS).filter(([k]) => S.facets[k].size).map(([k, f]) =>
  k === "score" ? `Score ${f.values([...S.facets[k]]).map((v) => (v === "none" ? "unscored" : v)).join(", ")}` : [...S.facets[k]].map(f.name).join(" or "));
const narrowedBy = () => [...[...S.flags].map((f) => FLAG_NAMES[f]), ...facetNames(), ...(S.query.trim() ? [`“${S.query.trim()}”`] : [])];
const ofAll = (n, total) => (narrowedBy().length && n !== total ? h("small", { class: "of" }, `/${total}`) : null);
function renderStages() {
  const counts = Object.fromEntries(STAGES.map((s) => [s, 0]));
  const totals = Object.fromEntries(STAGES.map((s) => [s, 0]));
  for (const j of S.jobs) {
    if (matches(j, { stages: false })) counts[stageOf(j)]++;
    if (SCOPES[S.scope].has(j)) totals[stageOf(j)]++;
  }
  const by = narrowedBy().join(", ");
  $("#stages").replaceChildren(...STAGES.map((s) => {
    const b = h("button", {
      class: "stage", "data-stage": s, "data-kind": PATH.includes(s) ? "path" : "side", "aria-pressed": String(S.stages.has(s)),
      title: (by && counts[s] !== totals[s] ? `${counts[s]} of your ${totals[s]} ${s} roles match ${by}. ` : "") + `${STAGE_BLURB[s]}. Click to show only these roles.`,
      onclick: () => { S.stages.has(s) ? S.stages.delete(s) : S.stages.add(s); renderJobs(); } },
      h("span", { class: "n" }, counts[s], ofAll(counts[s], totals[s])), h("span", { class: "l" }, s));
    b.style.setProperty("--n", counts[s]);      // a look can size the main line's stages by their count
    return b;
  }));
  // The strip under the stages is the same counts drawn to scale: where the pipeline's weight sits.
  $("#stage-meter").replaceChildren(...STAGES.filter((s) => counts[s]).map((s) => {
    const seg = h("span", { "data-stage": s });
    seg.style.flexGrow = counts[s];
    return seg;
  }));
}
function renderFlags() {
  $$("#flags .chip").forEach((b) => {
    const f = b.dataset.flag;
    b.setAttribute("aria-pressed", String(S.flags.has(f)));
    $("b", b).textContent = S.jobs.reduce((n, j) => n + (matches(j) && FLAGS[f](j) ? 1 : 0), 0);
  });
}
// Each value's count is of the roles the other filters leave, so it says what picking it would show.
function renderFacets() {
  const inScope = S.jobs.filter((j) => SCOPES[S.scope].has(j));
  for (const [k, f] of Object.entries(FACETS)) {
    const shown = S.jobs.filter((j) => matches(j, { skip: k }));
    const vals = f.values([...new Set([...inScope.flatMap((j) => facetKeys(f, j)), ...S.facets[k]])]);
    $(`#${k}-chips`).replaceChildren(...(inScope.length ? [h("span", { class: "facet-label" }, f.label)] : []), ...(inScope.length ? vals : []).map((v) => {
      const n = shown.reduce((c, j) => c + (facetKeys(f, j).includes(v) ? 1 : 0), 0);
      return h("button", { class: "chip", "data-facet": k, "data-value": v, "data-fit": k === "score" ? fitClass(v === "none" ? null : +v) || "none" : null,
        "aria-pressed": String(S.facets[k].has(v)), title: f.title(v),
        onclick: () => { S.facets[k].has(v) ? S.facets[k].delete(v) : S.facets[k].add(v); renderJobs(); } },
        k === "score" && v !== "none" ? h("span", { class: "sc" }, v) : f.name(v), " ", h("b", {}, n));
    }));
  }
}
$$("#flags .chip").forEach((b) => b.addEventListener("click", () => {
  const f = b.dataset.flag;
  S.flags.has(f) ? S.flags.delete(f) : S.flags.add(f);
  renderJobs();
}));
$("#job-filter").addEventListener("input", (e) => {
  S.query = e.target.value;
  clearTimeout(renderJobs._t);
  renderJobs._t = setTimeout(renderJobs, 60);
});
$("#job-sort").addEventListener("change", (e) => { S.sort = e.target.value; savePrefs(); renderJobs(); });
$("#job-group").addEventListener("change", (e) => { S.group = e.target.value; savePrefs(); renderJobs(); });
$$("#view-seg button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
function setView(view) {
  S.view = view; S.drawer = false;
  savePrefs();
  $$("#view-seg button").forEach((x) => x.classList.toggle("active", x.dataset.view === view));
  applyLayout();
  renderJobs();
}

// ---- jobs: folding -------------------------------------------------------------------------------
// Three parts fold on their own so the selected role, and above all its résumé and the chat, get the room:
// the filters to one strip, the list to a rail of scores, the role's header to one line.
const FOLDS = ["filters", "list", "head"];
const FLAG_NAMES = { starred: "Starred", referral: "Has referral", remote: "Remote", tailored: "Has résumé" };
const allFolded = () => FOLDS.every((k) => S.fold[k]);
function applyFolds() {
  const tab = $("#tab-jobs");
  for (const k of FOLDS) tab.classList.toggle(`fold-${k}`, S.fold[k]);
  $("#fold-filters").setAttribute("aria-expanded", String(!S.fold.filters));
  $("#fold-list").setAttribute("aria-expanded", String(!S.fold.list));
  $$(".fold-all").forEach((b) => { b.replaceChildren(icon(allFolded() ? "down" : "up"), allFolded() ? "Unfold everything" : "Fold everything"); });
  renderFoldStrip();
  renderRail();
  renderDetailHead();
}
function setFold(key, on) {
  S.fold[key] = on;
  savePrefs();
  applyFolds();
}
function foldAll() {
  const on = !allFolded();
  for (const k of FOLDS) S.fold[k] = on;
  savePrefs();
  applyFolds();
}
const foldAllButton = () => h("button", { class: "ghost small fold-all", title: "Fold the filters, the list and this header (⌘\\)",
  onclick: foldAll }, icon(allFolded() ? "down" : "up"), allFolded() ? "Unfold everything" : "Fold everything");
// A look that opens a stage button into a pill with its name (Sorbet) needs the name's and the count's widths
// to animate to; a look that keeps the name hidden gets it as a tooltip instead.
function miniStageSize(e) {
  const b = e.currentTarget, name = $(".lab > span", b);
  b.style.setProperty("--num-w", `${$(".num", b).offsetWidth}px`);
  b.style.setProperty("--lab-w", `${name.offsetWidth}px`);
  b.title = getComputedStyle($(".lab", b)).display === "none" ? `${b.dataset.stage}: ${$(".num", b).textContent}. Click to show only these roles.` : "";
}
// The filters, folded: the stage counts as small buttons that still filter, and what's showing.
function renderFoldStrip() {
  const strip = $("#fold-strip");
  if (!S.fold.filters) { strip.replaceChildren(); return; }
  const minis = S.scope === "tracker"
    ? [...PATH, ...EXITS].map((s) => {
        const b = $(`#stages .stage[data-stage="${s}"]`);
        const n = b ? $(".n", b).textContent : "0";
        return h("button", { class: "mini-stage", "data-stage": s, "data-kind": PATH.includes(s) ? "path" : "side",
          "aria-pressed": String(S.stages.has(s)), "aria-label": `${s}: ${n}`, onclick: () => b?.click(),
          onpointerenter: miniStageSize, onfocus: miniStageSize },
          h("span", { class: "goo", "aria-hidden": "true" }, h("i", { class: "b1" }), h("i", { class: "b2" }), h("i", { class: "b3" })),
          h("span", { class: "num" }, n), h("span", { class: "lab", "aria-hidden": "true" }, h("span", {}, s)));
      })
    : $$("#find-seg button").map((b) => h("button", { class: "chip", "aria-pressed": String(b.classList.contains("active")), onclick: () => b.click() },
        b.firstChild.textContent.trim(), " ", h("b", {}, $("b", b)?.textContent ?? "")));
  const showing = [...(S.scope === "tracker" ? [...S.stages] : []), ...narrowedBy()];
  strip.replaceChildren(
    h("button", { class: "ghost small fold-open", "aria-expanded": "false", title: "Show the search and filters", onclick: () => setFold("filters", false) },
      icon("down"), "Filters"),
    h("div", { class: "minis", role: "group", "aria-label": S.scope === "tracker" ? "Filter by stage" : "Filter new roles" }, ...minis),
    h("span", { class: "fold-what" }, showing.length ? ["Showing ", h("b", {}, showing.join(", ")), " · "] : "",
      `${S.visible.length} of ${plural(S.jobs.filter((j) => SCOPES[S.scope].has(j)).length, "role")}`,
      filtersOn() ? [" · ", h("button", { class: "link-btn", onclick: clearFilters }, "Clear filters")] : ""),
    h("button", { class: "ghost small fold-search", title: "Search (/)", onclick: () => { setFold("filters", false); $("#job-filter").focus(); } },
      "Search ", h("kbd", {}, "/")));
}
// The list, folded: each shown role as its score, under its company when grouped. Hover for the title.
function renderRail() {
  const box = $("#rail-jobs");
  if (!S.fold.list || boardMode()) { box.replaceChildren(); return; }
  const items = [];
  let last = null;
  for (const j of S.visible) {
    if (S.group && companyKey(j) !== last) {
      last = companyKey(j);
      items.push(h("div", { class: "rail-co", title: j.company || "No company" }, j.company || "—"));
    }
    const tile = fitTile(j);
    tile.removeAttribute("title");
    items.push(h("button", { class: "rail-job" + (j.id === S.selected ? " selected" : ""), "data-id": j.id, "data-stage": stageOf(j),
      title: [j.title, [j.company, j.status].filter(Boolean).join(" · ")].filter(Boolean).join("\n"), onclick: () => selectJob(j.id, true) }, tile));
  }
  box.replaceChildren(...items);
}
$("#fold-filters").addEventListener("click", () => setFold("filters", true));
$("#fold-list").addEventListener("click", () => setFold("list", true));
$("#unfold-list").addEventListener("click", () => setFold("list", false));

$("#run-searches").addEventListener("click", () => showTab("searches"));
$("#refresh-jobs").addEventListener("click", () => loadJobs({ refresh: true, announce: true }));

// ---- dashboard -------------------------------------------------------------------------------
// Counted from the tracked jobs on screen. applied_on is the day a job reached an applied stage, or its
// Due/Submitted day in Notion (see _applied_on in jobpipe/tracker.py).
const DASH_STAGES = [["In progress", "In progress"], ["Applied", "Applied"],
                     ["Interviewing", "Interviewing"], ["Offer", "Offer"], ["Denied", "Rejected"], ["Ghosted", "Ghosted"]];
const DASH_RANGES = [["14", "14 days"], ["30", "30 days"], ["all", "All"]];
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const keyDate = (k) => { const [y, m, d] = k.split("-").map(Number); return new Date(y, m - 1, d); };
const longDay = (k) => keyDate(k).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
const jobLine = (j) => (j.company && !j.title.includes(j.company) ? `${j.title} — ${j.company}` : j.title);

function renderDashboard() {
  const root = $("#dash");
  if (!S.loaded) { root.replaceChildren(h("p", { class: "dash-empty" }, "Loading the tracker…")); return; }
  const tracked = S.jobs.filter((j) => j.tracker_id);
  const applied = tracked.filter((j) => APPLIED_STAGES.includes(stageOf(j)));
  const now = new Date();
  const ago = (n) => dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - n));
  const today = ago(0);
  const lastWeek = applied.filter((j) => j.applied_on && j.applied_on > ago(7) && j.applied_on <= today).length;
  const todayN = applied.filter((j) => j.applied_on === today).length;

  const hero = h("section", { class: "card" },
    h("div", { class: "hero-label" }, "Jobs applied to"),
    h("div", { class: "hero-n" }, applied.length.toLocaleString()),
    h("p", { class: "hero-sub" }, h("b", {}, lastWeek), " in the last 7 days · ", h("b", {}, todayN), " today"));

  const count = (st) => tracked.filter((j) => stageOf(j) === st).length;
  const rows = DASH_STAGES.map(([st, label]) => [st, label, count(st)]);
  const top = Math.max(1, ...rows.map((r) => r[2]));
  const others = ["Not started", "Blocked", "Done", "Not Applying"].map((st) => [st, count(st)]).filter(([, n]) => n);
  const stages = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, "By stage")),
    h("ul", { class: "stage-bars" }, ...rows.map(([st, label, n]) => {
      const bar = h("span", { class: "bar" + (n ? "" : " zero") });
      bar.style.width = `calc((100% - 36px) * ${n / top})`;
      return h("li", {}, h("button", { "data-stage": st, title: `Show ${label} jobs in the Tracker`,
        "aria-label": `${label}: ${n}. Show them in the Tracker`,
        onclick: () => { S.stages = new Set([st]); showTab("tracker"); } },
        h("span", { class: "l" }, label), h("span", { class: "track" }, bar, h("span", { class: "v" }, n))));
    })),
    others.length > 0 && h("p", { class: "dash-foot" }, "Also in the tracker: ",
      others.map(([st, n]) => `${n} ${st}`).join(" · "), others.some(([st]) => st === "Done") ? ". Done counts as applied." : "."));

  root.replaceChildren(hero, stages, perDayCard(applied, today), flowCard());
}

function perDayCard(applied, today) {
  const dated = applied.filter((j) => j.applied_on && j.applied_on <= today);
  const undated = applied.length - dated.length;
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Range" }, ...DASH_RANGES.map(([k, label]) =>
    h("button", { class: S.dashRange === k ? "active" : "", "aria-pressed": String(S.dashRange === k),
      onclick: () => { S.dashRange = k; savePrefs(); renderDashboard(); } }, label)));
  const card = h("section", { class: "card wide" },
    h("div", { class: "card-head" }, h("h3", {}, "Applications per day"), seg));
  if (!dated.length) {
    card.append(h("p", { class: "dash-empty" }, "No applications with a date yet. Mark a job Applied in the Tracker and it shows here."));
    return card;
  }
  const by = new Map();
  for (const j of dated) {
    if (!by.has(j.applied_on)) by.set(j.applied_on, []);
    by.get(j.applied_on).push(j);
  }
  const end = keyDate(today);
  const start = S.dashRange === "all"
    ? keyDate(dated.reduce((m, j) => (j.applied_on < m ? j.applied_on : m), today))
    : new Date(end.getFullYear(), end.getMonth(), end.getDate() - Number(S.dashRange) + 1);
  const days = [];
  for (const d = start; d <= end; d.setDate(d.getDate() + 1)) days.push({ key: dayKey(d), jobs: by.get(dayKey(d)) || [] });

  const chart = h("div", { class: "day-chart" });
  card.append(chart);
  requestAnimationFrame(() => drawDays(chart, days));
  const shown = days.filter((d) => d.jobs.length).reverse();
  const total = shown.reduce((n, d) => n + d.jobs.length, 0);
  const before = dated.length - total;
  card.append(h("p", { class: "dash-foot" }, `${plural(total, "application")} in ${plural(days.length, "day")}`,
    before ? ` · ${before} earlier` : "",
    undated ? ` · ${undated} applied ${undated === 1 ? "job has" : "jobs have"} no date, so ${undated === 1 ? "it isn't" : "they aren't"} charted` : ""));
  card.append(h("details", { class: "dash-table" }, h("summary", {}, "Show as a table"),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Day"), h("th", {}, "Applied"), h("th", {}, "Jobs"))),
      h("tbody", {}, ...shown.map((d) => h("tr", {}, h("td", {}, longDay(d.key)), h("td", { class: "n" }, d.jobs.length),
        h("td", {}, d.jobs.map(jobLine).join("; "))))))));
  return card;
}

// One column per day, drawn to the card's width: hairline grid, the busiest day labeled, the rest on hover or focus.
function drawDays(box, days) {
  const svg = (tag, attrs = {}) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  const W = Math.max(280, box.clientWidth || 800), H = 220;
  const L = 30, R = 6, T = 20, B = 24;
  const pw = W - L - R, ph = H - T - B, y0 = T + ph;
  const peak = Math.max(...days.map((d) => d.jobs.length));
  const step = Math.max(1, Math.ceil(peak / 4));
  const yTop = Math.max(step, Math.ceil(peak / step) * step);
  const y = (n) => y0 - (n / yTop) * ph;
  const band = pw / days.length;
  const cw = Math.max(2, Math.min(24, band - 2, band * 0.72));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
    "aria-label": `Applications per day, ${longDay(days[0].key)} to ${longDay(days[days.length - 1].key)}, at most ${peak} a day` });
  const grid = svg("g", { class: "grid" }), axis = svg("g", { class: "axis" }), marks = svg("g"), hits = svg("g");
  for (let v = 0; v <= yTop; v += step) {
    grid.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }));
    const t = svg("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = v;
    axis.append(t);
  }
  const every = Math.max(1, Math.ceil(64 / band));      // date labels at least 64px apart, ending on today
  const peakAt = days.findIndex((d) => d.jobs.length === peak);
  const tip = h("div", { class: "dash-tip hidden", role: "status" });
  days.forEach((d, i) => {
    const n = d.jobs.length, cx = L + band * i + band / 2, x = cx - cw / 2;
    if (n) {
      const top = y(n), r = Math.min(4, cw / 2, y0 - top);
      marks.append(svg("path", { class: "col",
        d: `M${x},${y0}V${top + r}Q${x},${top} ${x + r},${top}H${x + cw - r}Q${x + cw},${top} ${x + cw},${top + r}V${y0}Z` }));
    }
    if (i === peakAt && peak) {
      const t = svg("text", { class: "cap", x: cx, y: y(n) - 6 });
      t.textContent = n;
      marks.append(t);
    }
    if ((days.length - 1 - i) % every === 0) {
      const t = svg("text", { x: cx, y: H - 6, "text-anchor": "middle" });
      t.textContent = fmtDate(keyDate(d.key));
      axis.append(t);
    }
    const hit = svg("rect", { class: "hit", x: L + band * i, y: T, width: band, height: ph, tabindex: "0",
      "aria-label": `${longDay(d.key)}: ${plural(n, "application")}` });
    const show = () => {
      const names = d.jobs.slice(0, 4).map((j) => h("li", {}, jobLine(j)));
      tip.replaceChildren(h("div", { class: "tv" }, plural(n, "application")), h("div", { class: "td" }, longDay(d.key)));
      if (names.length) tip.append(h("ul", {}, ...names, d.jobs.length > 4 && h("li", {}, `and ${d.jobs.length - 4} more`)));
      tip.classList.remove("hidden");
      const k = box.clientWidth / W, tw = tip.offsetWidth, above = y(n) * k - tip.offsetHeight - 10;
      if (above >= 0) {          // over the column, else beside it so the column stays in view
        tip.style.left = `${Math.min(Math.max(0, cx * k - tw / 2), box.clientWidth - tw)}px`;
        tip.style.top = `${above}px`;
      } else {
        const right = (cx + band / 2) * k + 6;
        tip.style.left = `${right + tw <= box.clientWidth ? right : Math.max(0, (cx - band / 2) * k - tw - 6)}px`;
        tip.style.top = "0px";
      }
    };
    const hide = () => tip.classList.add("hidden");
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
    hits.append(hit);
  });
  grid.append(svg("line", { x1: L, x2: W - R, y1: y0, y2: y0 }));
  root.append(grid, marks, axis, hits);
  box.replaceChildren(root, tip);
}
let dashResize;
window.addEventListener("resize", () => {
  clearTimeout(dashResize);
  dashResize = setTimeout(() => { if (S.tab === "dashboard") renderDashboard(); }, 120);
});

// ---- application flow -------------------------------------------------------------------------
// /api/flow (jobpipe/web/flow.py) counts where every application went: Applied, on to Interviewed, and where each
// ended. The Sankey is drawn to the card's width in the look's stage colours; a stage opens its jobs in the Tracker.
const FLOW_NODES = [
  ["applied", 0, "Applied", APPLIED_STAGES], ["interviewed", 1, "Interviewed", ["Interviewing", "Offer"]],
  ["offer", 2, "Offer", ["Offer"]], ["interviewing", 2, "Still interviewing", ["Interviewing"]],
  ["rejected", 2, "Rejected", ["Denied"]], ["ghosted", 2, "Ghosted", ["Ghosted"]], ["no_reply", 2, "No reply yet", ["Applied", "Done"]],
];
const flowValue = (f, id) => ({ applied: f.applications, offer: f.offers })[id] ?? f[id];

// Columns of nodes stacked from the top, one scale for all; links leave a node in the order given and arrive in the
// order they left, so they don't cross at the target. o: left, right (label room), nw (node width), gap, step (pixel stairs).
function sankey(nodes, links, W, H, o) {
  nodes = nodes.filter((n) => n.value > 0);
  const ids = new Set(nodes.map((n) => n.id));
  links = links.filter((l) => l.n > 0 && ids.has(l.from) && ids.has(l.to)).map((l) => ({ ...l }));
  const last = Math.max(0, ...nodes.map((n) => n.col));
  let k = Infinity;
  for (let c = 0; c <= last; c++) {
    const col = nodes.filter((n) => n.col === c);
    if (col.length) k = Math.min(k, (H - o.gap * (col.length - 1)) / col.reduce((s, n) => s + n.value, 0));
  }
  const by = {};
  for (let c = 0; c <= last; c++) {
    let y = 0;
    for (const n of nodes.filter((n) => n.col === c)) {
      by[n.id] = { ...n, x: o.left + (W - o.left - o.right - o.nw) * (last ? c / last : 0), y, w: o.nw, h: n.value * k };
      y += n.value * k + o.gap;
    }
  }
  for (const n of Object.values(by)) {
    let at = n.y;
    for (const l of links.filter((l) => l.from === n.id)) { l.sy = at; at += l.n * k; }
  }
  for (const n of Object.values(by)) {
    let at = n.y;
    for (const l of links.filter((l) => l.to === n.id).sort((a, b) => a.sy - b.sy)) { l.ty = at; at += l.n * k; }
  }
  for (const l of links) {
    const s = by[l.from], t = by[l.to], x0 = s.x + s.w, x1 = t.x, xm = (x0 + x1) / 2, th = l.n * k, sy = l.sy, ty = l.ty;
    l.d = o.step ? stairs(x0, x1, sy, ty, th)
      : `M${x0},${sy}C${xm},${sy} ${xm},${ty} ${x1},${ty}V${ty + th}C${xm},${ty + th} ${xm},${sy + th} ${x0},${sy + th}Z`;
  }
  return { nodes: Object.values(by), links, last };
}

// The S-curve in pixel steps: eight flat runs, each a little further along the ease from sy to ty.
function stairs(x0, x1, sy, ty, th, n = 8) {
  const r = Math.round, xs = [...Array(n + 1)].map((_, i) => r(x0 + (x1 - x0) * i / n));
  const ys = [...Array(n)].map((_, i) => { const u = i / (n - 1); return r(sy + (ty - sy) * u * u * (3 - 2 * u)); });
  let d = `M${xs[0]},${ys[0]}`;
  for (let i = 0; i < n; i++) d += `H${xs[i + 1]}` + (i < n - 1 ? `V${ys[i + 1]}` : "");
  d += `V${r(ty + th)}`;
  for (let i = n - 1; i >= 0; i--) d += `H${xs[i]}` + (i > 0 ? `V${r(ys[i - 1] + th)}` : "");
  return d + "Z";
}

// Right-hand labels at least `gap` apart, pushed down from the top and back up from the bottom.
function spread(ys, gap, max) {
  const out = ys.slice();
  for (let i = 1; i < out.length; i++) out[i] = Math.max(out[i], out[i - 1] + gap);
  for (let i = out.length - 1; i >= 0; i--) out[i] = Math.min(out[i], (i === out.length - 1 ? max : out[i + 1] - gap));
  return out;
}

function flowCard() {
  const card = h("section", { class: "card wide flow-card" });
  if (S.flow && S.flowFor === S.etag) fillFlowCard(card, S.flow);
  else {
    card.append(h("div", { class: "card-head" }, h("h3", {}, "Application flow")), h("p", { class: "dash-empty" }, "Counting…"));
    const want = S.etag;
    api("/api/flow").then((f) => { S.flow = f; S.flowFor = want; fillFlowCard(card, f); })
      .catch((e) => card.replaceChildren(h("div", { class: "card-head" }, h("h3", {}, "Application flow")), h("p", { class: "dash-empty" }, e.message)));
  }
  return card;
}

function fillFlowCard(card, f) {
  const since = f.start ? `, since ${fmtDate(keyDate(f.start))}` : "";
  const head = h("div", { class: "card-head" },
    h("div", { class: "flow-title" }, h("h3", {}, "Application flow"),
      h("span", { class: "muted" }, f.applications ? `Where your ${plural(f.applications, "application")} went${since}` : "")),
    f.applications > 0 && h("button", { class: "primary flow-share", onclick: () => openShare(f) }, icon("share"), "Share your search"));
  if (!f.applications) {
    card.replaceChildren(head, h("p", { class: "dash-empty" }, "No applications yet. Mark a job Applied in the Tracker and its path shows here."));
    return;
  }
  const chart = h("div", { class: "flow-chart" });
  const stat = (n, label, cls = "") => h("div", { class: "flow-stat " + cls }, h("b", {}, n), h("span", {}, label));
  const named = Object.fromEntries(FLOW_NODES.map(([id, , label]) => [id, label]));
  const rows = f.links.filter((l) => l.n);
  card.replaceChildren(head, chart,
    h("div", { class: "flow-stats" }, stat(f.applications, plural(f.applications, "application").replace(/^\d+ /, "")),
      stat(f.companies_interviewed, `${f.companies_interviewed === 1 ? "company" : "companies"} interviewed`),
      stat(pct(f.interview_rate), "interview rate"), stat(pct(f.heard_back), "heard back"),
      stat(f.offers, f.offers === 1 ? "offer" : "offers", "offers")),
    h("p", { class: "dash-foot" }, "Rejected and Ghosted are split by how far each job got first. Click a stage to see its jobs in the Tracker."),
    h("details", { class: "dash-table" }, h("summary", {}, "Show as a table"),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "From"), h("th", {}, "To"), h("th", {}, "Jobs"))),
        h("tbody", {}, ...rows.map((l) => h("tr", {}, h("td", {}, named[l.from]), h("td", {}, named[l.to]), h("td", { class: "n" }, l.n)))))));
  requestAnimationFrame(() => drawFlow(chart, f));
}

function drawFlow(box, f) {
  const svg = (tag, attrs = {}) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  const W = Math.max(320, box.clientWidth || 800), narrow = W < 620, H = narrow ? 260 : 300, T = 8;
  const L = narrow ? 64 : 100, R = narrow ? 118 : 160, LH = 20;
  const lay = sankey(FLOW_NODES.map(([id, col, label]) => ({ id, col, label, value: flowValue(f, id) })), f.links,
    W, H - 2 * T, { left: L, right: R, nw: 12, gap: 12, step: window.Look?.current() === "arcade" });
  const named = Object.fromEntries(FLOW_NODES.map(([id, , label, st]) => [id, { label, st }]));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label":
    `Application flow: ${lay.nodes.map((n) => `${n.value} ${n.label}`).join(", ")}` });
  const g = svg("g", { transform: `translate(0,${T})` });
  for (const l of lay.links) {
    const p = svg("path", { class: "link", "data-k": l.to, d: l.d });
    const t = svg("title");
    t.textContent = `${l.n} ${named[l.from].label} → ${named[l.to].label}`;
    p.append(t);
    g.append(p);
  }
  const ends = lay.nodes.filter((n) => n.col === lay.last && lay.last > 0);
  const endY = spread(ends.map((n) => n.y + n.h / 2), LH, H - 2 * T - LH / 2 + 4);
  for (const n of lay.nodes) {
    const node = svg("g", { class: "node", "data-k": n.id, tabindex: "0", role: "button",
      "aria-label": `${n.label}: ${n.value}. Show them in the Tracker` });
    const open = () => { S.stages = new Set(named[n.id].st); showTab("tracker"); };
    node.addEventListener("click", open);
    node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    node.append(svg("rect", { x: n.x, y: n.y, width: n.w, height: Math.max(2, n.h), rx: 3 }));
    const text = svg("text", {});
    const num = svg("tspan", { class: "n" });
    num.textContent = n.value;
    if (n.col === 0) {                       // number over label, left of the bar
      Object.assign(text.dataset, { side: "left" });
      text.setAttribute("text-anchor", "end");
      num.setAttribute("x", n.x - 8);
      num.setAttribute("y", n.y + n.h / 2 - 2);
      const lab = svg("tspan", { x: n.x - 8, dy: "1.3em" });
      lab.textContent = n.label;
      text.append(num, lab);
    } else {
      const y = n.col === lay.last ? endY[ends.indexOf(n)] : n.y + 14;
      text.setAttribute("x", n.x + n.w + 8);
      text.setAttribute("y", y + 5);
      if (n.col !== lay.last) text.classList.add("halo");
      text.append(num, ` ${n.label}`);
    }
    node.append(text);
    g.append(node);
  }
  root.append(g);
  box.replaceChildren(root);
}

// ---- share your search --------------------------------------------------------------------------
// A picture of the search to post: drawn on a canvas in the current look, here in the browser. Nothing is uploaded
// and there is no link; Save image downloads the PNG and Copy image puts it on the clipboard. Only numbers show
// until a switch adds the first name, or the offer's date, title or company.
const SHARE_STYLES = [["journey", "Journey"], ["receipt", "Receipt"], ["big", "Big number"]];
const SHARE_SIZES = [["square", "Square", "LinkedIn, Instagram", 540, 540], ["story", "Story", "Stories", 540, 960],
                     ["wide", "Wide", "X, Reddit", 800, 450]];
const SHARE_SCALE = 2;          // drawn at 540 points across, saved at twice that (1080 px)
const MONO = 'Menlo, "SF Mono", ui-monospace, monospace';
// Each look's card: its ground, ink and accent, the flows' colours (to Interviewed, to Rejected, to No reply), its type.
const CARD_SKINS = {
  sorbet: { bg: "#FF7EA9", ink: "#2A1B3D", accent: "#FFFFFF", offer: "#FFD166", node: "#2A1B3D", sub: "#2A1B3D",
    flows: ["rgba(42,27,61,.42)", "rgba(255,255,255,.42)", "rgba(255,255,255,.26)"], rule: "rgba(42,27,61,.18)",
    display: '"Fredoka", "Arial Rounded MT Bold", sans-serif', dw: 700, body: '"Nunito Sans", system-ui, sans-serif', bw: 800,
    paper: "#FFFDF8", paperInk: "#2A1B3D", paperAccent: "#D81B5E",
    decor(c, W, H, wide) {
      c.fillStyle = "#FF9FC0"; c.beginPath(); c.arc(W - 30, 30, wide ? 70 : 110, 0, 7); c.fill();
      if (!wide) { c.fillStyle = "#FFD166"; c.beginPath(); c.arc(W - 135, 75, 35, 0, 7); c.fill(); }
    } },
  transit: { bg: "#0B1F3A", ink: "#FFFFFF", accent: "#F3A9BB", offer: "#F3A9BB", node: "#FFFFFF", sub: "#CBD5E0", kick: "#DDAA00",
    flows: ["#00A4A7", "rgba(172,100,187,.8)", "rgba(255,255,255,.25)"], rule: "#4A5A70", upper: true,
    display: '"Barlow Condensed", "Arial Narrow", sans-serif', dw: 700, body: '"Barlow", "Arial Narrow", sans-serif', bw: 600,
    paper: "#FFFFFF", paperInk: "#0B1F3A", paperAccent: "#0066CC",
    decor(c, W) { c.fillStyle = "#DDAA00"; c.fillRect(0, 0, W, 10); } },
  bauhaus: { bg: "#FFD400", ink: "#000000", accent: "#FF4F1F", offer: "#FF4F1F", node: "#000000", sub: "#000000",
    flows: ["#2E6BFF", "#000000", "#FFFFFF"], rule: "#000000", ruleW: 3,
    display: '"Archivo Black", "Arial Black", sans-serif', dw: 400, body: '"Archivo", Arial, sans-serif', bw: 700,
    paper: "#FFFFFF", paperInk: "#000000", paperAccent: "#FF4F1F",
    decor(c, W, H, wide) {
      const r = wide ? 56 : 120;
      c.fillStyle = "#2E6BFF"; c.beginPath(); c.moveTo(W, 0); c.arc(W, 0, r, Math.PI / 2, Math.PI); c.closePath(); c.fill();
      c.fillStyle = "#FF4F1F"; c.fillRect(W - r - 30, 24, wide ? 16 : 26, wide ? 16 : 26);
    } },
  arcade: { bg: "#1B1B4D", ink: "#FFFFFF", accent: "#FFD500", offer: "#FFD500", node: "#FFFFFF", sub: "#FFFFFF", kick: "#16C7F2", stat: "#22D36B",
    flows: ["#FF2D95", "rgba(139,93,206,.8)", "rgba(255,255,255,.2)"], rule: "#4F4F80", step: true, pixel: true,
    display: '"Press Start 2P", "Courier New", monospace', dw: 400, body: '"Rubik", system-ui, sans-serif', bw: 500,
    paper: "#F3FCFF", paperInk: "#1B1B4D", paperAccent: "#D6136F",
    decor(c, W, H) { c.strokeStyle = "#FF2D95"; c.lineWidth = 4; c.strokeRect(10, 10, W - 20, H - 20); } },
  classic: { bg: "#FFFFFF", ink: "#15171c", accent: "#d3121f", offer: "#d3121f", node: "#15171c", sub: "#5f6672",
    flows: ["rgba(10,124,148,.4)", "rgba(184,66,58,.25)", "rgba(141,148,160,.22)"], rule: "#e1e4e9",
    display: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif', dw: 700, body: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif', bw: 600,
    paper: "#FFFFFF", paperInk: "#15171c", paperAccent: "#d3121f", paperBg: "#f3f4f6",
    decor(c, W) { c.fillStyle = "#d3121f"; c.fillRect(0, 0, W, 6); } },
};
const SHARE = { style: "journey", size: "square", name: false, date: false, title: false, company: false };

function openShare(f) {
  Object.assign(SHARE, { name: false, date: false, title: false, company: false });     // numbers only, every time
  const dlg = $("#share-dialog") || document.body.appendChild(h("dialog", { id: "share-dialog", class: "share-dialog", "aria-labelledby": "share-title" }));
  const canvas = h("canvas", { class: "share-canvas", role: "img" });
  const caption = h("p", { class: "muted share-caption" });
  const redraw = () => drawShareCard(canvas, f).then(() => {
    const on = [SHARE.name && "your name", SHARE.title && "the job title", SHARE.company && "the company", SHARE.date && "the offer date"].filter(Boolean);
    const size = SHARE_SIZES.find((s) => s[0] === SHARE.size);
    caption.textContent = `${on.length ? `Showing ${on.join(", ").replace(/, ([^,]*)$/, " and $1")}.` : "Showing numbers only."} ${size[3] * SHARE_SCALE} × ${size[4] * SHARE_SCALE} px.`;
  });
  const toggle = (key, label, hint, disabled) => h("label", { class: "share-toggle" + (disabled ? " off" : "") },
    h("span", {}, h("span", { class: "tl" }, label), hint && h("span", { class: "th" }, hint)),
    h("input", { type: "checkbox", role: "switch", disabled, onchange: (e) => { SHARE[key] = e.target.checked; redraw(); } }));
  const noOffer = !f.offer;
  const seg = (list, key, render, label) => h("div", { class: "seg share-seg", role: "group", "aria-label": label }, ...list.map((it) =>
    h("button", { type: "button", class: SHARE[key] === it[0] ? "active" : "", "aria-pressed": String(SHARE[key] === it[0]),
      onclick: (e) => {
        SHARE[key] = it[0];
        e.currentTarget.parentNode.querySelectorAll("button").forEach((b) => { b.classList.toggle("active", b === e.currentTarget); b.setAttribute("aria-pressed", String(b === e.currentTarget)); });
        redraw();
      } }, render(it))));
  const blob = () => new Promise((ok, no) => canvas.toBlob((b) => (b ? ok(b) : no(new Error("Couldn't draw the card."))), "image/png"));
  const save = async () => {
    const url = URL.createObjectURL(await blob());
    h("a", { href: url, download: `job-search-${SHARE.style}-${SHARE.size}.png` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob() })]);
      toast("Copied. Paste it into your post.");
    } catch (e) {
      toast("This browser wouldn't copy the picture. Use Save image instead.", true);
    }
  };
  dlg.replaceChildren(
    h("button", { class: "icon-btn share-close", title: "Close", "aria-label": "Close", onclick: () => dlg.close() }, icon("close")),
    h("div", { class: "share-grid" },
      h("div", { class: "share-controls" },
        h("h2", { id: "share-title" }, "Share your search"),
        h("p", { class: "muted" }, "Made on this Mac. Nothing is uploaded and there's no link: save the picture or copy it, then post it where you like."),
        h("h3", {}, "Show on your card"),
        h("p", { class: "muted share-note" }, "Numbers only until you switch something on."),
        f.first_name && toggle("name", `First name (${f.first_name})`),
        toggle("date", "Offer date", noOffer ? "Once a job reaches Offer" : "", noOffer),
        toggle("title", "Job title", noOffer ? "Once a job reaches Offer" : "The role you were offered", noOffer),
        toggle("company", "Company", noOffer ? "Once a job reaches Offer" : "Worth waiting until you've told your current employer.", noOffer),
        h("h3", {}, "Style"),
        seg(SHARE_STYLES, "style", ([, label]) => label, "Style"),
        h("h3", {}, "Size"),
        seg(SHARE_SIZES, "size", ([, label, where]) => [h("span", {}, label), h("small", {}, where)], "Size")),
      h("div", { class: "share-preview" },
        h("div", { class: "share-frame" }, canvas), caption,
        h("div", { class: "actions" },
          h("button", { class: "primary", onclick: () => save().catch((e) => toast(e.message, true)) }, icon("download"), "Save image"),
          h("button", { class: "ghost", onclick: copy }, icon("copy"), "Copy image")))));
  redraw();
  dlg.showModal();
}

const cardFont = (sk, size, display = false, weight) =>
  `${weight ?? (display ? sk.dw : sk.bw)} ${size}px ${display ? sk.display : sk.body}`;
const monthYear = (k) => keyDate(k).toLocaleDateString("en-US", { month: "short", year: "numeric" });
const dayMonth = (k) => keyDate(k).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

// The words on the card. With no switches on, nothing in it names you, the role or the company.
function shareWords(f) {
  const who = SHARE.name && f.first_name ? `${f.first_name}’s job search` : "My job search";
  const range = f.start ? (monthYear(f.start) === monthYear(f.today) ? monthYear(f.start) : `${monthYear(f.start)} to ${monthYear(f.today)}`) : "";
  const o = f.offer || {};
  let offer = "";
  if (f.offer && (SHARE.title || SHARE.company || SHARE.date)) {
    offer = SHARE.title && o.title ? `Offer: ${o.title}` : "Offer";
    if (SHARE.company && o.company) offer += ` at ${o.company}`;
    if (SHARE.date && o.date) offer += ` · ${dayMonth(o.date)}`;
  }
  return { kicker: [who, range].filter(Boolean).join(" · ").toUpperCase(), offer,
    apps: plural(f.applications, "application"), offers: plural(f.offers, "offer"),
    stats: [[String(f.companies_interviewed), f.companies_interviewed === 1 ? "company interviewed" : "companies interviewed"],
            [pct(f.interview_rate), "interview rate"], [pct(f.heard_back), "heard back"]] };
}

async function drawShareCard(canvas, f) {
  const look = window.Look?.current() || "classic";
  const sk = CARD_SKINS[look] || CARD_SKINS.classic;
  const [, , , W, H] = SHARE_SIZES.find((s) => s[0] === SHARE.size);
  try {
    await Promise.all([cardFont(sk, 40, true), cardFont(sk, 14), `700 14px ${MONO}`].map((font) => document.fonts.load(font)));
  } catch (_) { /* draws in the fallback font */ }
  canvas.width = W * SHARE_SCALE;
  canvas.height = H * SHARE_SCALE;
  canvas.style.aspectRatio = `${W} / ${H}`;
  canvas.setAttribute("aria-label", `Your ${SHARE.style} card: ${f.applications} applications, ${f.offers} offers`);
  const c = canvas.getContext("2d");
  c.setTransform(SHARE_SCALE, 0, 0, SHARE_SCALE, 0, 0);
  c.textBaseline = "alphabetic";
  c.fillStyle = sk.bg;
  c.fillRect(0, 0, W, H);
  sk.decor(c, W, H, SHARE.size === "wide");
  const words = shareWords(f);
  const card = { c, sk, f, W, H, words };
  ({ journey: drawJourney, receipt: drawReceipt, big: drawBig })[SHARE.style](card, SHARE.size);
}

// Text at (x, y), shrunk until it fits maxW; returns its width.
function cardText(c, text, x, y, font, color, maxW = Infinity, align = "left", spacing = 0) {
  let size = Number(font.match(/(\d+(?:\.\d+)?)px/)[1]);
  const at = (s) => font.replace(/\d+(?:\.\d+)?px/, `${s}px`);
  c.font = at(size);
  if ("letterSpacing" in c) c.letterSpacing = `${spacing * size}px`;
  while (size > 6 && c.measureText(text).width > maxW) { size -= 1; c.font = at(size); if ("letterSpacing" in c) c.letterSpacing = `${spacing * size}px`; }
  c.fillStyle = color;
  c.textAlign = align;
  c.fillText(text, x, y);
  const w = c.measureText(text).width;
  if ("letterSpacing" in c) c.letterSpacing = "0px";
  return w;
}

function cardKicker({ c, sk, words }, x, y, maxW, size = 12.5) {
  cardText(c, words.kicker, x, y, cardFont(sk, sk.pixel ? size * .76 : size), sk.kick || sk.ink, maxW, "left", sk.pixel ? 0 : .1);
}

function cardFooter({ c, sk, W }, x, y, w, note = true) {
  c.fillStyle = sk.rule;
  c.fillRect(x, y - 24, w, sk.ruleW || 2);
  cardText(c, "Job Oyster", x, y, cardFont(sk, sk.pixel ? 9 : 15, true), sk.kick || sk.ink);
  if (note) cardText(c, "a free, open-source job tracker", x + w, y, cardFont(sk, 12), sk.sub, w * .6, "right");
}

// Stats side by side (row) or one under another (stack).
function cardStats({ c, sk, words }, x, y, w, size, stack = false) {
  const num = (t, px, py) => cardText(c, t, px, py, cardFont(sk, sk.pixel ? size * .55 : size, true), sk.stat || sk.ink);
  words.stats.forEach(([n, label], i) => {
    if (stack) {
      const nw = num(n, x, y + i * size * 1.75);
      cardText(c, label, x + nw + 10, y + i * size * 1.75, cardFont(sk, size * .42), sk.sub, w - nw - 10);
    } else {
      const cw = w / 3, px = x + i * cw;
      num(n, px, y);
      cardText(c, label, px, y + size * .62, cardFont(sk, Math.max(10, size * .38)), sk.sub, cw - 8);
    }
  });
}

function cardHeadline({ c, sk, words }, x, y, size, maxW) {
  const s = sk.pixel ? size * .5 : size, gap = sk.pixel ? s * 1.6 : s * 1.04;
  const t = (txt) => (sk.upper ? txt.toUpperCase() : txt);
  cardText(c, t(`${words.apps}.`), x, y, cardFont(sk, s, true), sk.ink, maxW);
  cardText(c, t(`${words.offers}.`), x, y + gap, cardFont(sk, s, true), sk.accent, maxW);
  return y + gap;
}

function cardOffer({ c, sk, words }, x, y, size, maxW) {
  if (words.offer) cardText(c, words.offer, x, y, cardFont(sk, size), sk.ink, maxW);
}

// The share card's Sankey: Ghosted joins No reply, so the small picture stays readable.
function cardFlow({ c, sk, f }, x, y, w, h, labelSize) {
  const lk = (from, to) => f.links.filter((l) => l.from === from && to.includes(l.to)).reduce((s, l) => s + l.n, 0);
  const nodes = [{ id: "applied", col: 0, value: f.applications, label: "Applied" }, { id: "interviewed", col: 1, value: f.interviewed, label: "Interviewed" },
    { id: "offer", col: 2, value: f.offers, label: f.offers === 1 ? "Offer" : "Offers" }, { id: "interviewing", col: 2, value: f.interviewing, label: "Interviewing" },
    { id: "rejected", col: 2, value: f.rejected, label: "Rejected" }, { id: "no_reply", col: 2, value: f.no_reply + f.ghosted, label: "No reply" }];
  const links = [{ from: "applied", to: "interviewed", n: f.interviewed }, { from: "applied", to: "rejected", n: lk("applied", ["rejected"]) },
    { from: "applied", to: "no_reply", n: lk("applied", ["ghosted", "no_reply"]) }, { from: "interviewed", to: "offer", n: f.offers },
    { from: "interviewed", to: "interviewing", n: f.interviewing }, { from: "interviewed", to: "rejected", n: lk("interviewed", ["rejected"]) },
    { from: "interviewed", to: "no_reply", n: lk("interviewed", ["ghosted"]) }];
  const lh = labelSize * 1.5, top = lh + 4;
  const lay = sankey(nodes, links, w, h - top, { left: 0, right: labelSize * 7.4, nw: Math.max(4, w / 90), gap: labelSize * .6, step: sk.step });
  const fills = { interviewed: sk.flows[0], interviewing: sk.flows[0], offer: sk.offer, rejected: sk.flows[1], no_reply: sk.flows[2] };
  c.save();
  c.translate(x, y + top);
  for (const l of lay.links) { c.fillStyle = fills[l.to]; c.fill(new Path2D(l.d)); }
  const ends = lay.nodes.filter((n) => n.col === lay.last && lay.last > 0);
  const endY = spread(ends.map((n) => n.y + n.h / 2), lh, h - top - lh / 2);
  for (const n of lay.nodes) {
    c.fillStyle = n.id === "offer" ? sk.offer : sk.node;
    c.fillRect(n.x, n.y, n.w, Math.max(2, n.h));
    const lx = n.col === lay.last && lay.last > 0 ? n.x + n.w + 6 : n.x + (n.col ? 0 : n.w + 4);
    const ly = n.col === lay.last && lay.last > 0 ? endY[ends.indexOf(n)] + labelSize * .38 : n.y - 6;
    const nw = cardText(c, String(n.value), lx, ly, cardFont(sk, sk.pixel ? labelSize * .7 : labelSize * 1.25, true), sk.ink);
    cardText(c, n.label, lx + nw + 5, ly, cardFont(sk, labelSize), sk.ink);
  }
  c.restore();
}

function drawJourney(card, size) {
  const { W, H } = card;
  if (size === "square") {
    cardKicker(card, 36, 52, 468);
    const y = cardHeadline(card, 36, 104, 46, 468);
    cardOffer(card, 36, y + 30, 15, 468);
    cardFlow(card, 36, 196, 468, 210, 12.5);
    cardStats(card, 36, 450, 468, 30);
    cardFooter(card, 36, 512, 468);
  } else if (size === "story") {
    cardKicker(card, 40, 84, 460, 14);
    const y = cardHeadline(card, 40, 160, 56, 460);
    cardOffer(card, 40, y + 38, 18, 460);
    cardFlow(card, 40, 300, 460, 340, 15);
    cardStats(card, 40, 720, 460, 40, true);
    cardFooter(card, 40, 912, 460);
  } else {
    cardKicker(card, 40, 56, 300, 11);
    const y = cardHeadline(card, 40, 108, 40, 290);
    cardOffer(card, 40, y + 28, 13, 290);
    cardStats(card, 40, 270, 290, 28, true);
    cardFooter(card, 40, H - 32, W - 80);
    cardFlow(card, 360, 70, 400, 300, 12.5);
  }
}

function drawBig(card, size) {
  const { c, sk, f, W, H, words } = card;
  const big = (x, y, px, maxW) => {
    cardText(c, String(f.applications), x, y, cardFont(sk, sk.pixel ? px * .5 : px, true), sk.ink, maxW, "left", sk.pixel ? 0 : -.03);
    const s = sk.pixel ? px * .14 : px * .23;
    cardText(c, sk.upper ? "APPLICATIONS" : "applications", x, y + s * 1.25, cardFont(sk, s, true), sk.accent, maxW);
    const sub = f.offers ? `to land ${words.offers}` : "and counting";
    cardText(c, sub, x, y + s * 1.25 + px * .13, cardFont(sk, px * .1), sk.ink, maxW);
    return y + s * 1.25 + px * .13;
  };
  if (size === "square") {
    cardKicker(card, 32, 52, 476);
    const y = big(32, 210, 150, 476);
    cardOffer(card, 32, y + 28, 15, 476);
    cardStats(card, 32, 450, 476, 30);
    cardFooter(card, 32, 512, 476);
  } else if (size === "story") {
    cardKicker(card, 40, 84, 460, 14);
    const y = big(40, 400, 230, 460);
    cardOffer(card, 40, y + 40, 20, 460);
    cardStats(card, 40, 720, 460, 40, true);
    cardFooter(card, 40, 912, 460);
  } else {
    cardKicker(card, 40, 56, 420, 11);
    const y = big(40, 240, 170, 420);
    cardOffer(card, 40, y + 30, 14, 420);
    cardStats(card, 500, 180, 260, 32, true);
    cardFooter(card, 40, H - 32, W - 80);
  }
}

// A till receipt on the look's ground, a little askew.
function drawReceipt(card, size) {
  const { c, sk, f, W, H, words } = card;
  if (sk.paperBg) { c.fillStyle = sk.paperBg; c.fillRect(0, 0, W, H); sk.decor(c, W, H, size === "wide"); }
  const rows = [["APPLICATIONS SENT", f.applications], ["NO REPLY / GHOSTED", f.no_reply + f.ghosted], ["REJECTIONS", f.rejected],
    [f.companies_interviewed === 1 ? "COMPANY INTERVIEWED" : "COMPANIES INTERVIEWED", f.companies_interviewed], ["DAYS SEARCHING", f.days || "—"],
    null, ["HEARD BACK", pct(f.heard_back)], ["INTERVIEW RATE", pct(f.interview_rate)]];
  const pw = 300, lh = 19, mono = (px, w = 500) => `${w} ${px}px ${MONO}`;
  const offerLines = words.offer ? 1 : 0;
  const ph = 98 + rows.length * lh + 64 + offerLines * lh + 30;
  const room = size === "wide" ? [W * .5, H - 60] : [W - 120, H - 120];
  const k = Math.min(room[0] / pw, room[1] / ph, size === "story" ? 1.5 : 1.2);
  const cx = size === "wide" ? W * .7 : W / 2, cy = H / 2;
  c.save();
  c.translate(cx, cy);
  c.rotate(-2 * Math.PI / 180);
  c.scale(k, k);
  c.translate(-pw / 2, -ph / 2);
  c.shadowColor = "rgba(0,0,0,.25)"; c.shadowBlur = 18; c.shadowOffsetY = 8;
  const zig = new Path2D();
  const teeth = 24, tw = pw / teeth;
  zig.moveTo(0, 5);
  for (let i = 0; i < teeth; i++) { zig.lineTo(i * tw + tw / 2, 0); zig.lineTo((i + 1) * tw, 5); }
  zig.lineTo(pw, ph - 5);
  for (let i = teeth; i > 0; i--) { zig.lineTo(i * tw - tw / 2, ph); zig.lineTo((i - 1) * tw, ph - 5); }
  zig.closePath();
  c.fillStyle = sk.paper;
  c.fill(zig);
  c.shadowColor = "transparent";
  const ink = sk.paperInk, P = 20;
  let y = 40;
  cardText(c, "Job Oyster", pw / 2, y, cardFont(sk, sk.pixel ? 12 : 22, true), sk.paperAccent, pw - 2 * P, "center");
  y += 18;
  cardText(c, words.kicker, pw / 2, y, mono(9.5), ink, pw - 2 * P, "center");
  const dashed = (yy) => { c.save(); c.strokeStyle = ink; c.globalAlpha = .55; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(P, yy); c.lineTo(pw - P, yy); c.stroke(); c.restore(); };
  y += 14; dashed(y); y += 6;
  for (const r of rows) {
    if (!r) { y += 6; dashed(y - 2); y += 6; continue; }
    y += lh;
    cardText(c, r[0], P, y, mono(11.5), ink, pw * .72);
    cardText(c, String(r[1]), pw - P, y, mono(11.5, 700), ink, pw * .25, "right");
  }
  y += 12;
  c.fillStyle = ink; c.fillRect(P, y, pw - 2 * P, 1.5); c.fillRect(P, y + 4, pw - 2 * P, 1.5);
  y += 32;
  cardText(c, "TOTAL", P, y, cardFont(sk, sk.pixel ? 11 : 20, true), ink);
  cardText(c, f.offers ? words.offers.toUpperCase() : `${f.interviewed} INTERVIEWED`, pw - P, y, cardFont(sk, sk.pixel ? 11 : 20, true), sk.paperAccent, pw * .6, "right");
  if (words.offer) { y += lh; cardText(c, words.offer.toUpperCase(), pw - P, y, mono(10), ink, pw - 2 * P, "right"); }
  y += 26;
  cardText(c, "THANKS FOR APPLYING · KEEP GOING", pw / 2, y, mono(9), ink, pw - 2 * P, "center");
  c.restore();
  if (size === "wide") {
    cardKicker(card, 40, 56, 300, 11);
    const yy = cardHeadline(card, 40, 120, 38, 300);
    cardOffer(card, 40, yy + 28, 13, 300);
    cardText(c, "Job Oyster", 40, H - 32, cardFont(sk, sk.pixel ? 9 : 15, true), sk.kick || sk.ink);
  }
}

// ---- jobs: list and board ----------------------------------------------------------------------
function renderJobs() {
  S.tokens = S.query.toLowerCase().split(/\s+/).filter(Boolean);
  renderCounts();
  if (S.scope === "tracker") renderStages();
  else renderFindFilters();
  renderFlags();
  renderFacets();
  const list = S.visible = visibleJobs();
  $("#list-count").replaceChildren(...(filtersOn()
    ? [`${list.length} of ${plural(S.jobs.length, "role")}`, h("button", { class: "link-btn", onclick: clearFilters }, "Clear filters")]
    : [plural(list.length, "role")]));
  if (boardMode()) renderBoard(list);
  else renderList(list);
  renderBatch();
  renderFoldStrip();
  renderRail();
}

// ---- jobs: scoring (signal = one quick call; full = every requirement rated) ---------------------
const score = (j) => j.impact_score ?? j.fit;             // the full score where there is one
const canScore = (j) => j.local || !!j.url;                 // a Notion-only row needs a posting page to read
function applyScoring(sc) {
  const before = S.scoring;
  S.scoring = new Map(Object.entries(sc.active));
  S.scoreErrors = new Map(Object.entries(sc.errors));
  const changed = [...new Set([...before.keys(), ...S.scoring.keys()])]
    .filter((id) => before.get(id)?.state !== S.scoring.get(id)?.state);
  for (const id of changed) {
    const j = S.byId.get(id);
    if (j) j._el = j._card = null;
  }
  const finished = S.scoreFinished != null && sc.finished !== S.scoreFinished;
  S.scoreFinished = sc.finished;
  if (changed.length) {
    renderJobs();
    if (changed.includes(S.selected)) renderDetail();
  }
  if (finished) {            // some jobs left the queue: fetch their scores
    const done = changed.filter((id) => !S.scoring.has(id));
    done.forEach((id) => S.details.delete(id));
    loadJobs().then(() => {
      if (!done.includes(S.selected)) return;
      if (S.byId.get(S.selected)?.local) loadDetail(S.selected); else renderDetail();
    });
  }
  clearTimeout(applyScoring._t);
  if (S.scoring.size) applyScoring._t = setTimeout(loadSummary, 1500);   // follow the queue closely while it runs
}
async function scoreJobs(ids, kind = "signal") {
  ids = ids.filter((id) => !S.scoring.has(id));
  if (!ids.length) return;
  try {
    applyScoring(await post("/api/score", { job_ids: ids, kind }));
    S.picks.clear();
    renderJobs();
    if (ids.length > 1) toast(`${kind === "full" ? "Full" : "Signal"} scoring ${plural(ids.length, "job")}.`);
  } catch (e) { toast(e.message, true); }
}
// Make résumé for every checked role: the server queues them, the most at once and the rest in turn.
// Their badges become rings (dashed while waiting), and the bar above the list counts them down.
async function makeResumes(jobs) {
  const at = S.summary?.max_runs || 3, each = S.summary?.durations?.make_resume_s;
  const hours = each && (Math.ceil(jobs.length / at) * each) / 3600;
  if (jobs.length > 1 && !confirm(`Make ${plural(jobs.length, "résumé")}? Each is ${ACTIONS.resume.calls}. ${at} run at a time and the rest wait their turn` +
    (hours ? `: ${hours < 1 ? mins(hours * 3600) : `~${hours.toFixed(1)} hours`} in all.` : ".") + " You can stop the waiting ones at any time.")) return;
  try {
    const r = await post("/api/runs/resumes", { job_ids: jobs.map((j) => j.id) });
    S.picks.clear();
    const going = r.runs.filter((x) => x.status !== "queued").length;
    toast(!r.runs.length ? "Nothing to start: those résumés are already being made."
      : `Making ${plural(r.runs.length, "résumé")}: ${going} started${r.runs.length > going ? `, ${r.runs.length - going} waiting their turn` : ""}.` +
        (r.skipped.length ? ` Skipped ${r.skipped.length} (${[...new Set(r.skipped.map((x) => x.reason))].join(", ")}).` : "") + " Follow them in Logs.");
    loadSummary();
    loadJobs();     // each goes In progress as it's queued
  } catch (e) { toast(e.message, true); }
}
async function stopWaitingResumes() {
  try {
    const r = await post("/api/runs/resumes/stop-waiting");
    toast(r.stopped ? `Dropped ${plural(r.stopped, "résumé")} that hadn't started. The ones going will finish.` : "None were waiting.");
    loadSummary();
  } catch (e) { toast(e.message, true); }
}
function renderBatch() {
  const box = $("#batch");
  const picked = S.visible.filter((j) => S.picks.has(j.id));
  const all = $("#pick-all");
  all.checked = picked.length > 0 && picked.length === S.visible.length;
  all.indeterminate = picked.length > 0 && picked.length < S.visible.length;
  const running = [...S.scoring.values()].filter((x) => x.state === "running").length;
  const queued = S.scoring.size - running;
  const parts = [];
  if (S.scoring.size) parts.push(h("span", { class: "batch-status" }, spinner(), ` Scoring ${plural(S.scoring.size, "job")}`,
    queued ? ` (${queued} waiting)` : "", queued ? h("button", { class: "link-btn", title: "Jobs already being scored will finish",
      onclick: async () => { try { applyScoring(await post("/api/score/cancel")); } catch (e) { toast(e.message, true); } } }, "Stop the rest") : null));
  const waiting = [...RUNP.jobs.values()].filter((p) => p.queued).length;
  if (RUNP.jobs.size) parts.push(h("span", { class: "batch-status" }, spinner(), ` Making ${plural(RUNP.jobs.size, "résumé")}`,
    waiting ? ` (${waiting} waiting)` : "", waiting ? h("button", { class: "link-btn", title: "Résumés already being made will finish",
      onclick: stopWaitingResumes }, "Stop the rest") : null));
  if (picked.length) {
    const ok = picked.filter(canScore), full = ok.filter((j) => !j.tailored), sig = ok.filter((j) => j.impact_score == null);
    const make = picked.filter((j) => j.local && !j.tailored && !RUNP.jobs.has(j.id));
    const restore = S.findFilter === "dismissed", hide = picked.filter((j) => j.local && !j.tracker_id && j.dismissed === restore);
    parts.push(h("span", {}, h("b", {}, picked.length), " selected"),
      ...(S.scope === "find" ? [h("button", { class: "primary small", title: "Adds them to your tracker as Not started. You stay here.",
        onclick: () => trackJobs(picked.map((j) => j.id)) }, "Track"),
        h("button", { class: "ghost small", disabled: !hide.length,
          title: restore ? "Puts them back in New." : "Moves them to Dismissed. Restore brings them back.",
          onclick: () => dismissJobs(hide.map((j) => j.id), !restore) }, restore ? "Restore" : "Dismiss")] : []),
      h("button", { class: "ghost small", disabled: !sig.length, title: `${ACTIONS.signal.what} ${actionTime("signal")} each; roles with a full score are skipped.`,
        onclick: () => scoreJobs(sig.map((j) => j.id), "signal") }, "Signal score"),
      h("button", { class: "ghost small", disabled: !full.length, title: `${ACTIONS.full.what} ${actionTime("full")} each, one at a time.`,
        onclick: () => scoreJobs(full.map((j) => j.id), "full") }, "Full score"),
      h("button", { class: "ghost small", disabled: !make.length,
        title: `${ACTIONS.resume.what} ${actionTime("resume")} each, ${S.summary?.max_runs || 3} at a time; the rest wait their turn. Roles with a résumé, one being made, or no posting stored are skipped.`,
        onclick: () => makeResumes(make) }, make.length && make.length < picked.length ? `Make résumé (${make.length})` : "Make résumé"),
      h("button", { class: "link-btn", onclick: () => { S.picks.clear(); renderJobs(); } }, "Clear"));
  } else {
    const todo = S.visible.filter((j) => score(j) == null && canScore(j) && !S.scoring.has(j.id));
    if (todo.length) parts.push(h("button", { class: "ghost small", title: "One quick Claude call per job, three at a time",
      onclick: () => scoreJobs(todo.map((j) => j.id), "signal") }, `Signal score ${todo.length} unscored`));
  }
  box.replaceChildren(...parts);
  box.classList.toggle("hidden", !parts.length);
}
$("#pick-all").addEventListener("change", (e) => {
  S.visible.forEach((j) => (e.target.checked ? S.picks.add(j.id) : S.picks.delete(j.id)));
  renderJobs();
});
function emptyState() {
  if (!S.loaded) return h("div", { class: "list-empty" }, spinner(), " Loading roles…");
  if (filtersOn()) return h("div", { class: "list-empty" }, "No roles match these filters.", h("button", { class: "ghost", onclick: clearFilters }, "Clear filters"));
  return S.scope === "find"
    ? h("div", { class: "list-empty" }, "No new roles here. Search with your filters, or add a job you found yourself.",
        h("button", { class: "primary", onclick: () => showTab("searches") }, "Go to Filters"))
    : h("div", { class: "list-empty" }, "Nothing tracked yet. Track roles from Search Jobs.",
        h("button", { class: "ghost", onclick: () => showTab("find") }, "Go to Search Jobs"));
}
function starButton(j, label = false) {
  return h("button", { class: "star" + (label ? " labeled" : ""), "aria-pressed": String(j.starred),
    title: j.starred ? "Remove star" : "Star this role",
    onclick: (e) => { e.stopPropagation(); toggleStar(j, e.currentTarget); } }, icon("star"), label ? (j.starred ? "Starred" : "Star") : null);
}
// A role with a résumé made for it: a page icon beside the star that opens its Résumé tab.
const resumeMark = (j) => j.tailored && h("button", { class: "resume-mark", "aria-label": `Open the résumé made for ${j.title}`,
  title: `Résumé made${j.ats_total != null ? ` · ATS ${pct(j.ats_total)}` : ""}${j.resume_score != null ? ` · résumé ${j.resume_score}/10` : ""}. Open it.`,
  onclick: (e) => { e.stopPropagation(); S.detailTab = "resume"; S.detailTabJob = j.id; selectJob(j.id, true); } }, icon("resume"));
const rowEnd = (j) => h("div", { class: "row-end" }, starButton(j), resumeMark(j),
  !j.referral_url && h("button", { class: "ref-add", title: "Add a referral (R)", "aria-label": `Add a referral for ${j.title || "this role"}`,
    onclick: (e) => { e.stopPropagation(); selectJob(j.id, true); openReferral(j); } }, icon("personAdd")));
const fitTile = (j, cls = "") => RUNP.jobs.has(j.id) ? ringTile(j, cls) : h("div", {
  class: `fit ${fitClass(score(j))} ${j.impact_score != null ? "full" : ""} ${RUNP.done.has(j.id) ? "pop" : ""} ${cls}`, "data-job": j.id, "data-cls": cls,
  onanimationend: (e) => e.currentTarget.classList.remove("pop"),   // springs once, not each time the list is redrawn
  title: S.scoring.has(j.id) ? "Scoring…" : j.impact_score != null ? "Full score, out of 10" : j.fit != null ? "Signal score, out of 10" : "Not scored yet" },
  S.scoring.has(j.id) ? spinner() : score(j) ?? "–");
// Applied to, with no record of the résumé that was sent.
const SENT_STAGES = APPLIED_STAGES;
const showsSent = (j) => !!j.tracker_id && (SENT_STAGES.includes(j.status) || !!j.sent_resume);
const noSent = (j) => !!j.tracker_id && SENT_STAGES.includes(j.status) && !j.sent_resume;
const noSentTag = (j) => noSent(j) && h("span", { class: "tag warn-tag", title: "Add the résumé you sent in its Sent résumé tab." }, "No résumé on file");
const referralTag = (j) => j.referral_url && h("span", { class: "tag ref", title: `Referral: ${refLabel(j)}. Click to open it`,
  onclick: (e) => { e.stopPropagation(); selectJob(j.id, true); openReferral(j); } },
  icon("person"), refLabel(j), j.referral_step && h("span", { class: "ref-step" }, ` · ${refStepName(j.referral_step)}`));
// The company's funding round: "Series B", or "YC W26" when only the batch is known. See the Startups tab.
const roundLabel = (f) => (f.stage && f.stage !== "Unknown" ? f.stage : f.batch ? `YC ${f.batch}` : f.round || "Raised");
// Where a job board listed the role (Himalayas asks to be named as the source, with a link to its page).
const sourceTag = (j) => j.source && h("span", { class: "tag source-tag", title: `Listed on ${j.source}` }, j.source);
const fundingTag = (j) => j.funding && h("span", { class: "tag round-tag", "data-round": j.funding.stage || "Unknown", title: j.funding.line },
  roundLabel(j.funding), j.funding.amount && h("b", {}, ` ${j.funding.amount}`));

// The row's second line: company · pay · where (the US state or the country, from the server), and Remote or Hybrid.
const rowWhere = (j) => [j.company, known(j.pay), j.place, ["Remote", "Hybrid"].includes(j.mode) && j.mode].filter(Boolean).join(" · ");
function rowEl(j) {
  if (!j._el) {
    j._el = h("li", { "data-id": j.id, tabindex: "0" },
      h("input", { type: "checkbox", class: "pick", "aria-label": `Select ${j.title}` }),
      fitTile(j),
      h("div", { class: "row-main" },
        h("div", { class: "t" }, j.title || "(untitled)"),
        runStep(j),
        h("div", { class: "s", title: j.location || null }, rowWhere(j)),
        h("div", { class: "tags" },
          j.status && h("span", { class: "tag stage-tag", "data-stage": j.status }, j.status),
          fundingTag(j),
          sourceTag(j),
          j.tailored && h("span", { class: "tag ok" }, `Résumé${j.ats_total != null ? " " + pct(j.ats_total) : ""}`),
          noSentTag(j),
          referralTag(j),
          !j.tracker_id && j.posted && h("span", { class: "tag" }, `Posted ${fmtDate(j.posted.length === 10 ? j.posted + "T12:00" : j.posted)}`),
          j.pending && h("span", { class: "tag", title: "Saved here. It will be sent to Notion on the next sync." }, "To sync"),
          j.closed ? h("span", { class: "tag warn-tag", title: `Its posting had closed when the app checked on ${fmtDate(j.closed)}` }, "Closed")
            : j.dismissed && h("span", { class: "tag" }, "Dismissed"),
          !j.local && h("span", { class: "tag", title: "None of your filters found it. Paste its description on the role, or a signal score reads it from its page." }, "No posting stored"))),
      rowEnd(j));
  }
  j._el.classList.toggle("selected", j.id === S.selected);
  j._el.firstChild.checked = S.picks.has(j.id);
  return j._el;
}
function renderList(list) {
  let items = list.map(rowEl);
  if (S.group) {
    items = [];
    for (let i = 0; i < list.length;) {
      const key = companyKey(list[i]);
      let n = i;
      while (n < list.length && companyKey(list[n]) === key) n++;
      const jobs = list.slice(i, n);
      items.push(groupHead(key, jobs), ...(S.collapsed.has(key) ? [] : jobs.map(rowEl)));
      i = n;
    }
  }
  $("#jobs").replaceChildren(...items, ...(list.length ? [] : [emptyState()]));
}
function pick(e) {
  const el = e.target.closest("[data-id]");
  if (!el || e.target.matches(".pick")) return;     // the checkbox selects for a batch, not for viewing
  if (e.type === "click" || (e.key === "Enter" && !e.target.closest("button"))) selectJob(el.dataset.id, true);
}
$("#jobs").addEventListener("change", (e) => {
  if (!e.target.matches(".pick")) return;
  const id = e.target.closest("[data-id]").dataset.id;
  e.target.checked ? S.picks.add(id) : S.picks.delete(id);
  renderBatch();
});
$("#jobs").addEventListener("click", pick);
$("#jobs").addEventListener("keydown", pick);

function cardEl(j) {
  if (!j._card) {
    j._card = h("div", { class: "kcard", "data-id": j.id, tabindex: "0", draggable: j.tracker_id ? "true" : null },
      fitTile(j),
      h("div", { class: "row-main" },
        h("div", { class: "t" }, j.title || "(untitled)"),
        h("div", { class: "s" }, j.company),
        (j.tailored || j.referral_url || noSent(j) || j.funding) && h("div", { class: "tags" },
          fundingTag(j),
          j.tailored && h("span", { class: "tag ok" }, `Résumé${j.ats_total != null ? " " + pct(j.ats_total) : ""}`),
          noSentTag(j),
          referralTag(j))),
      rowEnd(j));
  }
  j._card.classList.toggle("selected", j.id === S.selected);
  return j._card;
}
function renderBoard(list) {
  const by = new Map(STAGES.map((s) => [s, []]));
  for (const j of list) by.get(stageOf(j)).push(j);
  const cols = STAGES.filter((s) => !S.stages.size || S.stages.has(s));
  $("#board").replaceChildren(...cols.map((stage) => {
    const jobs = by.get(stage);
    const shown = S.boardAll.has(stage) ? jobs : jobs.slice(0, BOARD_CAP);
    return h("section", { class: "col", "data-stage": stage },
      h("header", {}, h("span", { class: "dot" }), stage, h("span", { class: "n" }, jobs.length)),
      h("div", { class: "col-cards" }, ...(S.group ? withCompanyHeads(shown) : shown.map(cardEl)),
        jobs.length > shown.length && h("button", { class: "ghost", onclick: () => { S.boardAll.add(stage); renderJobs(); } },
          `Show ${jobs.length - shown.length} more`),
        !jobs.length && h("div", { class: "col-empty" },
          "Drag a role here.")));
  }));
}
// On the board, grouping adds a company line above each company's cards in a column.
function withCompanyHeads(jobs) {
  return jobs.flatMap((j, i) => i && companyKey(jobs[i - 1]) === companyKey(j) ? [cardEl(j)]
    : [h("div", { class: "col-group" }, j.company || "No company"), cardEl(j)]);
}
const board = $("#board");
board.addEventListener("click", pick);
board.addEventListener("keydown", pick);
board.addEventListener("dragstart", (e) => {
  const card = e.target.closest(".kcard");
  if (!card) return;
  e.dataTransfer.setData("text/plain", card.dataset.id);
  e.dataTransfer.effectAllowed = "move";
  board.classList.add("dragging");
});
board.addEventListener("dragend", () => { board.classList.remove("dragging"); $$(".col.over", board).forEach((c) => c.classList.remove("over")); });
board.addEventListener("dragover", (e) => {
  const col = e.target.closest(".col");
  if (!col) return;
  e.preventDefault();
  $$(".col.over", board).forEach((c) => c !== col && c.classList.remove("over"));
  col.classList.add("over");
});
board.addEventListener("drop", (e) => {
  const col = e.target.closest(".col");
  const j = S.byId.get(e.dataTransfer.getData("text/plain"));
  if (!col || !j) return;
  e.preventDefault();
  setStatus(j, col.dataset.stage);
});

// ---- jobs: star, status, referral, score ------------------------------------------------------
function repaint(j) {
  indexJob(j);
  renderJobs();
  if (j.id === S.selected) renderDetailHead();
}
async function toggleStar(j, btn) {
  j.starred = !j.starred;
  if (j.starred) window.Look?.sparkle(btn || $("#job-detail .star"));
  repaint(j);
  try { await patch(`/api/jobs/${encodeURIComponent(j.id)}`, { starred: j.starred }); }
  catch (e) { j.starred = !j.starred; repaint(j); toast(e.message, true); }
}
async function setStatus(j, status) {
  const prev = j.status;
  if (!j.tracker_id || status === prev) return;
  j.status = status;
  repaint(j);
  try {
    await post(`/api/tracker/${encodeURIComponent(j.tracker_id)}/status`, { status });
    // Applied, Done and Denied get the look's moment (confetti, the cat); the other statuses a plain toast.
    const shown = window.Look?.status({ job: j, status, prev, counts: stageCounts() });
    const notion = S.notionError ? "Notion will get it when it's back." : "";
    if (!shown) toast(`Status set to ${status}.${notion ? " " + notion : ""}`);
    else if (notion) toast(notion);
    loadJobs({ wait: true });         // once the background sync has run, show whether Notion has it
  } catch (e) { j.status = prev; repaint(j); toast(e.message, true); }
}

// ---- jobs: referral ----------------------------------------------------------------------------
// A toggle in the role's header unfolds a small form (no referral yet, or Edit) or the referral's steps
// and note. One role's is open at a time (S.refOpen); what's typed survives a repaint (S.refDraft).
const REF_STEPS = [["toask", "To ask"], ["asked", "Asked"], ["referred", "Referred"], ["thanked", "Thanked"]];
const REF_FIELDS = ["referral_url", "referral_name", "referral_step", "referral_dates", "referral_note"];
const refStepName = (k) => REF_STEPS.find((x) => x[0] === k)?.[1] || "";
const refEmail = (j) => (/^mailto:/i.test(j.referral_url || "") ? j.referral_url.slice(7) : "");
const refLabel = (j) => j.referral_name || refEmail(j) || host(j.referral_url);
const refHref = (j) => (refEmail(j) ? j.referral_url : safeHref(j.referral_url));
const looksLikeEmail = (v) => /^(mailto:)?[^@\s/:]+@[^@\s/]+\.[^@\s/.]+$/i.test(v.trim());
// The name a pasted contact gives: linkedin.com/in/maya-okafor-4b1 or maya.okafor@example.com → "Maya Okafor".
function nameFromContact(raw) {
  const v = raw.trim();
  const li = /linkedin\.com\/in\/([^/?#]+)/i.exec(v);
  let parts;
  if (li) {
    try { parts = decodeURIComponent(li[1]).split("-"); } catch (_) { return ""; }
  } else if (looksLikeEmail(v)) {
    parts = v.replace(/^mailto:/i, "").split("@")[0].split(/[._-]+/);
    if (parts.length < 2) return "";      // "sam@" could be anyone's first name or initials
  } else return "";
  parts = parts.filter((p) => p && !/\d/.test(p));
  return parts.map((p) => p[0].toUpperCase() + p.slice(1).toLowerCase()).join(" ");
}
// Other tracked roles at the same company with no referral yet: one tick adds the person to those too.
const refOthers = (j) => j.company ? [...S.byId.values()].filter((o) =>
  o.id !== j.id && o.tracker_id && !o.referral_url && companyKey(o) === companyKey(j)) : [];

function openReferral(j, edit = false) {
  S.refOpen = j.id; S.refEdit = edit; S.refDraft = null;
  renderDetailHead();
  const card = $("#ref-card");
  if (!card) return;
  card.classList.add("unfold");
  ($("input[name=contact]", card) || $(".ref-steps [aria-pressed=true]", card) || $(".ref-steps button", card))?.focus();
}
function foldReferral() {
  S.refOpen = null; S.refEdit = false; S.refDraft = null;
  renderDetailHead();
  $("#job-detail .ref-toggle")?.focus();
}
function refToggle(j) {
  const open = S.refOpen === j.id;
  const attrs = { "aria-expanded": String(open), "aria-controls": open ? "ref-card" : null, onclick: () => (open ? foldReferral() : openReferral(j)) };
  if (!j.referral_url) return h("button", { class: "ghost small ref-toggle add", title: "Add the person referring you (R)", ...attrs },
    icon("personAdd"), "Referral", icon("down"));
  return h("button", { class: "ref-chip ref-toggle", title: `${refEmail(j) || j.referral_url}. Its steps and note (R)`, ...attrs },
    icon("person"), h("span", { class: "ref-name" }, refLabel(j)),
    j.referral_step && h("span", { class: "ref-step" }, `· ${refStepName(j.referral_step)}`), icon("down"));
}
async function setReferral(j, body) {
  const r = await patch(`/api/jobs/${encodeURIComponent(j.id)}`, body);
  for (const k of REF_FIELDS) j[k] = r[k];
  indexJob(j);
}
function refCard(j) {
  if (S.refOpen !== j.id) return null;
  const fold = h("button", { class: "icon-btn ref-fold", title: "Fold the referral away (Esc)", "aria-label": "Fold the referral away", onclick: foldReferral }, icon("up"));
  const card = h("section", { class: "ref-card", id: "ref-card", "aria-label": "Referral",
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); foldReferral(); } } }, fold);
  if (!j.referral_url || S.refEdit) card.append(refForm(j));
  else card.append(...refDetails(j));
  return card;
}
function refForm(j) {
  const d = S.refDraft?.id === j.id ? S.refDraft
    : (S.refDraft = { id: j.id, contact: refEmail(j) || j.referral_url || "", name: j.referral_name || "", auto: false, all: false });
  const others = refOthers(j);
  const contact = h("input", { name: "contact", value: d.contact, autocomplete: "off", spellcheck: "false", "aria-label": "LinkedIn, link or email",
    placeholder: "Paste their LinkedIn, the referral link they sent, or their email" });
  const name = h("input", { name: "name", value: d.name, autocomplete: "off", maxlength: "120", placeholder: "Who is referring you", "aria-label": "Name" });
  const auto = h("span", { class: "ref-auto", hidden: !d.auto }, "from the ", looksLikeEmail(d.contact) ? "email" : "link");
  const more = h("div", { class: "ref-more", hidden: !(d.contact || j.referral_url) },
    h("label", { class: "ref-field" }, h("span", {}, "Name"), name, auto),
    others.length > 0 && h("label", { class: "check" }, h("input", { type: "checkbox", name: "all", checked: d.all }),
      ` Also for the ${others.length === 1 ? "other" : others.length + " other"} ${j.company} role${others.length === 1 ? "" : "s"}`),
    h("p", { class: "form-error hidden" }),
    h("div", { class: "ref-actions" },
      h("button", { class: "primary small", type: "submit" }, "Save referral"),
      h("button", { class: "ghost small", type: "button", onclick: () => (j.referral_url ? (S.refEdit = false, S.refDraft = null, renderDetailHead()) : foldReferral()) }, "Cancel"),
      j.referral_url && h("button", { class: "ghost small danger", type: "button", onclick: () => removeReferral(j) }, "Remove referral")));
  const find = !j.referral_url && j.company && h("a", { class: "ref-find", hidden: !!d.contact, target: "_blank", rel: "noopener",
    href: `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(j.company)}`,
    title: `A LinkedIn people search for ${j.company}` }, `Find people at ${j.company} ↗`);
  const form = h("form", { class: "ref-form", autocomplete: "off" },
    h("div", { class: "ref-line" }, contact, find), more);
  form.addEventListener("input", (e) => {
    if (e.target === contact) {
      const v = contact.value.trim(), guess = nameFromContact(v);
      more.hidden = !(v || j.referral_url);
      if (find) find.hidden = !!v;
      if (guess && (!name.value || d.auto)) { name.value = guess; d.auto = true; }
      else if (!guess && d.auto) { name.value = ""; d.auto = false; }
      auto.replaceChildren("from the ", looksLikeEmail(v) ? "email" : "link");
    }
    if (e.target === name) d.auto = false;
    auto.hidden = !d.auto;
    d.contact = contact.value; d.name = name.value; d.all = !!form.elements.namedItem("all")?.checked;
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); saveReferral(j, form); });
  return form;
}
async function saveReferral(j, form) {
  const d = S.refDraft;
  const contact = d.contact.trim(), name = d.name.trim();
  if (!contact) { form.elements.namedItem("contact").focus(); return; }
  const btn = $("button[type=submit]", form);
  btn.disabled = true;
  try {
    await setReferral(j, { referral_url: contact, referral_name: name });
  } catch (e) { btn.disabled = false; formError(form, e.message); return; }
  const others = d.all ? refOthers(j) : [];
  let missed = 0;
  for (const o of others) {
    try { await setReferral(o, { referral_url: contact, referral_name: name }); } catch (_) { missed++; }
  }
  S.refOpen = null; S.refEdit = false; S.refDraft = null;
  renderJobs();
  renderDetailHead();
  const t = $("#job-detail .ref-toggle");
  t?.classList.add("saved");
  t?.focus();
  const who = name || refLabel(j);
  toast(missed ? `Referral saved for ${who}, but ${plural(missed, "other role")} didn't take it.`
    : others.length ? `Referral saved: ${who}, on ${plural(others.length + 1, "role")}.` : `Referral saved: ${who}.`, !!missed);
}
async function removeReferral(j) {
  try {
    await setReferral(j, { referral_url: "" });
    S.refOpen = null; S.refEdit = false; S.refDraft = null;
    repaint(j);
    toast("Referral removed.");
  } catch (e) { toast(e.message, true); }
}
function refDetails(j) {
  const at = REF_STEPS.findIndex(([k]) => k === j.referral_step);
  const href = refHref(j);
  const note = h("textarea", { class: "ref-note", name: "note", rows: "2", maxlength: "2000", "aria-label": "Note",
    placeholder: "How you know them, what they said, when to follow up" });
  note.value = j.referral_note || "";
  // Saved a moment after typing stops, and on leaving the box, so a redraw can't lose it.
  const saveNote = async () => {
    clearTimeout(refDetails._t);
    if (note.value.trim() === (j.referral_note || "")) return;
    try { await setReferral(j, { referral_note: note.value }); renderJobs(); }
    catch (e) { toast(e.message, true); }
  };
  note.addEventListener("input", () => { clearTimeout(refDetails._t); refDetails._t = setTimeout(saveNote, 800); });
  note.addEventListener("change", saveNote);
  const setStep = async (k) => {
    try { await setReferral(j, { referral_step: k }); repaint(j); $(`#ref-card [data-step=${k}]`)?.focus(); }
    catch (e) { toast(e.message, true); }
  };
  const initials = refLabel(j).split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  return [
    h("div", { class: "ref-who" },
      h("span", { class: "ref-avatar", "aria-hidden": "true" }, initials || "?"),
      h("div", { class: "ref-id" }, h("b", {}, refLabel(j)),
        href && h("a", { href, target: refEmail(j) ? null : "_blank", rel: "noopener", title: refEmail(j) || j.referral_url },
          refEmail(j) || host(j.referral_url), refEmail(j) ? "" : " ↗")),
      h("button", { class: "ghost small", onclick: () => openReferral(j, true) }, "Edit"),
      h("button", { class: "ghost small", onclick: () => removeReferral(j) }, "Remove")),
    h("div", { class: "ref-steps", role: "group", "aria-label": "Where the referral stands" },
      ...REF_STEPS.map(([k, label], i) => h("button", { class: i < at ? "done" : i === at ? "now" : "", "data-step": k, "aria-pressed": String(i === at),
        onclick: () => setStep(k) }, label, j.referral_dates?.[k] && i <= at && h("small", {}, fmtDate(j.referral_dates[k] + "T12:00"))))),
    note,
  ];
}

// ---- jobs: detail ------------------------------------------------------------------------------
function selectJob(id, open = false) {
  const prev = S.byId.get(S.selected);
  S.selected = S.sel[S.scope] = id;
  if (open) S.drawer = true;
  if (S.refOpen && S.refOpen !== id) S.refOpen = S.refEdit = S.refDraft = null;   // another role's referral folds away
  for (const j of [prev, S.byId.get(id)]) {
    if (!j) continue;
    j._el?.classList.toggle("selected", j.id === id);
    j._card?.classList.toggle("selected", j.id === id);
  }
  $("#jobs-body").classList.toggle("detail-open", S.drawer);
  $$("#rail-jobs .rail-job").forEach((b) => b.classList.toggle("selected", b.dataset.id === id));
  $(`#rail-jobs .rail-job.selected`)?.scrollIntoView({ block: "nearest" });
  renderDetail();
  if (S.byId.get(id)?.local) loadDetail(id);
}
function closeDrawer() {
  S.drawer = false;
  $("#jobs-body").classList.remove("detail-open");
}
$("#drawer-backdrop").addEventListener("click", closeDrawer);

async function loadDetail(id) {
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(id)}`);
    // Re-render only when what's below the header changed, so a revisit doesn't reload the PDF.
    // The chat isn't part of it: CHATS holds it, and its panel redraws only itself.
    const sig = JSON.stringify([d.triage, d.triaged_at, d.full_score, d.full_scored_at, d.description.length,
                                d.resume && { ...d.resume, chat: null, chat_busy: null }, d.has_report, d.has_heatmap]);
    if (d.resume) chatFromServer(id, d.resume.chat, d.resume.chat_busy);
    const old = S.details.get(id);
    S.details.set(id, { d, sig });
    if (S.selected === id && (!old || old.sig !== sig)) renderDetail();
    const j = S.byId.get(id);
    if (!d.description.trim() && j?.url && !FETCHING.has(id)) fetchPosting(j, true);   // once, when it first opens
  } catch (e) {
    if (S.selected !== id || S.details.has(id)) return;
    // Server down: keep "Loading…", the banner says why, and the panel loads once it's back.
    if (e instanceof TypeError) $("#job-detail > .pad.muted")?.replaceChildren(spinner(), " Waiting for the app's server…");
    else $("#job-detail").append(h("div", { class: "pad muted" }, e.message));
  }
}

// Tracking keeps you in Find jobs, on the next role, so you can work down the list and open the Tracker after.
async function trackJob(j) {
  window.Look?.fly(j._el || j._card, $(".tabs [data-tab=tracker]"), getComputedStyle(document.documentElement).getPropertyValue("--st-not-started"));
  const at = S.visible.indexOf(j);
  try {
    const r = await post(`/api/jobs/${encodeURIComponent(j.id)}/track`);
    toast((r.action === "created" ? "Added to your tracker." : "Updated in your tracker.")
      + (S.notionError ? " Notion will get it when it's back." : ""));
    S.sel.tracker = j.id;
    S.picks.delete(j.id);
    await loadJobs();
    stepPast([j.id], at);
    loadJobs({ wait: true });
  } catch (e) { toast(e.message, true); }
}
// The checked roles in Find jobs, tracked in one go.
async function trackJobs(ids) {
  const at = S.visible.findIndex((j) => ids.includes(j.id));
  let done = 0;
  try {
    for (const id of ids) { await post(`/api/jobs/${encodeURIComponent(id)}/track`); done++; S.picks.delete(id); }
  } catch (e) { toast(e.message, true); }
  if (!done) return;
  toast(`Added ${plural(done, "role")} to your tracker.` + (S.notionError ? " Notion will get them when it's back." : ""));
  await loadJobs();
  stepPast(ids, at);
  loadJobs({ wait: true });
}
// The checked roles in Find jobs, dismissed (or restored) in one go.
async function dismissJobs(ids, dismissed) {
  const at = S.visible.findIndex((j) => ids.includes(j.id));
  let done = 0;
  try {
    for (const id of ids) { await post(`/api/jobs/${encodeURIComponent(id)}/dismiss`, { dismissed }); done++; S.picks.delete(id); }
  } catch (e) { toast(e.message, true); }
  if (!done) return;
  toast(dismissed ? `Dismissed ${plural(done, "role")}. They're under Dismissed if you change your mind.` : `Put ${plural(done, "role")} back in New.`);
  await loadJobs();
  stepPast(ids, at);
}
// After tracking, the open role left Find jobs: open the one that took its place in the list.
function stepPast(ids, at) {
  if (S.scope !== "find" || (S.selected && !ids.includes(S.selected))) return;
  const next = S.visible[Math.min(Math.max(at, 0), S.visible.length - 1)];
  if (next) { selectJob(next.id); next._el?.scrollIntoView({ block: "nearest" }); }
  else { S.selected = null; renderDetail(); }
}
async function dismissJob(j, dismissed) {
  j.dismissed = dismissed;
  try {
    await post(`/api/jobs/${encodeURIComponent(j.id)}/dismiss`, { dismissed });
    toast(dismissed ? "Dismissed. It's under Dismissed if you change your mind." : "Back in New.");
  } catch (e) { j.dismissed = !dismissed; toast(e.message, true); }
  if (!dismissed) S.findFilter = "new";
  j._el = j._card = null;
  setScope("find");       // moves on to the next role when this one left the list
}
// Only a dismissed role you never tracked: a tracked one has a tracker row (and maybe a Notion page) to keep.
async function deleteJob(j) {
  if (!confirm(`Delete ${j.title || "this job"}${j.company ? ` at ${j.company}` : ""}? Its posting text and scores are removed `
    + "from Job Oyster. If a search finds it again, it comes back as a new job.")) return;
  try {
    await request(`/api/jobs/${encodeURIComponent(j.id)}`, { method: "DELETE" });
  } catch (e) { toast(e.message, true); return; }
  S.details.delete(j.id);
  toast("Deleted.");
  await loadJobs();
  setScope("find");
}
function statusControl(j) {
  if (!j.tracker_id) {
    return h("span", { class: "actions-inline" },
      h("button", { class: "primary", onclick: () => trackJob(j) }, "Track"),
      h("button", { class: "ghost", onclick: () => dismissJob(j, !j.dismissed) }, j.dismissed ? "Restore" : "Dismiss"),
      j.dismissed && j.local && h("button", { class: "ghost danger", title: "Remove this job from Job Oyster", onclick: () => deleteJob(j) }, "Delete"));
  }
  const sel = h("select", { class: "status-select", "data-stage": j.status, "aria-label": "Status" },
    ...(S.summary?.statuses || [j.status]).map((s) => h("option", { value: s, selected: s === j.status }, s)));
  sel.addEventListener("change", () => setStatus(j, sel.value));
  return h("span", { class: "status-ctl" }, sel, ...quickStatus(j));
}
// The move that usually comes next, as a button beside the status menu (which still offers every status).
function quickStatus(j) {
  const go = (label, status, cls = "ghost small") => h("button", { class: `quick ${cls}`, "data-to": status, onclick: () => setStatus(j, status) }, label);
  switch (stageOf(j)) {
    case "Not started": case "In progress": case "Blocked": return [go("Mark applied", "Applied", "primary small")];
    case "Applied": return [go("Interviewing", "Interviewing", "primary small"), go("Denied", "Denied")];
    case "Interviewing": return [go("Offer", "Offer", "primary small"), go("Denied", "Denied")];
    case "Offer": return [go("Mark done", "Done")];
    case "Ghosted": {      // heard back after all: where it was, or the step after
      const back = j.ghosted_from || "Applied";
      return [go(`Back to ${back.toLowerCase()}`, back), back === "Applied" ? go("Interviewing", "Interviewing") : go("Offer", "Offer")];
    }
    default: return [go("Back to in progress", "In progress")];
  }
}
function detailHead(j) {
  if (S.fold.head) return h("div", { class: "dhead compact" },
    fitTile(j),
    h("h2", { title: [j.title, j.company].filter(Boolean).join(" · ") }, j.title || "(untitled)", j.company && h("span", { class: "sub" }, " · ", j.company)),
    statusControl(j),
    j.tailored ? h("span", { class: "score" }, "Résumé ", h("b", {}, j.resume_score ?? "–"), "/10 · ATS ", h("b", {}, pct(j.ats_total)))
      : j.impact_score != null && h("span", { class: "score" }, "Full score ", h("b", {}, j.impact_score), "/10"),
    refToggle(j),
    starButton(j),
    h("button", { class: "icon-btn fold-btn", title: "Show the details: location, links and the stage path", "aria-label": "Show the details",
      "aria-expanded": "false", onclick: () => setFold("head", false) }, icon("down")),
    h("button", { class: "icon-btn drawer-close", title: "Close", "aria-label": "Close", onclick: closeDrawer }, icon("close")),
    refCard(j));
  return h("div", { class: "dhead" },
    h("div", { class: "dhead-top" },
      fitTile(j, "big"),
      h("div", { class: "dhead-title" },
        h("h2", {}, j.title || "(untitled)"),
        h("div", { class: "sub" }, fundingTag(j), [j.company, j.location, known(j.mode), known(j.pay)].filter(Boolean).join(" · "),
          j.funding && h("span", { class: "funding-note" }, " · ", j.funding.line,
            j.funding.url && [" ", h("a", { href: safeHref(j.funding.url), target: "_blank", rel: "noopener", title: j.funding.headline }, "news ↗")],
            " · ", h("button", { class: "link-btn", onclick: () => openStartup(j.funding.startup_id, j.company) }, "Search Startups")))),
      h("div", { class: "dhead-actions" },
        starButton(j, true),
        // Full score first, then Make résumé, which reuses it.
        j.local && !j.tailored && j.impact_score == null && scoreButton(j.id, "full", false, true),
        j.local && !j.tailored && makeResumeButton(j, j.tracker_id && j.impact_score != null ? "primary" : "ghost"),
        j.local && !j.tailored && h("button", { class: "icon-btn help-btn", title: "What's the difference? What each does", "aria-label": "What each does",
          onclick: openActionsHelp }, icon("info")),
        h("button", { class: "icon-btn fold-btn", title: "Fold this header to one line", "aria-label": "Fold the header",
          "aria-expanded": "true", onclick: () => setFold("head", true) }, icon("up")),
        h("button", { class: "icon-btn drawer-close", title: "Close", "aria-label": "Close", onclick: closeDrawer }, icon("close")))),
    h("div", { class: "bar" },
      statusControl(j),
      refToggle(j),
      extLink(j.url, "Posting"),
      extLink(j.notion_url, "Notion"),
      j.tailored ? h("span", { class: "score" }, "Full score ", h("b", {}, j.impact_score ?? "–"), "/10 · Résumé ",
        h("b", {}, j.resume_score ?? "–"), "/10 · ATS ", h("b", {}, pct(j.ats_total)))
        : j.impact_score != null && h("span", { class: "score" }, "Full score ", h("b", {}, j.impact_score), "/10")),
    refCard(j),
    ghostNote(j),
    j.tracker_id && stagePath(j));
}
// A role the app moved to Ghosted on its own says so, when, and after how long (status_since: the day it reached
// the stage it was moved from).
function ghostNote(j) {
  if (stageOf(j) !== "Ghosted" || !j.ghosted_on) return null;
  const waited = j.status_since ? Math.round((keyDate(j.ghosted_on) - keyDate(j.status_since)) / 864e5) : null;
  return h("p", { class: "ghosted-note" }, h("b", {}, "Moved here on its own"),
    ` on ${fmtDate(keyDate(j.ghosted_on))}, after ${waited != null ? `${waited} days` : "a long wait"} at ${j.ghosted_from || "Applied"} with no change`,
    j.status_since ? ` (since ${fmtDate(keyDate(j.status_since))})` : "", ". Heard back? Pick its status.");
}
// Where a tracked role stands: the main line with the steps reached so far, and the exits, with the one taken.
function stagePath(j) {
  const cur = stageOf(j);
  const exit = EXITS.includes(cur) ? cur : null;
  // An exit is taken from somewhere on the line: Denied and Ghosted after applying (or interviewing), the other two before.
  const from = exit === "Ghosted" && j.ghosted_from === "Interviewing" ? "Interviewing" : ["Denied", "Ghosted"].includes(exit) ? "Applied" : "In progress";
  const at = exit ? PATH.indexOf(from) : PATH.indexOf(cur);
  return h("div", { class: "stage-path", "aria-label": `Status: ${cur}`, style: `--steps:${PATH.length}` },
    h("ol", { class: "path-line" }, ...PATH.map((s, i) => h("li", {
      class: ["step", i < at && "reached", i === at && (exit ? "reached" : "current")].filter(Boolean).join(" "),
      "data-stage": s, "aria-current": i === at && !exit ? "step" : null },
      h("span", { class: "dot" }), h("span", { class: "step-name" }, s)))),
    h("div", { class: "exits-line" }, h("span", { class: "lead" }, "Exits"),
      ...EXITS.map((s) => h("span", { class: "xstep" + (s === exit ? " taken" : ""), "data-stage": s }, h("i"), s))));
}
function renderDetailHead() {
  keepRefFocus(() => {
    const j = S.byId.get(S.selected);
    const old = $("#job-detail .dhead");
    if (j && old) old.replaceWith(detailHead(j));
  });
}
// A redraw keeps the cursor where it was in the referral card (the role's details load after it opens).
function keepRefFocus(draw) {
  const a = document.activeElement;
  const key = a?.closest?.("#ref-card") && (a.name ? `[name=${a.name}]` : a.dataset.step ? `[data-step=${a.dataset.step}]` : "");
  const at = key && a.selectionStart != null ? [a.selectionStart, a.selectionEnd] : null;
  draw();
  const el = key && $(`#ref-card ${key}`);
  if (!el || el === document.activeElement) return;
  el.focus();
  if (at) el.setSelectionRange(...at);
}
function renderDetail() { keepRefFocus(drawDetail); }
function scoreButton(id, kind, scored, primary = false) {
  const busy = S.scoring.get(id);
  const label = kind === "full" ? (scored ? "Run full score again" : "Run full score") : scored ? "Signal score again" : "Signal score";
  const blocked = kind === "full" && noPosting(S.byId.get(id));
  return h("button", { class: primary || !(scored || kind === "full") ? "primary" : "ghost", disabled: !!busy || blocked,
    title: blocked ? NO_POSTING : ACTIONS[kind].what,
    onclick: () => scoreJobs([id], kind) },
    busy?.kind === kind ? [spinner(), busy.state === "queued" ? " Waiting its turn…" : ` Scoring… ${actionTime(kind)}`] : [label, timeTag(kind)]);
}
function drawDetail() {
  const box = $("#job-detail");
  const j = S.byId.get(S.selected);
  if (!j) {
    box.replaceChildren(h("div", { class: "empty" }, S.visible.length || !S.loaded ? "Select a role to see its score, posting and résumé."
      : S.scope === "find" ? "No new roles here." : "Nothing tracked here yet."));
    return;
  }
  const head = detailHead(j);
  if (!j.local) {
    // Only in the tracker: no posting stored, but a résumé made outside the app may be.
    const tab = S.detailTabJob !== j.id && showsSent(j) ? "sent"
      : S.detailTab === "posting" ? "posting" : S.detailTab === "sent" && showsSent(j) ? "sent" : "resume";
    const tabs = [...(showsSent(j) ? [["sent", "Sent résumé"]] : []), ["resume", "Résumé"], ["posting", "Posting & score"]];
    const sub = h("div", { class: "subtabs" }, ...tabs.map(([k, label]) =>
      h("button", { class: k === tab ? "active" : "", onclick: () => { S.detailTab = k; S.detailTabJob = j.id; renderDetail(); } }, label)), foldAllButton());
    const panel = h("div", { class: "subpanel active" });
    box.replaceChildren(head, sub, panel);
    if (tab === "sent") renderSent(j, panel);
    else if (tab === "resume") renderOutside(j, panel);
    else {
      const read = scoreButton(j.id, "signal", false);
      read.className = "ghost";
      read.title = "Reads the posting from its page, when the page can be read";
      panel.append(h("div", { class: "pad" },
        h("div", { class: "notice" },
          h("p", {}, "This role is in your tracker but none of your filters found it, so its posting isn't stored yet. ",
            "Paste the description from the posting's page: it's kept with this role, and the scores and Make résumé compare your impact record against it."),
          scoreError(j.id),
          h("div", { class: "actions-inline" },
            h("button", { class: "primary", onclick: () => openDescribe(j) }, "Paste the description…"), read)),
        j.one_line && h("p", { class: "muted" }, j.one_line)));
    }
    return;
  }
  const d = S.details.get(j.id)?.d;
  if (!d) { box.replaceChildren(head, h("div", { class: "pad muted" }, spinner(), " Loading…")); return; }
  const off = (k) => (k === "heatmap" ? !d.has_heatmap : ["resume", "report"].includes(k) && !j.tailored);
  const tabs = [...(showsSent(j) ? [["sent", "Sent résumé"]] : []),
                ["resume", "Résumé"], ["report", "Fit report"], ["heatmap", "Heat map"], ["posting", "Posting & score"]];
  // A tab with nothing in it yet still opens (to say how to fill it), but only when clicked for this job:
  // moving to another job falls back to the posting, or for a job applied to, to the résumé sent.
  const fresh = S.detailTabJob !== j.id;
  const tab = fresh && showsSent(j) ? "sent"
    : (S.detailTab === "sent" && !showsSent(j)) || (off(S.detailTab) && fresh) ? "posting" : S.detailTab;
  const sub = h("div", { class: "subtabs" }, ...tabs.map(([k, label]) => h("button", {
    class: [k === tab && "active", off(k) && "empty"].filter(Boolean).join(" "),
    onclick: () => { S.detailTab = k; S.detailTabJob = j.id; renderDetail(); } }, label)), foldAllButton());
  const panel = h("div", { class: "subpanel active" });
  box.replaceChildren(head, sub, panel);
  if (tab === "sent") renderSent(j, panel);
  else if (off(tab) && tab === "resume") renderOutside(j, panel);
  else if (off(tab)) panel.append(emptyTab(j, d, tab));
  else if (tab === "resume") renderResume(d, panel);
  else if (tab === "report") renderReport(d, panel);
  else if (tab === "heatmap") panel.append(
    h("iframe", { class: "heatmap", sandbox: "allow-scripts", src: fileUrl(`/api/jobs/${encodeURIComponent(d.id)}/heatmap`) }));
  if (tab === "posting") panel.append(h("div", { class: "posting-split" },
    h("div", { class: "posting-pane" }, postingMeta(j, d), noPosting(j) ? postingNeeded(j) : postingBody(d.description)),
    h("aside", { class: "score-pane" }, scoreBlock(j, d))));
}

// What a Résumé, Fit report or Heat map tab says before the run that fills it.
function emptyTab(j, d, tab) {
  const tailor = makeResumeButton(j);
  const what = {
    resume: ["No résumé made yet", `${ACTIONS.resume.what} It takes ${actionTime("resume")}; you can follow its log in Logs.`],
    report: ["No fit report yet",
      "Make résumé writes the fit report: the scores, the ATS scorecard, the gaps, and at the end, the follow-up questions to answer. Add your answers as confirmed facts in the Impact record (under Profile), then make the résumé again."],
    heatmap: ["No heat map yet",
      `The heat map rates every requirement in the posting against your impact record and each résumé. A full score makes one in ${actionTime("full")} without writing a résumé; Make résumé makes one too.`],
  }[tab];
  return h("div", { class: "pad" }, h("div", { class: "notice" },
    h("h3", {}, what[0]), h("p", {}, what[1]), scoreError(d.id),
    h("div", { class: "actions-inline" }, tab === "heatmap" && scoreButton(d.id, "full", !!d.full_score), tailor)));
}

// The résumé sent with the application: the exact PDF, kept here and in Notion's Resume Used.
const SENT_SOURCE = {
  app: (x) => `The app's résumé (${x.note}), saved ${fmtDate(x.recorded)}`,
  upload: (x) => `Uploaded ${fmtDate(x.recorded)}`,
  notion: () => "From the Notion row's Resume Used",
};
async function renderSent(j, panel) {
  const id = encodeURIComponent(j.id);
  panel.replaceChildren(h("div", { class: "pad muted" }, spinner(), " Loading…"));
  let o;
  try { o = await api(`/api/jobs/${id}/sent`); }
  catch (e) { panel.replaceChildren(h("div", { class: "pad" }, h("p", { class: "form-error" }, e.message))); return; }
  if (S.selected !== j.id || !panel.isConnected) return;
  const done = (msg) => { toast(msg); loadJobs(); renderSent(j, panel); };
  const file = h("input", { type: "file", accept: "application/pdf,.pdf", class: "hidden" });
  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    try {
      await api(`/api/jobs/${id}/sent`, { method: "PUT", body: f,
        headers: { "Content-Type": "application/pdf", "X-Filename": encodeURIComponent(f.name) } });
      done(o.notion && !o.in_notion ? "Saved. It goes to Notion's Resume Used on the next sync." : "Saved.");
    } catch (e) { toast(e.message, true); }
  });
  const upload = (label, cls = "ghost") => h("button", { class: cls, onclick: () => file.click() }, label);
  const useCurrent = o.current_version && h("button", { class: o.sent ? "ghost" : "primary", onclick: async () => {
    try { await post(`/api/jobs/${id}/sent/current`); SENT_VIEW.delete(j.id); done(`Saved v${o.current_version} as the résumé sent.`); }
    catch (e) { toast(e.message, true); } } },
    o.sent?.source === "app" ? `Save v${o.current_version} as the résumé sent` : `Use the app's résumé (v${o.current_version})`);
  if (!o.sent) {
    panel.replaceChildren(h("div", { class: "pad" }, h("div", { class: "notice" },
      h("h3", {}, "No résumé on file for this application"),
      h("p", {}, "Add the PDF you sent, so you can see it here later. ",
        o.notion ? "It's also saved to the Notion row's Resume Used." : ""),
      h("div", { class: "actions-inline" }, upload("Upload the PDF you sent…", "primary"), useCurrent), file)));
    return;
  }
  const x = o.sent;
  const where = x.source === "notion" ? "" : o.in_notion ? " · In Notion" : o.notion ? " · Goes to Notion on the next sync" : "";
  const src = fileUrl(`/api/jobs/${id}/sent/file/${encodeURIComponent(x.download_name || x.name)}`) + `&n=${encodeURIComponent(x.recorded || x.name)}`;
  const forget = x.source !== "notion" && h("button", { class: "ghost danger", onclick: async () => {
    if (!confirm("Remove the copy kept here? Notion's Resume Used isn't changed.")) return;
    try { await api(`/api/jobs/${id}/sent`, { method: "DELETE" }); done("Removed."); }
    catch (e) { toast(e.message, true); } } }, "Remove");
  // Beside it, the résumé the app can edit for this role (its own, or the one made outside the app that it
  // shows), with the chat and Edit on page as on the Résumé tab. An edit is a new version of that résumé:
  // the copy on file stays as it was sent until you save the new version as the one sent.
  let r = null, d = S.details.get(j.id)?.d;
  if (o.current_version) {
    if (j.local && j.tailored && d?.resume) r = d.resume;
    else {
      try { r = (await api(`/api/jobs/${id}/outside-resume`)).resume; } catch { r = null; }
      if (S.selected !== j.id || !panel.isConnected) return;
      if (r) { d = { id: j.id, resume: r }; chatFromServer(j.id, r.chat, r.chat_busy); }
    }
  }
  const sentN = x.source === "app" && /^v\d+$/.test(x.note || "") ? Number(x.note.slice(1)) : null;
  const same = sentN != null && sentN === o.current_version;
  const bar = h("div", { class: "outside-bar" },
    h("span", {}, h("b", {}, x.name), h("span", { class: "muted" }, ` · ${SENT_SOURCE[x.source](x)}${where}`),
      r && !same && h("span", { class: "muted sent-edits" }, ` · Edits go to the app's résumé, now v${o.current_version}; this copy stays as sent.`),
      !r && h("span", { class: "muted sent-edits" }, " · To edit it here, the app needs the résumé's text: make one or pick one in the Résumé tab.")),
    !r && h("a", { href: src, target: "_blank" }, "Open PDF ↗"),
    upload("Replace…"), !same && useCurrent, forget, file);
  if (!r) {
    panel.replaceChildren(h("div", { class: "outside" }, bar, h("iframe", { class: "sent-pdf", src, title: "The résumé sent" })));
    return;
  }
  const label = `As sent${sentN ? ` (v${sentN})` : x.source === "upload" ? " (your upload)" : " (from Notion)"}`;
  const wrap = h("div", { class: "outside" }, bar);
  panel.replaceChildren(wrap);
  renderResume(d, wrap, { src, label, n: sentN });
}

// A résumé made outside the app (the resume-job-fit skill's run folder, or a saved résumé in
// the profile's references/) for a job the app didn't tailor: the one picked for it, or the one named for it.
async function renderOutside(j, panel) {
  const box = h("div", { class: "pad muted" }, spinner(), " Looking for a résumé…");
  panel.replaceChildren(box);
  let o;
  try { o = await api(`/api/jobs/${encodeURIComponent(j.id)}/outside-resume`); }
  catch (e) { box.replaceChildren(h("p", { class: "form-error" }, e.message)); return; }
  if (S.selected !== j.id || !panel.isConnected) return;
  const pick = h("select", { "aria-label": "Résumé to show" },
    h("option", { value: "" }, o.source ? "Choose another résumé…" : "Choose a résumé…"),
    ...o.options.map((x) => h("option", { value: x.key, selected: o.source?.key === x.key },
      x.headline ? `${x.label}: ${x.headline}` : x.label)),
    o.source && h("option", { value: "none" }, "None for this role"));
  pick.addEventListener("change", async () => {
    if (!pick.value) return;
    pick.disabled = true;
    try { await post(`/api/jobs/${encodeURIComponent(j.id)}/outside-resume`, { source: pick.value }); renderOutside(j, panel); }
    catch (e) { toast(e.message, true); pick.disabled = false; }
  });
  if (!o.resume) {
    panel.replaceChildren(h("div", { class: "pad" }, h("div", { class: "notice" },
      h("h3", {}, "No résumé for this role yet"),
      h("p", {}, j.local
        ? "Make one here, upload one you have (PDF, Word or text), or pick a résumé you made with the résumé skill in a Claude chat. Make résumé also writes the Fit report and the heat map."
        : "Upload one you have (PDF, Word or text), or pick a résumé you made with the résumé skill in a Claude chat. To make one here, the app needs the posting: paste its description."),
      o.options.length ? pick : h("p", { class: "muted" }, "No résumé runs (resume-runs/) or saved résumés (references/resume-*.md) in your profile folder."),
      h("div", { class: "actions-inline" },
        j.local ? makeResumeButton(j)
          : h("button", { class: "ghost", onclick: () => openDescribe(j) }, "Paste the description…"),
        uploadForJobButton(j, "Upload a résumé…")))));
    return;
  }
  const wrap = h("div", { class: "outside" }, h("div", { class: "outside-bar" },
    h("span", {}, o.matched ? "Matched by name: " : "Showing ", h("b", {}, o.source.label)), pick));
  panel.replaceChildren(wrap);
  chatFromServer(j.id, o.resume.chat, o.resume.chat_busy);
  renderResume({ id: j.id, resume: o.resume, upload: o.source.key === "upload" }, wrap);
}

// The posting, read beside its score. JobsPipe stores postings as plain text: blank-line paragraphs,
// "- " bullets, and section titles on their own short line.
const BULLET = /^\s*(?:[-*•·–]|\d+[.)])\s+/;
function postingBody(text) {
  const lines = (text || "").split("\n").map((l) => l.trim());
  const box = h("div", { class: "posting-body" });
  if (!lines.some(Boolean)) { box.append(h("p", { class: "muted" }, "No posting text is stored for this job. Paste the description from its page to score it and make its résumé.")); return box; }
  const next = (i) => lines.slice(i + 1).find(Boolean) || "";
  let list = null;
  lines.forEach((line, i) => {
    if (!line) return;
    if (BULLET.test(line)) {
      if (!list) box.append(list = h("ul"));
      list.append(h("li", {}, line.replace(BULLET, "")));
      return;
    }
    list = null;
    // A short line with no sentence ending, followed by a paragraph or a list, is a section title.
    const n = next(i);
    const title = line.length < 70 && !/[.,;!]$/.test(line) && (BULLET.test(n) || n.length >= 70);
    box.append(h(title ? "h4" : "p", {}, line));
  });
  return box;
}
function postingMeta(j, d) {
  const found = j.searches.filter((s) => s !== "manual");      // "manual": pasted or scored here, not found by a filter
  const posted = j.posted && new Date(j.posted.length === 10 ? j.posted + "T12:00" : j.posted);
  return h("div", { class: "posting-meta" },
    posted && !isNaN(posted) && h("span", {}, "Posted ", posted.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })),
    found.length ? h("span", {}, "Found by ", found.join(", ")) : null,
    j.source && h("span", { class: "source-note" }, "Listed on ", extLink(j.listing_url || j.url, j.source)),
    (!j.source || j.listing_url && j.listing_url !== j.url) && extLink(j.url, "Open the original"),
    d.description_pasted && h("span", {}, d.description_read ? "Added from its link " : "Pasted by you ", fmtDate(d.description_pasted)),
    !d.description_pasted && d.posting_kept && h("span", { title: "Kept because you applied: it stays here after the posting comes down." }, "Saved when you applied ", fmtDate(d.posting_kept)),
    !noPosting(j) && h("button", { class: "link-btn", onclick: () => openDescribe(j, d.description_pasted ? d.description : "") },
      d.description_pasted ? "Replace the pasted text…" : "Paste the description…"));
}

// Read a job's posting from its link, as Add job does: on its own once, when a job without its text first opens,
// and again from Fetch posting. FETCHING: job id -> "busy", or why its link couldn't be read.
const FETCHING = new Map();
async function fetchPosting(j, auto = false) {
  if (FETCHING.get(j.id) === "busy") return;
  FETCHING.set(j.id, "busy");
  if (S.selected === j.id) renderPostingNeeded();
  try {
    await post(`/api/jobs/${encodeURIComponent(j.id)}/fetch-posting`, {});
    FETCHING.set(j.id, "");
    S.details.delete(j.id);
    await loadJobs();
    if (S.selected === j.id) { renderDetail(); loadDetail(j.id); }
    if (!auto) toast("Posting added from its link.");
  } catch (e) {
    FETCHING.set(j.id, e.message);
    if (S.selected === j.id) renderPostingNeeded();
  }
}
// In the posting pane while the job has no posting text: why Run full score and Make résumé are off, and the two ways on.
// A closed one leads with that: pasting is the way on, since its page is gone.
function postingNeeded(j) {
  const st = FETCHING.get(j.id);
  if (st === "busy") return h("div", { class: "needs-posting", role: "status" }, spinner(), h("span", {}, "Reading the posting from its link…"));
  const closed = !!j.closed || /^This posting has closed/.test(st || "");
  const paste = h("button", { class: closed ? "primary small" : "ghost small", onclick: () => openDescribe(j) }, "Paste the description…");
  const fetch = j.url && h("button", { class: closed ? "ghost small" : "primary small", onclick: () => fetchPosting(j) },
    closed ? "Try fetching again" : "Fetch posting");
  return h("div", { class: "needs-posting", role: "status" },
    h("div", { class: "needs-why" },
      closed ? h("p", {}, h("b", {}, "This posting has closed."), " The employer took it down before its text was saved. "
          + "To score this job or make its résumé, paste the description if you have a copy.")
        : h("p", {}, h("b", {}, "No posting text yet."), j.url ? " Fetch it from the job's link, or paste the description, to score this job and make its résumé."
          : " Paste the description to score this job and make its résumé."),
      st && !closed && h("p", { class: "form-error" }, st)),
    h("div", { class: "actions-inline" }, ...(closed ? [paste, fetch] : [fetch, paste])));
}

function renderPostingNeeded() {
  const j = S.byId.get(S.selected), old = $("#job-detail .needs-posting");
  if (j && old) old.replaceWith(postingNeeded(j));
}

// The posting text pasted onto a role, for one whose stored text is missing, cut short or wrong.
const descDialog = $("#describe-dialog"), descForm = $("#describe-form");
function openDescribe(j, text = "") {
  descDialog._job = j;
  descForm.reset();
  formError(descForm);
  descForm.elements.namedItem("description").value = text;
  $("#describe-for").textContent = `${j.title}${j.company ? " at " + j.company : ""}. Copy the whole posting from its page. `
    + "It's kept with this role and used in place of any stored text when scoring it and making its résumé.";
  descDialog.showModal();
}
descForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const j = descDialog._job, save = $("button.primary", descForm);
  save.disabled = true;
  try {
    await put(`/api/jobs/${encodeURIComponent(j.id)}/description`, { description: descForm.elements.namedItem("description").value });
    descDialog.close();
    S.details.delete(j.id);
    await loadJobs();
    if (S.selected === j.id) { S.detailTab = "posting"; S.detailTabJob = j.id; renderDetail(); loadDetail(j.id); }
    scoreJobs([j.id], "signal");
    toast("Posting saved. Getting its signal score now.");
  } catch (err) { formError(descForm, err.message); }
  finally { save.disabled = false; }
});
$("[data-act=cancel]", descForm).addEventListener("click", () => descDialog.close());
function scoreError(id) {
  const msg = S.scoreErrors.get(id);
  return msg && h("p", { class: "form-error", role: "alert" }, msg);
}

// The rubric's bands (skill/references/scoring-and-report.md), lowest score of each band first.
const BANDS = [[10, "Exact match on skills, experience and level"], [9, "Significant match, missing at most one skill"],
               [8, "Covers most of the role"], [6, "Relevant skills and experience for about 60–70% of the role"],
               [4, "Relevant skills or relevant experience, not both"], [1, "Little relevant overlap"]];
// The signal score only says whether a role is worth a résumé; once a full score exists it replaces it.
function scoreBlock(j, d) {
  const full = j.tailored || d.full_score;
  return h("div", { class: "scorecard" }, scoreError(d.id), fullBlock(j, d), full ? null : signalBlock(j, d));
}
function fullBlock(j, d) {
  const f = d.full_score;
  if (j.tailored) {
    const n = j.impact_score;
    return h("div", { class: "card full-score" },
      h("div", { class: "score-head" },
        h("div", { class: `fit huge full ${fitClass(n)}` }, n ?? "–", h("small", {}, "/10")),
        h("div", {},
          h("h3", {}, "Full score"),
          h("div", { class: "band" }, n == null ? "" : BANDS.find(([min]) => n >= min)[1]),
          h("p", {}, "Make résumé rated every requirement: full score ", h("b", {}, n ?? "–"), "/10, the résumé it made ",
            h("b", {}, j.resume_score ?? "–"), "/10, ATS ", h("b", {}, pct(j.ats_total)),
            ". The résumé and ATS scores follow the current version: every edit is rated again. See Fit report and Heat map."))));
  }
  if (!f) {
    return h("div", { class: "notice" }, h("h3", {}, "Full score"),
      h("p", {}, `${ACTIONS.full.what} ${ACTIONS.full.calls[0].toUpperCase() + ACTIONS.full.calls.slice(1)}, ${actionTime("full")}.`),
      scoreButton(d.id, "full", false));
  }
  const impact = f.scores["Impact record"];
  const resumes = Object.entries(f.scores).filter(([src]) => src !== "Impact record");
  const best = resumes.reduce((a, b) => ((b[1] ?? 0) > (a[1] ?? 0) ? b : a), resumes[0] || ["", null]);
  const row = ([src, n]) => {
    const b = f.baseline[src] || {};
    return h("tr", { class: src === f.recommended_base ? "rec" : "" },
      h("td", {}, src, src === f.recommended_base ? h("span", { class: "tag" }, "best résumé") : null),
      h("td", {}, n ?? "–"), h("td", {}, pct(b.skills)), h("td", {}, pct(b.experience)), h("td", {}, b.total == null ? "–" : pct(b.total)));
  };
  return h("div", { class: "card full-score" },
    h("div", { class: "score-head" },
      h("div", { class: `fit huge full ${fitClass(impact)}` }, impact ?? "–", h("small", {}, "/10")),
      h("div", {},
        h("h3", {}, "Full score"),
        h("div", { class: "band" }, impact == null ? "" : BANDS.find(([min]) => impact >= min)[1]),
        best[1] != null && impact != null && impact - best[1] >= 1 && h("p", { class: "muted" },
          `Your best existing résumé scores ${best[1]}/10, so Make résumé can gain about ${impact - best[1]} ${impact - best[1] === 1 ? "point" : "points"}.`))),
    h("table", { class: "sources" },
      h("thead", {}, h("tr", {}, ...["Source", "/10", "Skills", "Exp.", "ATS"].map((x) => h("th", {}, x)))),
      h("tbody", {}, row(["Impact record", impact]), ...resumes.sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0)).map(row))),
    f.gaps?.length ? h("div", {}, h("h3", {}, "Gaps to confirm"), h("ul", {}, ...f.gaps.map((x) => h("li", {}, x)))) : null,
    h("div", { class: "score-foot" },
      h("span", { class: "muted" }, d.full_scored_at ? `Scored ${fmtDate(d.full_scored_at + "T12:00")}.` : ""),
      d.has_heatmap && h("button", { class: "ghost", onclick: () => { S.detailTab = "heatmap"; renderDetail(); } }, "Heat map"),
      scoreButton(d.id, "full", true)));
}
function signalBlock(j, d) {
  const t = d.triage;
  const rec = S.summary?.impact_record;
  const basis = ["From your ", h("button", { class: "link-btn inline-link", onclick: () => showTab("impact") }, "impact record"),
                 rec?.updated ? ` (last edited ${fmtDate(rec.updated * 1000)})` : "", " and confirmed facts"];
  if (!t) {
    return h("div", { class: "notice" },
      h("h3", {}, "Signal score"),
      h("p", {}, `${ACTIONS.signal.what} ${ACTIONS.signal.calls[0].toUpperCase() + ACTIONS.signal.calls.slice(1)}, ${actionTime("signal")}.`),
      scoreButton(d.id, "signal", false));
  }
  const list = (title, items, cls = "") => h("div", { class: `card ${cls}` }, h("h3", {}, title),
    items?.length ? h("ul", {}, ...items.map((x) => h("li", {}, x))) : h("p", { class: "muted" }, "None found."));
  return h("div", { class: "signal" },
    h("div", { class: "score-head" },
      h("div", { class: `fit huge ${fitClass(t.fit_score)}` }, t.fit_score, h("small", {}, "/10")),
      h("div", {},
        h("h3", {}, "Signal score"),
        h("div", { class: "band" }, BANDS.find(([min]) => t.fit_score >= min)[1]),
        h("p", {}, t.one_line),
        h("p", { class: "muted" }, t.band_reason, " Level: ", t.level_match, ". Best existing résumé: ", t.recommended_base, "."))),
    t.coverage_pct != null && t.requirements?.length ? h("details", { class: "rows" },
      h("summary", {}, `Covers ${Math.round(t.coverage_pct)}% of what the posting asks, weighted (${plural(t.requirements.length, "requirement")})`),
      h("table", { class: "sources" }, h("tbody", {}, ...t.requirements.map((r) => h("tr", {},
        h("td", {}, r.req), h("td", {}, r.weight), h("td", { class: `rate-${r.rating}` }, r.rating)))))) : null,
    h("div", { class: "score-lists" },
      list("Strongest matches", t.strongest_matches, "is-good"),
      list("Hard requirements to check", t.hard_requirement_issues, "is-warn"),
      list("Likely gaps (unconfirmed)", t.likely_gaps)),
    h("div", { class: "score-foot" },
      h("span", { class: "muted" }, basis, d.triaged_at ? ` on ${fmtDate(d.triaged_at + "T12:00")}.` : "."),
      scoreButton(d.id, "signal", true)));
}

async function renderReport(d, panel) {
  const box = h("div", { class: "pad" }, spinner());
  panel.append(box);
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(d.id)}/report`);
    box.replaceChildren(h("div", { class: "markdown", html: r.html }));
  } catch (e) { box.replaceChildren(h("div", { class: "muted" }, e.message)); }
}

// ---- résumé workspace ---------------------------------------------------------------------------
async function reloadResume(id) {
  const j = S.byId.get(id);
  if (!j?.local || !j.tailored) { renderDetail(); return; }   // an outside résumé: renderOutside reloads it
  S.details.delete(id);
  await loadJobs();         // the job's scores first (they follow the current version), then the panel showing them
  loadDetail(id);
}
// A résumé drawn in another format than the Profile's: redraw it in the default, or, when it would go over that
// format's page limit, ask Claude to trim it to fit (a proposal you accept, like any edit).
function formatNote(d, r, trim) {
  const f = r.format, def = r.default_format;
  if (!f || !def || f.id === def.id) return null;
  const id = encodeURIComponent(d.id);
  const box = h("div", { class: "format-note", role: "status" });
  const use = h("button", { class: "primary small" }, `Use ${def.name}`);
  const later = h("button", { class: "ghost small" }, `Keep ${f.name}`);
  later.addEventListener("click", () => box.remove());
  use.addEventListener("click", async () => {
    use.disabled = true;
    use.replaceChildren(spinner(), ` Redrawing…`);
    try {
      const res = await post(`/api/jobs/${id}/resume/format`);
      if (res.applied) { toast(`Redrawn in ${def.name}.`); reloadResume(d.id); return; }
      const go = h("button", { class: "primary small" }, `Trim to ${def.pages_word}`);
      go.addEventListener("click", () => { box.remove(); trim(res.trim, res.trim_format); });
      box.replaceChildren(h("span", { class: "grow" }, `In ${def.name} this résumé runs to ${res.pages} pages, over its ${def.pages_word}. `,
        "Claude can trim it to fit, deleting only; you see the change before it's saved."), go, later);
    } catch (e) { toast(e.message, true); use.disabled = false; use.textContent = `Use ${def.name}`; }
  });
  box.append(h("span", { class: "grow" }, `Drawn in ${f.name}. Your résumé format is ${def.name}.`), use, later);
  return box;
}
// The résumé chat's model menu: every AI set up on the Profile tab, with the Profile's chat model as the
// default. Your pick is remembered in this browser; picking the default again follows the Profile.
let CHAT_MODELS = null;     // a promise of /api/ai/chat-models, asked for again after the AI settings are saved
const chatModels = () => (CHAT_MODELS ||= api("/api/ai/chat-models").catch((e) => { CHAT_MODELS = null; throw e; }));
function chatChoice(v) {
  try {
    if (v === undefined) return localStorage.getItem("chat-model") || "";
    v ? localStorage.setItem("chat-model", v) : localStorage.removeItem("chat-model");
  } catch { return ""; }
}
function chatModelMenu() {
  const sel = h("select", { class: "chat-model", "aria-label": "Model for this chat", title: "Which AI answers in this chat" },
    h("option", { value: "" }, "Loading models…"));
  chatModels().then((d) => {
    const def = `${d.default.backend}|${d.default.model}`, want = chatChoice();
    const all = d.groups.flatMap((g) => g.models.map((m) => `${g.backend}|${m}`));
    const pick = all.includes(want) ? want : def;
    sel.replaceChildren(...d.groups.map((g) => h("optgroup", { label: g.label }, ...g.models.map((m) => {
      const v = `${g.backend}|${m}`;
      return h("option", { value: v, selected: v === pick }, `${g.short} · ${m}${v === def ? " (default)" : ""}`);
    }))));
    sel.dataset.default = def;
  }).catch(() => sel.replaceChildren(h("option", { value: "" }, "The Profile's model")));
  sel.addEventListener("change", () => chatChoice(sel.value === sel.dataset.default ? "" : sel.value));
  return sel;
}
const chatModelOf = (sel) => {
  const [backend, ...model] = sel.value.split("|");
  return backend ? { backend, model: model.join("|") } : {};
};
const chatModelName = (sel) => (sel.selectedOptions[0]?.textContent || "").split(" · ")[0] || "The AI";

// The line between the PDF and the chat: drag it (or focus it and use ← →) to give either side the room;
// a double-click puts it back. The PDF's share is kept for every role, in this browser.
function splitHandle(grid) {
  const MIN_PDF = 280, MIN_EDIT = 320;
  const handle = h("div", { class: "split-handle", role: "separator", tabindex: "0", "aria-orientation": "vertical",
    "aria-label": "Resize the PDF and the chat", title: "Drag to resize the PDF and the chat. Double-click puts it back." });
  const apply = (f) => {
    const w = grid.clientWidth;
    if (f != null && w) f = Math.min(1 - MIN_EDIT / w, Math.max(MIN_PDF / w, f));
    S.split = f;
    grid.style.setProperty("--pdf-fr", f == null ? "" : `${f.toFixed(4)}fr`);
    grid.style.setProperty("--edit-fr", f == null ? "" : `${(1 - f).toFixed(4)}fr`);
  };
  const share = () => grid.firstElementChild.getBoundingClientRect().width / (grid.clientWidth || 1);
  if (S.split != null) apply(S.split);
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    grid.classList.add("dragging");            // the PDFs stop taking the pointer while it moves over them
    const box = grid.getBoundingClientRect();
    const move = (ev) => apply((ev.clientX - box.left) / box.width);
    handle.addEventListener("pointermove", move);
    handle.addEventListener("lostpointercapture", () => {
      handle.removeEventListener("pointermove", move);
      grid.classList.remove("dragging");
      savePrefs();
    }, { once: true });
  });
  handle.addEventListener("dblclick", () => { apply(null); savePrefs(); });
  handle.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: -0.03, ArrowRight: 0.03 }[e.key];
    if (!step) return;
    e.preventDefault();
    apply(share() + step);
    savePrefs();
  });
  return handle;
}

// Which PDF the Sent résumé tab shows for a role: the copy on file ("sent"), or after an edit made there, the
// app's newest version ("current"), so the change is what you see when the tab redraws.
const SENT_VIEW = new Map();
// sent: on the Sent résumé tab, the copy on file ({src, label, n: the app's version it is, if it is one}),
// shown first and kept in the version menu. Editing there works on the current version, as on the Résumé tab.
function renderResume(d, panel, sent = null) {
  const r = d.resume;
  const id = encodeURIComponent(d.id);
  const urlOf = (v) => (v === "sent" ? sent.src : resumeUrl(id, v, r));
  const first = sent && SENT_VIEW.get(d.id) !== "current" ? "sent" : r.current;
  const toCurrent = () => { if (sent) SENT_VIEW.set(d.id, "current"); };
  const versionSel = h("select", { "aria-label": "Version to show" },
    sent && h("option", { value: "sent", selected: first === "sent" }, sent.label),
    ...r.versions.slice().reverse().map((v) => {
      const tags = [v.n === r.current && "current", v.n === sent?.n && "sent"].filter(Boolean).join(", ");
      return h("option", { value: v.n, selected: v.n === first }, `v${v.n}${tags ? ` (${tags})` : ""}`);
    }));
  const frame = h("iframe", { src: urlOf(first), title: "Résumé PDF" });
  const pdfLink = h("a", { href: urlOf(first), target: "_blank" }, "Open PDF ↗");
  versionSel.addEventListener("change", () => {
    pdf.single();
    frame.src = pdfLink.href = urlOf(versionSel.value);
    if (sent) SENT_VIEW.set(d.id, versionSel.value === "sent" ? "sent" : "current");
  });
  const editBtn = h("button", { class: "ghost small", title: "Change the wording yourself, right on the page" }, "Edit on page");
  const fmtNote = formatNote(d, r, (text, fmt) => { instr.value = chat.draft = text; send(fmt); });
  const pdfbar = h("div", { class: "pdfbar" }, "Version", versionSel, pdfLink, h("span", { class: "grow" }),
    !sent && uploadForJobButton(S.byId.get(d.id), "Upload…", "ghost small"), editBtn);
  const pdfpane = h("div", { class: "pdfpane" }, pdfbar, fmtNote, frame);
  const pdf = sideBySide(d, r, pdfpane);
  editBtn.addEventListener("click", () => { pdf.single(); toCurrent(); openPageEditor(d, pdfpane); });

  const editpane = h("div", { class: "editpane" });
  const cur = r.versions.find((v) => v.n === r.current);
  // Chat with Claude about this résumé: questions get answers; a change you ask for comes back as a proposal.
  const thread = h("div", { class: "chat-thread", "aria-live": "polite" });
  const chat = CHATS.get(d.id) || chatFromServer(d.id, r.chat, r.chat_busy);
  const instr = h("textarea", { rows: 2, placeholder: "Ask about this résumé, or say what to change…" });
  instr.value = chat.draft;
  instr.addEventListener("input", () => { chat.draft = instr.value; });
  const askBtn = h("button", { class: "primary" }, "Send");
  const newBtn = h("button", { class: "ghost small", title: "Clear this conversation (versions are kept)" }, "New chat");
  const modelSel = chatModelMenu();
  const askCard = h("div", { class: "card chat" },
    h("div", { class: "chat-head" }, h("h3", {}, "Chat"), modelSel, newBtn),
    thread,
    h("div", { class: "chat-input" }, instr, askBtn),
    h("p", { class: "muted chat-hint" }, "Enter sends, Shift+Enter adds a line. The résumé only changes when you ask, and you see every change before it's saved."));
  const proposalBox = h("div");
  const metrics = h("div", { class: "card" }, h("h3", {}, `Current version: v${r.current}`),
    cur?.rating && h("p", { class: "muted" }, spinner(), " Re-rating this edit: ATS shows the new keywords; skills and experience follow in a few seconds."),
    scoreMeters(S.byId.get(d.id), cur),
    h("div", { class: "metrics" },
      h("span", {}, "Keywords ", h("b", {}, pct(cur?.keywords_pct))),
      h("span", {}, "Format check ", checkBadge(cur?.check))));
  const ask = (text) => { if (r.editable === false) return; instr.value = chat.draft = text; instr.focus(); };
  const readOnly = r.editable === false && h("div", { class: "card" }, h("h3", {}, "Chat with Claude isn't available"),
    h("p", { class: "muted" }, "This résumé was saved without its posting and match brief, which Claude needs to check edits against. Use Make résumé in the app to get a copy Claude can edit. You can still change the wording yourself: Edit on page, above the PDF."));
  const scoreCard = d.upload && cur?.ats == null && !cur?.rating && uploadScoreCard(d.id, r.editable === false);
  editpane.append(...[scoreCard && r.editable === false ? scoreCard : readOnly || askCard, proposalBox,
    scoreCard && r.editable !== false && scoreCard, metrics, ...versionCards(d, r, pdf, ask)].filter(Boolean));
  const grid = h("div", { class: "resume" }, pdfpane);
  grid.append(splitHandle(grid), editpane);
  panel.append(grid);

  const starters = ["What's the weakest part of this résumé for this role?", "What would a recruiter question here?",
                    "Which posting keywords am I missing?"];
  const bubble = (m) => m.role === "note" ? h("div", { class: "msg note" }, m.text)
    : m.role === "user" ? h("div", { class: "msg me" }, m.text)
    : h("div", { class: "msg claude" }, h("div", { class: "markdown", html: m.html || "" }),
        m.proposal && h("div", { class: "msg-tag" }, "Proposed an edit"),
        m.via && h("div", { class: "msg-via" }, m.via));
  // Draws the chat from CHATS; this panel's copy replaces any earlier one's, so a reply that comes
  // back after the panel was redrawn lands in the one on screen.
  const paint = () => {
    if (!thread.isConnected) return;
    const near = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40;
    thread.replaceChildren(...(chat.msgs.length ? chat.msgs.map(bubble) : [h("div", { class: "msg claude intro" },
      h("p", {}, "Ask me anything about this résumé and the role, or tell me what to change. When you ask for a change I'll propose it, and the PDF shows it before anything is saved."),
      h("div", { class: "starters" }, ...starters.map((q) => h("button", { class: "ghost small", disabled: chat.busy, onclick: () => { instr.value = q; send(); } }, q))))]),
      chat.busy ? h("div", { class: "msg claude pending" }, spinner(), ` ${chat.thinking || "Claude"} is thinking…`) : "");
    if (near || chat.busy) thread.scrollTop = thread.scrollHeight;
    askBtn.disabled = newBtn.disabled = chat.busy;
    askBtn.title = chat.busy ? "Claude is still answering your last message" : "";
    if (chat.busy && !chat.sending) watchChat(d.id);    // sent from before a reload, or from another window
  };
  const showProposal = (p) => {
    pdf.single();
    toCurrent();
    renderProposal(d, p, proposalBox, frame, cur);
    proposalBox.scrollIntoView({ block: "start" });
  };
  const refocus = () => {      // the unsent text back in the box; don't undo the scroll to a proposal
    instr.value = chat.draft;
    instr.focus({ preventScroll: true });
  };
  chat.view = { paint, showProposal, refocus, isOn: () => thread.isConnected };
  async function send(format) {      // format: the trim for a new default format is held to it
    const text = instr.value.trim();
    if (!text || chat.busy) { instr.focus(); return; }
    chat.busy = chat.sending = true;
    chat.thinking = chatModelName(modelSel);
    chat.draft = instr.value = "";
    chat.msgs = [...chat.msgs, { role: "user", text }];
    chat.view.paint();
    try {
      const res = await post(`/api/jobs/${id}/edit`, { instruction: text, ...chatModelOf(modelSel),
                                                       ...(typeof format === "string" ? { format } : {}) });
      chat.msgs = res.chat;
      chat.busy = chat.sending = false;
      if (res.proposal) {
        const cached = S.details.get(d.id)?.d;
        if (cached?.resume) cached.resume.proposal = true;    // a redraw from the cache shows it too
        if (chat.view.isOn()) chat.view.showProposal(res.proposal);
      }
    } catch (e) {
      chat.busy = chat.sending = false;
      if (!chat.draft) chat.draft = text;     // back in the box to send again
      toast(e.message, true);
      chat.msgs = chat.msgs.slice(0, -1);
      await refreshChat(d.id);                // the server's copy: the message, and why it wasn't answered
    }
    if (chat.view.isOn()) { chat.view.paint(); chat.view.refocus(); }
  }
  askBtn.addEventListener("click", () => send());
  instr.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  newBtn.addEventListener("click", async () => {
    if (!chat.msgs.length || chat.busy) return;
    try { chat.msgs = (await post(`/api/jobs/${id}/chat/clear`)).chat; paint(); }
    catch (e) { toast(e.message, true); }
  });
  if (r.proposal) api(`/api/jobs/${id}/edit`).then((res) => { if (res.proposal) { pdf.single(); toCurrent(); renderProposal(d, res.proposal, proposalBox, frame, cur); } });
  paint();
}

// The résumé chat for each job, kept apart from its panel: the panel is redrawn whenever the job's
// data changes (a sync, another tab or role), and a message waiting on Claude, or one half typed,
// has to survive that. The server keeps a message as soon as it's sent and says while Claude is on it.
const CHATS = new Map();     // job id -> {msgs, busy, sending, draft, view}
function chatFromServer(id, msgs, busy) {
  const c = CHATS.get(id) || { msgs: [], busy: false, sending: false, draft: "", view: null };
  CHATS.set(id, c);
  if (!c.sending) { c.msgs = msgs || []; c.busy = !!busy; }   // this window's own send knows better
  c.view?.paint();
  return c;
}
async function refreshChat(id) {
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(id)}/chat`);
    chatFromServer(id, r.chat, r.busy);
  } catch { /* the next look at the job fetches it */ }
}
// Claude is answering a message this window didn't send (or sent before a reload): check back until it's done.
function watchChat(id) {
  const c = CHATS.get(id);
  if (!c || c.watching) return;
  c.watching = setTimeout(async () => {
    c.watching = null;
    if (c.sending || !c.view?.isOn()) return;        // the panel coming back watches again
    await refreshChat(id);
    if (!c.busy && c.msgs.at(-1)?.proposal) loadDetail(id);    // its proposed edit and scores
  }, 2000);
}

// Edit on page: the current version drawn in the PDF's layout (the same CSS), every line
// click-to-edit. Save sends only the lines that changed; the server writes them into the markdown,
// re-renders the PDF and keeps it as a new version. "Edit as text" is the fallback for changes
// the page can't make, such as adding a bullet or a job.
const PAGE_PX = 96;                                  // CSS px per inch
const plain = (t) => t.replace(/[\u00a0\s]+/g, " ").trim();
async function openPageEditor(d, pane) {
  const id = encodeURIComponent(d.id);
  let pg;
  try { pg = await api(`/api/jobs/${id}/resume/page`); }
  catch (e) { toast(e.message, true); return; }
  if (pg.proposal && !confirm("Claude has a proposed edit waiting. Saving a change of your own discards it. Edit anyway?")) return;
  const before = [...pane.childNodes];
  let mode = "page";
  const orig = new Map();
  const status = h("span", { class: "muted page-status" });
  const pages = h("span", { class: "muted page-count" });
  const modeBtn = h("button", { class: "ghost small" }, "Edit as text");
  const cancel = h("button", { class: "ghost small" }, "Cancel");
  const save = h("button", { class: "primary small", disabled: true }, "Save");
  const page = h("iframe", { class: "page-editor", title: "Résumé, editable", srcdoc: pg.html });
  const text = h("textarea", { class: "md-editor", spellcheck: "true", hidden: true, "aria-label": "Résumé markdown" });
  text.value = pg.markdown;
  pane.replaceChildren(h("div", { class: "pdfbar page-bar" }, h("b", {}, `Editing v${pg.n}`), status, pages,
    h("span", { class: "grow" }), modeBtn, cancel, save), page, text);

  const changed = () => [...orig].filter(([el, t]) => plain(el.textContent) !== plain(t));
  const dirty = () => (mode === "page" ? changed().length : text.value !== pg.markdown ? 1 : 0);
  const refresh = () => {
    const n = dirty();
    save.disabled = !n;
    status.textContent = mode === "text" ? (n ? "Unsaved changes" : "The résumé's markdown. Keep its shape: # Name, **headline**, contact line, ## sections.")
      : n ? `${n} change${n > 1 ? "s" : ""} not saved` : "Click any line to change it. Enter or Esc finishes a line.";
  };
  // Where the PDF's page breaks fall, roughly: the page is 11in with the format's margins.
  let guideTimer;
  const guides = () => {
    const doc = page.contentDocument;
    if (!doc?.body) return;
    doc.querySelectorAll(".page-guide").forEach((g) => g.remove());
    const m = pg.margin_in ?? 0.625, top = m * PAGE_PX, per = (11 - 2 * m) * PAGE_PX;
    const content = doc.body.scrollHeight - 2 * top;
    let k = 1;
    for (; k * per < content; k++) {
      const g = doc.createElement("div");
      g.className = "page-guide";
      g.style.top = `${top + k * per}px`;
      g.append(Object.assign(doc.createElement("span"), { textContent: `page ${k + 1} starts about here` }));
      doc.body.append(g);
    }
    pages.textContent = `≈ ${k} page${k > 1 ? "s" : ""}`;
    pages.classList.toggle("fail", k > (pg.max_pages ?? 2));
  };
  page.addEventListener("load", () => {
    const doc = page.contentDocument;
    orig.clear();
    doc.querySelectorAll("[data-ed]").forEach((el) => {
      orig.set(el, el.textContent);
      el.addEventListener("input", () => {
        el.classList.toggle("changed", plain(el.textContent) !== plain(orig.get(el)));
        refresh();
        clearTimeout(guideTimer);
        guideTimer = setTimeout(guides, 250);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); el.blur(); }
      });
      el.addEventListener("paste", (e) => {   // one line of plain text, whatever was copied
        e.preventDefault();
        doc.execCommand("insertText", false, plain(e.clipboardData.getData("text/plain")));
      });
    });
    doc.querySelectorAll("a").forEach((a) => a.addEventListener("click", (e) => e.preventDefault()));
    fit();
    guides();
    refresh();
  });
  // Shrink the 8.5in page to the pane's width; zoom keeps the line breaks the PDF has.
  const fit = () => {
    const root = page.contentDocument?.documentElement;
    if (root) root.style.zoom = Math.min(1, (page.clientWidth - 32) / (8.5 * PAGE_PX)).toFixed(3);
  };
  new ResizeObserver(fit).observe(page);
  text.addEventListener("input", refresh);

  modeBtn.addEventListener("click", () => {
    if (dirty() && !confirm("Switching discards the changes you haven't saved. Switch anyway?")) return;
    mode = mode === "page" ? "text" : "page";
    text.value = pg.markdown;
    if (mode === "page") page.srcdoc = pg.html;
    page.hidden = mode === "text";
    text.hidden = mode === "page";
    pages.hidden = mode === "text";
    modeBtn.textContent = mode === "page" ? "Edit as text" : "Edit on page";
    refresh();
    if (mode === "text") { text.setSelectionRange(0, 0); text.focus({ preventScroll: true }); text.scrollTop = 0; }
  });
  cancel.addEventListener("click", () => {
    if (dirty() && !confirm("Discard the changes you haven't saved?")) return;
    pane.replaceChildren(...before);
  });
  save.addEventListener("click", async () => {
    save.disabled = modeBtn.disabled = cancel.disabled = true;
    status.replaceChildren(spinner(), " Saving and making the PDF…");
    const body = mode === "page"
      ? { base: pg.n, edits: changed().map(([el, t]) => ({ key: el.dataset.ed, old: t, text: el.textContent })) }
      : { base: pg.n, markdown: text.value };
    try {
      const res = await post(`/api/jobs/${id}/resume/page`, body);
      const check = res.versions.find((v) => v.n === res.current)?.check;
      const warn = [res.pages > 2 && `it runs to ${res.pages} pages`, check?.passed === false && "the format check failed"].filter(Boolean);
      toast(`Saved as v${res.current}. The deliverable PDF now matches it.` + (warn.length ? ` Note: ${warn.join(" and ")}.` : ""), warn.length > 0);
      reloadResume(d.id);
      if (res.versions.find((v) => v.n === res.current)?.rating) {   // keywords are in; re-rate skills and experience
        post(`/api/jobs/${id}/resume/rescore`, { n: res.current })
          .then(() => reloadResume(d.id))
          .catch((e) => toast(`Couldn't re-rate the edit: ${e.message}`, true));
      }
    } catch (e) {
      toast(e.message, true);
      modeBtn.disabled = cancel.disabled = false;
      refresh();
    }
  });
}

// A résumé uploaded for a role the app didn't tailor has nothing to be scored on until the role's posting is
// broken down into requirements (its full score); Score résumé does that, then rates the upload on every one.
function uploadScoreCard(id, noChat) {
  const busy = S.scoring.get(id);
  return h("div", { class: "card upload-score" }, h("h3", {}, busy ? "Scoring your résumé" : "Not scored yet"),
    h("p", { class: "muted" }, busy
      ? `Breaking the posting into requirements (its full score, reused if it has one), then rating your résumé on each. ${busy.state === "queued" ? "Waiting its turn." : `It takes ${actionTime("upload")}.`}`
      : `Your résumé gets a résumé score, ATS and keywords, and the format check, once the posting is broken down into requirements and your résumé is rated on each.${noChat ? " That also turns on the chat." : ""}`),
    scoreError(id),
    !busy && h("button", { class: "primary", title: ACTIONS.upload.what, onclick: () => scoreJobs([id], "upload") },
      ACTIONS.upload.name, timeTag("upload")));
}

// The résumé's numbers drawn to scale: fit and résumé out of 10, ATS and keywords out of 100.
// Résumé and ATS are the version's own (each edit is re-rated), else the tailoring run's.
const versionAts = (j, v) => v?.ats ?? j?.ats_total;
const versionScore = (j, v) => v?.resume_score ?? j?.resume_score;
function scoreMeters(j, cur) {
  const pctBand = (v) => (v >= 80 ? "hi" : v >= 60 ? "mid" : "lo");
  const rows = [[j?.impact_score != null ? "Full score" : "Signal score", j && score(j), 10, "/10", fitClass], ["Résumé", versionScore(j, cur), 10, "/10", fitClass],
                ["ATS", versionAts(j, cur), 100, "%", pctBand], ["Keywords", cur?.keywords_pct, 100, "%", pctBand]]
    .filter(([, v]) => v != null);
  if (!rows.length) return null;
  return h("div", { class: "meters" }, ...rows.map(([label, v, max, unit, band]) => h("div", { class: `meter ${band(v)}` },
    h("span", { class: "lbl" }, label), h("span", { class: "val" }, Math.round(v), h("small", {}, unit)),
    h("span", { class: "track" }, h("i", { style: `width:${Math.max(2, Math.min(100, (v / max) * 100))}%` })))));
}
// ---- scores across versions -------------------------------------------------------------------
// Every version keeps the scores it was rated with. A chart draws one of them across the versions with the
// résumé score under it; clicking a version compares it (A) with another (B: the current one until you pick),
// as five score tiles, the requirements whose rating moved, the keywords gained and lost, and the two PDFs
// side by side. No AI call: the scores are stored, and the comparison is worked out from the two files.
const TREND = [["ats", "ATS", (v) => v.ats], ["keywords", "Keywords", (v) => v.keywords_pct],
               ["skills", "Skills", (v) => v.ats_parts?.skills], ["experience", "Experience", (v) => v.ats_parts?.experience]];
const ATS_TARGET = 80;
const COMPARE = new Map();     // job id -> {a, b, side}: kept while the panel is redrawn
function versionWhat(v) {
  if (v.instruction) return `“${v.instruction}”`;
  return v.source === "pipeline" ? "From Make résumé" : v.changes || v.source || "";
}
function versionShort(v) {
  if (v.source === "pipeline") return "Make résumé";
  if (v.source?.startsWith("restored")) return `Restored ${v.source.slice(9)}`;
  if (v.source === "by hand") return /^Edited as text/.test(v.changes || "") ? "Edited as text" : "Edit on page";
  if (v.source === "upload") return "Uploaded";
  const t = v.instruction || v.changes || "";
  return t.length > 24 ? `${t.slice(0, 23).trimEnd()}…` : t;
}
// The change from `was` to `now`: ▲ / ▼ with the amount (whole points for a %, tenths for a /10 score).
function deltaChip(now, was, pctUnit) {
  if (now == null || was == null) return null;
  const step = pctUnit ? 1 : 10, dv = Math.round((now - was) * step) / step;
  return h("span", { class: `delta ${dv > 0 ? "up" : dv < 0 ? "down" : "same"}` }, dv > 0 ? `▲${dv}` : dv < 0 ? `▼${-dv}` : "±0");
}
function compareState(jid, r) {
  const ns = r.versions.map((v) => v.n);
  const c = COMPARE.get(jid) || { side: false };
  if (!ns.includes(c.b)) c.b = r.current;
  if (!ns.includes(c.a) || c.a === c.b) c.a = ns[Math.max(0, ns.indexOf(c.b) - 1)];
  if (c.a === c.b) c.a = ns.find((n) => n !== c.b);
  COMPARE.set(jid, c);
  return c;
}

// The PDF pane shows version A beside version B while side by side is on; everything else (a proposal,
// another version picked, Edit on page) puts the one PDF back first.
function sideBySide(d, r, pane) {
  const id = encodeURIComponent(d.id);
  const c = () => compareState(d.id, r);
  const col = (tag, n) => {
    const v = r.versions.find((x) => x.n === n);
    return h("div", { class: "side-col" },
      h("div", { class: "side-cap" }, h("span", { class: `vtag ${tag.toLowerCase()}` }, `${tag} · v${n}`), " ", versionShort(v),
        h("span", { class: "muted" }, ` · ${v.resume_score ?? "–"}/10 · ${pct(v.ats)}`)),
      h("iframe", { src: resumeUrl(id, n, r), title: `Résumé v${n}` }));
  };
  let saved = null;            // the pane's own children while the two PDFs are up
  const show = () => {
    const s = c();
    if (!s.side) { if (saved) pane.replaceChildren(...saved); saved = null; return; }
    saved = saved || [...pane.childNodes];
    const one = h("button", { class: "ghost small" }, "One at a time");
    one.addEventListener("click", () => { s.side = false; show(); self.changed?.(); });
    pane.replaceChildren(h("div", { class: "pdfbar" }, h("b", {}, `v${s.a} beside v${s.b}`), h("span", { class: "grow" }), one),
      h("div", { class: "side-pdfs" }, col("A", s.a), col("B", s.b)));
  };
  if (c().side) show();
  const self = {
    show,
    single: () => { if (c().side) { c().side = false; show(); self.changed?.(); } },
    changed: null,         // set by the Compare card, whose Side by side button follows
  };
  return self;
}

function versionCards(d, r, pdf, ask) {
  if (r.versions.length < 2) {
    const v = r.versions[0];
    return [h("div", { class: "card" }, h("h3", {}, "Versions"),
      h("p", { class: "muted" }, `v${v.n}: ${versionWhat(v)}. Every edit you accept or make adds a version with its own scores, and you can compare any two here.`))];
  }
  const id = encodeURIComponent(d.id);
  const st = compareState(d.id, r);
  const byN = new Map(r.versions.map((v) => [v.n, v]));
  const metric = () => TREND.find(([k]) => k === S.trend) || TREND[0];
  const plot = h("div", { class: "trend-plot" });
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Score to chart" });
  const paintSeg = () => seg.replaceChildren(...TREND.map(([k, label]) => h("button", {
    class: metric()[0] === k ? "active" : "", "aria-pressed": String(metric()[0] === k),
    onclick: () => { S.trend = k; savePrefs(); paintSeg(); drawTrend(plot, r, metric(), st, pick); } }, label)));
  paintSeg();
  const since = h("div", { class: "trend-since" });
  const first = r.versions[0], cur = byN.get(r.current);
  since.append(h("span", { class: "muted" }, `Since v${first.n}`),
    h("span", {}, "Résumé ", h("b", {}, `${first.resume_score ?? "–"} → ${cur.resume_score ?? "–"}`), " ", deltaChip(cur.resume_score, first.resume_score)),
    h("span", {}, "ATS ", h("b", {}, `${pct(first.ats)} → ${pct(cur.ats)}`), " ", deltaChip(cur.ats, first.ats, true)));
  const trendCard = h("div", { class: "card trend" },
    h("div", { class: "card-head" }, h("h3", {}, "Scores across versions"), seg),
    since,
    h("p", { class: "muted trend-hint" }, `Click a version to compare it with v${st.b}. Pick any two in Compare below.`),
    plot);
  const cmp = h("div", { class: "card compare" });
  function pick(n) {
    if (n === st.b) return;
    st.a = n;
    drawTrend(plot, r, metric(), st, pick);
    paintCompare();
  }
  new ResizeObserver(() => drawTrend(plot, r, metric(), st, pick)).observe(plot);
  setTimeout(() => plot.childElementCount || drawTrend(plot, r, metric(), st, pick));   // the observer waits for a paint

  const options = (sel) => r.versions.slice().reverse().map((v) =>
    h("option", { value: v.n, selected: v.n === sel }, `v${v.n} · ${versionShort(v)}${v.n === r.current ? " (current)" : ""}`));
  let loading = 0;
  async function paintCompare() {
    const A = byN.get(st.a), B = byN.get(st.b);
    const selA = h("select", { "aria-label": "Version A" }, ...options(st.a));
    const selB = h("select", { "aria-label": "Version B" }, ...options(st.b));
    const choose = () => {
      const a = +selA.value, b = +selB.value;
      if (a === b) { toast("Pick two different versions."); paintCompare(); return; }
      st.a = a; st.b = b;
      drawTrend(plot, r, metric(), st, pick);
      paintCompare();
      if (st.side) pdf.show();
    };
    selA.addEventListener("change", choose);
    selB.addEventListener("change", choose);
    const swap = h("button", { class: "ghost small", title: "Swap A and B", "aria-label": "Swap A and B" }, "⇄");
    swap.addEventListener("click", () => { [st.a, st.b] = [st.b, st.a]; drawTrend(plot, r, metric(), st, pick); paintCompare(); if (st.side) pdf.show(); });
    const side = h("button", { class: `${st.side ? "primary" : "ghost"} small side-btn`, "aria-pressed": String(!!st.side) }, "Side by side");
    side.addEventListener("click", () => { st.side = !st.side; pdf.show(); pdf.changed(); });
    const restore = A.n !== r.current && h("button", { class: "ghost small", onclick: async () => {
      try { await post(`/api/jobs/${id}/restore`, { n: A.n }); toast(`Restored v${A.n} as a new version.`); reloadResume(d.id); }
      catch (e) { toast(e.message, true); } } }, `Restore v${A.n}`);
    const askBtn = r.editable !== false && h("button", { class: "ghost small", onclick: () =>
      ask(`What changed between v${A.n} and v${B.n}, and why did the scores move?`) }, "Ask Claude about it");
    const tile = (label, a, b, isPct) => h("div", { class: "cmp-tile" }, h("span", { class: "k" }, label),
      h("span", { class: "v" }, `${a == null ? "–" : Math.round(a * (isPct ? 1 : 10)) / (isPct ? 1 : 10)} → ${b == null ? "–" : Math.round(b * (isPct ? 1 : 10)) / (isPct ? 1 : 10)}`,
        h("small", {}, isPct ? "%" : "/10")),
      h("span", {}, deltaChip(b, a, isPct)));
    const moved = h("div", { class: "cmp-moved" }, h("p", { class: "muted" }, spinner(), " Comparing…"));
    cmp.replaceChildren(
      h("div", { class: "cmp-head" }, h("h3", {}, "Compare"), h("span", { class: "cmp-pick" }, h("span", { class: "vtag a" }, "A"), selA), swap,
        h("span", { class: "cmp-pick" }, h("span", { class: "vtag b" }, "B"), selB)),
      h("div", { class: "cmp-tiles" },
        tile("Résumé", A.resume_score, B.resume_score, false), tile("ATS", A.ats, B.ats, true),
        tile("Skills", A.ats_parts?.skills, B.ats_parts?.skills, true), tile("Experience", A.ats_parts?.experience, B.ats_parts?.experience, true),
        tile("Keywords", A.keywords_pct, B.keywords_pct, true)),
      moved,
      h("div", { class: "cmp-actions" }, side, restore, askBtn));
    const ticket = ++loading;
    try {
      const res = await api(`/api/jobs/${id}/resume/compare?a=${A.n}&b=${B.n}`);
      if (ticket !== loading) return;
      moved.replaceChildren(...compareDetail(res, A, B));
    } catch (e) { if (ticket === loading) moved.replaceChildren(h("p", { class: "muted" }, e.message)); }
  }
  pdf.changed = () => {         // the PDF pane went back to one PDF (or here, side by side was turned on or off)
    const b = cmp.querySelector(".side-btn");
    if (!b) return;
    b.setAttribute("aria-pressed", String(!!st.side));
    b.className = `${st.side ? "primary" : "ghost"} small side-btn`;
  };
  paintCompare();
  return [trendCard, cmp];
}

function compareDetail(res, A, B) {
  const rank = { missing: 0, partial: 1, strong: 2 };
  const up = res.moved.filter((m) => (rank[m.b] ?? 0) > (rank[m.a] ?? 0)).length;
  const out = [h("div", { class: "cmp-sub" }, h("b", {}, "What moved the score"),
    h("span", { class: "muted" }, res.moved.length ? `${up} up · ${res.moved.length - up} down` : ""))];
  if (!A.ratings || !B.ratings) out.push(h("p", { class: "muted" }, "One of these versions was saved before requirement ratings were kept, so only its scores compare."));
  else if (!res.moved.length) out.push(h("p", { class: "muted" }, "No requirement changed its rating."));
  out.push(...res.moved.map((m) => h("div", { class: "mv" },
    h("span", { class: "kind" }, m.section === "skills" ? "Skill" : "Exp."),
    h("span", { class: "req", title: `${m.requirement} (${m.weight})` }, m.requirement),
    h("span", { class: `rt ${m.a}` }, m.a), h("span", { class: "arrow", "aria-hidden": "true" }, "→"), h("span", { class: `rt ${m.b}` }, m.b),
    h("span", { class: `delta ${(rank[m.b] ?? 0) > (rank[m.a] ?? 0) ? "up" : "down"}`, "aria-label": (rank[m.b] ?? 0) > (rank[m.a] ?? 0) ? "up" : "down" },
      (rank[m.b] ?? 0) > (rank[m.a] ?? 0) ? "▲" : "▼"))));
  const kw = res.keywords;
  if (kw && (kw.gained.length || kw.lost.length)) {
    out.push(h("div", { class: "cmp-kw" }, h("span", { class: "muted" }, "Keywords"),
      ...kw.gained.map((k) => h("span", { class: "kw plus" }, `+ ${k}`)),
      ...kw.lost.map((k) => h("span", { class: "kw minus", title: `In v${A.n}, not in v${B.n}` }, `− ${k}`))));
  }
  const changed = res.diff.filter((o) => o.op !== "same").length;
  out.push(h("details", { class: "cmp-diff" }, h("summary", {}, `Wording changes (${plural(changed, "line")})`), renderDiff(res.diff)));
  return out;
}

// The chart: one score per version, a line through them, A and B tagged; the résumé score in a row under it.
function drawTrend(box, r, [key, label, get], st, pick) {
  if (!box.isConnected) return;
  const svg = (tag, attrs = {}) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  const vs = r.versions, n = vs.length;
  const W = Math.max(260, box.clientWidth || 560), H = 150;
  const L = 44, R = 30, T = 30, B = 12, P = 18;        // P: the first and last points sit in from the plot's edges
  const pw = W - L - R, ph = H - T - B, y0 = T + ph;
  const vals = vs.map(get);
  const have = vals.filter((v) => v != null);
  const target = key === "ats" ? ATS_TARGET : null;
  const lo = Math.max(0, Math.floor((Math.min(...have, target ?? 100) - 4) / 10) * 10);
  const hi = Math.min(100, Math.ceil((Math.max(...have, target ?? 0) + 4) / 10) * 10);
  const y = (v) => y0 - ((v - lo) / Math.max(1, hi - lo)) * ph;
  const x = (i) => L + P + (n === 1 ? (pw - 2 * P) / 2 : ((pw - 2 * P) * i) / (n - 1));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
    "aria-label": `${label} by version: ${vs.map((v, i) => `v${v.n} ${vals[i] == null ? "not scored" : pct(vals[i])}`).join(", ")}. Comparing v${st.a} with v${st.b}.` });
  const ia = vs.findIndex((v) => v.n === st.a), ib = vs.findIndex((v) => v.n === st.b);
  const band = svg("rect", { class: "range", x: Math.min(x(ia), x(ib)), y: T - 18, width: Math.abs(x(ib) - x(ia)), height: ph + 18, rx: 6 });
  const grid = svg("g", { class: "grid" }), axis = svg("g", { class: "axis" }), marks = svg("g"), hits = svg("g");
  for (let v = lo; v <= hi; v += 10) {
    if ((v - lo) % (hi - lo > 30 ? 20 : 10)) continue;
    grid.append(svg("line", { x1: L, x2: W - R + 20, y1: y(v), y2: y(v) }));
    const t = svg("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = `${v}%`;
    axis.append(t);
  }
  if (target != null) {
    grid.append(svg("line", { class: "target", x1: L, x2: W - R + 20, y1: y(target), y2: y(target) }));
    const t = svg("text", { class: "target-l", x: W - R + 20, y: y(target) + 14, "text-anchor": "end" });
    t.textContent = `target ${target}%`;
    axis.append(t);
  }
  let d = "";
  vals.forEach((v, i) => { if (v != null) d += `${d && vals[i - 1] != null ? "L" : "M"}${x(i)},${y(v)}`; });
  marks.append(svg("path", { class: "line", d }));
  const tags = [];
  vals.forEach((v, i) => {
    if (v == null) return;
    const role = i === ia ? "a" : i === ib ? "b" : "";
    marks.append(svg("circle", { class: `dot ${role}`, cx: x(i), cy: y(v), r: role ? 7 : 4.5 }));
    if (role) tags.push([role, i, v]);
  });
  for (const [role, i, v] of tags) {          // the A and B tags sit above their dots, inside the chart
    const text = `${role.toUpperCase()} · ${Math.round(v)}%`, w = 58;
    const cx = Math.min(Math.max(x(i), L + w / 2), W - w / 2 - 2), ty = Math.max(2, y(v) - 30);
    marks.append(svg("rect", { class: `ab ${role}`, x: cx - w / 2, y: ty, width: w, height: 20, rx: 7 }));
    const t = svg("text", { class: `ab-t ${role}`, x: cx, y: ty + 14, "text-anchor": "middle" });
    t.textContent = text;
    marks.append(t);
  }
  const tip = h("div", { class: "dash-tip trend-tip hidden", role: "status" });
  const slot = n > 1 ? (pw - 2 * P) / (n - 1) : pw;
  vs.forEach((v, i) => {
    const hit = svg("rect", { class: "hit", x: x(i) - slot / 2, y: 0, width: slot, height: H, tabindex: "0",
      "aria-label": `v${v.n}, ${versionShort(v)}: ${label} ${pct(vals[i])}, résumé ${v.resume_score ?? "–"}/10.${v.n === st.b ? "" : ` Compare with v${st.b}.`}` });
    const prev = vs[i - 1];
    const show = () => {
      tip.replaceChildren(h("div", { class: "tv" }, `v${v.n}`, h("span", { class: "td" }, ` · ${versionWhat(v)}`)),
        h("div", {}, `${label} `, h("b", {}, pct(vals[i])), " ", prev ? deltaChip(vals[i], vals[i - 1], true) : null,
          " · Résumé ", h("b", {}, v.resume_score ?? "–"), "/10 ", prev ? deltaChip(v.resume_score, prev.resume_score) : null),
        v.n === st.b ? null : h("div", { class: "td" }, `Click to compare with v${st.b}`));
      tip.classList.remove("hidden");
      const k = box.clientWidth / W, tw = tip.offsetWidth, py = (vals[i] == null ? y0 : y(vals[i])) * k;
      const above = py - tip.offsetHeight - 14;      // over the point, else under it
      tip.style.left = `${Math.min(Math.max(0, x(i) * k - tw / 2), box.clientWidth - tw)}px`;
      tip.style.top = `${above >= 0 ? above : py + 14}px`;
    };
    const hide = () => tip.classList.add("hidden");
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
    hit.addEventListener("click", () => pick(v.n));
    hit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(v.n); } });
    hits.append(hit);
  });
  root.append(band, grid, axis, marks, hits);
  // The résumé score for each version, and its name, under its point.
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(W / 72))));
  const rs = h("div", { class: "trend-row", "aria-hidden": "true" }, h("span", { class: "lbl" }, "Résumé"),
    ...vs.map((v, i) => h("span", { class: `rs ${fitClass(v.resume_score)} ${i === ia ? "a" : i === ib ? "b" : ""}`,
      style: `left:${(x(i) / W) * 100}%` }, v.resume_score ?? "–")));
  const xl = h("div", { class: "trend-x", "aria-hidden": "true" }, ...vs.map((v, i) =>
    (n - 1 - i) % every && i !== ia && i !== ib ? null : h("span", { class: i === ia || i === ib ? "on" : "", style: `left:${(x(i) / W) * 100}%;width:${Math.max(44, Math.min(110, slot * every - 6))}px` },
      h("b", {}, `v${v.n}${v.n === r.current ? " · current" : ""}`), n <= 6 ? versionShort(v) : "")));
  box.replaceChildren(root, rs, xl, tip);
}
function checkBadge(check) {
  if (!check || check.passed == null) return h("b", {}, "n/a");
  return h("span", { class: check.passed ? "pass" : "fail", title: check.output }, check.passed ? "PASS" : "FAIL");
}

function renderProposal(d, p, box, frame, cur) {
  const id = encodeURIComponent(d.id);
  frame.src = resumeUrl(id, "proposal", d.resume) + `&n=${Date.now()}`;
  const j = S.byId.get(d.id);
  const delta = (now, was, unit = "") => {
    const dv = now != null && was != null ? Math.round((now - was) * 10) / 10 : 0;
    return dv ? ` (${dv > 0 ? "+" : ""}${unit === "%" ? Math.round(dv) : dv})` : "";
  };
  const accept = h("button", { class: "primary" }, "Accept");
  const discard = h("button", { class: "ghost danger" }, "Discard");
  box.replaceChildren(h("div", { class: "card" },
    h("h3", {}, "Proposed edit (PDF on the left shows it)"),
    h("p", { class: "muted" }, `“${p.instruction}”`),
    h("div", { class: "metrics" },
      p.ats != null && h("span", { title: p.rerated === false ? "Couldn't re-rate the requirements, so skills and experience are the last version's; keywords are measured on this PDF." : "Skills and experience re-rated for this edit, keywords measured on this PDF" },
        "ATS ", h("b", {}, pct(p.ats)), delta(p.ats, versionAts(j, cur), "%"), p.rerated === false ? " (keywords only)" : ""),
      p.resume_score != null && h("span", {}, "Résumé ", h("b", {}, p.resume_score), "/10", delta(p.resume_score, versionScore(j, cur))),
      h("span", {}, "Keywords ", h("b", {}, pct(p.keywords_pct)), delta(p.keywords_pct, cur?.keywords_pct, "%")),
      h("span", {}, "Format check ", checkBadge(p.check)),
      h("span", {}, "Pages ", h("b", { class: p.pages === 2 ? "" : "fail" }, p.pages))),
    h("h3", {}, "Claude's note"),
    h("div", { class: "changes markdown", html: p.changes_html }),
    h("h3", {}, "Diff"),
    renderDiff(p.diff),
    h("div", { class: "actions" }, discard, accept)));
  accept.addEventListener("click", async () => {
    accept.disabled = discard.disabled = true;
    try { const hst = await post(`/api/jobs/${id}/edit/accept`); toast(`Saved as v${hst.current}. The deliverable PDF now matches it.`); reloadResume(d.id); }
    catch (e) { toast(e.message, true); accept.disabled = discard.disabled = false; }
  });
  discard.addEventListener("click", async () => {
    try { await post(`/api/jobs/${id}/edit/discard`); toast("Edit discarded."); reloadResume(d.id); }
    catch (e) { toast(e.message, true); }
  });
}

function renderDiff(ops) {
  const box = h("div", { class: "diff" });
  let sameRun = [];
  const flush = () => {
    if (!sameRun.length) return;
    if (sameRun.length > 4) {
      box.append(h("div", { class: "ctx" }, sameRun[0].new));
      box.append(h("div", { class: "ctx" }, `… ${sameRun.length - 2} unchanged lines …`));
      box.append(h("div", { class: "ctx" }, sameRun[sameRun.length - 1].new));
    } else sameRun.forEach((o) => box.append(h("div", { class: "ctx" }, o.new || " ")));
    sameRun = [];
  };
  for (const o of ops) {
    if (o.op === "same") { sameRun.push(o); continue; }
    flush();
    if (o.op === "add") box.append(h("div", { class: "add" }, "+ " + o.new));
    else if (o.op === "del") box.append(h("div", { class: "del" }, "- " + o.old));
    else box.append(h("div", { class: "chg" }, "~ ", ...o.words.map(([op, t]) =>
      op === "same" ? document.createTextNode(t) : h(op === "add" ? "ins" : "del", {}, t))));
  }
  flush();
  if (!ops.some((o) => o.op !== "same")) box.append(h("div", { class: "ctx" }, "No changes."));
  return box;
}

// ---- add a job ------------------------------------------------------------------------------------
// A role found outside the filters: paste its link and the posting is read from the page into the form.
// Fields you typed are never overwritten; ones the last read filled in are replaced (or cleared) by the next.
const addDialog = $("#paste-dialog"), addForm = $("#paste-form");
const addField = (name) => addForm.elements.namedItem(name);
const add = { url: "", filled: {}, seq: 0 };
function resetAdd() {
  addForm.reset();
  Object.assign(add, { url: "", filled: {} });
  add.seq++;
  lookupMsg();
  formError(addForm);
}
function lookupMsg(...parts) {
  const p = $(".lookup-msg", addForm);
  const warn = parts[0] === "warn";
  if (warn) parts.shift();
  p.replaceChildren(...parts);
  p.classList.toggle("warn", warn);
  p.classList.toggle("hidden", !parts.length);
}
async function readPosting({ force = false } = {}) {
  const url = addField("url").value.trim();
  if (!url || (url === add.url && !force)) return;
  add.url = url;
  const seq = ++add.seq, btn = $("[data-act=read]", addForm);
  formError(addForm);
  lookupMsg(spinner(), "Reading the posting…");
  btn.disabled = true;
  try {
    const d = await post("/api/jobs/lookup", { url });
    if (seq !== add.seq) return;
    addField("url").value = add.url = d.url;
    const got = { title: d.job_title || "", company: d.company || "", location: d.location || "",
                  description: d.readable ? d.description : "" };
    for (const [name, v] of Object.entries(got)) {
      const f = addField(name);
      if (!f.value.trim() || f.value === add.filled[name]) f.value = v;
    }
    add.filled = got;
    addField("remote").value = d.remote ? "true" : "";
    const e = d.existing;
    if (e) lookupMsg("warn", `Already in your jobs: ${e.title}${e.company ? " at " + e.company : ""}${e.status ? ` (${e.status})` : ""}.`,
      h("button", { type: "button", class: "link-btn", onclick: () => { addDialog.close(); openJob(e.id); } }, "Open it"));
    else if (d.readable) lookupMsg("Read from the posting. Check the details, then add it.");
    else if (d.browser_check) lookupMsg("warn", `${d.browser_check} only shows its pages to a person's browser, `
      + "so the app can't read this link. Paste the employer's own link above (a job board's Apply button leads "
      + "there), or copy the posting from the page and paste it below.");
    else lookupMsg("warn", (got.title ? "Found the title, but not" : "Couldn't read") + " the description on this page. "
      + "It may need a sign-in or a browser. Copy the posting from the page and paste it below.");
    if (d.browser_check && !d.readable) addField("url").select();  // pasting the employer's link reads it
    else if (!d.readable) addField("description").focus();
  } catch (err) {
    if (seq !== add.seq) return;
    add.url = "";
    lookupMsg("warn", err.message);
  } finally { if (seq === add.seq) btn.disabled = false; }
}
$("#paste-job").addEventListener("click", () => { addDialog.showModal(); addField("url").focus(); });
$("[data-act=read]", addForm).addEventListener("click", () => readPosting({ force: true }));
$("[data-act=cancel]", addForm).addEventListener("click", () => addDialog.close());
addField("url").addEventListener("paste", () => setTimeout(readPosting));
addField("url").addEventListener("change", () => readPosting());
addField("url").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); readPosting({ force: true }); } });
addForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const action = e.submitter?.value || "score";
  const body = Object.fromEntries(new FormData(addForm).entries());
  const text = body.description.trim();
  if (!text && !body.url.trim()) { formError(addForm, "Paste the posting's link, or its full description."); return; }
  body.description_read = !!text && text === (add.filled.description || "").trim();
  const buttons = $$(".actions button", addForm);
  buttons.forEach((b) => (b.disabled = true));
  formError(addForm);
  try {
    if (action === "tailor") {
      const r = await post("/api/runs", { kind: "tailor-pasted", ...body });
      addDialog.close(); resetAdd();
      runStarted(r);
      return;
    }
    const { id, existing } = await post("/api/jobs", body);
    addDialog.close(); resetAdd();
    await loadJobs();
    if (existing) { toast("That job is already here. Opening it."); openJob(id); return; }
    S.sel.find = id;                 // an added job is untracked, so it lands in Find jobs
    S.query = ""; $("#job-filter").value = ""; S.flags.clear(); clearFacets(); S.findFilter = "new";
    showTab("find");
    selectJob(id, true);
    scoreJobs([id], "signal");
  } catch (err) { formError(addForm, err.message); }
  finally { buttons.forEach((b) => (b.disabled = false)); }
});

// ---- searches ---------------------------------------------------------------------------------------
async function loadSearches() {
  try { applySearches(await api("/api/searches")); }
  catch (e) { toast(e.message, true); }
}
function applySearches(d) {
  S.requests = null;                     // the request bodies change with the filters and the defaults
  S.searches = d.searches;
  S.defaults = d.defaults;
  S.regions = d.regions || [];
  S.himalayas = !!d.himalayas;
  S.hiringcafe = d.hiringcafe || [];
  const ids = S.searches.map((s) => s.id);
  S.picked = new Set(S.picked ? ids.filter((id) => S.picked.has(id) || id === d.id) : ids);
  renderSearches();
}
const money = (n) => `$${Math.round(n / 1000)}k+`;
// A filter's `country`: a country code, a region (EU, Schengen, EEA, Europe, EMEA), a list of them, or Any.
const countries = (n) => `${n} ${n === 1 ? "country" : "countries"}`;
const whereParts = (c) => (Array.isArray(c) ? c : c ? String(c).split(/[\s,+;]+/) : []).filter(Boolean);
function whereCodes(c) {
  const parts = whereParts(c);
  if (!parts.length || parts.some((p) => p.toLowerCase() === "any")) return null;
  const found = new Set();
  for (const p of parts) {
    const r = S.regions.find((x) => x.id.toLowerCase() === p.toLowerCase());
    (r ? r.codes : [p.toUpperCase() === "UK" ? "GB" : p.toUpperCase()]).forEach((x) => found.add(x));   // ISO says GB
  }
  return [...found].sort();
}
function whereLabel(c) {
  const parts = whereParts(c), codes = whereCodes(c);
  if (!codes) return "any country";
  const regional = parts.some((p) => S.regions.some((r) => r.id.toLowerCase() === p.toLowerCase()));
  return parts.map((p) => (/^gb$/i.test(p) ? "UK" : p)).join(" + ") + (regional ? ` (${countries(codes.length)})` : "");
}
// The Where picker's choices: the default, the US, the UK (in no EU region), EU + Schengen, each region, anywhere,
// or codes of your own.
const WHERE_PAIR = "EU+Schengen";
function whereOptions() {
  const df = S.defaults.country;
  const opt = (value, label) => h("option", { value }, label);
  return [opt("", `Default (${whereLabel(df)})`), opt("US", "United States"), opt("GB", "United Kingdom"),
    opt(WHERE_PAIR, `EU + Schengen area (${countries(whereCodes(["EU", "Schengen"]).length)})`),
    ...S.regions.map((r) => opt(r.id, `${r.label} (${countries(r.codes.length)})`)),
    opt("Any", "Anywhere"), opt("other", "Other countries…")];
}
function whereChoice(c) {
  const parts = whereParts(c).map((p) => p.toLowerCase());
  if (!parts.length) return "";
  if (parts.length === 2 && parts.includes("eu") && parts.includes("schengen")) return WHERE_PAIR;
  if (parts.length === 1) {
    const own = { us: "US", gb: "GB", uk: "GB", any: "Any" }[parts[0]];
    if (own) return own;
    const r = S.regions.find((x) => x.id.toLowerCase() === parts[0]);
    if (r) return r.id;
  }
  return "other";
}
function whereNote() {
  const choice = sField("where").value;
  $(".where-codes", searchForm).classList.toggle("hidden", choice !== "other");
  const c = choice === "other" ? sField("where_codes").value : choice === WHERE_PAIR ? ["EU", "Schengen"] : choice || S.defaults.country;
  const codes = whereCodes(c);
  $(".where-note", searchForm).textContent = !codes ? "Every country JobsPipe covers. Startup roles from anywhere answer this filter."
    : codes.length > 1 ? `${countries(codes.length)}: ${codes.join(", ")}. Startup roles pinned to other countries are skipped.`
    : "";
}
function searchFacts(s) {
  const df = S.defaults, eff = (k) => s[k] ?? df[k];
  const wa = s.work_arrangement || [];
  return [s.remote ? "Remote" : wa.length ? wa.join(", ").replace(/^./, (c) => c.toUpperCase()) : "Any arrangement",
          (s.locations || []).join(", "), whereLabel(eff("country")),
          eff("min_salary_usd") ? money(eff("min_salary_usd")) : eff("min_salary_usd") === 0 ? "any pay" : "", eff("posted_within_days") != null ? `posted in the last ${plural(eff("posted_within_days"), "day")}` : "",
          `up to ${plural(eff("limit") ?? 10, "job")} per run`,
          S.himalayas && (s.remote || !(s.work_arrangement || []).length) ? "+ Himalayas (free)" : ""].filter(Boolean).join(" · ");
}
function renderSearches() {
  const df = S.defaults;
  if (shownRequests.size && !S.requests) loadRequests();
  $("#search-defaults").textContent = `Defaults: ${whereLabel(df.country)}, posted within ${df.posted_within_days ?? "any"} days, ${df.min_salary_usd ? money(df.min_salary_usd) : "any"} pay, ${df.limit ?? 10} jobs per search.${S.himalayas ? " Remote filters also search Himalayas, free." : ""}`;
  $("#searches").replaceChildren(...S.searches.map(searchCard),
    ...(S.searches.length ? [] : [h("div", { class: "notice" }, "No saved filters yet. Add one to start finding roles.")]));
  const picked = S.searches.filter((s) => S.picked.has(s.id));
  const cap = S.summary?.credits.per_run;
  const max = picked.reduce((n, s) => n + (s.limit ?? df.limit ?? 10), 0);
  const cafe = S.hiringcafe.filter((s) => !s.paused).length;
  $("#run-summary").replaceChildren(h("b", {}, `${picked.length} of ${S.searches.length}`), " selected · at most ",
    h("b", {}, plural(cap != null ? Math.min(max, cap) : max, "credit")), cap != null && max > cap ? ` (run cap ${cap})` : "",
    cafe ? ` · + ${plural(cafe, "HiringCafe search")} (free)` : "");
  $("#hiringcafe").replaceChildren(...S.hiringcafe.map(cafeCard),
    ...(S.hiringcafe.length ? [] : [h("div", { class: "notice" }, "No HiringCafe searches yet. Add one from its share link to check it on every run.")]));
}
// The exact JSON a filter sends to JobsPipe, shown on its card. Showing it sends nothing.
const shownRequests = new Set();
let requestsLoading = null;
function loadRequests() {
  requestsLoading = requestsLoading || api("/api/searches/requests")
    .then((r) => { S.requests = r; })
    .catch((e) => { toast(e.message, true); shownRequests.clear(); })
    .finally(() => { requestsLoading = null; renderSearches(); });
}
function toggleRequest(id) {
  shownRequests.has(id) ? shownRequests.delete(id) : shownRequests.add(id);
  renderSearches();
}
function searchCard(s) {
  const req = shownRequests.has(s.id) && (S.requests ? S.requests[s.id] : "Loading…");
  const pickBox = h("input", { type: "checkbox", checked: S.picked.has(s.id) });
  pickBox.addEventListener("change", () => { pickBox.checked ? S.picked.add(s.id) : S.picked.delete(s.id); renderSearches(); });
  const found = S.jobs.filter((j) => j.searches.includes(s.id)).length;
  const remove = h("button", { class: "ghost small danger" }, "Delete");
  remove.addEventListener("click", async () => {
    if (!remove.dataset.armed) {       // first click arms the button, the second deletes
      remove.dataset.armed = "1"; remove.textContent = "Confirm delete";
      setTimeout(() => { delete remove.dataset.armed; remove.textContent = "Delete"; }, 3000);
      return;
    }
    try { applySearches(await api(`/api/searches/${s.id}`, { method: "DELETE" })); toast(`Deleted “${s.name || s.id}”.`); }
    catch (e) { toast(e.message, true); }
  });
  return h("div", { class: "scard" + (S.picked.has(s.id) ? " picked" : "") },
    h("label", { class: "check", title: "Include in the next run" }, pickBox, h("span", { class: "nm" }, s.name || s.id)),
    h("div", { class: "tags" }, ...(s.titles || []).map((t) => h("span", { class: "tag" }, t))),
    h("div", { class: "facts" }, searchFacts(s)),
    (s.exclude_titles || []).length ? h("div", { class: "muted" }, "Excludes: ", s.exclude_titles.join(", ")) : null,
    (s.companies || []).length ? h("div", { class: "muted" }, "Only at: ", s.companies.join(", ")) : null,
    (s.exclude_companies || []).length ? h("div", { class: "muted" }, "Never at: ", s.exclude_companies.join(", ")) : null,
    req ? h("div", { class: "req" }, h("div", { class: "hint" }, "Sent to JobsPipe as POST /v1/jobs/search, with the defaults filled in. Showing it costs nothing."),
            h("pre", {}, typeof req === "string" ? req : JSON.stringify(req, null, 2))) : null,
    h("footer", {},
      h("span", { class: "muted" }, h("code", {}, s.id), ` · ${plural(found, "role")} found so far`),
      h("button", { class: "link-btn small", "aria-expanded": String(shownRequests.has(s.id)), onclick: () => toggleRequest(s.id) },
        shownRequests.has(s.id) ? "Hide request" : "Show request"),
      h("button", { class: "ghost small", onclick: () => openSearch(s, "edit") }, "Edit"),
      h("button", { class: "ghost small", onclick: () => openSearch({ ...s, name: `${s.name || s.id} (copy)` }, "new") }, "Duplicate"),
      remove));
}

// ---- HiringCafe searches: added from a link (the app keeps the search's filters), checked on every run, free ----
const fmtWhen = (v) => new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
function cafeCheck(c) {
  if (!c) return h("div", { class: "hc-check muted" }, "Not checked yet: the next run checks it.");
  if (c.error) return h("div", { class: "hc-check bad" }, `Last checked ${fmtWhen(c.at)}: ${c.error}`);
  return h("div", { class: "hc-check muted" }, `Last checked ${fmtWhen(c.at)} · `, h("b", {}, plural(c.new || 0, "new job")),
    ` of ${c.found || 0} found`);
}
function cafeCard(s) {
  const found = S.jobs.filter((j) => j.searches.includes(s.id)).length;
  const change = async (body, done) => {
    try { applySearches(await put(`/api/hiringcafe/${s.id}`, body)); toast(done); }
    catch (e) { toast(e.message, true); }
  };
  const remove = h("button", { class: "ghost small danger" }, "Delete");
  remove.addEventListener("click", async () => {
    if (!remove.dataset.armed) {       // first click arms the button, the second deletes
      remove.dataset.armed = "1"; remove.textContent = "Confirm delete";
      setTimeout(() => { delete remove.dataset.armed; remove.textContent = "Delete"; }, 3000);
      return;
    }
    try { applySearches(await api(`/api/hiringcafe/${s.id}`, { method: "DELETE" })); toast(`Deleted “${s.name}”.`); }
    catch (e) { toast(e.message, true); }
  });
  return h("div", { class: "scard" + (s.paused ? " paused" : "") },
    h("div", { class: "hc-top" }, h("span", { class: "nm" }, s.name), s.paused ? h("span", { class: "tag" }, "Paused") : null,
      h("span", { class: "grow" }), extLink(s.link, "Open on HiringCafe")),
    h("div", { class: "facts" }, s.facts),
    cafeCheck(s.check),
    h("footer", {},
      h("span", { class: "muted", title: `Its id in searches.yaml: ${s.id}` }, `${plural(found, "role")} found so far`),
      h("button", { class: "ghost small", title: "Check it now and give its new jobs a signal score",
                    onclick: () => startRun({ kind: "hiringcafe", search_ids: [s.id] }) }, "Check now"),
      h("button", { class: "ghost small", onclick: () => openCafe(s) }, "Rename"),
      h("button", { class: "ghost small", title: s.paused ? "Check it on every run again" : "Skip it on runs until you resume it",
                    onclick: () => change({ paused: !s.paused }, s.paused ? `Resumed “${s.name}”.` : `Paused “${s.name}”.`) },
        s.paused ? "Resume" : "Pause"),
      remove));
}
const cafeDialog = $("#hc-dialog"), cafeForm = $("#hc-form");
function openCafe(s) {
  cafeDialog._edit = s ? s.id : null;
  cafeForm.reset();
  formError(cafeForm);
  $("h2", cafeForm).textContent = s ? "Rename HiringCafe search" : "Add a HiringCafe search";
  $$(".hc-link", cafeForm).forEach((el) => el.classList.toggle("hidden", !!s));
  cafeForm.elements.namedItem("link").required = !s;
  cafeForm.elements.namedItem("name").value = s ? s.name : "";
  $("button.primary", cafeForm).textContent = s ? "Save name" : "Add search";
  cafeDialog.showModal();
  cafeForm.elements.namedItem(s ? "name" : "link").focus();
}
$("#add-hiringcafe").addEventListener("click", () => openCafe(null));
$("[data-act=cancel]", cafeForm).addEventListener("click", () => cafeDialog.close());
cafeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = cafeDialog._edit, name = cafeForm.elements.namedItem("name").value.trim();
  const button = $("button.primary", cafeForm);
  formError(cafeForm);
  button.disabled = true;
  try {
    if (id) applySearches(await put(`/api/hiringcafe/${id}`, { name }));
    else applySearches(await post("/api/hiringcafe", { link: cafeForm.elements.namedItem("link").value.trim(), name }));
    cafeDialog.close();
    toast(id ? "Name saved." : "HiringCafe search added. Every run checks it; Check now runs it once.");
  } catch (err) { formError(cafeForm, err.message); }
  finally { button.disabled = false; }
});

const searchDialog = $("#search-dialog"), searchForm = $("#search-form");
const sField = (name) => searchForm.elements.namedItem(name);
const lines = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);
const SEARCH_LISTS = ["titles", "locations", "exclude_titles", "companies", "exclude_companies"];
function openSearch(s, mode) {
  const df = S.defaults;
  searchDialog._base = s || {};
  searchDialog._edit = mode === "edit" ? s.id : null;
  searchForm.reset();
  formError(searchForm);
  $("h2", searchForm).textContent = mode === "edit" ? "Edit filter" : "New filter";
  s = s || { remote: true };
  sField("name").value = s.name || "";
  for (const k of SEARCH_LISTS) sField(k).value = (s[k] || []).join("\n");
  for (const k of ["min_salary_usd", "posted_within_days", "limit"]) {
    sField(k).value = s[k] ?? "";
    sField(k).placeholder = df[k] ?? "";
  }
  sField("exclude_titles").placeholder = (df.exclude_titles || []).join("\n");
  sField("exclude_companies").placeholder = (df.exclude_companies || []).join("\n");
  const wa = s.work_arrangement || [];
  const modeSel = sField("mode");
  $("option[value=keep]", modeSel)?.remove();
  if (wa.length && wa.join() !== "hybrid") modeSel.append(h("option", { value: "keep" }, wa.join(", ")));
  modeSel.value = s.remote ? "remote" : wa.join() === "hybrid" ? "hybrid" : wa.length ? "keep" : "any";
  sField("where").replaceChildren(...whereOptions());
  sField("where").value = whereChoice(s.country);
  sField("where_codes").value = sField("where").value === "other" ? whereParts(s.country).join(", ") : "";
  whereNote();
  searchDialog.showModal();
  sField("name").focus();
}
$("#add-search").addEventListener("click", () => openSearch(null, "new"));
sField("where").addEventListener("change", () => { whereNote(); if (sField("where").value === "other") sField("where_codes").focus(); });
sField("where_codes").addEventListener("input", whereNote);
$("[data-act=cancel]", searchForm).addEventListener("click", () => searchDialog.close());
function searchBody() {
  const body = { ...searchDialog._base, name: sField("name").value.trim() };
  for (const k of SEARCH_LISTS) body[k] = lines(sField(k).value);
  for (const k of ["min_salary_usd", "posted_within_days", "limit"]) body[k] = sField(k).value;
  const mode = sField("mode").value;
  if (mode !== "keep") { delete body.remote; delete body.work_arrangement; }
  if (mode === "remote") body.remote = true;
  if (mode === "hybrid") body.work_arrangement = ["hybrid"];
  const where = sField("where").value;
  body.country = where === "other" ? sField("where_codes").value : where === WHERE_PAIR ? ["EU", "Schengen"] : where;
  return body;
}
searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = searchBody();
  const id = searchDialog._edit;
  if (!id) delete body.id;        // the server names a new search from its name
  try {
    applySearches(await (id ? put(`/api/searches/${id}`, body) : post("/api/searches", body)));
    searchDialog.close();
    toast(id ? "Filter saved." : "Filter added.");
  } catch (err) { formError(searchForm, err.message); }
});
// Search once: the dialog's filter, run as it is and not saved. Its jobs land in Find jobs ("Found by one-off").
$("[data-act=once]", searchForm).addEventListener("click", async () => {
  const body = searchBody();
  delete body.id;
  formError(searchForm);
  try {
    const r = await post("/api/runs", { kind: "once", search: body, score: sField("once_score").checked });
    searchDialog.close();
    runStarted(r);
  } catch (err) { formError(searchForm, err.message); }
});
$$("[data-run]").forEach((b) => b.addEventListener("click", () => {
  const ids = S.searches.filter((s) => S.picked.has(s.id)).map((s) => s.id);
  if (!ids.length) { toast("Tick at least one filter.", true); return; }
  const kind = b.dataset.run;
  if (kind === "preflight") startRun({ kind: "preflight", search_ids: ids });
  else startRun({ kind: "run", search_ids: ids, no_tailor: kind === "triage", top: kind === "full" ? $("#run-top").value : null });
}));

// ---- credits ledger -------------------------------------------------------------------------------
const creditsDialog = $("#credits-dialog");
$("#credits").addEventListener("click", openCredits);
$("[data-act=close]", creditsDialog).addEventListener("click", () => creditsDialog.close());
async function openCredits() {
  $("#credits-body").replaceChildren(h("p", { class: "muted" }, spinner(), " Loading…"));
  creditsDialog.showModal();
  let d;
  try { d = await api("/api/credits"); }
  catch (e) { $("#credits-body").replaceChildren(h("p", { class: "form-error" }, e.message)); return; }
  const stat = (n, label) => h("div", { class: "cstat" }, h("b", {}, n.toLocaleString()), h("span", { class: "muted" }, label));
  const month = (m) => new Date(`${m}-15T12:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const charged = (c) => (c.credits_charged != null ? String(c.credits_charged)
    : h("span", { title: "JobsPipe didn't report the charge, so every job returned is counted" }, `${c.counted ?? c.returned}*`));
  const rows = d.calls.flatMap((c, i) => {
    const ok = c.status === "ok";
    const detail = h("tr", { class: "creq hidden" }, h("td", { colspan: 5 }, h("pre", {}, JSON.stringify(c.body || {}, null, 2))));
    const toggle = h("button", { class: "link-btn small", "aria-expanded": "false", onclick: () => {
      const open = detail.classList.toggle("hidden");
      toggle.setAttribute("aria-expanded", String(!open));
      toggle.textContent = open ? "Request" : "Hide";
    } }, "Request");
    return [h("tr", {},
      h("td", { class: "nowrap" }, new Date(c.at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })),
      h("td", {}, c.name || c.search, " ", toggle),
      h("td", { class: ok ? "" : "bad" }, ok ? "OK" : c.status),
      h("td", { class: "num" }, c.returned, c.jobs_already_paid ? h("span", { class: "muted" }, ` (${c.jobs_already_paid} already yours)`) : ""),
      h("td", { class: "num" }, charged(c))), detail];
  });
  $("#credits-body").replaceChildren(...[
    h("div", { class: "cstats" }, stat(d.used, d.period === "monthly" ? "used this month" : "used"), stat(d.left, "left"),
      stat(d.allowance, d.period === "monthly" ? "per month" : "allowance, one-time"), stat(d.per_run, "most per run")),
    d.months.length ? h("p", { class: "cmonths" }, ...d.months.map((m, i) => [i ? " · " : "", h("b", {}, m.credits), ` in ${month(m.month)}`])) : null,
    d.calls.length
      ? h("div", { class: "ctable" }, h("table", {},
          h("thead", {}, h("tr", {}, ...["When", "Filter", "Result", "Jobs", "Credits"].map((x) => h("th", {}, x)))),
          h("tbody", {}, ...rows)))
      : h("p", { class: "muted" }, "No searches yet."),
    d.calls.some((c) => c.credits_charged == null && c.status === "ok") ? h("p", { class: "hint" }, "* Not reported by JobsPipe, so counted as every job returned.") : null].filter(Boolean));
}

// ---- runs ------------------------------------------------------------------------------------------
async function startRun(body) {
  try { runStarted(await post("/api/runs", body)); }
  catch (e) { toast(e.message, true); }
}
function runStarted(r) {
  toast(r.status === "queued" ? `Waiting its turn: ${r.label}. It starts by itself when a run finishes.` : `Started: ${r.label}`);
  pollRun.live = r.id;
  showTab("runs");
  selectRun(r.id);
  loadSummary();
  loadJobs();     // a tailoring run puts its job In progress as it starts
}
async function loadRuns() {
  try {
    const d = await api("/api/runs");
    $("#runs").replaceChildren(...d.runs.map((r) => h("li", { class: r.id === S.runId ? "selected" : "", onclick: () => selectRun(r.id) },
      h("div", { class: `st ${r.status}` }, r.status), h("div", {}, r.label), h("div", { class: "muted" }, new Date(r.started).toLocaleString()))));
    if (!d.runs.length) $("#runs").append(h("li", { class: "muted" }, "No runs yet. Start one from Filters or a job."));
  } catch (e) { /* summary shows auth errors */ }
}
function selectRun(id) {
  S.runId = id; S.runSince = 0;
  $("#runlog").textContent = "";
  renderRunResults(null);
  loadRuns();
  pollRun();
}
async function pollRun() {
  if (S.runId == null) return;
  const id = S.runId;
  clearTimeout(pollRun._t);
  try {
    const r = await api(`/api/runs/${id}?since=${S.runSince}`);
    if (id !== S.runId) return;
    $("#runlog-title").textContent = `${r.label} · ${r.status}`;
    const log = $("#runlog");
    const WAIT = "Waiting its turn: the most runs are going. This starts by itself when one finishes.\n";
    if (r.status === "queued") log.textContent = WAIT;
    else if (log.textContent === WAIT) log.textContent = "";
    if (r.lines.length) {
      const pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.append(r.lines.join("\n") + "\n");
      S.runSince = r.line_count;
      if (pinned) log.scrollTop = log.scrollHeight;
    }
    renderRunResults(r);
    const going = r.status === "running" || r.status === "queued";
    $("#stop-run").classList.toggle("hidden", !going);
    $("#stop-run").onclick = () => post(`/api/runs/${id}/stop`).then(pollRun);
    if (going) {
      pollRun.live = id;
      pollRun._t = setTimeout(pollRun, document.hidden ? 5000 : 1500);
    } else if (pollRun.live === id) {   // it finished while we watched: pick up what it wrote
      pollRun.live = null;
      loadRuns(); loadSummary();
      S.details.clear();
      await loadJobs({ refresh: S.notion });
      if (S.byId.get(S.selected)?.local) loadDetail(S.selected);
    }
  } catch (e) { toast(e.message, true); }
}

// What a finished run scored or tailored, each a link to that job. Follow-up questions are at the end
// of a tailored job's fit report; a scored job's gaps are beside its posting.
function renderRunResults(r) {
  const box = $("#run-results");
  const busy = !r || r.status === "running" || r.status === "queued";
  const res = busy ? [] : r.results || [];
  box.classList.toggle("hidden", busy);
  if (busy) { box.replaceChildren(); return; }
  if (!res.length) {
    box.replaceChildren(h("p", { class: "muted" }, r.status === "done" ? "This run didn't score any jobs or make any résumés."
      : r.status === "interrupted" ? "The app was closed while this run was going, so it stopped and its results weren't collected."
      : "No jobs were scored and no résumés made."));
    return;
  }
  const tailored = res.filter((x) => x.tailored).length;
  box.replaceChildren(
    h("h3", {}, `Results: ${plural(res.length, "job")}`),
    h("p", { class: "muted" }, tailored
      ? "Open a job's Fit report: its follow-up questions are at the end. Add your answers as confirmed facts in the Impact record (under Profile), then make the résumé again if they change what it can claim."
      : "Open a job to see its score, matches and likely gaps beside the posting."),
    h("ul", {}, ...res.map((x) => h("li", {},
      h("span", { class: `fit ${fitClass(x.impact_score ?? x.fit)}` }, x.impact_score ?? x.fit ?? "–"),
      h("span", { class: "what" }, h("b", {}, x.title || "(untitled)"), " · ", x.company || ""),
      x.tailored && h("span", { class: "tag ok" }, `Résumé${x.ats_total != null ? " " + pct(x.ats_total) : ""}`),
      h("span", { class: "actions-inline" },
        x.tailored && h("button", { class: "ghost small", onclick: () => openJob(x.id, "report") }, "Fit report"),
        h("button", { class: "ghost small", onclick: () => openJob(x.id, x.tailored ? "resume" : "posting") }, x.tailored ? "Résumé" : "Open"))))));
}
// Show one job's detail on the given tab, in whichever list it's in, clearing filters that hide it.
function openJob(id, tab) {
  const j = S.byId.get(id);
  if (!j) { toast("That job isn't in the list yet. Try again in a moment.", true); return; }
  const scope = SCOPES.tracker.has(j) ? "tracker" : "find";
  S.detailTab = tab; S.detailTabJob = id;
  S.sel[scope] = id;
  showTab(scope);
  if (S.selected !== id) {
    clearFilters();
    if (scope === "find" && j.dismissed) { S.findFilter = "dismissed"; renderJobs(); }
    selectJob(id, true);
  } else if (!boardMode()) renderDetail();
  if (boardMode()) selectJob(id, true);
  j._el?.scrollIntoView({ block: "nearest" });
}


// ---- startups: who just raised, and their open roles --------------------------------------------------
const ROUNDS = ["Pre-seed", "Seed", "Series A", "Series B", "Series C", "Series D+", "Growth", "Unknown"];
const ROUND_SHORT = { "Pre-seed": "PS", Seed: "S", "Series A": "A", "Series B": "B", "Series C": "C", "Series D+": "D+", Growth: "G", Unknown: "?" };
const DAYS = 90;
const since = (days) => new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
const hiringForYou = (s) => !s.dismissed && !s.exited && !!s.roles?.jobs?.some((j) => j.match);
const SU_VIEWS = [
  ["hiring", "Hiring for you", hiringForYou],
  ["raised", "Raised recently", (s) => !s.dismissed && s.round?.date >= since(DAYS)],
  ["yc", "YC hiring", (s) => !s.dismissed && !!s.batch && s.hiring],
  ["all", "All", (s) => !s.dismissed],
  ["dismissed", "Dismissed", (s) => s.dismissed],
];
const SU = { list: [], byId: new Map(), loaded: false, query: "", tokens: [], view: "hiring", rounds: new Set(), sort: "newest",
             selected: null, visible: [], phrases: [], busy: new Map(), refreshed: null, lookup: false };
const SU_SORTS = {
  amount: (a, b) => (b.round?.amount_usd || 0) - (a.round?.amount_usd || 0) || a.name.localeCompare(b.name),
  name: (a, b) => a.name.localeCompare(b.name),
};
function indexStartup(s) {
  s._hay = [s.name, s.one_liner, s.hq, s.stage, s.line, s.batch, ...(s.industries || []), ...(s.tags || [])].join(" ").toLowerCase();
  s._el = null;
}
async function loadStartups({ quiet = false } = {}) {
  try {
    const d = await api("/api/startups");
    SU.list = d.startups; SU.phrases = d.phrases; SU.refreshed = d.refreshed; SU.lookup = d.lookup;
    SU.byId = new Map(SU.list.map((s) => [s.id, s]));
    SU.list.forEach(indexStartup);
    if (!SU.loaded && !SU.list.some(hiringForYou)) SU.view = "raised";   // nothing matched yet: start from the raises
    SU.loaded = true;
    renderStartups();
    if (SU.selected && !SU.byId.get(SU.selected)) SU.selected = null;
    renderStartupDetail();
    if (!SU.selected && SU.visible.length && !quiet) selectStartup(SU.visible[0].id);
  } catch (e) { if (!quiet) toast(e.message, true); }
}
const suMatches = (s, { rounds = true } = {}) => SU_VIEWS.find(([k]) => k === SU.view)[2](s)
  && SU.tokens.every((t) => s._hay.includes(t)) && (!rounds || !SU.rounds.size || SU.rounds.has(s.stage));
function renderStartupState() {
  const el = $("#startup-state");
  const going = (S.summary?.active_runs || []).find(isStartupRefresh);
  $("#startup-refresh").disabled = !!going;
  if (going) el.replaceChildren(spinner(), " Reading the sources… ", h("button", { class: "link-btn", onclick: () => { showTab("runs"); selectRun(going.id); } }, "follow it in Logs"));
  else if (SU.refreshed) {
    const hours = S.summary?.startups?.auto_refresh_hours;
    const inFind = S.jobs.filter((j) => j.searches?.includes("startups")).length;
    el.replaceChildren(`Sources and boards read ${new Date(SU.refreshed).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
      + (hours ? `, again every ${hours}h on its own` : "") + " · ",
      h("button", { class: "link-btn", title: "Roles matching your filters are added there on each refresh, with the round.",
        onclick: () => { showTab("find"); S.flags.clear(); clearFacets(); S.facets.company.add("startup"); renderJobs(); } }, `${plural(inFind, "startup role")} in Search Jobs`));
  } else el.textContent = "";
}
function renderStartups() {
  SU.tokens = SU.query.toLowerCase().split(/\s+/).filter(Boolean);
  $("#startup-views").replaceChildren(...SU_VIEWS.map(([k, label, test]) => h("button", {
    class: SU.view === k ? "active" : "", onclick: () => { SU.view = k; renderStartups(); } },
    label, " ", h("b", {}, SU.list.filter((s) => test(s) && SU.tokens.every((t) => s._hay.includes(t))).length))));
  $("#rounds").replaceChildren(...ROUNDS.map((r) => {
    const n = SU.list.filter((s) => suMatches(s, { rounds: false }) && s.stage === r).length;
    return (n || SU.rounds.has(r)) && h("button", { class: "chip round-chip", "data-round": r, "aria-pressed": String(SU.rounds.has(r)),
      onclick: () => { SU.rounds.has(r) ? SU.rounds.delete(r) : SU.rounds.add(r); renderStartups(); } }, r, h("b", {}, n));
  }).filter(Boolean));
  const list = SU.visible = SU.list.filter((s) => suMatches(s));
  if (SU_SORTS[SU.sort]) list.sort(SU_SORTS[SU.sort]);
  $("#startup-count").textContent = plural(list.length, "startup");
  $("#startups").replaceChildren(...list.map(startupRow), ...(list.length ? [] : [h("div", { class: "list-empty" },
    !SU.loaded ? [spinner(), " Loading…"] : SU.list.length ? (SU.view === "hiring" && !filtersOnStartups()
        ? "No startup has an open role matching your filters yet. Each refresh reads their careers boards, newest raise first, and matching roles land in Search Jobs."
        : "No startups match.")
      : ["No startups yet. Refresh sources reads the funding news, the YC directory and VC boards (free), then the startups' careers boards for roles matching your filters.",
         h("button", { class: "primary", onclick: refreshStartups }, "Refresh sources")])]));
  renderStartupState();
}
const filtersOnStartups = () => SU.tokens.length || SU.rounds.size;
const roundTile = (s, cls = "") => h("div", { class: `round-tile ${cls}`, "data-round": s.stage, title: s.line || "Round unknown" }, ROUND_SHORT[s.stage] || "?");
const rolesTag = (s) => {
  if (!s.roles) return null;
  const m = s.roles.jobs.filter((j) => j.match).length;
  return h("span", { class: "tag " + (m ? "ok" : "") }, m ? `${plural(m, "matching role")}` : `${plural(s.roles.jobs.length, "open role")}`);
};
function startupRow(s) {
  if (!s._el) {
    s._el = h("li", { "data-id": s.id, tabindex: "0", onclick: () => selectStartup(s.id), onkeydown: (e) => { if (e.key === "Enter") selectStartup(s.id); } },
      h("span", {}), roundTile(s),
      h("div", { class: "row-main" },
        h("div", { class: "t" }, s.name),
        h("div", { class: "s" }, [s.one_liner, s.hq].filter(Boolean).join(" · ")),
        h("div", { class: "tags" },
          h("span", { class: "tag round-tag", "data-round": s.stage, title: s.line }, s.round ? (s.round.name || s.stage) : s.stage_round || (s.batch ? `YC ${s.batch}` : "Round unknown"),
            s.round?.amount && h("b", {}, ` ${s.round.amount}`), s.round?.date && h("b", {}, ` · ${fmtDate(s.round.date + "T12:00")}`)),
          s.batch && (s.round || s.stage_round) && h("span", { class: "tag" }, `YC ${s.batch}`),
          s.exited && h("span", { class: "tag", title: "A VC board still lists it, but it was acquired or went public, so its roles aren't added to Search Jobs on their own" }, s.exited === "public" ? "Public" : "Acquired"),
          s.hiring && h("span", { class: "tag" }, "Hiring"),
          rolesTag(s),
          s.tracked && h("span", { class: "tag stage-tag", "data-stage": "Not started" }, "Tracked"))),
      h("span", {}));
  }
  s._el.classList.toggle("selected", s.id === SU.selected);
  return s._el;
}
function selectStartup(id) {
  const prev = SU.byId.get(SU.selected);
  SU.selected = id;
  prev?._el?.classList.remove("selected");
  SU.byId.get(id)?._el?.classList.add("selected");
  renderStartupDetail();
}
function openStartup(id, company) {
  showTab("startups");
  const s = SU.byId.get(id) || SU.list.find((x) => x.name.toLowerCase() === (company || "").toLowerCase());
  if (!s) { toast("That startup isn't in the list.", true); return; }
  if (!suMatches(s)) { SU.view = s.dismissed ? "dismissed" : "all"; SU.rounds.clear(); SU.query = ""; $("#startup-filter").value = ""; renderStartups(); }
  selectStartup(s.id);
  s._el?.scrollIntoView({ block: "nearest" });
}
function repaintStartup(s) {
  indexStartup(s);
  renderStartups();
  if (s.id === SU.selected) renderStartupDetail();
}
function suReplace(d) {
  const old = SU.byId.get(d.id);
  const s = old ? Object.assign(old, d) : d;
  if (!old) { SU.list.unshift(s); SU.byId.set(s.id, s); }
  repaintStartup(s);
  return s;
}
async function suAction(s, label, fn) {
  SU.busy.set(s.id, label); renderStartupDetail();
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { SU.busy.delete(s.id); renderStartupDetail(); }
}
const readRoles = (s) => suAction(s, "roles", async () => {
  const d = suReplace(await post(`/api/startups/${encodeURIComponent(s.id)}/roles`));
  if (!d.board) toast(d.careers_url ? "No Greenhouse, Lever or Ashby board found; its careers page is linked." : "Couldn't find a careers board on its website.", true);
  else if (!d.roles.jobs.length) toast("Its board lists no open roles right now.");
});
const lookUpRound = (s) => suAction(s, "enrich", async () => { suReplace(await post(`/api/startups/${encodeURIComponent(s.id)}/enrich`)); toast(`${s.name}: ${SU.byId.get(s.id).line}`); });
const trackStartup = (s) => suAction(s, "track", async () => {
  const r = await post(`/api/startups/${encodeURIComponent(s.id)}/track`);
  s.tracked = r.tracker_id || true; repaintStartup(s);
  toast(r.action === "created" ? `${s.name} is on your tracker as "Open roles".` : `${s.name} was already tracked.`);
  loadJobs();
});
const dismissStartup = (s, dismissed) => suAction(s, "dismiss", async () => {
  suReplace(await patch(`/api/startups/${encodeURIComponent(s.id)}`, { dismissed }));
  if (dismissed) { const next = SU.visible.find((x) => x.id !== s.id); SU.selected = null; renderStartups(); if (next) selectStartup(next.id); else renderStartupDetail(); }
});
async function addRole(s, role, btn) {
  btn.disabled = true;
  try {
    const { id, existing } = await post(`/api/startups/${encodeURIComponent(s.id)}/roles/add`, { url: role.url });
    role.job_id = id;
    await loadJobs();
    toast(existing ? "That role is already in your jobs." : `Added to Search Jobs: ${role.title}.`);
    renderStartupDetail();
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}
function renderStartupDetail() {
  const box = $("#startup-detail");
  const s = SU.byId.get(SU.selected);
  if (!s) {
    box.replaceChildren(h("div", { class: "empty" }, SU.visible.length || !SU.loaded ? "Select a startup to see its round, what it does and its open roles." : "No startups here."));
    return;
  }
  const busy = SU.busy.get(s.id);
  const btn = (label, kind, fn, cls = "ghost", title = null) => h("button", { class: cls, disabled: !!busy, title, onclick: () => fn(s) },
    busy === kind ? [spinner(), " ", label] : label);
  const r = s.round;
  const roundText = r ? [r.name || s.stage, r.amount, r.date && new Date(r.date + "T12:00").toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })].filter(Boolean).join(" · ")
    : s.stage_round ? `${s.stage_round}${s.stage_source ? ` · per the ${s.stage_source} board` : ""} · amount and date not in the news yet`
    : s.batch ? `Round not in the news yet · YC ${s.batch}${s.yc_stage ? ` (${s.yc_stage.toLowerCase()} stage)` : ""}` : "Round unknown";
  const head = h("div", { class: "dhead" },
    h("div", { class: "dhead-top" },
      roundTile(s, "big"),
      h("div", { class: "dhead-title" },
        h("h2", {}, s.name),
        h("div", { class: "sub" }, [s.hq, s.team_size && `${s.team_size} people`, ...(s.industries || []).slice(0, 3)].filter(Boolean).join(" · "))),
      h("div", { class: "dhead-actions" },
        btn("Check its board now", "roles", readRoles, "ghost", "Reads the company's own careers board (Greenhouse, Lever or Ashby) right now. Each refresh does this on its own, and matching roles go to Search Jobs."),
        btn(s.tracked ? "Tracked" : "Track company", "track", trackStartup, "ghost", "Adds an \"Open roles\" card for this company to your tracker, with its round, while you watch for the right role."))),
    h("div", { class: "bar" },
      h("span", { class: "tag round-tag", "data-round": s.stage }, roundText),
      r?.url && h("a", { href: safeHref(r.url), target: "_blank", rel: "noopener", title: r.headline }, r.source || "news", " ↗"),
      extLink(s.website, "Website"), extLink(s.careers_url || s.jobs_url, "Careers"), extLink(s.yc_url, "YC page"),
      h("span", { class: "grow" }),
      (s.stage === "Unknown" || !r) && btn("Look up the round", "enrich", lookUpRound, "ghost small",
        SU.lookup ? "Asks Fundable or People Data Labs for the latest round (one lookup credit)." : "Needs FUNDABLE_API_KEY or PDL_API_KEY in .env (both have free allowances)."),
      btn(s.dismissed ? "Restore" : "Dismiss", "dismiss", (x) => dismissStartup(x, !x.dismissed), "ghost small")));
  const pad = h("div", { class: "pad" });
  if (s.one_liner || s.description) pad.append(h("div", { class: "card" }, h("h3", {}, s.one_liner || "About"), s.description && h("p", { class: "posting-body" }, s.description)));
  if (r?.headline) pad.append(h("div", { class: "card" }, h("h3", {}, "The round"),
    h("p", {}, r.url ? h("a", { href: safeHref(r.url), target: "_blank", rel: "noopener" }, r.headline, " ↗") : r.headline),
    h("p", { class: "muted" }, [r.source, r.date && fmtDate(r.date + "T12:00")].filter(Boolean).join(" · "), " · A headline, read by the app: check the article before you quote the round.")));
  pad.append(rolesCard(s, busy));
  if (s.sources?.length) pad.append(h("div", { class: "card" }, h("h3", {}, "Seen in"),
    h("div", { class: "src-list" }, ...s.sources.map((x) => x.url ? h("a", { class: "tag", href: safeHref(x.url), target: "_blank", rel: "noopener" }, x.name || x.kind, x.at ? ` · ${fmtDate(x.at + "T12:00")}` : "")
      : h("span", { class: "tag" }, x.name || x.kind)))));
  box.replaceChildren(head, pad);
}
function rolesCard(s, busy) {
  const card = h("div", { class: "card" }, h("h3", {}, "Open roles"));
  if (!s.roles) {
    card.append(h("p", { class: "muted" }, busy === "roles" ? [spinner(), " Reading its careers board…"]
      : ["Its board hasn't been read yet. Each refresh reads the startups' boards on its own, newest raise first, and roles matching your filters (",
         SU.phrases.join(", ") || "none saved", ") go to Search Jobs. ", h("button", { class: "link-btn", onclick: () => readRoles(s) }, "Check its board now"), "."]));
    return card;
  }
  const jobs = s.roles.jobs, match = jobs.filter((j) => j.match), rest = jobs.filter((j) => !j.match);
  if (s.exited) card.append(h("p", { class: "muted" }, `It was ${s.exited === "public" ? "taken public" : "acquired"}, so it isn't a startup any more: its matching roles are shown here but not added to Search Jobs on their own. Add one if you want it.`));
  if (!s.board) card.append(h("p", { class: "muted" }, s.roles.from_vc_board ? "Listed by a VC portfolio board; the company's own board wasn't found." : "No Greenhouse, Lever or Ashby board was found on its website.",
    s.careers_url ? [" Its careers page: ", extLink(s.careers_url, host(s.careers_url))] : "", s.exited ? "" : " A role it lists that matches your filters is in Search Jobs already; another one can be ",
    s.exited ? "" : h("button", { class: "link-btn", onclick: () => { addDialog.showModal(); addField("company").value = s.name; addField("url").focus(); } }, "added by its link"), s.exited ? "" : "."));
  else card.append(h("p", { class: "muted" }, `${plural(jobs.length, "open role")} on `, extLink(s.board.url, s.board.kind.replace(/^./, (c) => c.toUpperCase())),
    s.roles.checked ? ` · read ${fmtDate(s.roles.checked)}` : "", match.length ? ` · ${match.length} match your filters (title and place) and are in Search Jobs` : jobs.length ? " · none match your filters' titles and places" : ""));
  const roleLi = (j) => {
    const added = j.job_id && h("button", { class: "ghost small", title: "Added on its own, because it matches your filters", onclick: () => openJob(j.job_id, "posting") }, j.status ? `Tracked · ${j.status}` : "In Search Jobs");
    const addBtn = h("button", { class: j.match ? "primary small" : "ghost small", title: "Stores the posting here, with the round, so you can score it or make its résumé" }, "Add to Search Jobs");
    addBtn.addEventListener("click", () => addRole(s, j, addBtn));
    return h("li", { class: j.match ? "match" : "" },
      h("div", { class: "what" }, h("div", { class: "t" }, j.title), h("div", { class: "s" }, [j.location, j.remote && "Remote", j.pay, j.posted && `Posted ${fmtDate(j.posted + "T12:00")}`].filter(Boolean).join(" · "))),
      extLink(j.url, "Posting"), added || addBtn);
  };
  if (match.length) card.append(h("ul", { class: "roles" }, ...match.map(roleLi)));
  if (rest.length) card.append(h("details", { class: "roles" }, h("summary", {}, `${plural(rest.length, "other role")} that don't match your filters`), h("ul", { class: "roles" }, ...rest.map(roleLi))));
  return card;
}
async function refreshStartups() {
  try {
    const r = await post("/api/startups/refresh");
    toast(`Started: ${r.label}. The list updates when it finishes.`);
    loadSummary();
  } catch (e) { toast(e.message, true); }
}
$("#startup-refresh").addEventListener("click", refreshStartups);
$("#startup-filter").addEventListener("input", (e) => { SU.query = e.target.value; clearTimeout(renderStartups._t); renderStartups._t = setTimeout(renderStartups, 60); });
$("#startup-sort").addEventListener("change", (e) => { SU.sort = e.target.value; renderStartups(); });
const suDialog = $("#startup-dialog"), suForm = $("#startup-form");
$("#startup-add").addEventListener("click", () => { suForm.reset(); formError(suForm); suDialog.showModal(); suForm.elements.namedItem("name").focus(); });
$("[data-act=cancel]", suForm).addEventListener("click", () => suDialog.close());
suForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(suForm).entries());
  if (!body.name.trim()) { formError(suForm, "Give the startup's name."); return; }
  try {
    const s = suReplace(await post("/api/startups", body));
    suDialog.close();
    SU.view = "all"; SU.rounds.clear(); renderStartups();
    selectStartup(s.id);
    toast(s.line ? `${s.name}: ${s.line}` : `Added ${s.name}. Check its board now reads its careers board.`);
  } catch (err) { formError(suForm, err.message); }
});

// ---- profile -------------------------------------------------------------------------------------
// searches.yaml's candidate section: who the résumés are for, and which résumés to score against.
const P = { data: null, rows: [], fallback: null, base: "" };
const pForm = $("#profile-form"), pField = (n) => pForm.elements.namedItem(n);
const PROFILE_TEXT = ["name", "pronouns", "city", "phone", "email", "linkedin", "pdf_name", "evidence"];
const PDF_NAME_DEFAULT = "{title} - {company}";
// The Profile's PDF file name pattern filled in, as config.fill_pdf_name does it on the server.
function fillPdfName(pattern, { name = "", title = "", company = "", version = 0, day = new Date() } = {}) {
  const unsafe = /[\\/:*?"<>|\x00-\x1f]+/g, two = (n) => String(n).padStart(2, "0");
  const clean = (x, n = 80) => (x || "").replace(unsafe, " ").trim().slice(0, n).trim();
  const vals = { "{name}": clean(name), "{first}": clean(name.split(/\s+/)[0]), "{company}": clean(company, 40),
    "{title}": clean(title), "{YYYY}": String(day.getFullYear()), "{YY}": two(day.getFullYear() % 100),
    "{MM}": two(day.getMonth() + 1), "{DD}": two(day.getDate()), "{v}": version ? `v${version}` : "",
    "{vv}": version > 1 ? `v${version}` : "" };
  let out = (pattern || PDF_NAME_DEFAULT).replace(/\{[^{}]*\}/g, (m) => (m in vals ? vals[m] : m));
  out = out.replace(unsafe, " ").replace(/\s+/g, " ").replace(/_{2,}/g, "_").replace(/-{2,}/g, "-")
    .replace(/(\s+[-_–—]+)+\s*$/, "");
  return out.replace(/^[\s\-_.,]+|[\s\-_.,]+$/g, "") || "Resume";
}
const fileLabel = (f) => f.replace(/^resume-|\.md$/g, "").split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") + " resume";

function profileState() {
  const out = Object.fromEntries(PROFILE_TEXT.map((k) => [k, pField(k).value.trim()]));
  const ticked = P.rows.filter((r) => r.ticked);
  const first = ticked.find((r) => r.file === P.fallback) || ticked[0];
  out.resumes = (first ? [first, ...ticked.filter((r) => r !== first)] : []).map((r) => ({ name: r.name.trim(), file: r.file }));
  return out;
}
const profileDirty = () => !!P.data && JSON.stringify(profileState()) !== P.base;

async function loadProfile() {
  if (PIN.saved == null || $("#pin-text").value.trim() === PIN.saved.trim()) loadPinned();   // not over unsaved edits
  if (!P.data || !profileDirty()) {
    try { applyProfile(await api("/api/profile")); } catch (e) { toast(e.message, true); }
  }
}
function applyProfile(d) {
  P.data = d;
  const c = d.candidate;
  for (const k of PROFILE_TEXT) pField(k).value = c[k] || "";
  pField("evidence").placeholder = d.default_evidence;
  const facts = new Map(d.files.map((f) => [f.file, f]));
  P.rows = [...c.resumes.map((r) => ({ ...r, ticked: facts.has(r.file), missing: !facts.has(r.file) })),
            ...d.files.filter((f) => !c.resumes.some((r) => r.file === f.file)).map((f) => ({ file: f.file, name: "", ticked: false }))];
  P.fallback = c.resumes.find((r) => facts.has(r.file))?.file || null;
  $("#profile-first").classList.toggle("hidden", d.saved && !d.example);
  $("#profile-first-text").textContent = d.example
    ? `This is a made-up example profile, so you can try the app. Put in your own details and save; your files are in ${d.folder}.`
    : "No profile is saved yet. Put in your details and save.";
  formError(pForm);
  renderResumeRows();
  P.base = JSON.stringify(profileState());
  profileChanged();
  const ir = d.impact_record;
  $("#profile-impact").textContent = ir.words ? `${ir.words.toLocaleString()} words` : "empty";
}
function renderResumeRows() {
  const facts = new Map(P.data.files.map((f) => [f.file, f]));
  if (!P.rows.some((r) => r.ticked && r.file === P.fallback)) P.fallback = P.rows.find((r) => r.ticked)?.file || null;
  $("#resume-files").replaceChildren(...P.rows.map((r) => {
    const f = facts.get(r.file);
    const tick = h("input", { type: "checkbox", checked: r.ticked, disabled: r.missing, "aria-label": `Score against ${r.file}` });
    const name = h("input", { type: "text", value: r.name, placeholder: fileLabel(r.file), disabled: !r.ticked, "aria-label": `Name for ${r.file}` });
    const fb = h("input", { type: "radio", name: "fallback", checked: r.ticked && r.file === P.fallback, disabled: !r.ticked });
    tick.addEventListener("change", () => {
      r.ticked = tick.checked;
      if (r.ticked && !r.name.trim()) r.name = fileLabel(r.file);
      renderResumeRows(); profileChanged();
      if (r.ticked) $(`[data-file="${CSS.escape(r.file)}"] input[type=text]`)?.select();
    });
    name.addEventListener("input", () => { r.name = name.value; profileChanged(); });
    fb.addEventListener("change", () => { P.fallback = r.file; profileChanged(); });
    return h("li", { class: "rfile" + (r.ticked ? " on" : ""), "data-file": r.file },
      tick, name,
      h("div", { class: "rmeta" }, h("code", {}, r.file),
        r.missing ? h("span", { class: "tag warn-tag" }, "file missing")
          : h("span", { class: "muted" }, [f.headline, plural(f.words, "word")].filter(Boolean).join(" · "))),
      h("label", { class: "check fb", title: "Where the writers start when the matcher's pick isn't one of these" }, fb, "Fallback"));
  }), ...(P.rows.length ? [] : [h("li", { class: "muted" }, "No résumés in your profile folder yet. Add one below.")]));
}
function profileChanged() {
  const name = pField("name").value.trim();
  $("#contact-preview").textContent = ["city", "phone", "email", "linkedin"].map((k) => pField(k).value.trim()).filter(Boolean).join(" | ") || "—";
  const pattern = pField("pdf_name").value.trim(), job = { name: name || "Your Name", title: "Staff Technical Program Manager", company: "Acme" };
  const versioned = /\{vv?\}/.test(pattern);
  $("#pdf-preview").replaceChildren(...(versioned ? [1, 2] : [0]).map((v) =>
    h("div", {}, `${fillPdfName(pattern, { ...job, version: v })}.pdf`)));
  const dirty = profileDirty();
  $("#profile-save").disabled = !dirty;
  $("#profile-discard").disabled = !dirty;
  $("#profile-state").textContent = dirty ? "Unsaved changes" : P.data?.saved ? "Saved in searches.yaml" : "";
}
pForm.addEventListener("input", (e) => { if (!e.target.closest(".rfile, .pin-card")) profileChanged(); });
pField("pdf_name").placeholder = PDF_NAME_DEFAULT;
$$("#pdf-vars .var-chip").forEach((b) => b.addEventListener("click", () => {
  const f = pField("pdf_name"), v = b.dataset.var;
  if (!f.value.trim()) { f.value = PDF_NAME_DEFAULT; delete f.dataset.caret; }   // add to the default, not to nothing
  const at = f.dataset.caret != null ? Math.min(+f.dataset.caret, f.value.length) : f.value.length;   // where the cursor was
  const before = f.value.slice(0, at), after = f.value.slice(at);
  const pad = (before && !/\s$/.test(before) ? " " : "");
  f.value = before + pad + v + (after && !/^\s/.test(after) ? " " : "") + after;
  const caret = (before + pad + v).length;
  f.focus(); f.setSelectionRange(caret, caret); f.dataset.caret = caret;
  profileChanged();
}));
pField("pdf_name").addEventListener("blur", (e) => { e.target.dataset.caret = e.target.selectionStart; });
$("#profile-discard").addEventListener("click", () => applyProfile(P.data));
pForm.addEventListener("submit", (e) => { e.preventDefault(); saveProfile(); });
async function saveProfile() {
  if (!profileDirty()) return;
  const btn = $("#profile-save");
  btn.disabled = true;
  try {
    applyProfile(await put("/api/profile", profileState()));
    toast("Profile saved. The next score or résumé uses it.");
  } catch (e) { formError(pForm, e.message); btn.disabled = false; }
}
$("#resume-add").addEventListener("click", () => $("#resume-upload").click());
$("#resume-upload").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (file) importResume(file);
});

// ---- uploading a résumé: the file is read and sorted into the app's markdown (Claude, about a minute), then
// checked word by word against the file. You see both side by side, fix anything flagged, name it and save.
const impDialog = $("#import-dialog");
impDialog.addEventListener("close", () => { F.forLook = false; });
// A button that uploads a résumé for one job: read and checked as on Profile, then saved as that job's résumé.
function uploadForJobButton(j, label, cls = "ghost") {
  if (!j) return null;
  const file = h("input", { type: "file", accept: $("#resume-upload").accept, hidden: true });
  file.addEventListener("change", () => {
    const f = file.files[0];
    file.value = "";
    if (f) importResume(f, j);
  });
  return h("span", { class: "upload-for-job" },
    h("button", { type: "button", class: cls, title: "Use a résumé you already have for this role: a PDF, Word or text file", onclick: () => file.click() }, label), file);
}
async function importResume(file, job = null) {
  const body = $("#import-body");
  $("#import-title").textContent = job ? "Add a résumé for this role" : "Add a résumé";
  $("#import-sub").textContent = job ? `${file.name} · for ${job.title}${job.company ? " at " + job.company : ""}` : file.name;
  body.replaceChildren(h("div", { class: "import-reading" }, h("span", { class: "import-blob", "aria-hidden": "true" }),
    h("p", {}, h("b", {}, "Reading your résumé…")),
    h("p", { class: "muted" }, "Claude is sorting it into sections, word for word. This takes about a minute.")));
  impDialog.showModal();
  let d;
  try {
    d = await api("/api/profile/resumes/import", { method: "POST", body: file,
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) } });
  } catch (err) {
    body.replaceChildren(h("p", { class: "form-error" }, err.message),
      h("div", { class: "actions" }, h("span", { class: "grow" }), h("button", { class: "ghost", onclick: () => impDialog.close() }, "Close")));
    return;
  }
  if (impDialog.open) renderImport(job ? { ...d, job } : d);
}
function renderImport(d) {
  const body = $("#import-body");
  $("#import-title").textContent = "Check what the app read";
  $("#import-sub").textContent = `${d.filename} · ${d.words.toLocaleString()} words in your file, ${d.kept}% kept`;
  const orig = d.has_original
    ? h("iframe", { class: "import-orig", src: fileUrl(`/api/profile/resumes/import/${d.id}/original.pdf`) + "#view=FitH", title: "Your file" })
    : h("pre", { class: "import-orig import-text" }, d.source);
  const sheet = h("iframe", { class: "import-sheet", title: "Your résumé as the app reads it", srcdoc: d.html || "" });
  sheet.addEventListener("load", () => {
    const root = sheet.contentDocument?.documentElement;
    if (root) root.style.zoom = Math.min(1, (sheet.clientWidth - 24) / (8.5 * PAGE_PX + 48)).toFixed(3);
  });
  const md = h("textarea", { class: "md-editor import-md", spellcheck: "true", hidden: true, "aria-label": "Résumé markdown" });
  md.value = d.markdown;
  const toggle = h("button", { type: "button", class: "ghost small" }, "Edit the markdown");
  const recheck = h("button", { type: "button", class: "ghost small", hidden: true }, "Check again");
  toggle.addEventListener("click", () => {
    const editing = md.hidden;
    md.hidden = !editing; sheet.hidden = editing; recheck.hidden = !editing;
    toggle.textContent = editing ? "Show the page" : "Edit the markdown";
    if (!editing) recheck.click();
  });
  recheck.addEventListener("click", async () => {
    if (md.value === d.markdown) return;
    try { const nd = await post(`/api/profile/resumes/import/${d.id}/check`, { markdown: md.value }); renderImport({ ...nd, job: d.job, _editing: !md.hidden }); }
    catch (err) { toast(err.message, true); }
  });
  const flags = h("ul", { class: "import-flags" }, ...d.flags.map((f) =>
    h("li", { class: f.level }, h("i", { "aria-hidden": "true" }, f.level === "ok" ? "✓" : f.level === "warn" ? "!" : "i"), h("span", {}, f.text))));
  const name = h("input", { type: "text", value: fileLabel(`resume-${d.name}.md`), "aria-label": "Name for this résumé" });
  const fname = h("input", { type: "text", value: d.name, "aria-label": "File name" });
  const score = h("input", { type: "checkbox", checked: true });
  const forLook = !!(F.forLook && d.look);
  const words = h("input", { type: "checkbox", checked: !forLook });
  const look = h("input", { type: "checkbox", checked: forLook, disabled: !d.look });
  const stem = d.filename.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ").replace(/\s*\bresume\b\s*/i, " ").trim();
  const lookName = h("input", { type: "text", value: `${stem || "My"} look`, "aria-label": "Name for the format" });
  const lookBox = h("div", { class: "import-look", hidden: !forLook });
  const record = h("input", { type: "checkbox", checked: !forLook });
  const save = h("button", { class: "primary" }, "Save");
  const err = h("p", { class: "form-error hidden" });
  const sync = () => {
    lookBox.hidden = !look.checked;
    save.disabled = !(words.checked && d.html) && !look.checked && !record.checked;
    save.textContent = !words.checked && !look.checked && record.checked ? "Next"
      : words.checked && look.checked ? "Save résumé and format" : look.checked ? "Save format" : "Save résumé";
    if (look.checked && !lookBox.childNodes.length) showLook(d, lookBox);
  };
  words.addEventListener("change", sync);
  look.addEventListener("change", sync);
  record.addEventListener("change", sync);
  const cols = h("div", { class: "import-cols" },
    h("section", {}, h("h3", {}, "What you uploaded"), orig),
    h("section", {}, h("div", { class: "import-col-head" }, h("h3", {}, "What the app read"), h("span", { class: "grow" }), recheck, toggle),
      sheet, md, flags));
  if (d.job) { jobImportSave(d, body, cols, md, recheck, toggle); return; }
  body.replaceChildren(
    cols,
    h("div", { class: "import-uses" },
      h("label", { class: "check import-use" }, words, h("b", {}, "Save its words as a résumé")),
      h("div", { class: "import-save" },
        h("label", {}, "Name ", name), h("label", {}, "File ", h("code", {}, "references/resume-"), fname, h("code", {}, ".md")),
        h("label", { class: "check" }, score, "Score jobs against it")),
      h("label", { class: "check import-use", title: d.look ? "" : "A look can only be read from a PDF or a Word file" }, look,
        h("b", {}, "Save its look as a format"), d.look ? null : h("span", { class: "muted" }, " (only from a PDF or Word file)")),
      h("div", { class: "import-save", hidden: !forLook, "data-look-name": "" }, h("label", {}, "Format name ", lookName)),
      lookBox,
      h("label", { class: "check import-use" }, record, h("b", {}, "Add what's new to my impact record"),
        h("span", { class: "muted" }, " (you pick each line next; nothing is added on its own)"))),
    err,
    h("div", { class: "actions" }, h("button", { class: "ghost", onclick: () => impDialog.close() }, "Cancel"), h("span", { class: "grow" }), save));
  if (d._editing) toggle.click();
  look.addEventListener("change", () => { body.querySelector("[data-look-name]").hidden = !look.checked; });
  sync();
  save.addEventListener("click", async () => {
    if (!md.hidden && md.value !== d.markdown) { recheck.click(); return; }
    save.disabled = true;
    if (look.checked) {        // first: saving the résumé clears the upload the look is read from
      try {
        const r = await post("/api/formats/own", { name: lookName.value, import_id: d.id });
        F.data = r; F.pick = r.created; F.forLook = false;
        if (S.tab === "profile") renderFormats();
        const made = r.formats.find((f) => f.id === r.created).name;
        toast(`Saved “${made}” in Your formats. Use ${made} there makes it your résumé format.`);
      } catch (e0) { formError(body, e0.message); save.disabled = false; return; }
      look.checked = false; look.disabled = true; sync();
      if (!words.checked) { if (record.checked) renderSuggestions({ markdown: md.value, source: d.filename }); else impDialog.close(); return; }
    }
    if (!words.checked) { renderSuggestions({ markdown: md.value, source: d.filename }); return; }
    const payload = { markdown: md.value, filename: `${fname.value.trim() || d.name}.md`, import_id: d.id };
    let res;
    try { res = await post("/api/profile/resumes", payload); }
    catch (e1) {
      if (!/already exists/.test(e1.message) || !confirm(`${e1.message} Replace it?`)) { formError(body, e1.message); save.disabled = false; return; }
      try { res = await post("/api/profile/resumes", { ...payload, replace: true }); }
      catch (e2) { formError(body, e2.message); save.disabled = false; return; }
    }
    if (record.checked) renderSuggestions({ markdown: md.value, source: d.filename });
    else impDialog.close();
    P.data.files = res.files;              // keep unsaved profile edits; tick the new résumé if asked
    let row = P.rows.find((r) => r.file === res.file);
    if (!row) P.rows.push(row = { file: res.file, name: "" });
    Object.assign(row, { ticked: score.checked, missing: false, name: name.value.trim() || row.name || fileLabel(res.file) });
    renderResumeRows(); profileChanged();
    toast(score.checked ? `Added ${res.file}. Save the profile to start scoring against it.` : `Added ${res.file}.`);
  });
}

// An upload for one job: it becomes that job's résumé, as its next version when it has one. Its words can also go
// to the impact record (picked line by line next), as from Profile.
function jobImportSave(d, body, cols, md, recheck, toggle) {
  const j = d.job;
  const record = h("input", { type: "checkbox" });
  const save = h("button", { class: "primary", disabled: !d.html }, "Use for this role");
  body.replaceChildren(cols,
    h("div", { class: "import-uses" },
      h("p", { class: "import-for" }, h("b", {}, `${j.title}${j.company ? " at " + j.company : ""}`), h("br"),
        h("span", { class: "muted" }, j.local && j.tailored
          ? "It becomes the next version of this role's résumé, drawn in its format. Earlier versions stay in the version menu."
          : "It becomes this role's résumé, drawn in your résumé format, with Edit on page and versions. You can switch back to another résumé from the menu above it.")),
      h("label", { class: "check import-use" }, record, h("b", {}, "Add what's new to my impact record"),
        h("span", { class: "muted" }, " (you pick each line next; nothing is added on its own)"))),
    h("p", { class: "form-error hidden" }),
    h("div", { class: "actions" }, h("button", { class: "ghost", onclick: () => impDialog.close() }, "Cancel"), h("span", { class: "grow" }), save));
  if (d._editing) toggle.click();
  save.addEventListener("click", async () => {
    if (!md.hidden && md.value !== d.markdown) { recheck.click(); return; }
    save.disabled = true;
    let res;
    try { res = await post(`/api/jobs/${encodeURIComponent(j.id)}/resume/upload`, { import_id: d.id, markdown: md.value }); }
    catch (e) { formError(body, e.message); save.disabled = false; return; }
    if (record.checked) renderSuggestions({ markdown: md.value, source: d.filename });
    else impDialog.close();
    toast((res.added_version ? `Saved as v${res.current} of this role's résumé.` : `${d.filename} is now this role's résumé.`) +
      (res.unscored ? ` Scoring it against the posting (${actionTime("upload")}).` : ""));
    S.detailTab = "resume"; S.detailTabJob = j.id;
    if (res.unscored) scoreJobs([j.id], "upload");
    reloadResume(j.id);
    if (res.versions.find((v) => v.n === res.current)?.rating) {   // keywords are in; re-rate skills and experience
      post(`/api/jobs/${encodeURIComponent(j.id)}/resume/rescore`, { n: res.current })
        .then(() => reloadResume(j.id))
        .catch((e) => toast(`Couldn't re-rate the upload: ${e.message}`, true));
    }
  });
}

// What a résumé has that the impact record doesn't, role by role: new lines ticked, lines the record has with less
// detail unticked, lines it already has folded away. Each role's ticked lines are added with one click.
// `into` shows it in another box (the setup wizard's) instead of the dialog, without the dialog's buttons.
async function renderSuggestions(params, into = null) {
  const body = into || $("#import-body");
  if (!into) {
    $("#import-title").textContent = "What's new for your impact record";
    $("#import-sub").textContent = `From ${params.source || params.file}. Nothing is added until you press Add.`;
    if (!impDialog.open) impDialog.showModal();
  }
  body.replaceChildren(h("div", { class: "import-reading" }, h("span", { class: "import-blob", "aria-hidden": "true" }),
    h("p", {}, h("b", {}, "Checking each line against your impact record…")),
    h("p", { class: "muted" }, "Up to half a minute: the evidence models run on this Mac.")));
  let r;
  try { r = await post("/api/impact-record/suggestions", params); }
  catch (e) {
    body.replaceChildren(h("p", { class: "form-error" }, e.message), ...(into ? [] : [h("div", { class: "actions" }, h("span", { class: "grow" }),
      h("button", { class: "ghost", onclick: () => impDialog.close() }, "Close"))]));
    return;
  }
  const source = r.source;
  const open = r.roles.filter((role) => role.lines.some((l) => l.status !== "covered"));
  const covered = r.roles.length - open.length;
  const card = (role) => {
    const boxes = [];
    const lineRow = (l) => {
      const cb = h("input", { type: "checkbox", checked: l.status === "new" });
      boxes.push([cb, l.text]);
      cb.addEventListener("change", count);
      return h("li", { class: `sugg-line ${l.status}` }, h("label", { class: "check" }, cb, h("span", {}, l.text)),
        h("small", {}, l.why));
    };
    const fresh = role.lines.filter((l) => l.status !== "covered"), have = role.lines.filter((l) => l.status === "covered");
    const add = h("button", { type: "button", class: "primary small" });
    const done = h("span", { class: "sugg-done", hidden: true });
    function count() {
      const n = boxes.filter(([cb]) => cb.checked).length;
      add.disabled = !n;
      add.textContent = role.known ? `Add ${plural(n, "line")}` : n ? `Add this role (${plural(n, "line")})` : "Add this role";
    }
    const list = h("ul", { class: "sugg-lines" }, ...fresh.map(lineRow));
    count();
    add.addEventListener("click", async () => {
      const lines = boxes.filter(([cb]) => cb.checked).map(([, t]) => t);
      add.disabled = true;
      try {
        const d = await DOCS.impact.amend("/suggestions/apply", { company: role.company, title: role.title, dates: role.dates, lines, source },
                                          /^from your résumés$/i);
        boxes.forEach(([cb]) => { if (cb.checked) { cb.checked = false; cb.disabled = true; cb.closest("li").classList.add("added"); } });
        done.hidden = false; done.textContent = `Added ${plural(lines.length, "line")} ✓`;
        count();
      } catch (e) { toast(e.message, true); count(); }
    });
    return h("section", { class: "sugg-role" },
      h("div", { class: "sugg-head" }, h("b", {}, role.company), h("span", {}, ` | ${role.title}`),
        role.dates && h("span", { class: "muted" }, ` · ${role.dates}`), !role.known && h("span", { class: "tag sugg-new" }, "New role"),
        h("span", { class: "grow" }), done, add),
      list,
      have.length ? h("details", { class: "sugg-have" }, h("summary", {}, `Already in your record (${have.length})`),
        h("ul", {}, ...have.map((l) => h("li", {}, l.text)))) : null);
  };
  body.replaceChildren(
    open.length ? h("div", { class: "sugg-list" }, ...open.map(card))
      : h("p", {}, "Your impact record already has everything in this résumé."),
    h("p", { class: "muted sugg-foot" },
      covered ? `${plural(covered, "role")} with nothing new left out. ` : "",
      "Added lines go in the record's “From your résumés” section, under their role. ",
      r.models ? "Checked with the evidence models and by words and figures." : `Checked by words and figures (the evidence models weren't used: ${r.reason || "off"}).`),
    ...(into ? [] : [h("div", { class: "actions" },
      h("button", { class: "ghost", onclick: () => { impDialog.close(); showTab("impact"); } }, "Open the impact record"),
      h("span", { class: "grow" }), h("button", { class: "primary", onclick: () => impDialog.close() }, "Done"))]));
}

// The Impact record tab: compare one of your saved résumés with the record.
$("#impact-compare").addEventListener("click", async () => {
  let prof;
  try { prof = await api("/api/profile"); } catch (e) { toast(e.message, true); return; }
  $("#import-title").textContent = "Compare a résumé with your impact record";
  $("#import-sub").textContent = "Pick one of your résumés. You choose what to add, line by line.";
  $("#import-body").replaceChildren(
    prof.files.length ? h("ul", { class: "sugg-files" }, ...prof.files.map((f) => h("li", {},
      h("button", { type: "button", class: "ghost", onclick: () => renderSuggestions({ file: f.file }) }, fileLabel(f.file)),
      h("span", { class: "muted" }, [f.headline, f.updated && `updated ${f.updated}`].filter(Boolean).join(" · ")))))
      : h("p", { class: "muted" }, "No résumés in your profile yet. Add one on the Profile tab."),
    h("p", { class: "muted" }, "Or add a new one, as a PDF or Word file, from Profile › Your résumés."),
    h("div", { class: "actions" }, h("span", { class: "grow" }), h("button", { class: "ghost", onclick: () => impDialog.close() }, "Close")));
  impDialog.showModal();
});

// The look read from an upload: what was found, what a one-column page leaves out, and the résumé drawn in it.
async function showLook(d, box) {
  box.replaceChildren(h("p", { class: "muted" }, spinner(), " Reading its look…"));
  let lk;
  try { lk = await api(`/api/profile/resumes/import/${d.id}/look`); }
  catch (e) { box.replaceChildren(h("p", { class: "form-error" }, e.message)); return; }
  box.replaceChildren(
    h("img", { class: "import-look-img", src: fileUrl(`/api/profile/resumes/import/${d.id}/look.png`), alt: "Your résumé drawn in this look" }),
    h("div", {},
      h("p", {}, h("b", {}, "Kept: "), lk.found.join(" · ") || "fonts, sizes and layout"),
      lk.not_copied.length ? h("div", {}, h("p", {}, h("b", {}, "Not copied, on purpose:")),
        h("ul", { class: "import-notes" }, ...lk.not_copied.map((n) => h("li", {}, n)))) : null,
      h("p", { class: "muted" }, "You can change any of it afterwards: Profile › Résumé format › Edit.")));
}

// ---- profile: the pinned section (references/pinned.md, jobpipe/pinned.py), word for word in every résumé.
// Saved on its own, not with the profile form it sits in; the preview redraws as you type.
const PIN = { saved: null, text: null, seq: 0 };
async function loadPinned() {
  let d;
  try { d = await api("/api/profile/pinned"); } catch (e) { toast(e.message, true); return; }
  PIN.saved = PIN.text = d.text;
  $("#pin-text").value = d.text;
  $("#pin-file").textContent = d.file;
  showPinPreview(d);
  pinChanged();
}
function showPinPreview(d) {
  const sheet = $("#pin-sheet"), empty = $("#pin-empty");
  $("#pin-words").textContent = d.words ? `About ${plural(d.words, "word")} of the page limit` : "";
  sheet.hidden = !d.html;
  empty.hidden = !!d.html;
  if (d.error) empty.textContent = d.error;
  else if (!d.html) empty.textContent = "Nothing is pinned, so every résumé is the writers' work alone.";
  if (d.html && sheet.srcdoc !== d.html) sheet.srcdoc = d.html;
}
// The sheet is a letter-size page: shrink it to the frame and bring the highlighted part into view.
$("#pin-sheet").addEventListener("load", () => {
  const doc = $("#pin-sheet").contentDocument;
  if (!doc?.body) return;
  doc.documentElement.style.zoom = Math.min(1, $("#pin-sheet").clientWidth / (doc.body.offsetWidth + 48));
  const pin = doc.querySelector(".pin");     // scrolls the frame only: scrollIntoView would move the Profile too
  if (pin) doc.scrollingElement.scrollTop = pin.getBoundingClientRect().top + doc.scrollingElement.scrollTop - 24;
});
function pinChanged() {
  const dirty = PIN.saved != null && $("#pin-text").value.trim() !== PIN.saved.trim();
  $("#pin-save").disabled = !dirty;
  $("#pin-discard").disabled = !dirty;
  $("#pin-state").textContent = dirty ? "Unsaved changes" : "";
}
$("#pin-text").addEventListener("input", () => {
  PIN.text = $("#pin-text").value;
  pinChanged();
  clearTimeout(PIN.timer);
  PIN.timer = setTimeout(async () => {
    const seq = ++PIN.seq;
    try {
      const d = await post("/api/profile/pinned/preview", { text: PIN.text });
      if (seq === PIN.seq) showPinPreview(d);
    } catch (e) { toast(e.message, true); }
  }, 400);
});
$("#pin-discard").addEventListener("click", loadPinned);
$("#pin-save").addEventListener("click", async () => {
  const btn = $("#pin-save");
  btn.disabled = true;
  try {
    const d = await put("/api/profile/pinned", { text: $("#pin-text").value });
    PIN.saved = PIN.text = d.text;
    $("#pin-text").value = d.text;
    PIN.seq++;
    showPinPreview(d);
    pinChanged();
    $("#pin-state").textContent = d.text ? "✓ saved" : "✓ nothing pinned";
    toast(d.text ? "Saved. Every résumé made from now on has this section, word for word." : "Nothing is pinned now.");
  } catch (e) { toast(e.message, true); pinChanged(); }
});

// ---- résumé format: the Profile's one default (jobpipe/formats.py). Choosing one can redraw every résumé
// already made; one that would go over the new format's page limit keeps its format until it's trimmed.
const F = { data: null, pick: null };
// ---- profile: the tracker (searches.yaml's tracker.ghost_after_days) ----------------------------------------
const GH = { saved: null };
async function loadGhostDays() {
  try { GH.saved = (await api("/api/tracker/settings")).ghost_after_days; } catch (e) { toast(e.message, true); return; }
  $("#ghosted-days").value = GH.saved;
  ghostDaysChanged();
}
function ghostDaysChanged() {
  const v = $("#ghosted-days").value.trim();
  $("#ghosted-save").disabled = v === "" || Number(v) === GH.saved;
  $("#ghosted-state").textContent = "";
}
$("#ghosted-days").addEventListener("input", ghostDaysChanged);
$("#ghosted-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#ghosted-save");
  btn.disabled = true;
  try {
    const r = await put("/api/tracker/settings", { ghost_after_days: $("#ghosted-days").value.trim() });
    GH.saved = r.ghost_after_days;
    if (S.summary) S.summary.ghost_after_days = r.ghost_after_days;
    $("#ghosted-days").value = r.ghost_after_days;
    $("#ghosted-state").textContent = r.ghost_after_days ? "✓ saved" : "✓ off";
    r.moved.forEach((m) => S.ghostSeen.add(`${m.id}@${dayKey(new Date())}`));   // said here, not again in a toast
    savePrefs();
    toast(!r.ghost_after_days ? "Roles won't move to Ghosted on their own."
      : r.moved.length ? `Saved. ${r.moved.length} role${r.moved.length === 1 ? " was" : "s were"} past ${r.ghost_after_days} days with no change and moved to Ghosted.`
      : `Saved. Roles move to Ghosted after ${r.ghost_after_days} days with no change.`);
    if (r.moved.length) loadJobs();
  } catch (err) { toast(err.message, true); ghostDaysChanged(); }
});

// ---- about ------------------------------------------------------------------------------------
// Profile's About card: the version this copy runs (the Mac app's number, or the commit a checkout is on) and
// the newest release. The guides and GitHub links are plain links in index.html.
const newerTag = (tag, v) => {
  const n = (x) => String(x).replace(/^v/, "").split(/[.-]/).map((p) => parseInt(p, 10) || 0);
  const a = n(tag), b = n(v);
  for (let i = 0; i < Math.max(a.length, b.length); i++) if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) > (b[i] || 0);
  return false;
};
const longDate = (d) => new Date(`${d}T12:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
async function loadAbout() {
  let r;
  try { r = await api("/api/about"); } catch (e) { $("#about-version").textContent = ""; return; }
  const run = r.running || {}, latest = r.latest;
  const rel = latest && h("a", { href: latest.url, target: "_blank", rel: "noopener" }, latest.tag);
  const parts = [];
  if (run.version) {
    parts.push(h("b", {}, `Version ${run.version}`));
    if (latest && newerTag(latest.tag, run.version))
      parts.push(" · ", rel, " is out. Check for Updates… in the Job Oyster menu gets it.");
    else if (latest) parts.push(" · the newest release");
  } else {
    parts.push(h("b", {}, run.commit ? "Running from the code" : "Version unknown"));
    if (run.commit) parts.push(`: ${run.branch ? run.branch + " at " : ""}${run.commit}, ${longDate(run.date)}`);
    if (latest) parts.push(" · newest release ", rel, latest.date ? `, ${longDate(latest.date)}` : "");
  }
  $("#about-version").replaceChildren(...parts);
}
$("#about-btn").addEventListener("click", async () => {
  await showTab("profile");      // the cards above it change height as they fill in
  $("#about-card").scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
});

async function loadFormats() {
  try { F.data = await api("/api/formats"); } catch (e) { toast(e.message, true); return; }
  if (!F.pick || F.pick === F.data.default) F.pick = F.data.default;
  renderFormats();
}
function renderFormats(result) {
  const d = F.data;
  const thumbV = (f) => (f.own ? `&v=${encodeURIComponent(JSON.stringify(f.tokens).length + f.name)}${F.bust || ""}` : "");
  const tile = (f) => {
    const b = h("button", { type: "button", class: "look-option format-option", role: "radio", "aria-checked": String(f.id === F.pick), "data-format": f.id },
      h("span", { class: "format-thumb" }, h("img", { src: fileUrl(`/api/formats/${f.id}/thumb.png`) + thumbV(f), alt: "", loading: "lazy" })),
      h("span", { class: "look-name" }, f.name, f.id === d.default && h("span", { class: "tag format-current" }, "current"),
        f.own && h("span", { class: "tag format-yours" }, "yours")),
      h("span", { class: "look-blurb" }, f.own ? (f.source ? `From ${f.source}` : "Your own") + (f.max_pages === 1 ? " · one page" : "") : f.blurb));
    b.addEventListener("click", () => { F.pick = f.id; renderFormats(); });
    if (!f.own) return b;
    return h("div", { class: "format-own" }, b, h("div", { class: "format-own-actions" },
      h("button", { type: "button", class: "link-btn small", onclick: () => editFormat(f.id) }, "Edit"),
      h("button", { type: "button", class: "link-btn small", onclick: () => deleteFormat(f) }, "Delete")));
  };
  const picked = d.formats.find((f) => f.id === F.pick) || d.formats[0];
  F.pick = picked.id;
  const add = h("button", { type: "button", class: "look-option format-option format-add" },
    h("span", { class: "format-thumb format-add-mark", "aria-hidden": "true" }, "+"), h("span", { class: "look-name" }, "From a résumé…"),
    h("span", { class: "look-blurb" }, "Upload one whose look you like (PDF or Word). As many as you want."));
  add.addEventListener("click", () => { F.forLook = true; $("#resume-upload").click(); });
  const custom = h("button", { type: "button", class: "look-option format-option format-add" },
    h("span", { class: "format-thumb format-add-mark", "aria-hidden": "true" }, "✎"), h("span", { class: "look-name" }, `Customise ${picked.name}…`),
    h("span", { class: "look-blurb" }, "A copy in Your formats with every setting to change."));
  custom.addEventListener("click", async () => {
    let name = `${picked.name} (mine)`;
    for (let k = 2; d.formats.some((f) => f.name.toLowerCase() === name.toLowerCase()); k++) name = `${picked.name} (mine ${k})`;
    try { const r = await post("/api/formats/own", { name, from: picked.id }); F.data = r; F.pick = r.created; renderFormats(); editFormat(r.created); }
    catch (e) { toast(e.message, true); }
  });
  $("#format-picker").replaceChildren(...d.formats.filter((f) => !f.own).map(tile));
  $("#format-own").replaceChildren(...d.formats.filter((f) => f.own).map(tile), add, custom);
  $("#format-pdf").href = fileUrl(`/api/formats/${F.pick}/preview.pdf`);
  $("#format-pdf").textContent = `Preview ${picked.name} PDF ↗`;
  $("#format-save").disabled = F.pick === d.default;
  $("#format-save").textContent = F.pick === d.default ? "In use" : `Use ${picked.name}`;
  const jobLink = (x) => h("li", {}, h("button", { type: "button", class: "link-btn", onclick: () => openJob(x.id, "resume") },
    [x.title, x.company].filter(Boolean).join(" · ") || x.id), x.pages ? h("span", { class: "muted" }, ` ${x.pages} pages`) : null);
  const def = d.formats.find((f) => f.id === d.default);
  const status = [];
  if (result) status.push(h("p", {}, result.redrawn ? `Redrew ${plural(result.redrawn, "résumé")} in ${def.name}.` : `${def.name} is your résumé format.`));
  if (d.behind.length) {
    status.push(h("p", { class: "muted" }, result?.over?.length
      ? `These run longer than ${def.pages_word} in ${def.name}, so they keep their format for now. Open one and use Trim to ${def.pages_word}:`
      : `Still in another format (open one to redraw it in ${def.name}):`),
      h("ul", { class: "format-behind" }, ...d.behind.map((x) => jobLink({ ...x, pages: result?.over?.find((o) => o.id === x.id)?.pages }))));
  }
  $("#format-status").replaceChildren(...status);
}
async function deleteFormat(f) {
  if (!confirm(`Delete “${f.name}”? Résumés already drawn in it keep their PDFs${f.id === F.data.default ? "; Signature becomes your format" : ""}.`)) return;
  try { F.data = await request(`/api/formats/own/${encodeURIComponent(f.id)}`, { method: "DELETE" }).then((r) => r.json()); F.pick = F.data.default; renderFormats(); }
  catch (e) { toast(e.message, true); }
}

// Every setting of a format of your own, with the fallback résumé redrawn as you change them.
const fmtDialog = $("#format-dialog");
const FMT_LABELS = {
  name_align: { left: "Left", center: "Centred" }, name_case: { "as-is": "As written", upper: "CAPITALS" },
  name_color: { ink: "Text colour", accent: "Accent" }, headline_color: { accent: "Accent", ink: "Text colour", muted: "Grey" },
  heading_case: { upper: "CAPITALS", title: "Title Case" }, heading_align: { left: "Left", center: "Centred" },
  heading_color: { accent: "Accent", ink: "Text colour" },
  heading_style: { plain: "Plain", rule: "Line under", bar: "Bar before", lines: "Lines beside" },
  header_rule: { none: "None", thin: "Thin line", thick: "Thick line", double: "Double line" },
  dates: { right: "Right of the role", under: "Under the role" },
};
function editFormat(fid) {
  const f = F.data.formats.find((x) => x.id === fid);
  if (!f) return;
  const t = { ...f.tokens }, C = F.data.choices, R = F.data.ranges;
  const form = h("form", { class: "fmt-form" });
  const img = h("img", { class: "fmt-preview", alt: `${f.name}, drawn with your fallback résumé` });
  const name = h("input", { type: "text", value: f.name, "aria-label": "Format name", required: true });
  const select = (k, opts) => {
    const el = h("select", { name: k }, ...opts.map((o) => h("option", { value: o, selected: String(t[k]) === String(o) }, FMT_LABELS[k]?.[o] || o)));
    el.addEventListener("change", () => { t[k] = el.value; changed(); });
    return el;
  };
  const number = (k, step) => {
    const el = h("input", { type: "number", name: k, min: R[k][0], max: R[k][1], step, value: t[k] });
    el.addEventListener("input", () => { t[k] = parseFloat(el.value); changed(); });
    return el;
  };
  const check = (k, label) => {
    const el = h("input", { type: "checkbox", name: k, checked: !!t[k] });
    el.addEventListener("change", () => { t[k] = el.checked; changed(); });
    return h("label", { class: "check" }, el, label);
  };
  const color = (k) => {
    const el = h("input", { type: "color", name: k, value: (t[k] || "#000000").toLowerCase() });
    el.addEventListener("input", () => { t[k] = el.value.toUpperCase(); none && (none.checked = false); changed(); });
    return el;
  };
  const accent = color("accent");
  const noneBox = h("input", { type: "checkbox", checked: !t.accent });
  const none = noneBox;
  noneBox.addEventListener("change", () => { t.accent = noneBox.checked ? "" : accent.value.toUpperCase(); changed(); });
  const row = (label, ...controls) => h("label", { class: "fmt-row" }, h("span", {}, label), h("span", { class: "fmt-ctl" }, ...controls));
  const group = (title, ...rows) => h("fieldset", {}, h("legend", {}, title), ...rows);
  form.append(
    row("Name", name),
    group("Type", row("Body", select("body_font", C.fonts), number("body_size", 0.25), "pt"),
      row("Headings", select("heading_font", C.fonts), number("heading_size", 0.25), "pt"),
      row("Your name", select("name_font", C.fonts), number("name_size", 0.5), "pt")),
    group("Colour", row("Accent", accent, h("label", { class: "check" }, noneBox, "None")), row("Text", color("ink"))),
    group("Header", row("Name", select("name_align", C.name_align), select("name_case", C.name_case), select("name_color", C.name_color)),
      row("Headline", select("headline_color", C.headline_color), check("headline_italic", "Italic")),
      row("Line under it", select("header_rule", C.header_rule))),
    group("Sections", row("Headings", select("heading_case", C.heading_case), select("heading_align", C.heading_align), select("heading_color", C.heading_color)),
      row("Heading style", select("heading_style", C.heading_style)),
      row("Dates", select("dates", C.dates)), row("Bullet", select("bullet", C.bullet)),
      h("div", { class: "fmt-row" }, h("span", {}), h("span", { class: "fmt-ctl" }, check("role_rule", "Line between roles"), check("skills_first", "Skills after the summary")))),
    group("Page", row("Margins", number("margin_in", 0.05), "in"),
      row("Length", select("max_pages", ["2", "1"]), h("span", { class: "muted" }, "pages"))));
  const notes = f.notes?.not_copied?.length ? h("div", { class: "fmt-notes" }, h("b", {}, `Not copied from ${f.source}:`),
    h("ul", {}, ...f.notes.not_copied.map((n) => h("li", {}, n)))) : null;
  const err = h("p", { class: "form-error hidden" });
  const save = h("button", { type: "button", class: "primary" }, "Save");
  $("#format-dialog-body").replaceChildren(h("div", { class: "fmt-cols" }, h("div", {}, form, notes), h("div", { class: "fmt-side" }, img,
    h("p", { class: "muted" }, "Your fallback résumé, redrawn as you change things. Words and ATS results stay the same in every format."))),
    err, h("div", { class: "actions" },
      h("button", { type: "button", class: "ghost", onclick: () => { fmtDialog.close(); deleteFormat(f); } }, "Delete"),
      h("span", { class: "grow" }), h("button", { type: "button", class: "ghost", onclick: () => fmtDialog.close() }, "Cancel"), save));
  $("#format-dialog-title").textContent = `Edit ${f.name}`;
  let timer, seq = 0;
  const changed = () => {          // the page's image policy allows data: URLs, not blob: ones
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      img.classList.add("loading");
      try {
        const res = await request("/api/formats/own/preview", { method: "POST", body: JSON.stringify({ tokens: t }) });
        if (!res.ok || mine !== seq) return;
        const blob = await res.blob();
        const src = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
        if (mine === seq) img.src = src;
      } finally { if (mine === seq) img.classList.remove("loading"); }
    }, 350);
  };
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      F.data = await put(`/api/formats/own/${encodeURIComponent(fid)}`, { name: name.value, tokens: t });
      F.bust = Date.now(); fmtDialog.close(); renderFormats();
      if (fid === F.data.default && confirm(`Saved. ${f.name} is your résumé format: redraw the résumés already made in it with the new settings?`)) {
        toast("Redrawing your résumés…");
        const r = await put("/api/profile/format", { format: fid, redraw: true, force: true });
        F.data = r; renderFormats(r); S.details.clear();
      } else toast("Saved.");
    } catch (e) { formError($("#format-dialog-body"), e.message); save.disabled = false; }
  });
  fmtDialog.showModal();
  changed();
}

$("#format-save").addEventListener("click", async () => {
  const btn = $("#format-save"), redraw = $("#format-redraw").checked;
  btn.disabled = true;
  btn.replaceChildren(spinner(), redraw ? " Redrawing your résumés…" : " Saving…");
  try {
    const res = await put("/api/profile/format", { format: F.pick, redraw });
    F.data = res; F.pick = res.default;
    renderFormats(res);
    S.details.clear();                  // résumé panels show the new format
  } catch (e) { toast(e.message, true); renderFormats(); }
});

$("#profile-impact-link").addEventListener("click", () => showTab("impact"));
document.addEventListener("keydown", (e) => {
  if (S.tab === "profile" && (e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveProfile(); }
});
window.addEventListener("beforeunload", (e) => { if (profileDirty() || aiDirty()) e.preventDefault(); });

// ---- the AI: which provider and models do the work (searches.yaml's models: section, the key in the profile's .env)
const AI = { data: null, base: "", models: [] };
const aForm = $("#ai-form"), aField = (n) => aForm.elements.namedItem(n);
const AI_STAGES = [["triage", "Signal score", "one short call per job"],
                   ["analysis", "Analysis", "reading the posting, matching your evidence"],
                   ["writer", "Writing", "the résumé drafts and the merge"],
                   ["chat", "Résumé chat", "answering you and drafting the edits"],
                   ["rescore", "Live scores", "re-rating each résumé edit in the chat"]];
const AI_NOTES = {
  "claude-code": "Uses your Claude Pro or Max plan through Claude Code, so there's nothing to pay per call. Sign in once with the button below.",
  api: "Pays per call with a Claude API key (console.anthropic.com).",
  openai: "Pays per call with an OpenAI API key. A ChatGPT subscription doesn't include API use.",
  gemini: "Uses a Google AI Studio key, paid per call. Check Google's page for any free allowance.",
  kimi: "Moonshot AI's Kimi models, paid per call with a key from the international Kimi platform (a key from the China platform, moonshot.cn, won't work here).",
  openrouter: "One key for models from many companies, paid per call; a few models are free.",
  ollama: "Free, on this Mac. Install Ollama, download a model, and give it a large context window (OLLAMA_CONTEXT_LENGTH=65536): each step reads your whole impact record and every résumé. Small models give weak results.",
  lmstudio: "Free, on this Mac. Start LM Studio's server and load a model with a context length of 64k or more: each step reads your whole impact record and every résumé. Small models give weak results.",
  "openai-compatible": "Any server with an OpenAI-style chat API (vLLM, LiteLLM, Together, Groq and others).",
};
const isClaude = (b) => b === "claude-code" || b === "api";
const aiProvider = (b) => AI.data?.providers.find((p) => p.id === b);

function aiState() {
  const out = { backend: aField("backend").value, base_url: aField("base_url").value.trim(), stages: {} };
  for (const [s] of AI_STAGES) out.stages[s] = { model: $(`#ai-model-${s}`).value.trim(), effort: $(`#ai-effort-${s}`).value };
  return out;
}
const aiDirty = () => !!AI.data && (JSON.stringify(aiState()) !== AI.base || !!aField("key").value.trim());

async function loadAI() {
  loadKeys();
  if (AI.data && aiDirty()) return;
  try { applyAI(await api("/api/ai")); } catch (e) { toast(e.message, true); }
}
async function loadKeys(d) {
  try { d = d || await api("/api/keys"); } catch (e) { return; }
  $("#keys").replaceChildren(...d.keys.map((k) => {
    const box = h("input", { type: "password", autocomplete: "new-password", spellcheck: "false",
                             placeholder: k.set ? "•••••••• (saved)" : "paste the key", "aria-label": `${k.label} key` });
    const save = h("button", { type: "button", class: "ghost", disabled: true }, "Save");
    box.addEventListener("input", () => { save.disabled = !box.value.trim(); });
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        loadKeys(await put("/api/keys", { name: k.name, value: box.value.trim() }));
        toast(`${k.label} key saved.${k.restart ? " Quit and reopen the app to start using it." : ""}`);
      } catch (e) { toast(e.message, true); save.disabled = false; }
    });
    return h("li", {}, h("div", { class: "what" }, k.label, " ", extLink(k.url, "get one"), h("span", { class: "muted" }, k.for)), box, save);
  }));
}
function applyAI(d) {
  AI.data = d;
  aField("backend").replaceChildren(...d.providers.map((p) => h("option", { value: p.id }, p.label + (p.key_set ? " · key saved" : ""))));
  aField("backend").value = d.backend;
  aField("base_url").value = d.base_url;
  aField("key").value = "";
  $("#ai-stages").replaceChildren(...AI_STAGES.flatMap(([s, name, what]) => [
    h("div", { class: "ai-step" }, name, h("span", { class: "muted" }, what)),
    h("div", { class: "ai-model" },
      h("select", { id: `ai-pick-${s}`, class: "hidden", "aria-label": `${name} model` }),
      h("input", { id: `ai-model-${s}`, value: d.stages[s].model, placeholder: "model name", spellcheck: "false", "aria-label": `${name} model name` })),
    h("select", { id: `ai-effort-${s}`, "aria-label": `${name} effort` },
      ...["low", "medium", "high"].map((e) => h("option", { value: e, selected: d.stages[s].effort === e }, e))),
  ]));
  for (const [s] of AI_STAGES) $(`#ai-pick-${s}`).addEventListener("change", () => modelPicked(s));
  AI.typing = new Set();
  AI.base = JSON.stringify(aiState());
  formError(aForm);
  aiProviderShown(false);
}
// Show the key and address fields the chosen provider needs, and offer its models.
function aiProviderShown(changed) {
  const b = aField("backend").value, p = aiProvider(b) || {};
  $("#ai-key-row").classList.toggle("hidden", !p.key_env);
  keyHint();
  aField("key").placeholder = p.key_set ? "••••••••" : p.local || b === "openai-compatible" ? "if the server needs one" : "paste your key";
  $("#ai-url-row").classList.toggle("hidden", !(b === "openai-compatible" || p.local));
  aField("base_url").placeholder = p.base_url || "http://localhost:8000/v1";
  $("#ai-note").replaceChildren(AI_NOTES[b] || "", p.signup ? " " : "", p.signup ? extLink(p.signup, "Get started") : "");
  $("#ai-claude").classList.toggle("hidden", b !== "claude-code");
  if (b === "claude-code") claudeStatus();
  if (changed) {
    const saved = AI.data.saved?.[b];                 // this AI was used before: put back what was picked for it
    const wasClaude = isClaude(AI.lastBackend ?? AI.data.backend);
    const fresh = isClaude(b) !== wasClaude || !isClaude(b);   // a different family of models: the old names won't work
    for (const [s] of AI_STAGES) {
      if (saved?.stages[s]) {
        $(`#ai-model-${s}`).value = saved.stages[s].model;
        $(`#ai-effort-${s}`).value = saved.stages[s].effort || "medium";
      } else if (fresh) {                             // a step it has no pick for (saved before the step existed)
        const small = (s === "rescore" || s === "chat") && AI.data.claude_models.find((m) => m.includes("sonnet"));
        $(`#ai-model-${s}`).value = isClaude(b) ? small || AI.data.claude_models[0]
          : p.defaults?.[s] || (s === "chat" ? aiState().stages.writer.model : "");   // the chat: else the writing model
      }
    }
    if (saved) aField("base_url").value = saved.base_url;
  }
  AI.lastBackend = b;
  if (changed) AI.typing.clear();
  setModels(isClaude(b) ? AI.data.claude_models : []);
  $("#ai-state").textContent = "";
  if (!isClaude(b) && (p.key_set || p.local)) checkAI(true);
  aiChanged();
}
// Whether the provider's key is saved, or typed here and not saved yet.
function keyHint() {
  const p = aiProvider(aField("backend").value) || {}, typed = !!aField("key").value.trim(), hint = $("#ai-key-hint");
  hint.textContent = !p.key_env ? "" : typed ? "not saved yet: click Save AI settings"
    : p.key_set ? `✓ saved as ${p.key_env}; paste a new one to replace it` : "none saved yet";
  hint.classList.toggle("warn", !!p.key_env && typed);
  hint.classList.toggle("ok", !!p.key_env && !typed && !!p.key_set);
}
// Each step picks from a menu of the provider's models once they're known; "Other model…" types a name instead.
const OTHER_MODEL = "\u0000other";
function setModels(list) {
  AI.models = list;
  for (const [s] of AI_STAGES) syncPick(s);
}
function syncPick(s) {
  const box = $(`#ai-model-${s}`), pick = $(`#ai-pick-${s}`), list = AI.models || [];
  const menu = list.length > 0 && !AI.typing.has(s);
  pick.classList.toggle("hidden", !menu);
  box.classList.toggle("hidden", menu);
  if (!menu) return;
  const cur = box.value.trim();
  pick.replaceChildren(
    ...(cur ? [] : [h("option", { value: "", disabled: true }, "Choose a model")]),
    ...(cur && !list.includes(cur) ? [h("option", { value: cur }, `${cur} (not listed)`)] : []),
    ...list.map((m) => h("option", { value: m }, m)),
    h("option", { value: OTHER_MODEL }, "Other model…"));
  pick.value = cur;
}
function modelPicked(s) {
  const box = $(`#ai-model-${s}`), pick = $(`#ai-pick-${s}`);
  if (pick.value === OTHER_MODEL) {
    AI.typing.add(s);
    syncPick(s);
    box.focus();
    box.select();
  } else box.value = pick.value;
  aiChanged();
}
async function checkAI(quiet = false) {
  const s = aiState(), state = $("#ai-state");
  state.replaceChildren(spinner(), " Checking…");
  try {
    const typed = !!aField("key").value.trim();
    const d = await post("/api/ai/models", { backend: s.backend, base_url: s.base_url, key: aField("key").value.trim() });
    for (const [st] of AI_STAGES) {                  // fill empty model boxes: the provider's pick if listed, else the first
      const box = $(`#ai-model-${st}`), pick = aiProvider(s.backend)?.defaults?.[st];
      if (!box.value && d.models.length) box.value = d.models.includes(pick) ? pick : d.models[0];
    }
    setModels(d.models);
    if (s.backend === "claude-code") claudeStatus();
    state.textContent = isClaude(s.backend) ? "" : typed
      ? `The key works: ${plural(d.models.length, "model")} available. Pick one for each step, then Save AI settings to keep it.`
      : `Connected: ${plural(d.models.length, "model")} available. Pick one for each step.`;
    aiChanged();
  } catch (e) { state.textContent = quiet ? "" : e.message; if (!quiet) toast(e.message, true); }
}
function aiChanged() {
  const dirty = aiDirty();
  $("#ai-save").disabled = !dirty;
  if (dirty && !$("#ai-state").textContent) $("#ai-state").textContent = "Unsaved changes";
}
// Whether Claude Code is here and signed in: a green check when it's ready, else what's missing.
async function claudeStatus() {
  const st = $("#ai-claude-state"), btn = $("#ai-claude-login");
  st.classList.remove("ok", "warn");
  st.replaceChildren(spinner(), " Checking…");
  try {
    const d = await api("/api/ai/claude");
    btn.disabled = !d.found;
    btn.textContent = d.signed_in ? "Sign in again…" : "Sign in to Claude…";
    st.classList.toggle("ok", !!d.signed_in);
    st.classList.toggle("warn", !d.found || d.signed_in === false);
    st.replaceChildren(!d.found
      ? h("span", {}, "Claude Code isn't installed. ", extLink("https://claude.ai/download", "Install the Claude app"), ", then reopen this tab.")
      : d.signed_in ? `✓ Ready: signed in${d.account ? ` as ${d.account}` : ""}, Claude Code ${d.version}.`
      : d.signed_in === false ? "Not signed in, or the sign-in expired. Click Sign in to Claude…, then type /login in Terminal."
      : "Opens Terminal: type /login there and sign in with your Claude account.");
  } catch (e) { st.textContent = e.message; }
}
$("#ai-claude-login").addEventListener("click", async () => {
  try {
    await post("/api/ai/claude-login");
    toast("Terminal is open: type /login, sign in, then close it.");
    window.addEventListener("focus", claudeStatus, { once: true });   // back from Terminal: show whether it worked
  } catch (e) { toast(e.message, true); }
});
aField("backend").addEventListener("change", () => aiProviderShown(true));
aForm.addEventListener("input", (e) => {
  if (e.target.name === "key") keyHint();
  if (e.target.name !== "backend" && !e.target.closest("#keys")) aiChanged();
});
$("#ai-check").addEventListener("click", () => checkAI());
aForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!aiDirty()) return;
  const btn = $("#ai-save");
  btn.disabled = true;
  try {
    const key = aField("key").value.trim(), label = aiProvider(aiState().backend)?.label;
    applyAI(await put("/api/ai", { ...aiState(), key }));
    CHAT_MODELS = null;                                // the chat's model menu offers the new set-up
    loadSummary();
    toast(`${key ? `${label} key saved. ` : ""}AI settings saved. The next score or résumé uses them; runs already going finish on the AI they started with.`);
  } catch (err) { formError(aForm, err.message); btn.disabled = false; }
});

// ---- markdown documents: the impact record and interview prep ---------------------------------------
// Each is saved a moment after you stop typing, on ⌘S, and when you switch tabs. Each save sends the
// version it started from; if the file was changed elsewhere meanwhile (Claude, another editor), the
// server refuses and the banner offers to load that version or save over it. While a document is open
// and has no unsaved edits, changes made elsewhere are loaded as they happen.
// Views: Formatted (a Typora-style editor, static/vendor/editor.js, loaded when first shown), Markdown
// (the text), and Side by side (the text and a preview). The text box always holds the current markdown;
// the formatted editor writes into it as you type.
const AUTOSAVE_MS = 1200;

function loadScript(src) {
  return new Promise((ok, fail) => document.head.append(h("script", { src, onload: ok, onerror: () => fail(new Error("couldn't load the editor")) })));
}
// The outline: every # and ## heading outside code blocks, with its line number.
function headings(text) {
  const out = [];
  let fenced = false;
  text.split("\n").forEach((line, i) => {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const m = !fenced && /^(#{1,2})\s+(.+?)\s*#*\s*$/.exec(line);
    if (m) out.push({ level: m[1].length, line: i, label: m[2].replace(/\\(.)/g, "$1").replace(/[*_`]/g, "") });
  });
  return out;
}

function docEditor({ key, url, onSaved = () => {} }) {
  const el = (part) => $(`#${key}-${part}`);
  const text = el("text"), preview = el("preview");
  const D = { loaded: false, version: null, saved: "", updated: null, saving: null, saves: 0, timer: null, failed: "",
              conflict: null, previewed: null, previewTimer: null, mirror: null, editor: null, opening: null };
  const dirty = () => D.loaded && text.value !== D.saved;
  const view = () => S.docViews[key];

  async function load() {
    if (D.saving) return;
    const saves = D.saves;
    let d;
    try { d = await api(url); }
    catch (e) { if (!D.loaded) setState(`Couldn't open it: ${e.message}`, "bad"); return; }
    if (D.saving || D.saves !== saves) return;    // a save started meanwhile: this reply may be older than it
    // Unsaved edits stay put; if the file moved on underneath them, the next save reports the conflict.
    if (sync()) edited();
    if (!dirty()) apply(d);
    if (view() === "formatted" && !D.editor) await setView("formatted");
  }
  function apply(d) {
    Object.assign(D, { loaded: true, version: d.version, saved: d.markdown, updated: d.updated, conflict: null, failed: "" });
    if (text.value !== d.markdown) {
      const top = text.scrollTop;
      text.value = d.markdown;
      text.scrollTop = top;
    }
    if (D.editor && D.editor.getMarkdown() !== d.markdown) { D.editor.setMarkdown(d.markdown); D.editor.rebase(d.markdown); }
    el("conflict").classList.add("hidden");
    onSaved(d);
    changed();
  }

  async function save(force = false) {
    clearTimeout(D.timer);
    sync();
    if (!dirty() || (D.conflict && !force)) return;
    if (D.saving) { await D.saving; return save(force); }
    const md = text.value;
    D.failed = "";
    D.saves++;
    setState("Saving…");
    D.saving = (async () => {
      try {
        const res = await request(url, { method: "PUT", body: JSON.stringify({ markdown: md, version: D.version, force }) });
        const d = await res.json();
        if (res.status === 409) {
          D.conflict = d.current;
          el("conflict").classList.remove("hidden");
          return;
        }
        if (!res.ok) throw new Error(d.detail || res.statusText);
        Object.assign(D, { version: d.version, saved: md, updated: d.updated, conflict: null });
        el("conflict").classList.add("hidden");
        onSaved(d);
      } catch (e) {
        D.failed = e instanceof TypeError ? "the app's server isn't running" : e.message;
      } finally { D.saving = null; }
    })();
    await D.saving;
    state();
    if (dirty() && !D.conflict && !D.failed) D.timer = setTimeout(save, AUTOSAVE_MS);   // typed while it saved
  }

  function state() {
    el("dirty").classList.toggle("hidden", !dirty());
    if (D.saving) return;
    if (D.conflict) return setState("Not saved: changed elsewhere", "bad");
    if (D.failed) return setState(`Not saved: ${D.failed}`, "bad");
    if (dirty()) return setState("Unsaved changes");
    const when = D.updated && new Date(D.updated * 1000);
    setState(!when ? "" : `Saved ${when.toDateString() === new Date().toDateString()
      ? when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : fmtDate(when)}`, "good");
  }
  function setState(msg, tone = "") {
    const s = el("state");
    s.textContent = msg;
    s.className = `save-state ${tone}`;
  }

  function changed() {
    state();
    clearTimeout(D.previewTimer);
    D.previewTimer = setTimeout(() => { renderOutline(); renderPreview(); }, 350);
  }
  function edited() {
    changed();
    clearTimeout(D.timer);
    if (!D.conflict) D.timer = setTimeout(save, AUTOSAVE_MS);
  }
  text.addEventListener("input", edited);
  text.addEventListener("blur", () => save());

  // The formatted view. Blocks you didn't touch keep their exact markdown; only edited ones are rewritten.
  function sync() {
    if (view() !== "formatted" || !D.editor) return false;
    const md = D.editor.getMarkdown();
    if (md === text.value) return false;
    text.value = md;
    return true;
  }
  function openEditor() {
    D.opening = D.opening || (async () => {
      try {
        if (!window.MarkdownEditor) await loadScript($("meta[name=editor-src]").content);
        D.editor = await MarkdownEditor.create(el("editor"), text.value, { onChange: () => { if (sync()) edited(); } });
        el("editor").addEventListener("focusout", () => save());
      } catch (e) {
        D.opening = null;
        setState(`Formatted view unavailable: ${e.message}`, "bad");
      }
    })();
    return D.opening;
  }

  async function renderPreview() {
    if (view() !== "split" || D.previewed === text.value) return;
    const md = text.value;
    try {
      const { html } = await post(`${url}/preview`, { markdown: md });
      if (text.value !== md) return;      // a newer render is on its way
      preview.innerHTML = html;
      D.previewed = md;
    } catch (e) { /* the editor still works; the state line reports a server that's gone */ }
  }

  function renderOutline() {
    el("outline").replaceChildren(...headings(text.value).map((x, n) =>
      h("button", { class: `l${x.level}`, title: x.label, onclick: () => jumpTo(x, n) }, x.label)));
  }
  function jumpTo(x, n) {
    if (view() === "formatted") {
      const box = el("wysiwyg"), hd = $$("h1, h2", box)[n];
      if (hd) box.scrollTop = hd.offsetTop - box.offsetTop - 12;
      return;
    }
    const pos = text.value.split("\n").slice(0, x.line).reduce((a, l) => a + l.length + 1, 0);
    text.scrollTop = Math.max(0, caretTop(pos) - 12);
    text.focus({ preventScroll: true });
    text.setSelectionRange(pos, pos);
    if (view() === "split") {
      const hd = $$("h1, h2", preview)[n];
      if (hd) preview.scrollTop = hd.offsetTop - preview.offsetTop - 12;
    }
  }
  // Where character `pos` sits in the textarea, measured on a hidden copy laid out the same way.
  function caretTop(pos) {
    const m = D.mirror || (D.mirror = document.body.appendChild(h("div", { class: "doc-mirror", "aria-hidden": "true" })));
    const cs = getComputedStyle(text);
    for (const k of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "tabSize",
                     "paddingTop", "paddingLeft", "paddingRight"]) m.style[k] = cs[k];
    m.style.width = `${text.clientWidth}px`;
    m.textContent = text.value.slice(0, pos);
    const mark = m.appendChild(h("span", {}, "​"));
    return mark.offsetTop - parseFloat(cs.paddingTop);
  }

  async function setView(v) {
    sync();                      // leaving the formatted view: the text box gets its latest
    S.docViews[key] = v;
    savePrefs();
    $$(`#${key}-view button`).forEach((b) => b.classList.toggle("active", b.dataset.view === v));
    el("body").dataset.view = v;
    renderPreview();
    if (v === "formatted" && D.loaded) {
      await openEditor();
      // Edited as text meanwhile: show that, keeping the text exactly as typed.
      if (D.editor && D.editor.getMarkdown() !== text.value) { D.editor.setMarkdown(text.value); D.editor.rebase(text.value); }
    }
  }
  $$(`#${key}-view button`).forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  el("save").addEventListener("click", () => save());
  el("conflict").addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (act === "load") { apply(D.conflict); toast("Loaded the version on disk."); }
    else if (act === "force") save(true);
  });

  // A change made on the server (a confirmed fact): send unsaved edits first, then post with the version saved.
  async function amend(path, body, jump = /^confirmed facts$/i) {
    if (!D.loaded) await load();
    await save();
    if (D.conflict) throw new Error("Sort out the changed-elsewhere banner first.");
    if (D.failed) throw new Error(`Couldn't save your edits first: ${D.failed}`);
    const res = await request(`${url}${path}`, { method: "POST", body: JSON.stringify({ ...body, version: D.version }) });
    const d = await res.json();
    if (res.status === 409) {
      D.conflict = d.current;
      el("conflict").classList.remove("hidden");
      state();
      throw new Error(d.detail);
    }
    if (!res.ok) throw new Error(d.detail || res.statusText);
    apply(d);
    const n = headings(d.markdown).findIndex((x) => x.level === 1 && jump.test(x.label));
    if (n >= 0) requestAnimationFrame(() => jumpTo(headings(d.markdown)[n], n));
    return d;
  }

  return { load, save, setView, amend, dirty: () => { sync(); return dirty(); },
           toggleView: () => setView(view() === "formatted" ? "edit" : "formatted") };
}

const DOCS = {
  impact: docEditor({ key: "impact", url: "/api/impact-record",
                      onSaved: (d) => { if (S.summary?.impact_record) S.summary.impact_record.updated = d.updated; } }),
  prep: docEditor({ key: "prep", url: "/api/interview-prep",
                    onSaved: (d) => { Object.assign($("#prep-path"), { textContent: d.path.split("/").slice(-2).join("/"), title: d.path }); } }),
};
// Confirmed facts: answers to a fit report's follow-up questions, added to the end of the impact record.
const factBox = $("#fact-box"), factOpen = $("#fact-open");
function showFactBox(open) {
  factBox.classList.toggle("hidden", !open);
  factOpen.setAttribute("aria-expanded", String(open));
  if (open) $("#fact-text").focus();
}
factOpen.addEventListener("click", () => showFactBox(factBox.classList.contains("hidden")));
$("#fact-cancel").addEventListener("click", () => showFactBox(false));
factBox.addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = $("#fact-text"), btn = $("button[type=submit]", factBox);
  if (!t.value.trim()) return t.focus();
  btn.disabled = true;
  try {
    await DOCS.impact.amend("/facts", { text: t.value });
    t.value = "";
    showFactBox(false);
    toast("Added to Confirmed facts at the end of the record.");
  } catch (err) { toast(err.message, true); }
  finally { btn.disabled = false; }
});

document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || !(S.tab in DOCS)) return;
  if (e.key === "s") { e.preventDefault(); DOCS[S.tab].save(); }
  else if (e.key === "/") { e.preventDefault(); DOCS[S.tab].toggleView(); }
});
window.addEventListener("beforeunload", (e) => {
  for (const d of Object.values(DOCS)) if (d.dirty()) { d.save(); e.preventDefault(); e.returnValue = ""; }
});

// ---- keyboard ---------------------------------------------------------------------------------------
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key === "\\" && S.tab in SCOPES && !document.querySelector("dialog[open]")) { e.preventDefault(); foldAll(); }
});
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]")) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) {
    if (e.key === "Escape" && e.target.id === "job-filter") e.target.blur();
    return;
  }
  if (e.key === "/") { e.preventDefault(); showTab(S.scope); if (S.fold.filters) setFold("filters", false); $("#job-filter").focus(); return; }
  if (!(S.tab in SCOPES)) return;
  const j = S.byId.get(S.selected);
  if (e.key === "Escape") S.refOpen === j?.id ? foldReferral() : closeDrawer();
  else if (e.key === "s" && j) toggleStar(j);
  else if (e.key === "r" && j) { e.preventDefault(); if (!S.drawer) selectJob(j.id, true); openReferral(j); }
  else if ((e.key === "j" || e.key === "k") && !boardMode() && S.visible.some((x) => !collapsed(x))) {
    const shown = S.visible.filter((x) => !collapsed(x));
    const i = shown.indexOf(j);
    const next = shown[Math.max(0, Math.min(shown.length - 1, i + (e.key === "j" ? 1 : -1)))];
    selectJob(next.id);
    next._el?.scrollIntoView({ block: "nearest" });
  }
});

// ---- the setup wizard (onboarding.py): your details, your LinkedIn, then an interview about each past job that
// fills in the impact record. Every step can be skipped. It opens by itself on first use (no profile saved yet,
// never finished or skipped); Profile › Setup opens it again, where you left off.
const SETUP_STEPS = [["welcome", "Welcome"], ["about", "About you"], ["ai", "Your AI"], ["linkedin", "LinkedIn"],
                     ["interview", "Interviews"], ["done", "Done"]];
const WIZ = { step: 0, data: null, imported: null, importBox: null, role: null, extra: [] };
const setupDialog = $("#setup-dialog");
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false && k !== ""));
const setupBody = $("#setup-body"), setupFootEl = $("#setup-foot");

async function openSetup(step = WIZ.step) {
  try { WIZ.data = await api("/api/setup"); } catch (e) { toast(e.message, true); return; }
  if (!setupDialog.open) setupDialog.showModal();
  setupStep(step);
}
function setupStep(n) {
  WIZ.step = Math.max(0, Math.min(SETUP_STEPS.length - 1, n));
  fill($("#setup-steps"), ...SETUP_STEPS.map(([, label], i) => h("li", {
    class: i < WIZ.step ? "past" : i === WIZ.step ? "now" : "", "aria-current": i === WIZ.step ? "step" : null },
    h("button", { type: "button", disabled: i === WIZ.step, onclick: () => setupStep(i) },
      h("span", { class: "n" }, i < WIZ.step ? "✓" : String(i + 1)), h("span", { class: "l" }, label)))));
  $("#setup-steps .now").scrollIntoView({ block: "nearest", inline: "nearest" });
  setupDialog.dataset.step = SETUP_STEPS[WIZ.step][0];
  SETUP_RENDER[SETUP_STEPS[WIZ.step][0]]();
  setupBody.scrollTop = 0;
}
function setupFoot(...right) {
  fill(setupFootEl, 
    WIZ.step < SETUP_STEPS.length - 1 ? h("button", { type: "button", class: "link-btn setup-skip", onclick: skipSetup }, "Skip setup for now") : null,
    h("span", { class: "grow" }),
    WIZ.step > 0 && WIZ.step < SETUP_STEPS.length - 1 ? h("button", { type: "button", class: "ghost", onclick: () => setupStep(WIZ.step - 1) }, "Back") : null,
    ...right);
}
const setupNext = (label = "Next", fn = null) =>
  h("button", { type: "button", class: "primary", onclick: fn || (() => setupStep(WIZ.step + 1)) }, label);
const setupHead = (title, sub) => [h("h2", { id: "setup-title" }, title), sub ? h("p", { class: "muted setup-sub" }, sub) : null];

// Skipping asks once, because the setup is what makes the scores and résumés good.
function skipSetup() {
  if (WIZ.data?.state) { setupDialog.close(); return; }
  fill(setupFootEl, h("span", { class: "setup-confirm" },
    h("b", {}, "Skip the rest? "), "Your résumés are only as good as your impact record. You can carry on any time from Profile › Setup."),
    h("span", { class: "grow" }),
    h("button", { type: "button", class: "ghost", onclick: () => setupStep(WIZ.step) }, "Keep going"),
    h("button", { type: "button", class: "primary", onclick: async () => {
      try { WIZ.data = await put("/api/setup", { state: "skipped" }); } catch (e) { toast(e.message, true); }
      setupDialog.close();
    } }, "Skip"));
}
setupDialog.addEventListener("cancel", (e) => { if (!WIZ.data?.state) { e.preventDefault(); skipSetup(); } });
setupDialog.addEventListener("close", setupCard);
$("#setup-x").addEventListener("click", () => WIZ.data?.state ? setupDialog.close() : skipSetup());
$("#setup-open").addEventListener("click", () => openSetup(WIZ.step > 0 && WIZ.step < SETUP_STEPS.length - 1 ? WIZ.step : 0));
function setupCard() {
  const mid = WIZ.step > 0 && WIZ.step < SETUP_STEPS.length - 1;
  $("#setup-open").textContent = mid ? "Carry on with setup" : WIZ.data?.state === "done" ? "Run setup again" : "Run setup";
}

const SETUP_RENDER = {
  welcome() {
    fill(setupBody, ...setupHead("Welcome to Job Oyster"),
      h("p", { class: "setup-lede" }, "Every score and résumé the app makes is drawn from your ", h("b", {}, "impact record"),
        ": what you did at each job, and what came of it. The fuller it is, the better your résumés. This setup fills it in with you."),
      h("ol", { class: "setup-plan" },
        h("li", {}, h("b", {}, "About you"), h("span", {}, "The name and contact line on every résumé."), h("small", {}, "1 minute")),
        h("li", {}, h("b", {}, "Your LinkedIn"), h("span", {}, "Your jobs and what you wrote about them, from LinkedIn's PDF or data export."), h("small", {}, "2 minutes")),
        h("li", {}, h("b", {}, "Interviews"), h("span", {}, "Your AI asks about each past job, one question at a time, then drafts lines for your record. You check every line."), h("small", {}, "about 5 minutes a job"))),
      h("p", { class: "muted" }, "Each step can be skipped, and Profile › Setup brings you back here."));
    setupFoot(setupNext("Let's start"));
  },

  async about() {
    fill(setupBody, ...setupHead("About you", "The name and contact line on every résumé. You can change them later on Profile."), h("p", {}, spinner()));
    let prof;
    try { prof = await api("/api/profile"); } catch (e) { setupBody.append(h("p", { class: "form-error" }, e.message)); return; }
    const c = prof.candidate, blank = prof.example || !prof.saved;
    const field = (name, label, attrs = {}, hint = "") => h("label", {}, label, hint ? [" ", h("span", { class: "hint" }, hint)] : null,
      h("input", { name, value: blank ? "" : c[name] || "", ...attrs }));
    const form = h("form", { class: "setup-form", autocomplete: "off", novalidate: true },
      h("div", { class: "row2" },
        field("name", "Name", { required: true, placeholder: "Your Name" }),
        h("label", {}, "Pronouns ", h("span", { class: "hint" }, "how the prompts refer to you"),
          h("select", { name: "pronouns" }, ...["he/him", "she/her", "they/them"].map((p) => h("option", { selected: c.pronouns === p }, p)))),
        field("city", "City", { placeholder: "Portland, OR" }, "City, ST"),
        field("phone", "Phone", { type: "tel", placeholder: "+1 555-555-5555" }),
        field("email", "Email", { type: "email", placeholder: "you@example.com" }),
        field("linkedin", "LinkedIn", { placeholder: "linkedin.com/in/your-name", spellcheck: "false" }, "your profile's address")),
      h("p", { class: "muted setup-note" }, "The LinkedIn address goes on your résumés. LinkedIn doesn't let apps read a profile from its address, so the next steps bring your jobs in another way."),
      prof.example ? h("p", { class: "setup-note" }, "The app is showing an example profile. Put in your own details.") : null,
      h("p", { class: "form-error hidden" }));
    fill(setupBody, ...setupHead("About you", "The name and contact line on every résumé. You can change them later on Profile."), form);
    const save = async () => {
      const vals = Object.fromEntries(["name", "pronouns", "city", "phone", "email", "linkedin"].map((k) => [k, form.elements.namedItem(k).value.trim()]));
      if (!vals.name) { formError(form, "Put in your name: it heads every résumé."); form.elements.namedItem("name").focus(); return; }
      if (!blank && Object.entries(vals).every(([k, v]) => v === (c[k] || ""))) { setupStep(WIZ.step + 1); return; }
      try { applyProfile(await put("/api/profile", { ...c, ...vals })); setupStep(WIZ.step + 1); }
      catch (e) { formError(form, e.message); }
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); save(); });
    setupFoot(setupNext("Save and continue", save));
    form.elements.namedItem("name").focus();
  },

  async ai() {
    fill(setupBody, ...setupHead("Your AI", "The interviews run through the AI that scores your jobs and writes your résumés."), h("p", {}, spinner()));
    setupFoot(setupNext());
    let d, claude = null;
    try {
      d = await api("/api/ai");
      if (isClaude(d.backend) && d.backend === "claude-code") claude = await api("/api/ai/claude");
    } catch (e) { setupBody.append(h("p", { class: "form-error" }, e.message)); return; }
    const p = d.providers.find((x) => x.id === d.backend) || { label: d.backend };
    const ready = claude ? !!claude.found && claude.signed_in !== false : p.local || p.key_set;
    const how = claude ? (!claude.found ? "Claude Code isn't installed, so the app can't reach Claude yet."
      : claude.signed_in ? `It runs on your Claude subscription, through Claude Code, signed in${claude.account ? ` as ${claude.account}` : ""}.`
      : claude.signed_in === false ? "It runs on your Claude subscription, through Claude Code, but Claude Code isn't signed in (or the sign-in expired). Sign in first."
      : "It runs on your Claude subscription, through Claude Code. If you haven't signed in to Claude Code yet, do that first.")
      : p.local ? "It runs on this computer." : p.key_set ? "Its key is saved in your profile folder." : "Its key isn't saved yet.";
    const change = h("button", { type: "button", class: "ghost", onclick: () => {
      setupDialog.close();
      showTab("profile")?.then(() => $("#ai-form").scrollIntoView({ behavior: "smooth", block: "start" }));
      toast("Set up the AI, then press Carry on with setup (Profile › Setup).");
    } }, "Change the AI…");
    fill(setupBody, ...setupHead("Your AI", "The interviews run through the AI that scores your jobs and writes your résumés."),
      h("div", { class: `setup-ai ${ready ? "ok" : "warn"}` },
        h("span", { class: "setup-ai-dot", "aria-hidden": "true" }),
        h("div", {}, h("b", {}, p.label, d.stages?.chat?.model ? ` · ${d.stages.chat.model}` : ""), h("p", {}, how),
          h("div", { class: "setup-ai-actions" },
            claude?.found ? h("button", { type: "button", class: "ghost", onclick: async () => {
              try { await post("/api/ai/claude-login"); toast("Terminal is open: type /login, sign in, then close it."); }
              catch (e) { toast(e.message, true); } } }, "Sign in to Claude…") : null,
            claude && !claude.found ? extLink("https://claude.ai/download", "Install the Claude app") : null,
            change))),
      h("p", { class: "muted" }, "Your answers go to this AI only, as your postings and résumés already do. ",
        ready ? "" : "You can import LinkedIn without it, and come back for the interviews once it's set up."));
  },

  linkedin() {
    if (WIZ.imported) return setupImported();
    const file = h("input", { type: "file", accept: ".pdf,.zip,application/pdf,application/zip", hidden: true });
    file.addEventListener("change", () => { const f = file.files[0]; file.value = ""; if (f) setupImport(f); });
    const choose = (label) => h("button", { type: "button", class: "primary", onclick: () => file.click() }, label);
    const drop = h("div", { class: "setup-drop" },
      h("div", { class: "setup-way" },
        h("h3", {}, "Your profile as a PDF ", h("span", { class: "tag" }, "quickest")),
        h("ol", {}, h("li", {}, "Open ", extLink("https://www.linkedin.com/in/me/", "your LinkedIn profile"), "."),
          h("li", {}, "Click ", h("b", {}, "Resources"), " (or ", h("b", {}, "More"), ") under your name, then ", h("b", {}, "Save to PDF"), "."),
          h("li", {}, "Add the PDF it saves (Profile.pdf) here.")),
        choose("Choose the PDF…")),
      h("div", { class: "setup-way" },
        h("h3", {}, "Your data export ", h("span", { class: "tag" }, "most complete")),
        h("ol", {}, h("li", {}, "Open ", extLink("https://www.linkedin.com/mypreferences/d/download-my-data", "Get a copy of your data"), "."),
          h("li", {}, "Pick ", h("b", {}, "Positions"), ", ", h("b", {}, "Profile"), " and ", h("b", {}, "Skills"), " (or everything), then ", h("b", {}, "Request archive"), "."),
          h("li", {}, "LinkedIn emails a link, usually within minutes. Add the ZIP it gives you here.")),
        choose("Choose the ZIP…")),
      file);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) setupImport(f); });
    fill(setupBody, ...setupHead("Bring in your LinkedIn", "Your jobs, dates and what you wrote about each, as lines for your impact record. You pick every line."),
      drop,
      h("p", { class: "muted setup-note" }, "Why not just your profile's address? LinkedIn shows apps only a sliver of a profile and blocks reading the rest, so one of these exports is the way in. Both stay on this computer. You can drop the file onto this box."));
    setupFoot(setupNext("Skip this step"));
  },

  async interview() {
    fill(setupBody, ...setupHead("Interviews", "Pick a job. Your AI asks up to six questions about it, then drafts lines for your impact record."), h("p", {}, spinner()));
    setupFoot(setupNext("Finish"));
    try { WIZ.data = await api("/api/setup"); } catch (e) { setupBody.append(h("p", { class: "form-error" }, e.message)); return; }
    const pane = h("div", { class: "setup-chat" });
    const list = h("ul", { class: "setup-roles" });
    const roles = () => [...WIZ.data.roles, ...WIZ.extra.filter((x) => !WIZ.data.roles.some((r) => r.key === x.key))];
    const drawList = () => fill(list, ...roles().map((r) => h("li", {},
      h("button", { type: "button", class: r.key === WIZ.role?.key ? "on" : "", onclick: () => { WIZ.role = r; drawList(); setupInterview(r, pane, drawList); if (matchMedia("(max-width: 720px)").matches) pane.scrollIntoView({ block: "start" }); } },
        h("b", {}, r.title), h("span", {}, r.company), r.dates ? h("small", {}, r.dates) : null,
        r.added ? h("span", { class: "tag setup-added" }, `${plural(r.added, "line")} added`)
          : r.answers ? h("span", { class: "tag" }, `${plural(r.answers, "answer")}`) : null))));
    const company = h("input", { placeholder: "Company", "aria-label": "Company" }), title = h("input", { placeholder: "Job title", "aria-label": "Job title" }),
      dates = h("input", { placeholder: "2019–2022", "aria-label": "Dates" });
    const addForm = h("form", { class: "setup-addrole" }, h("b", {}, "Another job"), company, title, dates,
      h("button", { class: "ghost small" }, "Add"));
    addForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!company.value.trim() || !title.value.trim()) { (company.value.trim() ? title : company).focus(); return; }
      const r = { company: company.value.trim(), title: title.value.trim(), dates: dates.value.trim(), notes: "", answers: 0, added: 0,
                  key: (company.value + " " + title.value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80) };
      WIZ.extra.push(r); addForm.reset(); WIZ.role = r; drawList(); setupInterview(r, pane, drawList);
    });
    fill(setupBody, ...setupHead("Interviews", "Pick a job. Your AI asks up to six questions about it, then drafts lines for your impact record."),
      h("div", { class: "setup-iv" }, h("div", { class: "setup-side" }, list, addForm), pane));
    drawList();
    const r = WIZ.role && roles().find((x) => x.key === WIZ.role.key);
    if (r) setupInterview(r, pane, drawList);
    else fill(pane, h("div", { class: "setup-empty" },
      h("p", {}, h("b", {}, roles().length ? "Pick a job on the left to start." : "Add a job on the left to start.")),
      h("p", { class: "muted" }, "It asks about scope, what you owned, results with numbers, a hard problem and the tools you used. Answer in your own words; rough numbers are fine, and the draft says which figures to check. Nothing goes into your record until you add it.")));
  },

  async done() {
    try { WIZ.data = await put("/api/setup", { state: "done" }); } catch (e) { toast(e.message, true); }
    const li = WIZ.data?.linkedin, done = (WIZ.data?.roles || []).filter((r) => r.added);
    const added = done.reduce((n, r) => n + r.added, 0);
    fill(setupBody, h("div", { class: "setup-done" },
      h("span", { class: "setup-done-mark", "aria-hidden": "true" }, "✓"),
      ...setupHead("You're set up", "From here, search for jobs: each one is scored against your impact record, and résumés are written from it."),
      h("ul", { class: "setup-sum" },
        h("li", {}, li ? `LinkedIn: ${plural(li.roles, "job")} read from ${li.file}.` : "LinkedIn: skipped. Add it any time from here."),
        h("li", {}, added ? `Interviews: ${plural(added, "line")} added for ${plural(done.length, "job")}.` : "Interviews: none yet. They're here whenever you have ten minutes.")),
      h("p", { class: "muted" }, "Profile › Setup brings you back to any step.")));
    fill(setupFootEl, h("span", { class: "grow" }),
      h("button", { type: "button", class: "ghost", onclick: () => { setupDialog.close(); showTab("impact"); } }, "Open my impact record"),
      h("button", { type: "button", class: "primary", onclick: () => { setupDialog.close(); showTab("find"); } }, "Search jobs"));
  },
};

async function setupImport(f) {
  fill(setupBody, ...setupHead("Bring in your LinkedIn"), h("div", { class: "import-reading" },
    h("span", { class: "import-blob", "aria-hidden": "true" }), h("p", {}, h("b", {}, `Reading ${f.name}…`)),
    h("p", { class: "muted" }, "A few seconds for LinkedIn's PDF or ZIP; about a minute if your AI has to sort a different PDF.")));
  try {
    WIZ.imported = await api("/api/setup/linkedin", { method: "POST", body: f,
      headers: { "Content-Type": f.type || "application/octet-stream", "X-Filename": encodeURIComponent(f.name) } });
  } catch (e) {
    fill(setupBody, ...setupHead("Bring in your LinkedIn"), h("p", { class: "form-error" }, e.message),
      h("p", {}, h("button", { type: "button", class: "ghost", onclick: () => SETUP_RENDER.linkedin() }, "Try another file")));
    return;
  }
  WIZ.importBox = null;
  // Fill blanks on the profile from the export (never over what you typed).
  try {
    const prof = await api("/api/profile");
    const blanks = Object.entries(WIZ.imported.fields).filter(([k]) => !prof.example && prof.saved && !prof.candidate[k]);
    if (blanks.length) {
      applyProfile(await put("/api/profile", { ...prof.candidate, ...Object.fromEntries(blanks) }));
      WIZ.imported.filled = blanks.map(([k]) => k);
    }
  } catch { /* the profile stays as it was */ }
  setupImported();
}
function setupImported() {
  const d = WIZ.imported;
  if (!WIZ.importBox) { WIZ.importBox = h("div", { class: "setup-sugg" }); renderSuggestions({ import_id: d.id }, WIZ.importBox); }
  fill(setupBody, ...setupHead("Bring in your LinkedIn", "Tick the lines to keep. Each role's Add puts them in your impact record."),
    h("p", { class: "setup-got" }, h("b", {}, `${plural(d.roles.length, "job")} and ${plural(d.lines, "line")} `), `from ${d.filename}`,
      d.filled?.length ? `; your profile's blank ${d.filled.join(", ")} filled in from it` : "", ". ",
      h("button", { type: "button", class: "link-btn", onclick: () => { WIZ.imported = null; SETUP_RENDER.linkedin(); } }, "Use another file")),
    d.heuristic ? h("p", { class: "setup-note" }, `Your AI couldn't sort this file (${d.heuristic}), so it was sorted roughly: check the roles.`) : null,
    d.roles.length && !d.lines ? h("p", { class: "setup-note" }, "LinkedIn had no descriptions under your jobs, so there's nothing to add yet. The interviews are the way to fill them in.") : null,
    WIZ.importBox);
  setupFoot(setupNext("Continue to interviews"));
}

// One job's interview: the questions and your answers, then the drafted lines to check and add.
async function setupInterview(role, pane, redraw) {
  const say = (who, text) => h("div", { class: `setup-msg ${who}` }, h("p", {}, text));
  const thinking = (what) => h("div", { class: "setup-msg ai thinking" }, h("p", {}, spinner(), " ", what));
  const head = h("div", { class: "setup-chat-head" }, h("b", {}, role.title), h("span", {}, `· ${role.company}`),
    role.dates ? h("span", { class: "muted" }, `· ${role.dates}`) : null, h("span", { class: "grow" }),
    h("button", { type: "button", class: "link-btn", onclick: () => run({ restart: true }) }, "Start over"));
  const thread = h("div", { class: "setup-thread", "aria-live": "polite" });
  const box = h("div", { class: "setup-answer" });
  fill(pane, head, thread, box);
  let iv = null;
  const answered = () => (iv?.turns || []).filter((t) => t.a.trim()).length;
  function draw(extra = null) {
    fill(thread, ...(iv?.turns || []).flatMap((t) => [say("ai", t.q), t.a ? say("you", t.a) : null]), extra);
    thread.scrollTop = thread.scrollHeight;
    const last = iv?.turns.at(-1);
    if (!last || last.a || iv.done) { fill(box); return; }
    const ta = h("textarea", { rows: 3, placeholder: "Your answer, in your own words. Rough numbers are fine.", "aria-label": "Your answer" });
    const send = h("button", { type: "button", class: "primary", onclick: () => ta.value.trim() && run({ answer: ta.value.trim() }) }, "Send");
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send.click(); } });
    fill(box, ta, h("div", { class: "setup-answer-bar" },
      h("span", { class: "muted" }, `Question ${iv.turns.length} of up to 6 · ⌘↩ sends`), h("span", { class: "grow" }),
      answered() ? h("button", { type: "button", class: "ghost", onclick: () => run({ finish: true }) }, "That's enough: draft my lines") : null, send));
    ta.focus();
  }
  async function run(extra) {
    fill(box);
    draw(thinking(extra.finish ? "Wrapping up…" : "Your AI is thinking of a question…"));
    if (extra.answer) { iv.turns.at(-1).a = extra.answer; draw(thinking("Your AI is thinking of the next question…")); }
    try { iv = await post("/api/setup/interview", { role, ...extra }); }
    catch (e) { draw(h("p", { class: "form-error" }, e.message)); if (iv && extra.answer) fill(box, h("button", { type: "button", class: "ghost", onclick: () => run({}) }, "Ask again")); return; }
    role.key = iv.key; role.answers = answered(); WIZ.role = role; redraw();
    if (iv.done) draftLines(); else draw();
  }
  async function draftLines() {
    iv.done = true;
    draw(thinking("Drafting lines for your impact record from your answers…"));
    let d;
    try { d = await post(`/api/setup/interview/${encodeURIComponent(iv.key)}/draft`); }
    catch (e) { draw(h("p", { class: "form-error" }, e.message)); fill(box, h("button", { type: "button", class: "ghost", onclick: draftLines }, "Try again")); return; }
    if (!d.lines.length) { draw(say("ai", "Your answers didn't add anything your record doesn't already say about this job. Try another job, or Start over.")); return; }
    const rows = d.lines.map((l) => {
      const cb = h("input", { type: "checkbox", checked: l.ok }), text = h("input", { type: "text", value: l.text, "aria-label": "Line" });
      cb.addEventListener("change", count);
      return { cb, text, el: h("li", { class: `sugg-line ${l.ok ? "new" : "detail"}` }, h("label", { class: "check" }, cb, text), l.why ? h("small", {}, l.why) : null) };
    });
    const add = h("button", { type: "button", class: "primary" });
    function count() { const n = rows.filter((r) => r.cb.checked).length; add.disabled = !n; add.textContent = `Add ${plural(n, "line")} to my impact record`; }
    count();
    add.addEventListener("click", async () => {
      const lines = rows.filter((r) => r.cb.checked).map((r) => r.text.value.trim()).filter(Boolean);
      add.disabled = true;
      try {
        await DOCS.impact.amend("/interview-apply", { key: iv.key, lines }, /^from your interviews$/i);
        rows.forEach((r) => { if (r.cb.checked) { r.cb.checked = false; r.cb.disabled = r.text.disabled = true; r.el.classList.add("added"); } });
        role.added = (role.added || 0) + lines.length; redraw();
        add.replaceWith(h("span", { class: "sugg-done" }, `Added ${plural(lines.length, "line")} ✓`));
      } catch (e) { toast(e.message, true); count(); }
    });
    draw(h("div", { class: "setup-draft" }, h("p", {}, h("b", {}, "Lines for your impact record. "),
      h("span", { class: "muted" }, "Edit any of them; untick what's wrong. A figure that isn't in your answers is unticked until you check it.")),
      h("ul", { class: "sugg-lines" }, ...rows.map((r) => r.el)), h("div", { class: "actions" }, h("span", { class: "grow" }), add)));
  }
  fill(thread, thinking("Loading…"));
  if (role.answers || role.added) {
    try { iv = { ...(await api(`/api/setup/interview/${encodeURIComponent(role.key)}`)), key: role.key }; }
    catch { iv = null; }
  }
  if (!iv || !iv.turns?.length) return run({});
  if (iv.turns.at(-1).a && answered()) { iv.done = true; draw(); fill(box, h("div", { class: "setup-answer-bar" },
    h("span", { class: "muted" }, `${plural(answered(), "answer")} so far.`), h("span", { class: "grow" }),
    h("button", { type: "button", class: "ghost", onclick: () => { iv.done = false; run({}); } }, "Keep answering"),
    h("button", { type: "button", class: "primary", onclick: draftLines }, "Draft my lines"))); return; }
  draw();
}

// ---- boot -------------------------------------------------------------------------------------------
(async function boot() {
  if (!TOKEN) $("#auth-error").classList.remove("hidden");
  $$("[data-icon]").forEach((el) => { el.innerHTML = ICONS[el.dataset.icon]; });
  $("#job-sort").value = S.sort;
  $("#job-group").value = S.group;
  $$("#view-seg button").forEach((x) => x.classList.toggle("active", x.dataset.view === S.view));
  applyLayout();
  applyFolds();
  Object.entries(DOCS).forEach(([k, d]) => d.setView(S.docViews[k]));
  await Promise.all([loadSummary(), loadJobs()]);
  setScope("tracker");
  api("/api/setup").then((d) => { WIZ.data = d; setupCard(); if (d.show) openSetup(0); }).catch(() => {});
  // An open document also picks up changes made elsewhere, e.g. notes Claude adds to interview prep.
  setInterval(() => { if (!document.hidden) { loadSummary(); if (S.tab in DOCS) DOCS[S.tab].load(); } }, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { loadSummary(); loadJobs(); if (S.tab in DOCS) DOCS[S.tab].load(); }
  });
  window.addEventListener("focus", () => { if (S.tab in DOCS) DOCS[S.tab].load(); });
})();
