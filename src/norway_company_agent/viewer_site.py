"""Self-contained static viewer site for a batch of envelopes (no network, no CDN, opens from file://).

Layout written to <out_dir>:
  index.html          search index (one row per company) + CSS + JS inline
  data/<orgnr>.js     one envelope per company (claims, evidence, synthesis, changes), loaded on demand

Search by name, organisation number, municipality or industry; a company page with the synthesis (every sentence links to
its sources), unknowns, changes since the previous run, the accounts history and every fact with its source URL,
retrieval time and reporting period; side-by-side comparison of two companies. Responsive down to 390 px.
Output is deterministic for the same input (no build timestamps), so a rerun produces identical files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .industry_en import english_industry
from .synthesis import LEGAL_FORMS, add_synthesis, nace_sector

ASSETS = Path(__file__).resolve().parent / "viewer_assets"
DROP_KEYS = {"attempts"}
SHORT_FORM = {"AS": "AS", "ASA": "ASA"}


def _js(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _slim(env: dict) -> dict:
    out = {k: v for k, v in env.items() if k not in DROP_KEYS}
    claims = []
    for c in env.get("claims") or []:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        if isinstance(c.get("candidates"), list):
            c["candidates_checked"] = len(c["candidates"])
            c["candidates"] = c["candidates"][:5]
        claims.append(c)
    out["claims"] = claims
    out["changes"] = [{k: v for k, v in ch.items() if k != "old_evidence"} | {"previous_sources": [e.get("source_url") for e in ch.get("old_evidence") or [] if isinstance(e, dict)][:3]}
                      for ch in env.get("changes") or [] if isinstance(ch, dict)]
    errs = env.get("errors") or []
    out["errors"] = [{k: (str(v)[:300] if k == "trace" else v) for k, v in e.items()} if isinstance(e, dict) else str(e)[:300] for e in errs[:10]] if isinstance(errs, list) else []
    return out


def _first(env: dict, field: str) -> dict | None:
    for c in env.get("claims") or []:
        if isinstance(c, dict) and c.get("field") == field and c.get("availability") == "available" and c.get("value") is not None:
            return c
    return None


def _has(env: dict, field: str) -> int:
    return sum(1 for c in env.get("claims") or [] if isinstance(c, dict) and c.get("field") == field and c.get("availability") == "available" and c.get("value") is not None)


FIN_FIELDS = ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt")
# The five data areas of Builderr's sample site (company record, financials, people & locations, company website, public
# footprint). An area counts when at least one cited fact is available in it; routine register updates do not count as footprint.
AREAS = (("company", "Company record"), ("financials", "Financials"), ("people_places", "People & locations"),
         ("website", "Company website"), ("footprint", "Hiring & public activity"))


def coverage(env: dict) -> list[int]:
    """1/0 per data area in AREAS order."""
    ok = [c for c in env.get("claims") or [] if isinstance(c, dict) and c.get("availability") == "available" and c.get("value") is not None]
    fields = {c.get("field") for c in ok}
    footprint = any(c.get("field") in ("social_profile", "job_posting") or (c.get("field") == "dated_activity" and isinstance(c.get("value"), dict)
                    and c["value"].get("kind") != "official_register_event") for c in ok)
    return [int(bool(fields & {"legal_name", "organisation_number", "legal_form", "business_address"})), int(bool(fields & set(FIN_FIELDS))),
            int(bool(fields & {"role", "registered_workplace"})), int("official_website" in fields), int(footprint)]


def index_row(env: dict) -> dict:
    ident = env.get("identity") if isinstance(env.get("identity"), dict) else {}
    syn = env.get("synthesis") or {}
    org = re.sub(r"\D", "", str(env.get("organisation_number") or ""))
    form = str((_first(env, "legal_form") or {}).get("value") or ident.get("legal_form") or "")
    ind = (_first(env, "industry") or {}).get("value") or ident.get("industry") or {}
    ind = ind if isinstance(ind, dict) else {}
    addr = (_first(env, "business_address") or {}).get("value") or ident.get("business_address") or {}
    addr = addr if isinstance(addr, dict) else {}
    kommune = str(addr.get("kommune") or addr.get("poststed") or "")
    poststed = str(addr.get("poststed") or "")
    rev = None
    revs = [c for c in env.get("claims") or [] if isinstance(c, dict) and c.get("field") == "revenue" and c.get("availability") == "available"
            and isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool)]
    if revs:
        latest = [c for c in revs if c.get("latest")] or sorted(revs, key=lambda c: str(c.get("reporting_period")), reverse=True)
        c = latest[0]
        rev = [c["value"], c.get("unit") or "", str(c.get("reporting_period") or "").split("/")[-1][:4]]
    emp = (_first(env, "registered_employees") or {}).get("value")
    warn = ""
    for s in syn.get("sentences") or []:
        if s.get("flag") == "warning":
            warn = "bankrupt/liquidation" if s.get("topic") == "status" else "negative equity"
            break
    if not warn and env.get("state") in ("failed", "not_found"):
        warn = env.get("state").replace("_", " ")
    return {
        "o": org, "n": str((_first(env, "legal_name") or {}).get("value") or ident.get("name") or org), "f": form,
        "fd": LEGAL_FORMS.get(form.upper(), form), "k": kommune.title() if kommune.isupper() else kommune,
        "i": str(ind.get("kode") or ""), "il": str(ind.get("beskrivelse") or ""), "sec": nace_sector(ind.get("kode")) or "", "ie": english_industry(ind.get("kode"))[0] or "",
        "pt": (poststed.title() if poststed.isupper() else poststed) if poststed and poststed != kommune else "", "pn": str(addr.get("postnummer") or ""),
        "cov": coverage(env), "h": str(syn.get("headline") or ""), "st": str(env.get("state") or ""), "rev": rev,
        "emp": emp if isinstance(emp, (int, float)) and not isinstance(emp, bool) else None,
        "b": {"web": _has(env, "official_website"), "prof": _has(env, "social_profile"), "jobs": _has(env, "job_posting"),
              "act": _has(env, "dated_activity"), "chg": len(syn.get("changes") or []), "warn": warn},
    }


def build_site(envelopes: Iterable[dict], out_dir: Path, *, title: str = "Signalpost company profiles") -> dict:
    out_dir = Path(out_dir)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rows, seen, run_ids, skipped = [], set(), [], 0
    for env in envelopes:
        if not isinstance(env, dict):
            skipped += 1
            continue
        if not isinstance(env.get("synthesis"), dict):
            env = add_synthesis(env)
        row = index_row(env)
        if not row["o"] or row["o"] in seen:
            skipped += 1
            continue
        seen.add(row["o"])
        rid = str((env.get("run") or {}).get("run_id") or env.get("run_id") or "")
        if rid and rid not in run_ids:
            run_ids.append(rid)
        slim = _slim(env)
        slim["organisation_number"] = row["o"]
        (data_dir / f"{row['o']}.js").write_text(f"window.SP_DATA_LOAD({_js(row['o'])},{_js(slim)});\n", encoding="utf-8")
        rows.append(row)
    rows.sort(key=lambda r: r["n"].lower())
    meta = {"run_ids": ", ".join(run_ids[:3]) + (" …" if len(run_ids) > 3 else ""), "companies": len(rows),
            "all_areas": sum(1 for r in rows if sum(r["cov"]) == len(AREAS)), "areas": [a[1] for a in AREAS]}
    css = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in ("app.css", "lane11.css"))
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' file:; img-src 'self' data:; connect-src 'none'">
<meta name="referrer" content="no-referrer">
<title>{title}</title><style>{css}</style></head><body>
<a class="skip" href="#app">Skip to content</a>
<header class="top"><div class="bar"><a class="brand" href="#/">Signalpost<small id="meta"></small></a>
<div class="search" role="search"><label for="q" class="skip">Search companies</label>
<input id="q" type="search" placeholder="Name, org. no. or town" autocomplete="off" enterkeyhint="search"></div></div></header>
<main id="app" tabindex="-1"></main>
<div id="tray" class="tray" aria-live="polite"></div>
<div id="sheet-bg" class="sheet-bg"></div><aside id="sheet" class="sheet" role="dialog" aria-modal="true" aria-label="Sources"></aside>
<footer>Generated from Signalpost envelopes. Facts come only from the cited sources; summaries are deterministic templates over those facts (no AI model).
Availability states: available, not found, not applicable, ambiguous, blocked, failed. Missing is never shown as zero.</footer>
<script>window.SP_INDEX={_js(rows)};window.SP_META={_js(meta)};</script>
<script>{js}</script>
</body></html>
"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    return {"companies": len(rows), "skipped": skipped, "out_dir": str(out_dir), "index_bytes": len(html.encode("utf-8"))}


def read_jsonl(path: Path) -> list[Any]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append(None)
    return out
