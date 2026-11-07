"use strict";
/* Signalpost static viewer. No network: the index is inline, each company is data/<orgnr>.js loaded on demand.
   Routes (all state is in the URL, so every view can be shared):
     #/?q=&f=&town=&type=&sector=&sort=   find (search + filters)
     #/c/<org>[/<section>|/source/<claim ids>|/ask/<intent>]   company profile, a section, an open source, an Ask answer
     #/compare/<orgA>/<orgB>              side-by-side comparison */
const IDX = window.SP_INDEX || [];
const META = window.SP_META || {};
const cache = {}, waiting = {};
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const safeUrl = u => /^https?:\/\//i.test(String(u || "")) ? String(u) : null;
const fold = s => String(s ?? "").toLowerCase().replace(/æ/g, "ae").replace(/ø/g, "o").replace(/å/g, "a").normalize("NFD").replace(/[̀-ͯ]/g, "");
const nf = new Intl.NumberFormat("nb-NO", {maximumFractionDigits: 0});
const plural = (n, w, p) => `${nf.format(n)} ${n === 1 ? w : (p || w + "s")}`;
const STATE_TXT = {available: "available", not_available: "not found", not_applicable: "not applicable", ambiguous: "ambiguous", blocked: "blocked", failed: "failed"};
const stateBadge = (s, extra = "") => `<span class="st ${esc(s)}" title="${esc(STATE_HELP[s] || "")}">${esc(STATE_TXT[s] || s || "unknown")}</span>${extra}`;
const STATE_HELP = {
  available: "Found, with a cited source", not_available: "Checked; nothing found (not the same as zero)", not_applicable: "Does not apply to this entity",
  ambiguous: "A candidate was found but could not be tied to this organisation number, so it is not published", blocked: "The source refused automated access",
  failed: "Could not be checked in this run"
};
const FORM = {AS: "Private limited company", ASA: "Public limited company", ENK: "Sole proprietorship", NUF: "Branch of foreign company", BRL: "Housing cooperative",
  ESEK: "Condominium association", STI: "Foundation", FLI: "Association / club", SA: "Cooperative", DA: "Partnership (shared liability)", ANS: "General partnership",
  SAM: "Co-ownership", KS: "Limited partnership", IKS: "Inter-municipal company", BBL: "Housing cooperative association", SPA: "Savings bank", PK: "Pension fund"};
const LABEL = {legal_name: "Legal name", organisation_number: "Organisation number", legal_form: "Legal form", business_address: "Business address", industry: "Industry",
  registered_employees: "Employees (register)", founded_date: "Founded", registry_registration_date: "Registered", stated_activity: "Stated purpose",
  public_brand: "Public brand", self_description: "Self-description (website)", bankrupt: "Bankrupt", under_liquidation: "Under liquidation",
  revenue: "Revenue", operating_result: "Operating result", profit_before_tax: "Profit before tax", annual_result: "Net result", assets: "Total assets",
  equity: "Equity", debt: "Debt", role: "Role", registered_workplace: "Workplace (subunit)", group_structure: "Group", official_website: "Official website",
  social_profile: "Company-owned profile", job_posting: "Job ad", dated_activity: "Dated activity", parent_organisation_number: "Parent",
  deleted_from_register: "Deleted from register"};
const RANK = p => p >= 90 ? "top 10%" : p >= 75 ? "top quarter" : p > 55 ? "above median" : p >= 45 ? "near median" : p > 25 ? "below median" : p > 10 ? "bottom quarter" : "bottom 10%";
const FIN = ["revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt"];
const AREAS = META.areas || ["Company record", "Financials", "People & locations", "Company website", "Hiring & public activity"];
const ROLE_ORDER = ["DAGL", "INNH", "LEDE", "NEST", "MEDL", "VARA", "KONT", "REVI", "REGN"];
const ROLE_EN = {DAGL: "Managing director", INNH: "Owner", LEDE: "Board chair", NEST: "Deputy chair", MEDL: "Board member", VARA: "Deputy board member",
  REVI: "Auditor", REGN: "Accountant", KONT: "Contact person", FFØR: "Business manager", DTPR: "Partner", DTSO: "Partner", BOBE: "Estate administrator", HFOR: "Main representative"};
const isRoutine = c => c && c.value && c.value.kind === "official_register_event";

/* ---------------- data loading ---------------- */
window.SP_DATA_LOAD = (org, env) => { cache[org] = env; (waiting[org] || []).forEach(f => f(env)); delete waiting[org]; };
const ROW = Object.create(null);
IDX.forEach(r => { ROW[r.o] = r; });
function load(org) {
  if (cache[org]) return Promise.resolve(cache[org]);
  if (!ROW[org]) return Promise.resolve(null);
  return new Promise(res => {
    const first = !waiting[org];
    (waiting[org] = waiting[org] || []).push(res);
    if (first) {
      const s = document.createElement("script");
      s.src = `data/${org}.js`;
      s.onerror = () => { (waiting[org] || []).forEach(f => f(null)); delete waiting[org]; };
      document.head.appendChild(s);
    }
  });
}

/* ---------------- compare tray ---------------- */
let picks = [];
try { picks = JSON.parse(sessionStorage.getItem("sp-picks") || "[]").filter(o => ROW[o]).slice(0, 2); } catch (e) { picks = []; }
const nameOf = o => (ROW[o] || {}).n || o;
function togglePick(o) {
  picks = picks.includes(o) ? picks.filter(x => x !== o) : picks.concat(o).slice(-2);
  try { sessionStorage.setItem("sp-picks", JSON.stringify(picks)); } catch (e) {}
  drawTray();
  document.querySelectorAll(`[data-pick="${o}"]`).forEach(b => { b.setAttribute("aria-pressed", picks.includes(o)); b.textContent = picks.includes(o) ? "✓ Added to compare" : "+ Compare"; });
}
function drawTray() {
  const t = $("#tray");
  t.classList.toggle("on", picks.length > 0 && !location.hash.startsWith("#/compare/"));
  t.innerHTML = `<div class="in"><b class="small">Compare (${picks.length} of 2):</b>${picks.map(o => `<span class="pick"><span>${esc(nameOf(o))}</span><button aria-label="Remove ${esc(nameOf(o))} from comparison" data-unpick="${o}">×</button></span>`).join("")}
    ${picks.length === 2 ? `<a class="btn primary sm" id="tray-go" href="#/compare/${picks[0]}/${picks[1]}">Compare these two →</a>` : `<span class="small muted">Now pick a second company with “+ Compare”.</span>`}</div>`;
}
const pickBtn = o => `<button class="btn sm" data-pick="${o}" aria-pressed="${picks.includes(o)}">${picks.includes(o) ? "✓ Added to compare" : "+ Compare"}</button>`;
const covDots = cov => `<span class="dots" aria-hidden="true">${(cov || []).map(v => `<i class="${v ? "on" : ""}"></i>`).join("")}</span>`;
const covN = cov => (cov || []).reduce((a, b) => a + b, 0);
const covPill = r => `<span class="covpill ${covN(r.cov) < 4 ? "mid" : ""}" title="${esc(AREAS.map((a, i) => `${a}: ${r.cov && r.cov[i] ? "found" : "not found"}`).join("\n"))}" aria-label="${covN(r.cov)} of ${AREAS.length} data areas">${covDots(r.cov)} ${covN(r.cov)}/${AREAS.length} data</span>`;

/* ---------------- search page ---------------- */
const FILTERS = [["all5", "All five areas", "data found in all five areas: company record, financials, people & locations, website, hiring & public activity"],
  ["web", "Verified website", "a company website proven to belong to this organisation number"], ["prof", "Social profiles", "company-owned profiles linked from the verified website"],
  ["jobs", "Hiring now", "at least one active job ad"], ["act", "Dated activity", "dated news, public contracts or register updates"],
  ["chg", "Changed since last run", "facts that changed since the previous run"], ["warn", "Warnings", "bankruptcy, liquidation or negative equity"]];
const SORTS = [["data", "Most data found"], ["match", "Best match"], ["name", "Name A–Z"], ["revenue", "Revenue (high to low)"], ["employees", "Employees (high to low)"]];
const st = {q: "", f: new Set(), sort: "", limit: 50, form: "", town: "", sector: "", fopen: false};
const counted = key => { const n = {}; IDX.forEach(r => { const v = key(r); if (v) n[v] = (n[v] || 0) + 1; }); return n; };
let _lists = null;
const LISTS = () => _lists || (_lists = (() => {
  const forms = counted(r => r.f), towns = counted(r => r.k), secs = counted(r => r.sec);
  return {forms: Object.keys(forms).sort((a, b) => forms[b] - forms[a] || a.localeCompare(b)), towns: Object.keys(towns).sort((a, b) => a.localeCompare(b, "nb")).map(t => [t, towns[t]]),
    secs: Object.keys(secs).sort((a, b) => a.localeCompare(b)).map(s => [s, secs[s]])};
})());
function matches(r, q) {
  if (!q) return 1;
  const digits = q.replace(/\s/g, "");
  if (/^\d{3,9}$/.test(digits)) return r.o.startsWith(digits) ? 3 : (digits.length === 4 && r.pn === digits) ? 2 : r.o.includes(digits) ? 1 : 0;
  const n = r._n || (r._n = fold(r.n)), words = fold(q).split(/\s+/).filter(Boolean);
  if (n.startsWith(words.join(" "))) return 4;
  const hay = r._h || (r._h = fold([r.n, r.k, r.pt, r.pn, r.il, r.ie, r.sec, r.f, r.fd].join(" ")));
  if (!words.every(w => hay.includes(w))) return 0;
  return words.every(w => n.includes(w)) ? 3 : (r._t || (r._t = fold(r.k + " " + r.pt))).includes(words.join(" ")) ? 2 : 1;
}
const rich = r => covN(r.cov) * 100 + (r.b.web ? 8 : 0) + Math.min(r.b.prof, 4) + Math.min(r.b.jobs, 4) + Math.min(r.b.act, 20) / 10;
function filtered() {
  const q = st.q.trim();
  const rows = [];
  for (const r of IDX) {
    if (st.form && r.f !== st.form) continue;
    if (st.town && r.k !== st.town) continue;
    if (st.sector && r.sec !== st.sector) continue;
    let ok = true;
    for (const k of st.f) { if (k === "all5" ? covN(r.cov) !== AREAS.length : !(r.b && r.b[k])) { ok = false; break; } }
    if (!ok) continue;
    const s = matches(r, q);
    if (s) rows.push([r, s]);
  }
  const sort = st.sort || (q ? "match" : "data");
  const byName = (a, b) => a[0].n.localeCompare(b[0].n, "nb");
  const by = {match: (a, b) => b[1] - a[1] || rich(b[0]) - rich(a[0]) || byName(a, b), data: (a, b) => rich(b[0]) - rich(a[0]) || byName(a, b), name: byName,
    revenue: (a, b) => ((b[0].rev || [null])[0] ?? -Infinity) - ((a[0].rev || [null])[0] ?? -Infinity) || byName(a, b), employees: (a, b) => (b[0].emp ?? -1) - (a[0].emp ?? -1) || byName(a, b)};
  rows.sort(by[sort] || by.data);
  return {rows, sort};
}
function searchHash() {
  const p = new URLSearchParams();
  if (st.q.trim()) p.set("q", st.q.trim());
  if (st.f.size) p.set("f", [...st.f].join(","));
  if (st.town) p.set("town", st.town);
  if (st.form) p.set("type", st.form);
  if (st.sector) p.set("sector", st.sector);
  if (st.sort) p.set("sort", st.sort);
  const s = p.toString();
  return "#/" + (s ? "?" + s : "");
}
function readSearchHash(qs) {
  const p = new URLSearchParams(qs || "");
  st.q = p.get("q") || ""; st.f = new Set((p.get("f") || "").split(",").filter(k => FILTERS.some(f => f[0] === k)));
  st.town = p.get("town") || ""; st.form = p.get("type") || ""; st.sector = p.get("sector") || ""; st.sort = SORTS.some(s => s[0] === p.get("sort")) ? p.get("sort") : "";
  st.limit = 50;
  if ($("#q").value !== st.q) $("#q").value = st.q;
}
function rowHtml(r) {
  return `<article class="row">
      <div class="rmain"><h3><a href="#/c/${r.o}">${esc(r.n)}</a></h3><div class="small muted">Org. no. ${r.o} · ${esc(r.fd || r.f)}${r.k ? " · " + esc(r.k) : ""}${r.pt ? " (" + esc(r.pt) + ")" : ""}${r.rev ? ` · revenue ${esc(r.rev[1] || "")} ${nf.format(r.rev[0])} (${esc(r.rev[2])})` : ""}</div></div>
      <div class="acts">${covPill(r)}<span class="actb"><a class="btn sm" href="#/c/${r.o}">Open</a>${pickBtn(r.o)}</span></div>
      <div class="hl">${esc(r.h || r.il || "")}</div><div class="badges">${badgeRow(r)}</div></article>`;
}
function badgeRow(r) {
  const b = r.b || {};
  const on = (k, t) => `<span class="b ${b[k] ? "on" : ""}" title="${b[k] ? "found, with source" : "not found or not checked; see the company page"}">${b[k] ? "✓" : "–"} ${t}</span>`;
  return `${b.warn ? `<span class="b warn">⚠ ${esc(b.warn)}</span>` : ""}${b.chg ? `<span class="b chg">${b.chg} change${b.chg > 1 ? "s" : ""}</span>` : ""}
    ${on("web", "website")}${on("prof", "profiles")}${on("jobs", "hiring")}${on("act", "activity")}`;
}
function renderSearch() {
  document.title = "Signalpost — find a company";
  const t0 = performance.now();
  const {rows, sort} = filtered();
  const L = LISTS(), q = st.q.trim();
  const shown = rows.slice(0, st.limit);
  const active = st.f.size || st.town || st.form || st.sector;
  $("#app").innerHTML = `
    ${q ? "" : `<section class="intro" aria-label="How to use this site">
      <div class="stat"><b>${nf.format(IDX.length)}</b> companies in this run <span class="muted">·</span> <b>${nf.format(META.all_areas ?? IDX.filter(r => covN(r.cov) === AREAS.length).length)}</b> have data in all five areas</div>
      <ol class="how"><li><b>Find</b> a company by name, organisation number or town.</li><li><b>Compare</b> two: tap “+ Compare” on each.</li><li><b>Verify</b> any fact: every value has a “source” button with the URL, retrieval time and quoted text.</li></ol>
    </section>`}
    <details class="filters" ${window.innerWidth >= 700 || active || st.fopen ? "open" : ""}><summary class="fsum">Filters and sorting${active ? ` <span class="b on">${st.f.size + !!st.town + !!st.form + !!st.sector} on</span>` : ""}</summary>
      <div class="chips" role="group" aria-label="Show only"><span class="small muted">Show only:</span>${FILTERS.map(([k, t, h]) => `<button class="chip" data-f="${k}" aria-pressed="${st.f.has(k)}" title="${esc(h)}">${t}</button>`).join("")}</div>
      <div class="sels">
      <label>Town <select id="town"><option value="">all towns</option>${L.towns.map(([t, n]) => `<option value="${esc(t)}" ${st.town === t ? "selected" : ""}>${esc(t)} (${n})</option>`).join("")}</select></label>
      <label>Sector <select id="sector"><option value="">all sectors</option>${L.secs.map(([s, n]) => `<option value="${esc(s)}" ${st.sector === s ? "selected" : ""}>${esc(s)} (${n})</option>`).join("")}</select></label>
      <label>Type <select id="form"><option value="">all types</option>${L.forms.map(f => `<option value="${esc(f)}" ${st.form === f ? "selected" : ""}>${esc(FORM[f] || f)}</option>`).join("")}</select></label>
      <label>Sort <select id="sort">${SORTS.filter(s => q || s[0] !== "match").map(([k, t]) => `<option value="${k}" ${sort === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      ${active ? `<button class="btn sm" id="reset">Clear filters</button>` : ""}</div>
    </details>
    <p class="small muted" id="count" aria-live="polite">${nf.format(rows.length)} of ${nf.format(IDX.length)} companies${q ? ` match “${esc(q)}”` : ""}${sort === "data" ? " · profiles with the most verified data first" : ""}.</p>
    <div class="list">${shown.map(([r]) => rowHtml(r)).join("") || `<div class="empty">No company matches. Try part of the name, the 9-digit organisation number or a town${active ? `, or <button class="linkbtn" id="reset2">clear the filters</button>` : ""}.</div>`}</div>
    ${rows.length > shown.length ? `<button class="btn more" id="more">Show ${Math.min(50, rows.length - shown.length)} more</button>` : ""}
    ${q || active ? "" : legend()}`;
  window.SP_LAST_SEARCH_MS = performance.now() - t0;
}
function legend() {
  return `<div class="legend card"><h2>How to read this site</h2><p class="small"><b>Data areas</b> (${AREAS.map(esc).join(", ")}): an area counts when at least one fact in it was found with a source. The count is not a rating of the company.</p>
    ${Object.keys(STATE_TXT).map(s => `<div>${stateBadge(s)} ${esc(STATE_HELP[s])}</div>`).join("")}
    <p class="small muted">Missing values are shown as missing, never as zero. Every fact links to its source with the retrieval time and, for accounts, the reporting period.</p></div>`;
}

/* ---------------- helpers over one envelope ---------------- */
const evMap = env => env._ev || (env._ev = Object.fromEntries((env.evidence || []).map(e => [e.id, e])));
const claimMap = env => env._cm || (env._cm = Object.fromEntries((env.claims || []).map(c => [c.id, c])));
const avail = (env, f) => (env.claims || []).filter(c => c.field === f && c.availability === "available" && c.value !== null && c.value !== undefined);
const firstOf = (env, f) => avail(env, f)[0] || null;
const gapOf = (env, f) => (env.claims || []).find(c => c.field === f && c.availability !== "available") || null;
const hasSrc = c => !!(c && (c.evidence_ids || []).length);
const changedKeys = env => new Set((env.changes || []).map(c => c.key));
const yearOf = c => String(c && c.reporting_period || "").split("/").pop().slice(0, 4);
function latestFin(env, f) {
  const cs = avail(env, f);
  return cs.find(c => c.latest) || cs.sort((a, b) => String(b.reporting_period).localeCompare(String(a.reporting_period)))[0] || null;
}
function prevFin(env, f, cur) {
  if (!cur) return null;
  return avail(env, f).filter(c => String(c.reporting_period) < String(cur.reporting_period)).sort((a, b) => String(b.reporting_period).localeCompare(String(a.reporting_period)))[0] || null;
}
function fmtMoney(v, unit) { return v === null || v === undefined || isNaN(Number(v)) ? "—" : `${esc(unit || "")} ${nf.format(Number(v))}`.trim(); }
function shortMoney(v, unit) {
  const n = Number(v), a = Math.abs(n), u = unit ? esc(unit) + " " : "";
  if (a >= 1e9) return `${u}${(n / 1e9).toFixed(1)} bn`;
  if (a >= 1e6) return `${u}${(n / 1e6).toFixed(1)} m`;
  if (a >= 1e4) return `${u}${nf.format(Math.round(n / 1e3))} k`;
  return `${u}${nf.format(n)}`;
}
const tcase = s => s && s === s.toUpperCase() ? s.toLowerCase().replace(/(^|[\s-])\S/g, m => m.toUpperCase()) : s;
const personName = v => [].concat(v.name || []).join(" ");
function fmtVal(c) {
  const v = c.value;
  if (v === null || v === undefined) return `<span class="muted">—</span>`;
  if (FIN.includes(c.field)) return `<span class="num">${fmtMoney(v, c.unit)}</span>`;
  if (typeof v === "boolean") return v ? "<b>Yes</b>" : "No";
  if (typeof v === "number") return `<span class="num">${nf.format(v)}</span>`;
  if (typeof v === "string") return safeUrl(v) ? `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>` : esc(v);
  if (c.field === "role") return `<b>${esc(v.role || v.role_code)}</b>: ${esc(personName(v))}${v.organisation_number ? ` (${esc(v.organisation_number)})` : ""}${v.last_changed ? `<span class="note">registered ${esc(v.last_changed)}</span>` : ""}`;
  if (c.field === "registered_workplace") return `${esc(v.name || "")}${v.organisation_number ? ` (${esc(v.organisation_number)})` : ""}<span class="note">${esc(addr(v.address))}${v.employees != null ? ` · ${nf.format(v.employees)} employees` : ""}</span>`;
  if (c.field === "group_structure") return groupHtml(v, c);
  if (c.field === "job_posting") return `${safeUrl(v.url) ? `<a href="${esc(v.url)}" target="_blank" rel="noopener noreferrer">${esc(v.title || "Job ad")}</a>` : esc(v.title || "Job ad")}<span class="note">${esc([(v.locations || []).join("; "), v.published ? "published " + v.published : "", v.expires ? "apply by " + v.expires : ""].filter(Boolean).join(" · "))}</span>`;
  if (v.adresse !== undefined || v.postnummer) return esc(addr(v));
  if (v.kode !== undefined || v.beskrivelse) return `${esc(v.kode || "")} ${esc(v.beskrivelse || "")}`;
  const u = safeUrl(v.url);
  if (u) return `${v.date ? `<b>${esc(String(v.date).slice(0, 10))}</b> · ` : ""}<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(v.title || v.platform || u)}</a>${v.platform && v.title ? ` <span class="muted">(${esc(v.platform)})</span>` : ""}${v.location ? `<span class="note">${esc(v.location)}</span>` : ""}`;
  if (v.title) return esc(v.title);
  return `<code class="small">${esc(JSON.stringify(v).slice(0, 240))}</code>`;
}
function addr(a) {
  if (!a || typeof a !== "object") return "";
  return [[].concat(a.adresse || []).join(", "), `${a.postnummer || ""} ${a.poststed || ""}`.trim(), a.kommune && a.kommune !== a.poststed ? a.kommune : "", a.land && a.land !== "Norge" ? a.land : ""].filter(Boolean).join(", ");
}
function groupHtml(t, c) {
  let n = 0;
  const walk = (node, d) => {
    if (!node || typeof node !== "object" || d > 6 || n > 40) return "";
    n++;
    const me = node.organisasjonsnummer === c._org;
    const kids = (node.children || []).map(k => walk(k, d + 1)).join("");
    return `<li>${me ? "<b>" : ""}${ROW[node.organisasjonsnummer] && !me ? `<a href="#/c/${esc(node.organisasjonsnummer)}">${esc(node.navn || "?")}</a>` : esc(node.navn || "?")} <span class="muted">${esc(node.organisasjonsnummer || "")}${node.grunnlag ? " · " + esc(node.grunnlag) : ""}</span>${me ? " ← this company</b>" : ""}${kids ? `<ul>${kids}</ul>` : ""}</li>`;
  };
  return `<ul class="small tree">${walk(t, 0)}</ul>${n > 40 ? `<span class="note">tree truncated</span>` : ""}`;
}
const srcBtn = (ids, label, cls = "srcbtn") => (ids || []).length ? `<button class="${cls}" data-src="${esc(ids.join(","))}" aria-label="Show source for ${esc(label)}">source</button>` : "";

/* ---------------- source sheet ---------------- */
let lastFocus = null, sheetEnv = null, sheetBase = null;
function refHtml(env, refIds) {
  const refs = ((env.synthesis || {}).references || []).filter(r => (refIds || []).includes(r.id));
  return refs.map(r => `<div class="ev ref"><div class="small muted">Reference table (not a fact about this company)</div>
    <div class="val">${esc(r.kind === "peer_reference" ? "Peer comparison table" : "Universe counts")}</div><div class="small">${esc(r.description || "")}</div>
    <dl>${(r.source_urls || []).filter(Boolean).map(u => `<dt>Source</dt><dd>${safeUrl(u) && !u.includes("{") ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>` : esc(u)}</dd>`).join("")}
      ${r.sample_manifest_sha256 ? `<dt>Sample fingerprint</dt><dd><code>${esc(String(r.sample_manifest_sha256).slice(0, 16))}…</code></dd>` : ""}
      ${r.universe_sha256 ? `<dt>Universe fingerprint</dt><dd><code>${esc(String(r.universe_sha256).slice(0, 16))}…</code></dd>` : ""}
      ${r.path ? `<dt>File in agent</dt><dd><code>${esc(r.path)}</code></dd>` : ""}</dl></div>`).join("");
}
const PROOF = {orgnr_on_site: "the site prints this company's exact organisation number", name_and_address: "the site shows the registered legal name together with the registered address",
  registry: "the website is listed in the official register", registry_name_on_site: "the website is listed in the official register and shows the legal name",
  strong_multi_signal: "several independent signals on the site match the register"};
const proofText = m => PROOF[m] || String(m || "").replace(/_/g, " ");
function openSheet(env, ids, title, refIds, base) {
  const cm = claimMap(env), em = evMap(env);
  const cl = ids.map(i => cm[i]).filter(Boolean);
  if (!$("#sheet").classList.contains("open")) lastFocus = document.activeElement;
  sheetEnv = env; sheetBase = base || null;
  const link = base ? location.href.split("#")[0] + `#${base}/source/${ids.join(",")}` : "";
  $("#sheet").innerHTML = `<button class="x" aria-label="Close sources" id="sheet-x">×</button><h2>${esc(title || "Sources")}</h2>
    <p class="small muted">${esc(env.identity?.name || env.organisation_number)} · org. no. ${esc(env.organisation_number)}</p>${refHtml(env, refIds)}
    ${cl.length === 1 && cl[0].availability === "available" ? `<p class="plain">${esc(plainLine(env, cl[0]))}</p>` : ""}
    ${cl.map(c => `<div class="ev"><div class="small muted">${esc(LABEL[c.field] || c.field)} ${stateBadge(c.availability)}${c.stale ? ' <span class="st ambiguous">carried forward</span>' : ""}</div>
      <div class="val">${fmtVal(c)}</div>${c.note ? `<div class="small muted">${esc(c.note)}</div>` : ""}
      ${c.field === "official_website" && c.identity_method ? `<div class="small"><b>Why we trust it:</b> ${esc(proofText(c.identity_method))}</div>` : ""}
      ${dedupe((c.evidence_ids || []).map(i => em[i]).filter(Boolean)).map(e => `<dl>
        <dt>Source</dt><dd>${safeUrl(e.source_url) ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">${esc(e.source_url)}</a>` : esc(e.source_url)}</dd>
        <dt>Type</dt><dd>${esc(String(e.source_class || "").replace(/_/g, " "))}</dd>
        <dt>Retrieved</dt><dd>${esc(fmtTime(e.retrieved_at))}</dd>
        ${e.reporting_period || c.reporting_period ? `<dt>Period</dt><dd>${esc(e.reporting_period || c.reporting_period)}</dd>` : ""}
        ${c.confidence != null && c.confidence < 1 ? `<dt>Match confidence</dt><dd>${esc(Math.round(c.confidence * 100))}%</dd>` : ""}
      </dl>${e.content_sha256 ? `<details class="tech small"><summary>Technical details</summary>Document fingerprint (SHA-256 of the exact copy that was read): <code>${esc(String(e.content_sha256))}</code>${e.extraction_method ? `<br>Extraction: ${esc(e.extraction_method)}` : ""}</details>` : ""}${e.claim_span ? `<div class="span"><span class="muted small">Text from the source${/^[\[{]|=/.test(String(e.claim_span)) ? " (as stored in the register's data file)" : ""}:</span> “${esc(String(e.claim_span).slice(0, 400))}”</div>` : ""}
      ${safeUrl(e.source_url) ? `<a class="open-src" href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>` : ""}`).join("") || `<p class="small muted">No source document: this records that the value was not found.</p>`}
      </div>`).join("") || "<p>No cited claims.</p>"}
    ${link ? `<div class="share small"><label for="sheet-link">Link to this source view${location.protocol === "file:" ? " (a web link once the site is hosted)" : ""}</label><div class="shrow"><input id="sheet-link" readonly value="${esc(link)}"><button class="btn sm" data-copy="#sheet-link">Copy</button></div></div>` : ""}`;
  $("#sheet").classList.add("open"); $("#sheet-bg").classList.add("open");
  if (base) history.replaceState(null, "", `#${base}/source/${ids.join(",")}`);
  $("#sheet-x").focus();
}
const CLASS_EN = {official_registry_live: "the official business register (Brønnøysund, live API)", official_registry_bulk: "the official business register (Brønnøysund bulk file)",
  official_annual_accounts: "the official annual accounts register (Regnskapsregisteret)", company_owned_website: "the company's own website"};
const unhtml = h => h.replace(/<span class="note">/g, " · ").replace(/<[^>]+>/g, "").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
function plainLine(env, c) {
  const e = evMap(env)[(c.evidence_ids || [])[0]] || {};
  return `${LABEL[c.field] || c.field}${c.reporting_period ? " " + yearOf(c) : ""}: ${unhtml(fmtVal(c)).trim()} — from ${CLASS_EN[e.source_class] || String(e.source_class || "the source").replace(/_/g, " ")}, retrieved ${fmtDate(e.retrieved_at)}.`;
}
function dedupe(evs) { const seen = new Set(); return evs.filter(e => { const k = e.source_url + "|" + (e.claim_span || ""); if (seen.has(k)) return false; seen.add(k); return true; }); }
function closeSheet() {
  closeSheetQuiet();
  if (sheetBase && location.hash.includes("/source/")) history.replaceState(null, "", `#${sheetBase}`);
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}
function closeSheetQuiet() { $("#sheet").classList.remove("open"); $("#sheet-bg").classList.remove("open"); }
const fmtTime = t => t ? String(t).replace("T", " ").replace(/\.\d+Z$/, "Z").replace(/Z$/, " UTC") : "—";
const fmtDate = t => t ? String(t).slice(0, 10) : "—";

/* ---------------- company page: sections ---------------- */
function factRows(env, cl) {
  const chg = changedKeys(env);
  return cl.map(c => `<tr id="f-${esc(c.id)}" class="${chg.has(c.key) ? "changed" : ""}"><th scope="row">${esc(LABEL[c.field] || c.field)}${chg.has(c.key) ? ' <span class="b chg">changed</span>' : ""}</th>
      <td>${c.availability === "available" ? fmtVal(c) : /not.?run/.test(c.note || "") ? `<span class="st failed">not checked</span>` : stateBadge(c.availability)}${c.availability !== "available" && c.note ? `<span class="note">${esc(c.note)}</span>` : ""}
      ${srcBtn(hasSrc(c) ? [c.id] : [], LABEL[c.field] || c.field)}</td></tr>`).join("");
}
function finTable(env) {
  const cl = (env.claims || []).filter(c => FIN.includes(c.field));
  if (!cl.length) return `<p class="muted">No annual accounts in the register for this entity${(env.identity || {}).legal_form === "ENK" ? " (sole proprietorships do not file accounts)" : ""}.</p>`;
  const periods = [...new Set(cl.map(c => c.reporting_period || (c.key || "").split("@")[1] || "?"))].sort().reverse().slice(0, window.innerWidth < 500 ? 3 : 6);
  const cell = (f, p) => {
    const c = cl.find(x => x.field === f && (x.reporting_period || (x.key || "").split("@")[1]) === p);
    if (!c) return `<td class="muted">·</td>`;
    if (c.availability !== "available" || c.value == null) return `<td title="${esc(c.note || STATE_HELP[c.availability] || "")}"><span class="muted">— </span><span class="st ${esc(c.availability)}">${c.availability === "not_available" ? "not filed" : esc(STATE_TXT[c.availability] || c.availability)}</span></td>`;
    return `<td><button class="vbtn num" data-src="${esc(c.id)}" aria-label="${esc(LABEL[f])} ${esc(p)}: show source">${nf.format(c.value)}</button></td>`;
  };
  const unit = (cl.find(c => c.unit) || {}).unit || "currency not stated";
  return `<p class="small muted">Tap any number to see the filing it comes from. “not filed” = absent in the filed accounts, not zero.</p><div class="fin"><table><thead><tr><th>${esc(unit)}</th>${periods.map(p => `<th>${esc(p.includes("/") ? p.split("/")[1].slice(0, 4) : p.slice(0, 4))}</th>`).join("")}</tr></thead>
    <tbody>${FIN.filter(f => cl.some(c => c.field === f)).map(f => `<tr><td>${esc(LABEL[f])}</td>${periods.map(p => cell(f, p)).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="small muted">Reporting periods: ${periods.map(esc).join(", ")}.</p>`;
}
const SUM_GROUPS = [["What it is", ["identity", "activity", "status", "size"]], ["Latest filed numbers", ["accounts"]], ["Compared with peers", ["peers", "market"]],
  ["People, group and places", ["leadership", "group", "places"]], ["Online, hiring and activity", ["online", "hiring", "activity_dated"]], ["Changes", ["changes"]]];
const citeBtn = (ids, refs, label) => { const n = (ids || []).length + (refs || []).length;
  return n ? `<button class="cite" data-cite="${esc((ids || []).join(","))}"${(refs || []).length ? ` data-ref="${esc(refs.join(","))}"` : ""} aria-label="Sources for: ${esc(String(label).slice(0, 60))}">${n} source${n > 1 ? "s" : ""}</button>` : ""; };
function summaryHtml(env) {
  const s = env.synthesis || {};
  if (!s.sentences || !s.sentences.length) return `<p class="muted">No supported summary: ${esc((s.unknowns || [])[0]?.text || "no verified facts")}.</p>`;
  const known = new Set(SUM_GROUPS.flatMap(g => g[1]));
  const groups = SUM_GROUPS.map(([t, topics]) => [t, s.sentences.filter(x => topics.includes(x.topic))]).concat([["More", s.sentences.filter(x => !known.has(x.topic))]]);
  const phone = window.innerWidth < 600;
  return groups.filter(g => g[1].length).map(([t, ss], i) => `<details class="sumg"${i < (phone ? 1 : 2) || ss.some(x => x.flag) ? " open" : ""}><summary class="sumh">${esc(t)} <span class="muted">(${ss.length})</span></summary>` +
    ss.map(x => `<p class="s ${esc(x.flag || "")}">${esc(x.text)}${citeBtn(x.claim_ids, x.reference_ids, x.text)}</p>`).join("") + "</details>").join("");
}
const AUD = {apply: "Applying for a job", sell: "Selling to it / giving credit", partner: "Partnering", invest: "Investing"};
function decisionHtml(env) {
  const d = (env.synthesis || {}).decision || [];
  if (!d.length) return `<p class="muted small">Not enough verified facts for decision notes.</p>`;
  return `<div class="dec">${d.map(b => `<div class="decb"><h4>${esc(AUD[b.audience] || b.title)}</h4><ul>${(b.points || []).map(p => `<li>${esc(p.text[0].toUpperCase() + p.text.slice(1))}${citeBtn(p.claim_ids, p.reference_ids, p.text)}</li>`).join("")}</ul></div>`).join("")}</div>
    <p class="small muted">Facts only, each with its source; not advice. Peer positions come from a sampled reference table (open “sources” to see how it was built).</p>`;
}
const PEER_FMT = {revenue: v => fmtMoney(v, "NOK"), annual_result: v => fmtMoney(v, "NOK"), operating_margin: v => `${Number(v).toFixed(1)}%`, equity_ratio: v => `${Number(v).toFixed(0)}%`, revenue_growth: v => `${v > 0 ? "+" : ""}${Number(v).toFixed(0)}%`};
const PEER_LBL = {revenue: "Revenue", revenue_growth: "Revenue growth", operating_margin: "Operating margin", annual_result: "Net result", equity_ratio: "Equity ratio"};
const peerSentence = env => ((env.synthesis || {}).sentences || []).find(x => x.derived === "peer_position");
function peersHtml(env) {
  const s = peerSentence(env);
  const mk = ((env.synthesis || {}).sentences || []).find(x => x.topic === "market");
  const mkHtml = mk ? `<p class="small">${esc(mk.text)}${citeBtn(mk.claim_ids, mk.reference_ids, mk.text)}</p>` : "";
  if (!s) return `<p class="muted small">No peer comparison: ${(env.claims || []).some(c => FIN.includes(c.field) && c.availability === "available") ? "too few comparable companies in the reference sample, or no industry code" : "no filed accounts in this envelope"}.</p>${mkHtml}`;
  const pos = s.peer_positions || {};
  return `<p class="small muted">Peers: ${esc(s.peer_label || "")}. The dot is this company; the shaded band is the middle half of peers; the line is the median.</p>` +
    Object.entries(pos).map(([k, p]) => `<div class="pbar"><div class="plbl"><b>${esc(PEER_LBL[k] || k)}: ${esc(RANK(p.percentile))}</b> <span class="small muted">peer median ${esc(PEER_FMT[k] ? PEER_FMT[k](p.median) : p.median)} · ${p.n} peers</span></div>
    <div class="ptrack" role="img" aria-label="${esc(PEER_LBL[k] || k)} at about the ${p.percentile}th percentile of peers"><span class="pq"></span><span class="pmed"></span><span class="pdot" style="left:${Math.max(1, Math.min(99, p.percentile))}%"></span></div></div>`).join("") +
    `<p class="small">${esc(s.text)}${citeBtn(s.claim_ids, s.reference_ids, s.text)}</p>${mkHtml}`;
}
function unknownsHtml(env) {
  const u = (env.synthesis || {}).unknowns || [];
  if (!u.length) return `<p class="muted small">Nothing flagged as unknown.</p>`;
  return `<ul class="unk">${u.map(x => `<li>${stateBadge(x.availability)}${x.expected_for_legal_form ? ' <span class="st expected" title="Common for this legal form">usual for this type</span>' : ""} ${esc(x.text)}.
    ${x.reason ? `<span class="note">Why: ${esc(x.reason)}</span>` : ""}${(x.claim_ids || []).length ? `<button class="srcbtn" data-cite="${esc(x.claim_ids.join(","))}">what was checked</button>` : ""}</li>`).join("")}</ul>`;
}
function changesHtml(env) {
  const ch = (env.synthesis || {}).changes || [];
  const prev = (env.refresh || {}).previous_run_id;
  if (!ch.length) return prev ? `<p class="small">No material changes since the previous run <b>${esc(prev)}</b>${env.refresh.previous_completed_at ? ` (${esc(fmtTime(env.refresh.previous_completed_at))})` : ""}.</p>` : `<p class="small muted">First observation: no previous run to compare against.</p>`;
  return `<p class="small">Since run <b>${esc(prev || "previous")}</b>:</p><ul>${ch.map(c => `<li>${esc(c.text)}${(c.claim_ids || []).length ? `<button class="cite" data-cite="${esc(c.claim_ids.join(","))}">source</button>` : ""}${(c.previous_source_urls || []).map(u => safeUrl(u) ? ` <a class="small" href="${esc(u)}" target="_blank" rel="noopener noreferrer">previous source ↗</a>` : "").join("")}</li>`).join("")}</ul>`;
}
function tilesHtml(env) {
  const tile = (label, value, sub, ids, cls = "") => ids && ids.length
    ? `<button class="tile ${cls}" data-src="${esc(ids.join(","))}" aria-label="${esc(label)}: ${esc(String(value).replace(/<[^>]+>/g, ""))}. Show source"><span class="tl">${esc(label)}</span><strong>${value}</strong><small>${sub} · <u>source</u></small></button>`
    : `<div class="tile off ${cls}"><span class="tl">${esc(label)}</span><strong>${value}</strong><small>${sub}</small></div>`;
  const fin = f => { const c = latestFin(env, f); if (c) return [shortMoney(c.value, c.unit), `${yearOf(c)} annual accounts`, [c.id]];
    const g = gapOf(env, f); return [g ? (g.availability === "not_available" ? "Not reported" : esc(STATE_TXT[g.availability])) : "No accounts", g ? esc(g.note && /absent/.test(g.note) ? "absent in the filed accounts (not zero)" : "no filed accounts found") : "none in the register", []]; };
  const emp = firstOf(env, "registered_employees"), empGap = gapOf(env, "registered_employees");
  const wps = avail(env, "registered_workplace"), wpGap = gapOf(env, "registered_workplace");
  const jobs = avail(env, "job_posting"), jobGap = gapOf(env, "job_posting");
  const acts = avail(env, "dated_activity"), ref = String((env.run || {}).completed_at || "").slice(0, 10);
  const cut = ref ? `${Number(ref.slice(0, 4)) - 1}${ref.slice(4)}` : "";
  const recent = acts.filter(c => String(c.value.date || "") >= cut);
  const own = recent.filter(c => !isRoutine(c));
  const latest = acts.map(c => String(c.value.date || "")).sort().pop();
  const site = firstOf(env, "official_website"), profs = avail(env, "social_profile");
  const [rv, rs, ri] = fin("revenue"), [ov, os, oi] = fin("operating_result");
  return `<div class="tiles">
    ${tile("Latest reported revenue", rv, rs, ri)}
    ${tile("Latest operating result", ov, os, oi)}
    ${tile("Employees in register", emp ? nf.format(emp.value) : "Not registered", emp ? "official company record" : "the register lists it only when reported", emp ? [emp.id] : (hasSrc(empGap) ? [empGap.id] : []))}
    ${tile("Registered workplaces", wps.length ? nf.format(wps.length) : "None registered", wps.length ? "official subunits" : "no subunits in the register", wps.length ? wps.map(c => c.id) : (hasSrc(wpGap) ? [wpGap.id] : []))}
    ${tile("Open roles found", jobs.length ? nf.format(jobs.length) : jobGap && jobGap.availability !== "available" && jobGap.availability !== "not_available" ? esc(STATE_TXT[jobGap.availability]) : "None found", jobs.length ? "active job ads" : "NAV job feed and verified site checked", jobs.map(c => c.id), jobs.length ? "" : "zero")}
    ${tile("Activity, last 12 months", nf.format(recent.length), recent.length ? `${own.length} by the company or public bodies · latest ${esc(fmtDate(latest))}` : latest ? `latest ${esc(fmtDate(latest))}` : "no dated activity found", recent.slice(0, 40).map(c => c.id))}
    ${tile("Company website", site ? esc(String(site.value).replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")) : esc(STATE_TXT[(gapOf(env, "official_website") || {}).availability] || "not found"), site ? "verified: " + esc(proofText(site.identity_method)) : "not published without proof it belongs to this company", site ? [site.id] : [], "wide")}
    ${(() => { const r = ["DAGL", "INNH", "LEDE"].map(k => avail(env, "role").find(c => c.value.role_code === k)).find(Boolean);
      return tile(r ? ROLE_EN[r.value.role_code] : "Managing director", r ? esc(personName(r.value) || r.value.organisation_number) : "Not registered", r ? "official roles register" : "no managing director in the register", r ? [r.id] : []); })()}
  </div>`;
}
function leadershipHtml(env) {
  const roles = avail(env, "role").slice().sort((a, b) => ((ROLE_ORDER.indexOf(a.value.role_code) + 1) || 99) - ((ROLE_ORDER.indexOf(b.value.role_code) + 1) || 99));
  const grp = firstOf(env, "group_structure"), gap = gapOf(env, "role");
  const card = c => `<div class="info"><strong>${esc(personName(c.value) || c.value.organisation_number || "Name not reported")}</strong>
    <small>${esc(ROLE_EN[c.value.role_code] || c.value.role || c.value.role_code)}${c.value.role && ROLE_EN[c.value.role_code] ? ` <span class="muted">(${esc(c.value.role)})</span>` : ""}${c.value.last_changed ? ` · registered ${esc(c.value.last_changed)}` : ""}</small>${srcBtn([c.id], "role")}</div>`;
  const cards = roles.slice(0, 8).map(card).join("");
  return (cards ? `<div class="cards">${cards}</div>${roles.length > 8 ? `<details><summary class="small">Show ${roles.length - 8} more roles</summary><div class="cards">${roles.slice(8, 120).map(card).join("")}</div></details>` : ""}` : `<p class="empty-s">${gap ? stateBadge(gap.availability) + " " + esc(gap.note || "No public roles in the register.") : "No public roles in the register."}</p>`) +
    (grp ? `<details class="regf"><summary class="sumh">Group structure</summary>${srcBtn([grp.id], "group structure")}${groupHtml(grp.value, grp)}</details>` : "");
}
const wpCard = c => `<div class="info"><strong>${esc(c.value.name || "Workplace")}</strong><small>${esc(addr(c.value.address) || "Address not reported")}${c.value.employees != null ? ` · ${plural(c.value.employees, "employee")}` : ""}${c.value.organisation_number ? ` · org. no. ${esc(c.value.organisation_number)}` : ""}</small>${srcBtn([c.id], "workplace")}</div>`;
function locationsHtml(env) {
  const a = firstOf(env, "business_address"), wps = avail(env, "registered_workplace"), gap = gapOf(env, "registered_workplace");
  return `${a ? `<div class="info wide"><strong>Business address</strong><small>${esc(addr(a.value))}</small>${srcBtn([a.id], "business address")}</div>` : `<p class="empty-s">No business address in the register.</p>`}
    <h3 class="sub">Registered workplaces (${wps.length})</h3>
    ${wps.length ? `<div class="cards">${wps.slice(0, 6).map(wpCard).join("")}</div>${wps.length > 6 ? `<details><summary class="small">Show ${wps.length - 6} more workplaces</summary><div class="cards">${wps.slice(6, 200).map(wpCard).join("")}</div></details>` : ""}`
      : `<p class="empty-s">${esc((gap || {}).note || "No registered subunits.")} ${srcBtn(hasSrc(gap) ? [gap.id] : [], "workplaces")}</p>`}`;
}
function actItem(c) {
  const v = c.value || {}, u = safeUrl(v.url);
  return `<li><span class="d">${esc(fmtDate(v.date))}</span> <span class="k ${isRoutine(c) ? "reg" : ""}">${esc(KIND[v.kind] || (v.kind || "").replace(/_/g, " "))}</span>
    ${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(v.title || u)}</a>` : esc(v.title || "")}${srcBtn([c.id], "dated activity")}</li>`;
}
const KIND = {official_register_event: "register", company_news_or_post: "company news", public_procurement_award: "public contract"};
function footprintHtml(env) {
  const site = firstOf(env, "official_website"), sg = gapOf(env, "official_website");
  const profs = avail(env, "social_profile"), pg = gapOf(env, "social_profile");
  const jobs = avail(env, "job_posting"), jg = gapOf(env, "job_posting");
  const acts = avail(env, "dated_activity").slice().sort((a, b) => String(b.value.date || "").localeCompare(String(a.value.date || "")));
  const own = acts.filter(c => !isRoutine(c)), reg = acts.filter(isRoutine);
  return `<h3 class="sub">Company website</h3>
    ${site ? `<p><a href="${esc(site.value)}" target="_blank" rel="noopener noreferrer"><b>${esc(site.value)}</b></a> ${srcBtn([site.id], "website")}<span class="note">Verified: ${esc(proofText(site.identity_method))}.</span></p>`
      : `<p class="empty-s">${stateBadge((sg || {}).availability || "not_available")} ${esc((sg || {}).note || "No company-owned website found.")}</p>`}
    <h3 class="sub">Company-owned profiles</h3>
    ${profs.length ? `<div class="plinks">${profs.map(c => `<span class="plink">${safeUrl(c.value.url) ? `<a href="${esc(c.value.url)}" target="_blank" rel="noopener noreferrer">${esc(c.value.platform || "profile")} ↗</a>` : esc(c.value.platform)}${srcBtn([c.id], (c.value.platform || "profile") + " profile")}</span>`).join("")}</div><p class="small muted">Only profiles linked from the verified company website are shown.</p>`
      : `<p class="empty-s">${esc((pg || {}).note || "No verified company profile found.")}</p>`}
    <h3 class="sub">Open roles found (${jobs.length})</h3>
    ${jobs.length ? `<div class="cards">${jobs.slice(0, 20).map(c => `<div class="info">${fmtVal(c)}${srcBtn([c.id], "job ad")}</div>`).join("")}</div>`
      : `<p class="empty-s">${esc((jg || {}).note || "No active job ad found.")} This does not prove the company is not hiring.</p>`}
    <h3 class="sub">Recent activity (${own.length})</h3>
    ${own.length ? `<ul class="acts-l">${own.slice(0, 5).map(actItem).join("")}</ul>${own.length > 5 ? `<details><summary class="small">Show ${own.length - 5} older items</summary><ul class="acts-l">${own.slice(5, 200).map(actItem).join("")}</ul></details>` : ""}`
      : `<p class="empty-s">No dated news, posts or public contracts found for this company.</p>`}
    ${reg.length ? `<details class="regd"><summary class="small">Register updates (${reg.length}) — routine filings and role changes from the official register</summary><ul class="acts-l">${reg.slice(0, 200).map(actItem).join("")}</ul></details>` : ""}`;
}
function auditHtml(env, row) {
  const ws = (env.claims || []).find(c => c.field === "official_website") || {};
  const urls = new Set((env.evidence || []).map(e => e.source_url).filter(Boolean));
  const card = (k, v) => `<div class="acard"><span>${esc(k)}</span><strong>${v}</strong></div>`;
  return `<div class="agrid">
    ${card("Website identity", ws.availability === "available" ? `Exact entity · published` : ws.availability === "ambiguous" ? "Candidate found · withheld" : ws.availability ? esc(STATE_TXT[ws.availability]) + " · nothing published" : "not checked")}
    ${card("How it was proved", ws.availability === "available" ? esc(proofText(ws.identity_method)) : "—")}
    ${card("Website candidates checked", ws.candidates_checked != null ? nf.format(ws.candidates_checked) : (ws.candidates || []).length ? nf.format(ws.candidates.length) : "—")}
    ${card("Source documents read", nf.format(urls.size))}
    ${card("Run", `${esc(env.run?.run_id || env.run_id || "")} · ${esc(env.state || "")}`)}
    ${card("Collected", esc(fmtTime(env.run?.completed_at)))}
    ${card("Material changes", ((env.synthesis || {}).changes || []).length ? nf.format(env.synthesis.changes.length) + " found" : (env.refresh || {}).previous_run_id ? "none since previous run" : "first observation")}
    ${card("Run errors", nf.format((env.errors || []).length))}
  </div>
  ${ws.note && ws.availability !== "available" ? `<p class="small">Website: ${esc(ws.note)}.</p>` : ""}
  ${(ws.candidates || []).length && ws.availability !== "available" ? `<details><summary class="small">Website candidates that were rejected (${ws.candidates.length})</summary><ul class="small">${ws.candidates.slice(0, 5).map(c => `<li>${esc(typeof c === "string" ? c : (c.url || c.domain || JSON.stringify(c)).toString().slice(0, 160))}${typeof c === "object" && c.reason ? ` — ${esc(c.reason)}` : ""}</li>`).join("")}</ul></details>` : ""}
  <h3 class="sub">Changes since previous run</h3>${changesHtml(env)}
  <h3 class="sub">What we don't know</h3>${unknownsHtml(env)}
  ${(env.errors || []).length ? `<h3 class="sub">Run errors</h3><ul class="small">${env.errors.slice(0, 8).map(e => `<li>${esc(e.step || "")}: ${esc(String(e.error || JSON.stringify(e)).slice(0, 200))}</li>`).join("")}</ul>` : ""}`;
}
function sourcesFoundHtml(env, row) {
  const cov = row.cov || [];
  const byHost = {};
  const em = evMap(env);
  for (const c of env.claims || []) {
    if (c.availability !== "available") continue;
    for (const id of c.evidence_ids || []) {
      const e = em[id]; if (!e || !safeUrl(e.source_url)) continue;
      let h = ""; try { h = new URL(e.source_url).hostname.replace(/^www\./, ""); } catch (x) { continue; }
      const b = byHost[h] || (byHost[h] = {n: 0, urls: new Set(), cls: e.source_class, last: ""});
      b.n++; b.urls.add(e.source_url); if (String(e.retrieved_at || "") > b.last) b.last = String(e.retrieved_at || "");
    }
  }
  const hosts = Object.entries(byHost).sort((a, b) => b[1].n - a[1].n);
  return `<div class="covp">${AREAS.map((a, i) => `<div class="covr"><span>${esc(a)}</span><span class="covc ${cov[i] ? "" : "off"}">${cov[i] ? "Included" : "Not available"}</span></div>`).join("")}</div>
    <p class="small muted">This count shows areas with at least one sourced fact. It does not mean every fact was found, and it is not a rating of the company.</p>
    <h3 class="sub">Where the facts come from</h3>
    <table class="hosts"><thead><tr><th>Source</th><th>Facts</th><th>Last retrieved</th></tr></thead><tbody>${hosts.map(([h, b]) => `<tr><td><b>${esc(h)}</b><span class="note">${esc(String(b.cls || "").replace(/_/g, " "))} · ${plural(b.urls.size, "document")}</span>${[...b.urls].slice(0, 2).map(u => `<a class="note" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u.length > 80 ? u.slice(0, 77) + "…" : u)}</a>`).join("")}</td><td class="num">${b.n}</td><td>${esc(fmtDate(b.last))}</td></tr>`).join("")}</tbody></table>`;
}
const SECS = [["overview", "Overview"], ["financials", "Financials"], ["leadership", "Leadership"], ["locations", "Locations"], ["footprint", "Hiring & activity"], ["audit", "How we checked this"], ["coverage", "Sources found"]];

/* ---------------- Ask Signalpost (deterministic: question intents mapped to cited facts; no model, no network) ---------------- */
const PROMPTS = [["brief", "Company brief"], ["financials", "Latest financials"], ["leadership", "Who runs it?"], ["working", "Working here"], ["activity", "Recent activity"],
  ["locations", "Where is it?"], ["online", "Website & profiles"], ["verify", "Is this the right company?"], ["unknown", "What is missing?"]];
const INTENTS = [
  ["status", /bankrupt|insolv|liquidat|wound up|dissolv|deleted|still active|is it active|konkurs|avvikl|slettet/],
  ["changes", /chang|differ|since last|new since|refresh|endring/],
  ["unknown", /unknown|missing|don.?t (you )?know|not found|gaps?\b|mangler/],
  ["peers", /peer|competitor|benchmark|industry average|similar compan|how does it compare|rank/],
  ["financials", /revenue|turnover|sales|profit|loss|result|financ|account|filing|doing|earn|money|equity|debt|assets|margin|growth|income|omsetning|resultat|regnskap|egenkapital|gjeld|overskudd|underskudd/],
  ["leadership", /who runs|who owns|run it|runs the|lead|ceo|chief|manag|director|board|chair|owner|auditor|accountant|daglig|styre|eier|revisor|people/],
  ["working", /work|job|hir|career|recruit|vacanc|employ|staff|people work|salary|ansatt|stilling|jobb/],
  ["locations", /where|locat|address|office|workplace|branch|town|city|municip|based|adresse|kontor|kommune/],
  ["online", /website|web site|\bsite|homepage|url|social|linkedin|facebook|instagram|twitter|youtube|tiktok|profile|online|nettside/],
  ["activity", /recent|news|publish|activit|latest|announce|update|tender|contract|press|post|nyhet|anbud/],
  ["verify", /verif|trust|right company|correct company|same company|proof|prove|identity|source|evidence|how do you know|reliab|accurate/],
  ["founded", /found|establish|how old|\bage\b|since when|start|registered when|stiftet/],
  ["what", /what (does|do|is)|industry|sector|business|purpose|do they do|bransje|formål/],
  ["brief", /brief|overview|summary|summar|about|tell me|describe|who are/],
];
/* Things no source in this profile can answer: say so first, then show what the sources do say on the nearest topic. */
const OUT_OF_SCOPE = /colou?r|favou?rite|salary|salaries|\bpay\b|wage|bonus|culture|review|rating|glassdoor|opinion|reputation|share price|stock|valuation|worth buying|phone number|e-?mail|private|married|forecast|predict|future|next year|will it|should i (invest|buy)|lonn|kultur/;
function intentOf(q) {
  const t = fold(q);
  for (const [k, re] of INTENTS) if (re.test(t)) return k;
  return null;
}
function answer(env, intent) {
  const S = (env.synthesis || {}).sentences || [], U = (env.synthesis || {}).unknowns || [];
  const name = env.identity?.name || env.organisation_number;
  const out = {title: "", items: [], missing: []};
  const say = (html, ids, refs) => out.items.push({html, ids: (ids || []).filter(Boolean), refs: refs || []});
  const miss = (c, fallback) => out.missing.push({text: c ? `${LABEL[c.field] || c.field}: ${c.note || STATE_HELP[c.availability] || ""}` : fallback, st: c ? c.availability : "not_available", ids: hasSrc(c) ? [c.id] : []});
  const sent = topic => S.filter(x => x.topic === topic);
  const unk = topic => U.filter(x => x.topic === topic).forEach(u => out.missing.push({text: u.text + (u.reason ? ` (${u.reason})` : ""), st: u.availability, ids: u.claim_ids || []}));
  switch (intent) {
    case "brief": {
      out.title = "Company brief";
      if ((env.synthesis || {}).headline) say(`<b>${esc(env.synthesis.headline)}</b>`, (env.synthesis.supported_claim_ids || []).slice(0, 0));
      for (const t of ["identity", "activity", "status", "accounts", "leadership", "online", "hiring", "activity_dated"]) { const x = sent(t)[0]; if (x && out.items.length < 6) say(esc(x.text), x.claim_ids, x.reference_ids); }
      if (!out.items.length) out.missing.push({text: "No verified facts for this company in this run.", st: "not_available", ids: []});
      break;
    }
    case "financials": {
      out.title = "Latest filed financials";
      const r = latestFin(env, "revenue"), o = latestFin(env, "operating_result"), n = latestFin(env, "annual_result"), e = latestFin(env, "equity"), a = latestFin(env, "assets"), d = latestFin(env, "debt");
      const any = r || o || n || e || a;
      if (!any) { (env.claims || []).filter(c => FIN.includes(c.field)).slice(0, 2).forEach(c => miss(c)); if (!out.missing.length) out.missing.push({text: "No annual accounts for this entity in the accounts register" + ((env.identity || {}).legal_form === "ENK" ? " (sole proprietorships do not file accounts)" : ""), st: "not_available", ids: []}); break; }
      const yr = yearOf(any);
      say(`For the ${esc(yr)} accounts (period ${esc(any.reporting_period || "")}):`, []);
      [[r, "Revenue"], [o, "Operating result"], [n, "Net result"], [e, "Equity"], [a, "Total assets"], [d, "Debt"]].forEach(([c, l]) => { if (c) say(`${l}: <b>${fmtMoney(c.value, c.unit)}</b>${yearOf(c) !== yr ? ` (${esc(yearOf(c))})` : ""}`, [c.id]); });
      if (!r) { const g = (env.claims || []).find(c => c.field === "revenue" && c.availability !== "available" && String(c.reporting_period) === String(any.reporting_period)); if (g) miss(g); }
      const p = prevFin(env, "revenue", r);
      if (r && p && Number(p.value) > 0) say(`Revenue changed by <b>${((r.value - p.value) / p.value * 100).toFixed(0)}%</b> from ${esc(yearOf(p))} (${fmtMoney(p.value, p.unit)}) to ${esc(yearOf(r))}.`, [p.id, r.id]);
      if (e && a && Number(a.value) > 0) say(`Equity ratio (equity ÷ total assets): <b>${(e.value / a.value * 100).toFixed(0)}%</b>.`, [e.id, a.id]);
      const ps = peerSentence(env); if (ps) say(esc(ps.text), ps.claim_ids, ps.reference_ids);
      S.filter(x => x.topic === "accounts" && x.flag === "warning").forEach(x => say(`⚠ ${esc(x.text)}`, x.claim_ids));
      break;
    }
    case "leadership": {
      out.title = "Who runs it";
      const roles = avail(env, "role").slice().sort((a, b) => ((ROLE_ORDER.indexOf(a.value.role_code) + 1) || 99) - ((ROLE_ORDER.indexOf(b.value.role_code) + 1) || 99));
      roles.slice(0, 8).forEach(c => say(`<b>${esc(personName(c.value) || c.value.organisation_number)}</b> — ${esc(ROLE_EN[c.value.role_code] || c.value.role)}${c.value.last_changed ? ` <span class="muted">(registered ${esc(c.value.last_changed)})</span>` : ""}`, [c.id]));
      if (roles.length > 8) say(`${roles.length - 8} more roles are listed in the Leadership section.`, []);
      if (!roles.length) { miss(gapOf(env, "role"), "No public roles in the register."); unk("leadership"); }
      const g = firstOf(env, "group_structure"); if (g) say(`It is part of a registered group structure (see Leadership).`, [g.id]);
      break;
    }
    case "working": {
      out.title = "What the sources say about working here";
      const jobs = avail(env, "job_posting"), emp = firstOf(env, "registered_employees"), wps = avail(env, "registered_workplace");
      if (jobs.length) { say(`${plural(jobs.length, "active job ad")} found:`, []); jobs.slice(0, 6).forEach(c => say(`${esc(c.value.title || "Job ad")}${(c.value.locations || []).length ? ` — ${esc(c.value.locations.join("; "))}` : ""}${c.value.expires ? ` <span class="muted">(apply by ${esc(c.value.expires)})</span>` : ""}`, [c.id])); }
      else miss(gapOf(env, "job_posting"), "No active job ad found.");
      if (emp) say(`The register lists <b>${plural(emp.value, "employee")}</b>.`, [emp.id]); else miss(gapOf(env, "registered_employees"), "Employee count not registered.");
      if (wps.length) say(`${plural(wps.length, "registered workplace")}: ${esc(wps.slice(0, 4).map(c => c.value.name + (c.value.address && c.value.address.poststed ? " (" + c.value.address.poststed + ")" : "")).join("; "))}${wps.length > 4 ? " …" : ""}.`, wps.slice(0, 4).map(c => c.id));
      const d = ((env.synthesis || {}).decision || []).find(b => b.audience === "apply"); if (d) (d.points || []).slice(0, 3).forEach(p => say(esc(p.text[0].toUpperCase() + p.text.slice(1)), p.claim_ids, p.reference_ids));
      out.note = "Signalpost uses no employee-review sites, so it cannot judge culture or pay. No job ad found does not prove the company is not hiring.";
      break;
    }
    case "activity": {
      out.title = "Recent activity";
      const acts = avail(env, "dated_activity").slice().sort((a, b) => String(b.value.date || "").localeCompare(String(a.value.date || "")));
      const own = acts.filter(c => !isRoutine(c)), reg = acts.filter(isRoutine);
      own.slice(0, 5).forEach(c => say(`<b>${esc(fmtDate(c.value.date))}</b> · ${esc(KIND[c.value.kind] || "")}: ${esc(c.value.title || "")}`, [c.id]));
      if (!own.length) out.missing.push({text: "No dated news, posts or public contracts found for this company", st: "not_available", ids: []});
      reg.slice(0, own.length ? 2 : 4).forEach(c => say(`<b>${esc(fmtDate(c.value.date))}</b> · register: ${esc(String(c.value.title || "").replace(/^Register update: /, ""))}`, [c.id]));
      const x = sent("activity_dated")[0]; if (x) say(esc(x.text), x.claim_ids);
      break;
    }
    case "locations": {
      out.title = "Where it operates";
      const a = firstOf(env, "business_address"); if (a) say(`Business address: <b>${esc(addr(a.value))}</b>`, [a.id]); else miss(gapOf(env, "business_address"), "No business address in the register.");
      const wps = avail(env, "registered_workplace");
      wps.slice(0, 6).forEach(c => say(`Workplace: <b>${esc(c.value.name || "")}</b> — ${esc(addr(c.value.address))}${c.value.employees != null ? ` (${plural(c.value.employees, "employee")})` : ""}`, [c.id]));
      if (wps.length > 6) say(`${wps.length - 6} more workplaces in the Locations section.`, []);
      if (!wps.length) miss(gapOf(env, "registered_workplace"), "No registered workplaces.");
      break;
    }
    case "online": {
      out.title = "Website and company-owned profiles";
      const s = firstOf(env, "official_website");
      if (s) say(`Official website: <a href="${esc(s.value)}" target="_blank" rel="noopener noreferrer">${esc(s.value)}</a>. Verified because ${esc(proofText(s.identity_method))}.`, [s.id]); else miss(gapOf(env, "official_website"), "No company-owned website found.");
      const p = avail(env, "social_profile"); if (p.length) say(`Profiles linked from that website: ${p.map(c => esc(c.value.platform)).join(", ")}.`, p.map(c => c.id)); else miss(gapOf(env, "social_profile"), "No company-owned profiles found.");
      const b = firstOf(env, "public_brand"); if (b) say(`Public brand on the website: <b>${esc(b.value)}</b>.`, [b.id]);
      break;
    }
    case "verify": {
      out.title = "Is this the right company?";
      const o = firstOf(env, "organisation_number"), l = firstOf(env, "legal_name");
      if (o) say(`Every fact on this page is tied to organisation number <b>${esc(o.value)}</b>${l ? ` (${esc(l.value)})` : ""}, read from the official register.`, [o.id, l && l.id]);
      const s = firstOf(env, "official_website");
      if (s) say(`The website is published only because ${esc(proofText(s.identity_method))}.`, [s.id]);
      else { const g = gapOf(env, "official_website"); if (g) say(`No website is published: ${esc(g.note || STATE_HELP[g.availability])}. Unproven candidates are withheld rather than guessed.`, hasSrc(g) ? [g.id] : []); }
      say(`Data collected ${esc(fmtTime(env.run?.completed_at))} in run ${esc(env.run?.run_id || "")}. Each “source” button shows the URL, retrieval time, reporting period and the quoted text.`, []);
      break;
    }
    case "unknown": {
      out.title = "What is missing";
      U.forEach(u => out.missing.push({text: u.text + (u.reason ? ` (${u.reason})` : ""), st: u.availability, ids: u.claim_ids || []}));
      if (!U.length) say("Nothing is flagged as unknown for this company.", []);
      break;
    }
    case "changes": {
      out.title = "Changes since the previous run";
      const ch = (env.synthesis || {}).changes || [];
      ch.forEach(c => say(esc(c.text), c.claim_ids));
      if (!ch.length) say((env.refresh || {}).previous_run_id ? `No material changes since run ${esc(env.refresh.previous_run_id)}.` : "This is the first observation of this company, so there is nothing to compare against.", []);
      break;
    }
    case "peers": {
      out.title = "Compared with peers";
      const ps = peerSentence(env), mk = sent("market")[0];
      if (ps) say(esc(ps.text), ps.claim_ids, ps.reference_ids); else out.missing.push({text: "No peer comparison (no filed accounts, or too few comparable companies in the reference sample)", st: "not_available", ids: []});
      if (mk) say(esc(mk.text), mk.claim_ids, mk.reference_ids);
      out.note = "To compare with a specific company, use “Compare with…” at the top of this page.";
      break;
    }
    case "status": {
      out.title = "Is it active?";
      const del = firstOf(env, "deleted_from_register"), b = firstOf(env, "bankrupt"), q = firstOf(env, "under_liquidation");
      if (del) say(`⚠ The register reports this entity as <b>deleted</b> on ${esc(del.value)}.`, [del.id]);
      if (b) say(b.value ? "⚠ The register reports it as <b>bankrupt</b>." : "No bankruptcy is registered.", [b.id]);
      if (q) say(q.value ? "⚠ The register reports it as <b>under liquidation</b>." : "No liquidation is registered.", [q.id]);
      sent("status").forEach(x => say(esc(x.text), x.claim_ids));
      if (!out.items.length) miss(gapOf(env, "bankrupt"), "Status not available.");
      break;
    }
    case "founded": {
      out.title = "When it was founded";
      const f = firstOf(env, "founded_date"), r = firstOf(env, "registry_registration_date");
      if (f) say(`Founded <b>${esc(f.value)}</b>.`, [f.id]); else miss(gapOf(env, "founded_date"), "Founding date not in the register.");
      if (r) say(`Entered in the register <b>${esc(r.value)}</b>.`, [r.id]);
      break;
    }
    case "what": {
      out.title = "What the company does";
      sent("activity").forEach(x => say(esc(x.text), x.claim_ids, x.reference_ids));
      const sd = firstOf(env, "self_description"); if (sd) say(`On its website: “${esc(String(sd.value).slice(0, 300))}”`, [sd.id]);
      if (!out.items.length) { const i = firstOf(env, "industry"); if (i) say(`Registered industry: ${esc(i.value.kode)} ${esc(i.value.beskrivelse)}.`, [i.id]); else miss(gapOf(env, "industry"), "No industry in the register."); }
      break;
    }
    default:
      out.title = "Not enough evidence";
      out.missing.push({text: "This profile's sources do not answer that question. Try one of the questions above, or rephrase it using words like revenue, board, jobs, address, website or news", st: "not_available", ids: []});
  }
  return out;
}
function answerHtml(env, intent, q) {
  const a = answer(env, intent);
  const supported = a.items.some(i => i.ids.length || i.refs.length);
  const oos = q && intent !== "none" && OUT_OF_SCOPE.test(fold(q));
  return `${oos ? `<div class="miss"><b>Not in the sources.</b> Nothing on this profile answers “${esc(q)}” directly, so Signalpost does not guess. Here is what the sources do say about ${esc(a.title.toLowerCase())}:</div>` : ""}
    <h3>${esc(a.title)}</h3>${q && !oos ? `<p class="small muted">You asked: “${esc(q)}”</p>` : ""}
    ${a.items.map(i => `<p>${i.html}${citeBtn(i.ids, i.refs, i.html.replace(/<[^>]+>/g, ""))}</p>`).join("")}
    ${a.missing.length ? `<div class="miss"><b>Not found</b><ul>${a.missing.map(m => `<li>${stateBadge(m.st)} ${esc(m.text)}${(m.ids || []).length ? `<button class="srcbtn" data-cite="${esc(m.ids.join(","))}">what was checked</button>` : ""}</li>`).join("")}</ul></div>` : ""}
    ${a.note ? `<p class="small muted">${esc(a.note)}</p>` : ""}
    <p class="small muted">${supported ? "Each “source” button opens the exact document behind that sentence." : ""}</p>`;
}
function askHtml(env, intent) {
  return `<aside class="card ask" id="sec-ask" aria-labelledby="ask-h"><div class="overline">Company research assistant</div><h2 id="ask-h">Ask Signalpost</h2>
    <p class="small muted">Ask what the company does, who runs it, where it operates, how the latest filing looks, or whether it is hiring.</p>
    <form id="askf" class="askf"><input id="askq" type="text" placeholder="e.g. Is it hiring? Who is the CEO?" aria-label="Ask about this company" autocomplete="off" enterkeyhint="send"><button class="btn primary sm" type="submit">Check the evidence</button></form>
    <div class="prompts">${PROMPTS.map(([k, t]) => `<button class="prompt" type="button" data-ask="${k}" aria-pressed="${k === intent}">${esc(t)}</button>`).join("")}</div>
    <div class="answer" id="answer" aria-live="polite">${answerHtml(env, intent || "brief")}</div>
    <p class="agent-foot small">Every answer is built only from the sources on this profile, by fixed rules (no AI model). Missing evidence stays missing.</p></aside>`;
}

/* ---------------- company page ---------------- */
let current = null;
async function renderCompany(org, sub, arg) {
  if (!current || current.org !== org) {
    $("#app").innerHTML = `<p class="muted">Loading ${esc(org)}…</p>`;
    const env = await load(org);
    if (!env) { current = null; $("#app").innerHTML = `<div class="empty">No company with organisation number ${esc(org)} in this run. <a href="#/">Back to search</a></div>`; return; }
    current = {org, env};
    drawCompany(env, sub === "ask" ? arg : null);
  }
  const env = current.env;
  if (sub === "source" && arg) { const ids = arg.split(",").filter(id => claimMap(env)[id]); if (ids.length) openSheet(env, ids, ids.length === 1 ? `Source: ${LABEL[claimMap(env)[ids[0]].field] || "fact"}` : "Sources", [], `/c/${org}`); }
  else if (sub === "ask" && arg) { setAnswer(env, arg); jump("ask"); }
  else if (sub && SECS.some(s => s[0] === sub)) jump(sub);
  else window.scrollTo(0, 0);
}
function jump(sec) { const el = document.getElementById("sec-" + sec); if (el) { el.querySelectorAll("details.secd").forEach(d => { d.open = true; }); el.scrollIntoView({block: "start"}); } }
function setAnswer(env, intent, q) {
  const box = $("#answer"); if (!box) return;
  box.innerHTML = answerHtml(env, intent, q);
  document.querySelectorAll(".prompt").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.ask === intent)));
}
function drawCompany(env, intent) {
  const org = env.organisation_number;
  (env.claims || []).forEach(c => { c._org = org; });
  const id = env.identity || {}, row = ROW[org] || {cov: []};
  document.title = `${id.name || org} — Signalpost`;
  const site = firstOf(env, "official_website");
  const n = covN(row.cov);
  const warn = (row.b || {}).warn;
  $("#app").innerHTML = `<nav class="crumbs"><a href="#/">← All companies</a></nav>
  <div class="hero card"><div class="htop"><div class="hmain">
    <div class="overline">${esc(row.ie || row.il || "Norwegian company")}</div>
    <h1>${esc(id.name || org)}</h1>
    <div class="id">Organisation number <b>${esc(org)}</b> · ${esc(FORM[id.legal_form] || id.legal_form_description || id.legal_form || "")}${row.k ? " · " + esc(row.k) : ""}
      <button class="covpill ${n < 4 ? "mid" : ""} linkish" data-jump="coverage" title="Data found in ${n} of the ${AREAS.length} areas (company record, financials, people & locations, website, hiring & public activity). Not a rating.">${covDots(row.cov)} data found in ${n} of ${AREAS.length} areas</button></div>
    ${warn ? `<p class="warnline">⚠ ${esc(warn)}</p>` : ""}
    ${(env.synthesis || {}).headline ? `<p class="deck">${esc(env.synthesis.headline)}</p>` : ""}
    <div class="fresh small muted">Last checked ${esc(fmtTime(env.run?.completed_at))}${env.state && env.state !== "completed" ? ` · <span class="st ${env.state === "partial" ? "ambiguous" : "failed"}">${esc(env.state)} run</span>` : ""} · tap any value to see its source</div>
  </div></div>
  ${tilesHtml(env)}
  <div class="tools">${pickBtn(org)}<div class="cmpwith"><label for="cw" class="skip">Compare with another company</label><input id="cw" type="search" placeholder="Compare with…" autocomplete="off" data-base="${org}"><div id="cwres" class="cwres"></div></div>
    ${site && safeUrl(site.value) ? `<a class="btn sm" href="${esc(site.value)}" target="_blank" rel="noopener noreferrer">Website ↗</a>` : ""}
    <a class="btn sm" href="https://data.brreg.no/enhetsregisteret/oppslag/enheter/${esc(org)}" target="_blank" rel="noopener noreferrer">Official register ↗</a></div></div>
  <nav class="secnav" aria-label="Company profile sections"><span class="small muted">Jump to:</span>${SECS.map(([k, t]) => `<button data-jump="${k}">${esc(t)}</button>`).join("")}<button data-jump="ask">Ask a question</button></nav>
  <div class="pgrid">
    <div class="pmain">
      <section class="card sec" id="sec-overview"><div class="sh"><h2>Company overview</h2><span class="kick">What the company does · built only from the cited facts</span></div>${summaryHtml(env)}
        <details class="decd"><summary class="sumh">Before you decide</summary>${decisionHtml(env)}</details>
        <details class="regf"><summary class="sumh">All register facts</summary><table>${factRows(env, (env.claims || []).filter(c => c.section === "legal_identity"))}</table></details></section>
      <section class="card sec" id="sec-financials"><div class="sh"><h2>Financial snapshot</h2><span class="kick">Latest submitted annual accounts and history ${stateBadge(env.sections?.annual_accounts?.availability || "not_available")}</span></div>${finTable(env)}
        <h3 class="sub">Compared with peers</h3>${peersHtml(env)}</section>
      <section class="card sec" id="sec-leadership"><div class="sh"><h2>Leadership</h2><span class="kick">Public roles in the official register</span></div>${leadershipHtml(env)}</section>
      <section class="card sec" id="sec-locations"><div class="sh"><h2>Locations</h2><span class="kick">Registered operating presence</span></div>${locationsHtml(env)}</section>
      <section class="card sec" id="sec-footprint"><div class="sh"><h2>Hiring &amp; public activity</h2><span class="kick">Verified company pages, job ads and dated activity</span></div>${footprintHtml(env)}</section>
      <section class="card sec" id="sec-audit"><div class="sh"><h2>How we checked this</h2><span class="kick">Run &amp; identity evidence: why facts were published or withheld</span></div><details class="secd"><summary class="small">Show how this profile was collected and checked</summary>${auditHtml(env, row)}</details></section>
      <section class="card sec" id="sec-coverage"><div class="sh"><h2>Sources found</h2><span class="kick">${n} of ${AREAS.length} data areas</span></div>${sourcesFoundHtml(env, row)}</section>
      <p class="small muted">Summary method: ${esc((env.synthesis || {}).method || "none")}.</p>
    </div>
    ${askHtml(env, intent)}
  </div>`;
  $("#app").onclick = ev => {
    const j = ev.target.closest("[data-jump]"); if (j) { history.replaceState(null, "", `#/c/${org}/${j.dataset.jump}`); jump(j.dataset.jump); return; }
    const a = ev.target.closest("[data-ask]"); if (a) { setAnswer(env, a.dataset.ask); history.replaceState(null, "", `#/c/${org}/ask/${a.dataset.ask}`); return; }
    const t = ev.target.closest("[data-src],[data-cite]");
    if (!t) return;
    const ids = (t.dataset.src || t.dataset.cite).split(",").filter(Boolean);
    const c = claimMap(env)[ids[0]];
    openSheet(env, ids, t.dataset.src && ids.length === 1 ? `Source: ${LABEL[c?.field] || "fact"}` : "Sources for this statement", (t.dataset.ref || "").split(",").filter(Boolean), `/c/${org}`);
  };
  $("#askf").onsubmit = ev => {
    ev.preventDefault();
    const q = $("#askq").value.trim(); if (!q) return;
    const it = intentOf(q) || "none";
    setAnswer(env, it, q);
    if (it !== "none") history.replaceState(null, "", `#/c/${org}/ask/${it}`);
  };
}

/* ---------------- compare page ---------------- */
function cmpRows(env) {
  const latest = f => latestFin(env, f);
  const role = code => (env.claims || []).find(c => c.field === "role" && c.availability === "available" && c.value?.role_code === code) || null;
  const rev = latest("revenue"), prev = prevFin(env, "revenue", rev);
  const eq = latest("equity"), as = latest("assets"), op = latest("operating_result");
  const g = (c, f) => c ? {c, html: f ? f(c) : fmtVal(c)} : null;
  const year = c => c && c.reporting_period ? ` <span class="muted small">(${esc(yearOf(c))})</span>` : "";
  const many = f => { const cs = avail(env, f); return cs.length ? {c: cs[0], ids: cs.map(c => c.id).slice(0, 60), html: `${cs.length}${f === "dated_activity" ? `, latest ${esc(String(cs.map(c => c.value?.date || "").sort().pop()).slice(0, 10))}` : ""}`} : null; };
  const gap = f => { const c = gapOf(env, f); return c ? {c, html: /not.?run/.test(c.note || "") ? `<span class="st failed" title="This source was not checked in this run">not checked</span>` : stateBadge(c.availability)} : null; };
  const row = ROW[env.organisation_number] || {};
  const own = avail(env, "dated_activity").filter(c => !isRoutine(c));
  return {
    "Data found": [["Data areas", {c: null, html: `${covDots(row.cov)} <b>${covN(row.cov)} of ${AREAS.length}</b>`}], ["Missing areas", {c: null, html: AREAS.filter((a, i) => !(row.cov || [])[i]).map(esc).join(", ") || "none"}]],
    "Identity": [["Organisation number", g(firstOf(env, "organisation_number"))], ["Legal form", g(firstOf(env, "legal_form"), c => esc(FORM[c.value] || c.value))], ["Municipality", g(firstOf(env, "business_address"), c => esc(tcase(c.value.kommune) || addr(c.value)))],
      ["Industry", g(firstOf(env, "industry"))], ["Founded", g(firstOf(env, "founded_date")) || gap("founded_date")], ["Employees (register)", g(firstOf(env, "registered_employees")) || gap("registered_employees")]],
    "Latest accounts": [["Revenue", g(rev, c => fmtMoney(c.value, c.unit) + year(c)) || gap("revenue")],
      ["Revenue growth", rev && prev && Number(prev.value) > 0 ? {c: rev, ids: [prev.id, rev.id], html: `${((rev.value - prev.value) / prev.value * 100).toFixed(0)}% <span class="muted small">vs ${esc(yearOf(prev))}</span>`} : null],
      ["Operating result", g(op, c => fmtMoney(c.value, c.unit) + year(c)) || gap("operating_result")],
      ["Net result", g(latest("annual_result"), c => fmtMoney(c.value, c.unit) + year(c)) || gap("annual_result")],
      ["Equity", g(eq, c => fmtMoney(c.value, c.unit) + year(c)) || gap("equity")],
      ["Total assets", g(as, c => fmtMoney(c.value, c.unit) + year(c)) || gap("assets")],
      ["Equity ratio", eq && as && Number(as.value) > 0 ? {c: eq, ids: [eq.id, as.id], html: `${(eq.value / as.value * 100).toFixed(0)}%`} : null],
      ["Operating margin", rev && op && Number(rev.value) > 0 ? {c: rev, ids: [op.id, rev.id], html: `${(op.value / rev.value * 100).toFixed(1)}%`} : null]],
    "Position among peers": Object.keys(PEER_LBL).map(k => { const s = peerSentence(env); const p = s && (s.peer_positions || {})[k];
      return [PEER_LBL[k], p ? {c: null, ids: s.claim_ids, refs: s.reference_ids, html: `${esc(RANK(p.percentile))} <span class="muted small">(higher than about ${p.percentile}% of ${p.n} peers)</span>`} : null]; }),
    "People": [["Managing director", g(role("DAGL"), c => esc(personName(c.value)))], ["Board chair", g(role("LEDE"), c => esc(personName(c.value)))],
      ["Owner (sole prop.)", g(role("INNH"), c => esc(personName(c.value)))], ["Auditor", g(role("REVI"), c => esc(personName(c.value) || c.value.organisation_number))], ["Workplaces", many("registered_workplace")]],
    "Online, hiring, activity": [["Website", g(firstOf(env, "official_website")) || gap("official_website")], ["Profiles", many("social_profile") || gap("social_profile")],
      ["Active job ads", many("job_posting") || gap("job_posting")], ["Company news & contracts", own.length ? {c: own[0], ids: own.map(c => c.id).slice(0, 60), html: `${own.length}, latest ${esc(own.map(c => String(c.value.date || "")).sort().pop().slice(0, 10))}`} : {c: null, html: stateBadge("not_available")}],
      ["All dated activity", many("dated_activity") || gap("dated_activity")]],
    "Refresh": [["Changes since previous run", {c: null, html: String(((env.synthesis || {}).changes || []).length)}], ["Unknowns", {c: null, html: `${((env.synthesis || {}).unknowns || []).length} <a class="small" href="#/c/${env.organisation_number}/audit">see list</a>`}],
      ["Data collected", {c: null, html: esc(fmtTime(env.run?.completed_at))}]],
  };
}
function keyDiffs(ea, eb) {
  const nm = e => esc(String(e.identity?.name || e.organisation_number).replace(/\s+(AS|ASA|SA|ANS|DA|IKS|KS)$/i, ""));
  const out = [];
  const money = c => c ? `${shortMoney(c.value, c.unit)} (${esc(yearOf(c))})` : "not filed";
  const ra = latestFin(ea, "revenue"), rb = latestFin(eb, "revenue");
  if (ra || rb) {
    let t = `<b>Revenue:</b> ${nm(ea)} ${money(ra)} · ${nm(eb)} ${money(rb)}`;
    if (ra && rb && ra.value > 0 && rb.value > 0) { const big = ra.value >= rb.value, r = big ? ra.value / rb.value : rb.value / ra.value; if (r >= 1.5) t += ` — ${big ? nm(ea) : nm(eb)} is about ${r >= 10 ? nf.format(Math.round(r)) : r.toFixed(1)}× larger`; }
    out.push(t);
  }
  const mg = e => { const r = latestFin(e, "revenue"), o = latestFin(e, "operating_result"); return r && o && r.value > 0 ? `${(o.value / r.value * 100).toFixed(1)}%` : "—"; };
  if (ra || rb) out.push(`<b>Operating margin:</b> ${nm(ea)} ${mg(ea)} · ${nm(eb)} ${mg(eb)}`);
  const emp = e => { const c = firstOf(e, "registered_employees"); return c ? nf.format(c.value) : "not registered"; };
  out.push(`<b>Employees (register):</b> ${nm(ea)} ${emp(ea)} · ${nm(eb)} ${emp(eb)}`);
  const jobs = e => avail(e, "job_posting").length;
  out.push(`<b>Hiring now:</b> ${nm(ea)} ${jobs(ea) ? plural(jobs(ea), "job ad") : "no job ads found"} · ${nm(eb)} ${jobs(eb) ? plural(jobs(eb), "job ad") : "no job ads found"}`);
  const web = e => firstOf(e, "official_website") ? "verified website" : "no verified website";
  out.push(`<b>Online:</b> ${nm(ea)} ${web(ea)} · ${nm(eb)} ${web(eb)}`);
  const ca = covN((ROW[ea.organisation_number] || {}).cov), cb = covN((ROW[eb.organisation_number] || {}).cov);
  out.push(`<b>Data found:</b> ${nm(ea)} ${ca} of ${AREAS.length} areas · ${nm(eb)} ${cb} of ${AREAS.length} areas`);
  return `<div class="keyd"><h3>Key differences</h3><ul>${out.map(x => `<li>${x}</li>`).join("")}</ul><p class="small muted">Every value is in the table below with its source.</p></div>`;
}
const HIGHER_ROWS = new Set(["Revenue", "Operating result", "Net result", "Equity", "Total assets", "Employees (register)", "Workplaces", "Active job ads"]);
function higher(lbl, x, y) {
  if (!HIGHER_ROWS.has(lbl) || !x || !y || !x.c || !y.c || x.c.availability !== "available" || y.c.availability !== "available") return "";
  const n = o => FIN.includes(o.c.field) || o.c.field === "registered_employees" ? Number(o.c.value) : (o.ids || []).length;
  const va = n(x), vb = n(y);
  return isNaN(va) || isNaN(vb) || va === vb ? "" : va > vb ? "a" : "b";
}
async function renderCompare(a, b) {
  current = null;
  $("#app").innerHTML = `<p class="muted">Loading…</p>`;
  const [ea, eb] = await Promise.all([load(a), load(b)]);
  if (!ea || !eb) { $("#app").innerHTML = `<div class="empty">One of these organisation numbers is not in this run. <a href="#/">Back to search</a></div>`; return; }
  [ea, eb].forEach(e => (e.claims || []).forEach(c => { c._org = e.organisation_number; }));
  document.title = `Compare ${ea.identity?.name} and ${eb.identity?.name} — Signalpost`;
  const ra = cmpRows(ea), rb = cmpRows(eb);
  const cell = (x, side, hi) => {
    if (!x) return `<td class="muted">—</td>`;
    const ids = x.ids || (x.c && x.c.id && hasSrc(x.c) ? [x.c.id] : []);
    return `<td>${hi ? '<span class="hi" title="higher of the two">▲</span> ' : ""}${x.html}${ids.length ? ` <button class="srcbtn src" data-side="${side}" data-cite="${esc(ids.join(","))}"${(x.refs || []).length ? ` data-ref="${esc(x.refs.join(","))}"` : ""} aria-label="source">source</button>` : ""}</td>`;
  };
  const link = location.href.split("#")[0] + `#/compare/${a}/${b}`;
  const head = (e, o, other, side) => `<th scope="col"><a href="#/c/${o}">${esc(e.identity?.name || o)}</a><div class="small muted">${o}</div>
    <div class="chg-side"><label class="skip" for="ch-${side}">Replace ${esc(e.identity?.name || o)}</label><input id="ch-${side}" class="chq" type="search" placeholder="Replace…" autocomplete="off" data-side="${side}" data-other="${other}"><div class="cwres" id="chr-${side}"></div></div></th>`;
  $("#app").innerHTML = `<nav class="crumbs"><a href="#/">← All companies</a></nav>
    <div class="card cmp"><div class="sh"><h2>Side-by-side comparison</h2><span class="kick">Every value has a source button; “—” means no supported value</span></div>
    <div class="cmptools"><a class="btn sm" href="#/compare/${b}/${a}" id="swap">⇄ Swap sides</a><div class="share small"><label for="cmp-link" class="muted">Link to this comparison${location.protocol === "file:" ? " (a web link once the site is hosted)" : ""}</label><div class="shrow"><input id="cmp-link" readonly value="${esc(link)}"><button class="btn sm" data-copy="#cmp-link">Copy</button></div></div></div>
    ${keyDiffs(ea, eb)}
    <div class="cmpwrap"><table>
    <thead><tr><th class="lblh"></th>${head(ea, a, b, "a")}${head(eb, b, a, "b")}</tr></thead>
    <tbody>${Object.keys(ra).map(grp => `<tr class="grp"><th colspan="3">${esc(grp)}</th></tr>` + ra[grp].map(([lbl, x], i) => { const y = rb[grp][i][1], hi = higher(lbl, x, y);
      return `<tr><th scope="row">${esc(lbl)}</th>${cell(x, "a", hi === "a")}${cell(y, "b", hi === "b")}</tr>`; }).join("")).join("")}</tbody></table></div>
    <p class="small muted">▲ marks the higher of two numbers (not a judgement: higher debt or more employees is not automatically better).</p>
    <p class="small muted">“source” opens the document, retrieval time, reporting period and quoted text behind the value.</p></div>
    <div class="grid two">${[ea, eb].map(e => `<div class="card summary"><h2>${esc(e.identity?.name || e.organisation_number)}</h2><p class="verdict">${esc((e.synthesis || {}).headline || "")}</p>
      ${((e.synthesis || {}).decision || []).map(b => `<p class="small"><b>${esc(AUD[b.audience] || b.title)}:</b> ${esc((b.points || []).map(p => p.text).join("; "))}.</p>`).join("")}<a href="#/c/${e.organisation_number}">Full profile →</a></div>`).join("")}</div>`;
  $("#app").onclick = ev => {
    const t = ev.target.closest("[data-cite]");
    if (t) openSheet(t.dataset.side === "b" ? eb : ea, t.dataset.cite.split(",").filter(Boolean), "Source", (t.dataset.ref || "").split(",").filter(Boolean));
  };
}

/* ---------------- router + events ---------------- */
function route() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const p = decodeURIComponent(path).split("/").filter(Boolean);
  closeSheetQuiet();
  drawTray();
  if (p[0] === "c" && p[1]) { $("#q").value = ""; st.q = ""; return renderCompany(p[1].replace(/\D/g, ""), p[2], p[3]); }
  current = null;
  window.scrollTo(0, 0);
  if (p[0] === "compare" && p[1] && p[2]) { $("#q").value = ""; st.q = ""; return renderCompare(p[1].replace(/\D/g, ""), p[2].replace(/\D/g, "")); }
  $("#app").onclick = null;
  readSearchHash(qs);
  renderSearch();
}
function updateSearch() { st.limit = 50; history.replaceState(null, "", searchHash()); renderSearch(); }
function onSearchPage() { const h = location.hash; return !h || h === "#" || h.startsWith("#/?") || h === "#/"; }
document.addEventListener("click", ev => {
  const pk = ev.target.closest("[data-pick]"); if (pk) { togglePick(pk.dataset.pick); return; }
  const up = ev.target.closest("[data-unpick]"); if (up) { togglePick(up.dataset.unpick); return; }
  const f = ev.target.closest("[data-f]"); if (f) { st.f.has(f.dataset.f) ? st.f.delete(f.dataset.f) : st.f.add(f.dataset.f); updateSearch(); return; }
  const cp = ev.target.closest("[data-copy]");
  if (cp) { const inp = $(cp.dataset.copy); inp.select(); let ok = false; try { ok = document.execCommand("copy"); } catch (e) {} if (navigator.clipboard && !ok) navigator.clipboard.writeText(inp.value).then(() => {}, () => {}); cp.textContent = "Copied"; return; }
  const fs = ev.target.closest(".fsum"); if (fs) { st.fopen = !fs.parentElement.open; return; }
  if (ev.target.id === "more") { st.limit += 50; renderSearch(); return; }
  if (ev.target.id === "reset" || ev.target.id === "reset2") { st.f.clear(); st.town = st.form = st.sector = ""; updateSearch(); return; }
  if (ev.target.id === "sheet-x" || ev.target.id === "sheet-bg") closeSheet();
});
document.addEventListener("change", ev => {
  const k = {sort: "sort", form: "form", town: "town", sector: "sector"}[ev.target.id];
  if (k) { st[k] = ev.target.value; updateSearch(); }
});
document.addEventListener("keydown", ev => {
  if (ev.key === "Escape" && $("#sheet").classList.contains("open")) closeSheet();
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); $("#q").focus(); }
});
$("#q").addEventListener("input", ev => {
  st.q = ev.target.value;
  if (!onSearchPage()) { const q = st.q; history.replaceState(null, "", searchHash()); route(); $("#q").value = q; st.q = q; renderSearch(); $("#q").focus(); return; }
  updateSearch();
});
$("#q").addEventListener("keydown", ev => {
  if (ev.key === "Enter") { const first = $(".row h3 a"); if (first) location.hash = first.getAttribute("href"); }
});
const quick = (q, exclude) => q ? IDX.map(r => [r, r.o === exclude ? 0 : matches(r, q)]).filter(x => x[1]).sort((a, b) => b[1] - a[1] || rich(b[0]) - rich(a[0])).slice(0, 6).map(x => x[0]) : [];
document.addEventListener("input", ev => {
  if (ev.target.id === "cw") {
    const q = ev.target.value.trim(), base = ev.target.dataset.base;
    $("#cwres").innerHTML = quick(q, base).map(r => `<a class="cwhit" href="#/compare/${base}/${r.o}">${esc(r.n)} <span class="muted small">${r.o}${r.k ? " · " + esc(r.k) : ""}</span></a>`).join("") || (q ? `<p class="small muted">No match.</p>` : "");
  }
  if (ev.target.classList.contains("chq")) {
    const q = ev.target.value.trim(), side = ev.target.dataset.side, other = ev.target.dataset.other;
    $("#chr-" + side).innerHTML = quick(q, other).map(r => `<a class="cwhit" href="${side === "a" ? `#/compare/${r.o}/${other}` : `#/compare/${other}/${r.o}`}">${esc(r.n)} <span class="muted small">${r.o}</span></a>`).join("") || (q ? `<p class="small muted">No match.</p>` : "");
  }
});
$("#meta").textContent = `${nf.format(IDX.length)} companies`;
window.addEventListener("hashchange", route);
drawTray();
route();
