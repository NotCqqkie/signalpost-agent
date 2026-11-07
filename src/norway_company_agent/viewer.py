"""Static, dependency-free HTML viewer for a batch of envelopes.

One self-contained file: search by name or organisation number, open a profile, compare two companies side by side,
and open the source behind every claim (URL, retrieval time, content hash, span). Responsive for phones.
"""
from __future__ import annotations

import json
from pathlib import Path

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost company profiles</title>
<style>
:root{--fg:#1d2433;--muted:#5d6b82;--line:#dfe4ec;--bg:#f6f8fb;--card:#fff;--accent:#1f5fbf;--ok:#1b7f4b;--warn:#a15c00;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);background:var(--bg)}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 14px;z-index:2}
h1{font-size:17px;margin:0 0 8px}input{width:100%;padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:8px}
main{display:grid;grid-template-columns:320px 1fr;gap:14px;padding:14px;max-width:1400px;margin:auto}
#list{background:var(--card);border:1px solid var(--line);border-radius:10px;max-height:calc(100vh - 120px);overflow:auto}
.row{padding:9px 12px;border-bottom:1px solid var(--line);cursor:pointer}.row:hover,.row.sel{background:#eef3fb}
.row small{color:var(--muted);display:block}.badges span{font-size:11px;padding:1px 6px;border-radius:9px;margin-right:4px;background:#eef1f5;color:var(--muted)}
.badges span.on{background:#e3f4ea;color:var(--ok)}
#panes{display:grid;grid-template-columns:1fr;gap:14px}#panes.two{grid-template-columns:1fr 1fr}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.card h2{margin:0;font-size:19px}.meta{color:var(--muted);font-size:13px;margin:2px 0 10px}
.summary{background:#f3f6fb;border-radius:8px;padding:10px;margin:8px 0 12px}
h3{font-size:14px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:5px 4px;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
td:first-child{color:var(--muted);width:34%}
.st{font-size:12px;padding:1px 7px;border-radius:9px}.available{background:#e3f4ea;color:var(--ok)}.not_available,.not_applicable{background:#eef1f5;color:var(--muted)}
.ambiguous,.blocked{background:#fff4e0;color:var(--warn)}.failed{background:#fde8e8;color:var(--bad)}
a{color:var(--accent)}button{font:inherit;border:1px solid var(--line);background:var(--card);border-radius:7px;padding:5px 10px;cursor:pointer}
.src{font-size:12px;color:var(--muted);border-left:3px solid var(--line);padding:4px 8px;margin:4px 0;display:none}.src.open{display:block}
.toolbar{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;font-size:13px;color:var(--muted)}
@media (max-width:820px){main{grid-template-columns:1fr}#list{max-height:38vh}#panes.two{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Signalpost — company profiles <span id="count" class="meta"></span></h1>
<input id="q" type="search" placeholder="Search name, organisation number, municipality, industry…" autocomplete="off">
<div class="toolbar"><span>Click a company to open it. <b>Compare:</b> tick two companies.</span><button id="clear">Clear comparison</button></div></header>
<main><div id="list"></div><div id="panes"><div class="card">Select a company.</div></div></main>
<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let picked = [];
function fam(e, f){ return e.claims.filter(c => c.family === f && c.availability === "available"); }
function badges(e){ return ["website","social_profiles","hiring","dated_activity"].map(f => `<span class="${fam(e,f).length?'on':''}">${{website:'site',social_profiles:'profiles',hiring:'jobs',dated_activity:'activity'}[f]}</span>`).join(""); }
function list(){
  const q = $("#q").value.trim().toLowerCase();
  const rows = DATA.filter(e => !q || [e.organisation_number, e.identity?.name, e.identity?.business_address?.kommune, e.identity?.industry?.beskrivelse].join(" ").toLowerCase().includes(q));
  $("#count").textContent = `${rows.length} of ${DATA.length}`;
  $("#list").innerHTML = rows.slice(0, 400).map(e => `<div class="row ${picked.includes(e.organisation_number)?'sel':''}" data-o="${e.organisation_number}">
    <input type="checkbox" ${picked.includes(e.organisation_number)?'checked':''} data-c="${e.organisation_number}" aria-label="compare"> <b>${esc(e.identity?.name || e.organisation_number)}</b>
    <small>${e.organisation_number} · ${esc(e.identity?.business_address?.kommune || "")}</small><div class="badges">${badges(e)}</div></div>`).join("");
}
function val(c){
  const v = c.value;
  if (v === null || v === undefined) return `<span class="st ${c.availability}">${c.availability}</span> ${esc(c.note || "")}`;
  if (c.family === "financials") return `${Number(v).toLocaleString("nb-NO")} ${esc(c.unit || "")} <small class="meta">${esc(c.reporting_period || "")}</small>`;
  if (typeof v !== "object") return /^https?:/.test(v) ? `<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a>` : esc(v);
  if (v.url) return `${v.date ? esc(v.date) + " · " : ""}<a href="${esc(v.url)}" target="_blank" rel="noopener noreferrer">${esc(v.title || v.platform || v.url)}</a>${v.platform && v.title ? "" : ""}`;
  if (v.role) return `${esc(v.role)}: ${esc(v.name || v.organisation_number)}`;
  if (v.adresse || v.postnummer) return esc([].concat(v.adresse || []).join(", ") + ", " + (v.postnummer || "") + " " + (v.poststed || ""));
  if (v.beskrivelse) return esc(v.kode + " " + v.beskrivelse);
  if (v.name) return esc(v.name + (v.organisation_number ? " (" + v.organisation_number + ")" : ""));
  return esc(JSON.stringify(v).slice(0, 200));
}
const LABEL = {legal_name:"Legal name",organisation_number:"Org. number",legal_form:"Legal form",business_address:"Address",industry:"Industry",registered_employees:"Employees (register)",founded_date:"Founded",registry_registration_date:"Registered",stated_activity:"Stated activity",public_brand:"Public brand",self_description:"Self-description",bankrupt:"Bankrupt",under_liquidation:"Under liquidation",revenue:"Revenue",operating_result:"Operating result",profit_before_tax:"Profit before tax",annual_result:"Annual result",assets:"Total assets",equity:"Equity",debt:"Debt",role:"Role",registered_workplace:"Workplace",group_structure:"Group",official_website:"Website",social_profile:"Profile",job_posting:"Job posting",dated_activity:"Dated activity",annual_accounts:"Annual accounts"};
const SECTIONS = [["legal_identity","Identity and brand"],["annual_accounts","Latest annual accounts"],["leadership_workplaces","Leadership and workplaces"],["website_profiles","Website and company-owned profiles"],["hiring_activity","Hiring and dated activity"]];
function card(e){
  const ev = Object.fromEntries(e.evidence.map(x => [x.id, x]));
  let h = `<h2>${esc(e.identity?.name || e.organisation_number)}</h2><div class="meta">Org. nr ${e.organisation_number} · run ${esc(e.run?.run_id)} · ${esc(e.run?.completed_at)} · state ${esc(e.state)}</div>`;
  h += `<div class="summary">${esc(e.summary?.text || "")}</div>`;
  for (const [s, title] of SECTIONS) {
    let cl = e.claims.filter(c => c.section === s);
    if (s === "annual_accounts") cl = cl.filter(c => c.latest || c.availability !== "available");
    h += `<h3>${title} <span class="st ${e.sections?.[s]?.availability}">${esc(e.sections?.[s]?.availability)}</span></h3><table>`;
    for (const c of cl.slice(0, 40)) {
      const srcs = (c.evidence_ids || []).map(i => ev[i]).filter(Boolean);
      h += `<tr><td>${esc(LABEL[c.field] || c.field)}</td><td>${val(c)} ${srcs.length ? `<button data-s="${e.organisation_number}-${c.id}">source</button>` : ""}
        <div class="src" id="s-${e.organisation_number}-${c.id}">${srcs.map(x => `<div><a href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">${esc(x.source_url)}</a><br>${esc(x.source_class)} · retrieved ${esc(x.retrieved_at)}${x.reporting_period ? " · period " + esc(x.reporting_period) : ""}<br>sha256 ${esc((x.content_sha256 || "").slice(0, 16))}… · ${esc(x.extraction_method || "")}<br>“${esc(x.claim_span || "")}”</div>`).join("")}</div></td></tr>`;
    }
    h += `</table>`;
  }
  if (e.changes?.length) h += `<h3>Changes since previous run</h3><table>${e.changes.map(c => `<tr><td>${esc(c.change_type)}</td><td>${esc(c.field)}: ${esc(JSON.stringify(c.old_value)).slice(0,120)} → ${esc(JSON.stringify(c.new_value)).slice(0,120)}</td></tr>`).join("")}</table>`;
  return `<div class="card">${h}</div>`;
}
function show(orgs){
  const es = orgs.map(o => DATA.find(e => e.organisation_number === o)).filter(Boolean);
  $("#panes").className = es.length === 2 ? "two" : "";
  $("#panes").innerHTML = es.map(card).join("") || `<div class="card">Select a company.</div>`;
}
$("#q").addEventListener("input", list);
$("#list").addEventListener("click", ev => {
  const c = ev.target.dataset.c;
  if (c) { picked = picked.includes(c) ? picked.filter(x => x !== c) : picked.concat(c).slice(-2); list(); show(picked); return; }
  const r = ev.target.closest(".row"); if (r) { if (!picked.length) show([r.dataset.o]); else { picked = picked.concat(r.dataset.o).slice(-2); list(); show(picked); } }
});
$("#panes").addEventListener("click", ev => { const s = ev.target.dataset.s; if (s) document.getElementById("s-" + s).classList.toggle("open"); });
$("#clear").addEventListener("click", () => { picked = []; list(); show([]); });
const hash = location.hash.slice(1); list(); if (hash) show(hash.split(",").slice(0, 2));
</script></body></html>"""


def write_viewer(envelopes: list[dict], path: Path) -> None:
    slim = []
    for e in envelopes:
        slim.append({k: e.get(k) for k in ("organisation_number", "identity", "state", "run", "sections", "claims", "evidence", "summary", "changes")})
    data = json.dumps(slim, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE.replace("__DATA__", data), encoding="utf-8")
