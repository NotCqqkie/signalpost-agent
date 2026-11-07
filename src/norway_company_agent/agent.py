"""Signalpost batch agent: one terminal envelope per input organisation number.

Phases (all bounded by a global deadline; every input still gets an envelope if a phase fails or times out):
  A  per company, in parallel: official BRREG modules, then website discovery/verification and a bounded crawl
  B  NAV job-feed index (built in the background during A), candidate lookup and orgnr confirmation
  C  companies with confirmed NAV ads but no verified site: try the employer homepage declared in the ad
  D  contract envelopes (claims + evidence + availability), refresh diff vs a previous snapshot, synthesis
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Any

from .domain_list import list_candidates
from .evidence import utc_now
from .site_news import site_news_profile
from .http import fetch_json
from .inputs import mod11_ok as valid_orgnr
from .footprint import LEGAL_FORMS, crawl_verified_site, discover_website, fold, mod11_ok, tokens
from .nav_jobs import BASE as NAV_BASE, NavIndex
from .net import Fetcher
from .official import fetch_official_modules
from .registry_events import add_registry_event_claims, fetch_update_events
from .ted_awards import TedAwards, add_ted_claims
from .smilefjes import SmilefjesIndex, add_smilefjes_claims, fetch_company_ratings
from .state_aid import add_state_aid_claims, fetch_state_aid
from .fin_licences import add_fin_licence_claims, fetch_fin_licences
from .rcn_projects import RcnProjects, add_rcn_claims
from .website import normalize_homepage
from .sampling import iter_bulk

AGENT_VERSION = "signalpost-agent/1.0"
STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")
BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"
BRREG_SUBUNIT = "https://data.brreg.no/enhetsregisteret/api/underenheter/{org}"


# ------------------------------------------------------------------ inputs
def read_inputs(path: str) -> tuple[list[str], list[dict]]:
    """Return (unique valid-looking organisation numbers in input order, input problems). Never raises on bad rows.
    Lane 9: tolerant decoding/parsing lives in `inputs.py` (encodings, JSON shapes, key variants, number formats)."""
    from .inputs import read_inputs as _read
    return _read(path)


def load_registry(bulk: str | None, orgs: list[str]) -> tuple[dict[str, dict], dict]:
    found: dict[str, dict] = {}
    meta: dict[str, Any] = {"bulk": None}
    if bulk and Path(bulk).exists():
        sha = hashlib.sha256(Path(bulk).read_bytes()).hexdigest()
        wanted = set(orgs)
        retrieved = utc_now()
        try:
            for rec in iter_bulk(bulk):
                if rec["organisation_number"] in wanted:
                    rec["bulk_sha256"] = sha
                    rec["bulk_retrieved_at"] = retrieved
                    found[rec["organisation_number"]] = rec
                    if len(found) == len(wanted):
                        break
            meta["bulk"] = {"path": str(bulk), "sha256": sha, "matched": len(found)}
        except Exception as exc:  # unreadable snapshot: fall back to the live API per company
            meta["bulk"] = {"path": str(bulk), "error": f"{type(exc).__name__}: {exc}"[:300]}
    return found, meta


# ------------------------------------------------------------------ evidence/claims builder
class Builder:
    def __init__(self, org: str):
        self.org = org
        self.claims: list[dict] = []
        self.evidence: list[dict] = []
        self._ev_index: dict[tuple, str] = {}

    def ev(self, source_url: str, source_class: str, retrieved_at: str | None, sha: str | None, span: str | None = None,
           method: str = "", reporting_period: str | None = None) -> str:
        key = (source_url, sha, span, reporting_period, method)
        if key in self._ev_index:
            return self._ev_index[key]
        eid = f"ev-{len(self.evidence) + 1}"
        self.evidence.append({"id": eid, "source_url": source_url, "source_class": source_class, "retrieved_at": retrieved_at,
                              "content_sha256": sha, "claim_span": (span or "")[:500] or None, "reporting_period": reporting_period,
                              "extraction_method": method or None})
        self._ev_index[key] = eid
        return eid

    def claim(self, section: str, field: str, value: Any, availability: str, evidence_ids: list[str] | None = None, *, family: str | None = None,
              key: str | None = None, confidence: float | None = None, note: str | None = None, reporting_period: str | None = None, **extra: Any) -> dict:
        assert availability in STATES
        c = {"id": f"c-{len(self.claims) + 1}", "section": section, "field": field, "family": family or field, "key": key or field,
             "value": value if availability == "available" else (value if value not in (0, 0.0) else None),
             "availability": availability, "confidence": confidence if confidence is not None else (1.0 if availability == "available" else None),
             "evidence_ids": evidence_ids or [], **({"reporting_period": reporting_period} if reporting_period else {}),
             **({"note": note} if note else {}), **extra}
        self.claims.append(c)
        return c


def bulk_value(v: Any, as_int: bool = False) -> Any:
    """A bulk-CSV cell as the live API would type it: "true"/"false" -> bool; counts (as_int) -> int."""
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        if as_int and t.isdigit():
            return int(t)
        return t
    return v


def canonical(v: Any) -> Any:
    """Representation-independent form for refresh comparisons (live API vs bulk CSV, older envelopes)."""
    if isinstance(v, dict):
        return {k: canonical(x) for k, x in sorted(v.items()) if x not in (None, "", [], {})}
    if isinstance(v, list):
        return [canonical(x) for x in v]
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        if re.fullmatch(r"-?\d+(\.0+)?", t):
            return int(float(t))
        return t
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def money(v: Any) -> str:
    try:
        return f"NOK {float(v):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return "n/a"


def add_identity(b: Builder, reg: dict | None, live_rec: dict | None) -> dict:
    live = (live_rec or {}).get("value") or {}
    raw = (reg or {}).get("raw") or {}
    if live_rec and live_rec.get("status") == "available":
        src, cls, at, sha = live_rec["source_url"], "official_registry_live", live_rec.get("retrieved_at"), live_rec.get("content_sha256")
        body = live.get("raw") or {}
    elif reg:
        src, cls, at, sha = BULK_URL + f"#organisasjonsnummer={b.org}", "official_registry_bulk", reg.get("bulk_retrieved_at"), reg.get("bulk_sha256")
        body = {}
    else:
        return {}

    def pick(live_key: str, raw_key: str) -> Any:
        v = body.get(live_key) if body else None
        if v in (None, "", [], {}):
            v = live.get(live_key)
        if v in (None, "", [], {}):
            v = bulk_value(raw.get(raw_key), as_int=raw_key == "antallAnsatte")  # lane 9: bulk CSV cells are strings; use the live API's types
        return v if v not in ("", [], {}) else None

    name = pick("navn", "navn") or live.get("name") or (reg or {}).get("name")
    form = pick("organisasjonsform", "organisasjonsform.kode")
    form_code = form.get("kode") if isinstance(form, dict) else form
    addr = live.get("business_address") or body.get("forretningsadresse")
    if not addr and raw.get("forretningsadresse.adresse") is not None:  # same keys as the live register's forretningsadresse
        addr = {k: v for k, v in {"land": raw.get("forretningsadresse.land"), "landkode": raw.get("forretningsadresse.landkode"),
                                  "postnummer": raw.get("forretningsadresse.postnummer"), "poststed": raw.get("forretningsadresse.poststed"),
                                  "adresse": [x for x in str(raw.get("forretningsadresse.adresse") or "").splitlines() if x.strip()],
                                  "kommune": raw.get("forretningsadresse.kommune"), "kommunenummer": raw.get("forretningsadresse.kommunenummer")}.items()
                if v not in (None, "", [])}
    industry = body.get("naeringskode1") or live.get("industry") or ({"kode": raw.get("naeringskode1.kode"), "beskrivelse": raw.get("naeringskode1.beskrivelse")} if raw.get("naeringskode1.kode") else None)
    ident = {
        "organisation_number": b.org, "name": name, "legal_form": form_code,
        "legal_form_description": (form.get("beskrivelse") if isinstance(form, dict) else raw.get("organisasjonsform.beskrivelse")),
        "business_address": addr, "industry": industry,
        "employees": pick("antallAnsatte", "antallAnsatte"),
        "founded": pick("stiftelsesdato", "stiftelsesdato"),
        "registered": pick("registreringsdatoEnhetsregisteret", "registreringsdatoenhetsregisteret"),
        "website": pick("hjemmeside", "hjemmeside"),
        "purpose": pick("vedtektsfestetFormaal", "vedtektsfestetFormaal"),
        "activity": pick("aktivitet", "aktivitet"),
        "bankrupt": pick("konkurs", "konkurs"), "liquidating": pick("underAvvikling", "underAvvikling"),
        "latest_submitted_accounts": pick("sisteInnsendteAarsregnskap", "sisteInnsendteAarsregnskap"),
        "parent_organisation_number": pick("overordnetEnhet", "overordnetEnhet"),
        "email": raw.get("epostadresse") or body.get("epostadresse"),
        "phones": [x for x in (raw.get("telefon"), raw.get("mobil"), body.get("telefon"), body.get("mobil")) if x],
    }
    for k in ("purpose", "activity"):
        if isinstance(ident[k], list):
            ident[k] = " ".join(str(x) for x in ident[k])
    S = "legal_identity"

    def c(field: str, value: Any, span_key: str, **kw: Any) -> None:
        if value in (None, ""):
            b.claim(S, field, None, "not_available", family="registry", key=field, note="not present in the official register record")
            return
        e = b.ev(src, cls, at, sha, f'{span_key}={json.dumps(value, ensure_ascii=False)[:300]}', "brreg_entity_field")
        b.claim(S, field, value, "available", [e], family="registry", key=field, **kw)

    c("legal_name", name, "navn")
    c("organisation_number", b.org, "organisasjonsnummer")
    c("legal_form", form_code, "organisasjonsform.kode")
    c("business_address", addr, "forretningsadresse")
    c("industry", industry, "naeringskode1")
    c("registered_employees", ident["employees"], "antallAnsatte")
    c("founded_date", ident["founded"], "stiftelsesdato")
    c("registry_registration_date", ident["registered"], "registreringsdatoEnhetsregisteret")
    c("stated_activity", ident["purpose"] or ident["activity"], "vedtektsfestetFormaal")
    c("bankrupt", ident["bankrupt"], "konkurs")
    c("under_liquidation", ident["liquidating"], "underAvvikling")
    deleted = body.get("slettedato") or ("deleted" if body.get("respons_klasse") == "SlettetEnhet" else None)
    ident["deleted"] = deleted
    if deleted:
        e = b.ev(src, cls, at, sha, f'respons_klasse={body.get("respons_klasse")} slettedato={body.get("slettedato")}', "brreg_entity_field")
        b.claim(S, "deleted_from_register", deleted, "available", [e], family="registry", key="deleted_from_register",
                note="the live register reports this entity as deleted (slettet)")
    return ident


FIN_FIELDS = ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt")


def add_financials(b: Builder, rec: dict | None) -> list[dict]:
    S = "annual_accounts"
    if not rec or rec.get("status") in (None, "not_fetched"):
        b.claim(S, "annual_accounts", None, "failed", family="financials", note="annual-accounts source not reached")
        return []
    if rec.get("status") == "not_found":
        e = b.ev(rec["source_url"], "official_annual_accounts", rec.get("retrieved_at"), rec.get("content_sha256"), "HTTP 404", "brreg_regnskap_api")
        b.claim(S, "annual_accounts", None, "not_available", [e], family="financials", note="no annual accounts published in Regnskapsregisteret's open API")
        return []
    if rec.get("status") == "unsupported_layout":
        e = b.ev(rec["source_url"], "official_annual_accounts", rec.get("retrieved_at"), rec.get("content_sha256"), str(rec.get("note") or "")[:200], "brreg_regnskap_api")
        b.claim(S, "annual_accounts", None, "not_available", [e], family="financials",
                note="Regnskapsregisteret's open API does not serve this accounts layout (e.g. insurance, bank, pension fund, non-profit); no figures published")
        return []
    if rec.get("status") != "available":
        b.claim(S, "annual_accounts", None, "failed", family="financials", note=str(rec.get("note") or "source error")[:200])
        return []
    records = (rec.get("value") or {}).get("records") or []
    if not records:
        e = b.ev(rec["source_url"], "official_annual_accounts", rec.get("retrieved_at"), rec.get("content_sha256"), "[]", "brreg_regnskap_api")
        b.claim(S, "annual_accounts", None, "not_available", [e], family="financials", note="the register returned no account records")
        return []
    records = sorted(records, key=lambda r: str((r.get("period") or {}).get("tilDato") or ""), reverse=True)
    for i, r in enumerate(records):
        period = (r.get("period") or {})
        rp = f"{period.get('fraDato')}/{period.get('tilDato')}"
        for f in FIN_FIELDS:
            v = r.get(f)
            if v is None:
                b.claim(S, f, None, "not_available", family="financials", key=f"{f}@{period.get('tilDato')}", reporting_period=rp,
                        note="field absent in the filed accounts record (not treated as zero)")
                continue
            e = b.ev(rec["source_url"], "official_annual_accounts", rec.get("retrieved_at"), rec.get("content_sha256"),
                     f"regnskap id={r.get('record_id')} type={r.get('account_type')} {f}={v} {r.get('currency') or ''}", "brreg_regnskap_api", rp)
            b.claim(S, f, v, "available", [e], family="financials", key=f"{f}@{period.get('tilDato')}", reporting_period=rp,
                    unit=r.get("currency") or "NOK", latest=i == 0, account_type=r.get("account_type"))
    return records


def add_roles_locations(b: Builder, roles_rec: dict | None, loc_rec: dict | None, group_rec: dict | None) -> tuple[list, list]:
    S = "leadership_workplaces"
    roles: list = []
    locations: list = []
    if roles_rec and roles_rec.get("status") == "available":
        roles = [r for r in (roles_rec.get("value") or {}).get("roles") or [] if not r.get("inactive")]
        if not roles:
            e = b.ev(roles_rec["source_url"], "official_roles", roles_rec.get("retrieved_at"), roles_rec.get("content_sha256"), "rollegrupper=[]", "brreg_roles_api")
            b.claim(S, "role", None, "not_available", [e], family="roles", note="no active registered roles")
        for r in roles:
            who = r.get("name") or r.get("organisation_number")
            e = b.ev(roles_rec["source_url"], "official_roles", roles_rec.get("retrieved_at"), roles_rec.get("content_sha256"),
                     f"{r.get('group')}: {r.get('role')} = {who}" + (f" (sist endret {r.get('last_changed')})" if r.get("last_changed") else ""), "brreg_roles_api")
            b.claim(S, "role", {"role": r.get("role"), "role_code": r.get("role_code"), "name": r.get("name"), "organisation_number": r.get("organisation_number"),
                                "last_changed": r.get("last_changed")}, "available", [e], family="roles", key=f"role:{r.get('role_code')}:{who}")
    elif roles_rec and roles_rec.get("status") == "not_found":
        e = b.ev(roles_rec["source_url"], "official_roles", roles_rec.get("retrieved_at"), roles_rec.get("content_sha256"), str(roles_rec.get("note") or "HTTP 404"), "brreg_roles_api")
        b.claim(S, "role", None, "not_available", [e], family="roles", note="the roles register has no record for this entity (e.g. deleted)")
    else:
        b.claim(S, "role", None, "failed", family="roles", note=str((roles_rec or {}).get("note") or "roles source not reached")[:200])
    if loc_rec and loc_rec.get("status") == "available":
        locations = (loc_rec.get("value") or {}).get("locations") or []
        if not locations:
            e = b.ev(loc_rec["source_url"], "official_subunits", loc_rec.get("retrieved_at"), loc_rec.get("content_sha256"), "underenheter=[]", "brreg_subunits_api")
            b.claim(S, "registered_workplace", None, "not_available", [e], family="workplaces", note="no registered subunits (workplaces)")
        for l in locations:
            e = b.ev(loc_rec["source_url"], "official_subunits", loc_rec.get("retrieved_at"), loc_rec.get("content_sha256"),
                     f"underenhet {l.get('organisation_number')} {l.get('name')}", "brreg_subunits_api")
            b.claim(S, "registered_workplace", {"organisation_number": l.get("organisation_number"), "name": l.get("name"), "address": l.get("address"),
                                                "employees": l.get("employees")}, "available", [e], family="workplaces", key=f"subunit:{l.get('organisation_number')}")
    elif loc_rec and loc_rec.get("status") == "not_found":
        b.claim(S, "registered_workplace", None, "not_available", family="workplaces", note="no registered subunits")
    else:
        b.claim(S, "registered_workplace", None, "failed", family="workplaces", note=str((loc_rec or {}).get("note") or "subunit source not reached")[:200])
    if group_rec and group_rec.get("status") == "available":
        body = group_rec.get("value") or {}
        e = b.ev(group_rec["source_url"], "official_group_structure", group_rec.get("retrieved_at"), group_rec.get("content_sha256"), json.dumps(body, ensure_ascii=False)[:300], "brreg_group_api")
        b.claim(S, "group_structure", body, "available", [e], family="group")
    else:
        b.claim(S, "group_structure", None, "not_available", family="group", note="no group structure published for this entity")
    return roles, locations


def add_footprint(b: Builder, site: dict | None, crawl: dict | None, jobs_nav: list[dict], nav_state: str, ident: dict) -> None:
    S = "website_profiles"
    if not site:
        b.claim(S, "official_website", None, "failed", family="website", note="website step did not run (time budget)")
    elif site["status"] == "verified":
        att = site["selected"]
        v = att["verdict"]
        home = att["pages"][0]
        tls_note = "" if v.get("tls_verified", True) else "+tls_unverified"
        e = b.ev(v["proof_url"], "company_owned_website", v["proof_retrieved_at"], v["proof_sha256"], v.get("span"), f"website_identity:{v['method']}{tls_note}")
        e2 = b.ev(home.url, "company_owned_website", home.resp.retrieved_at, home.resp.sha256, f"redirect_chain={' -> '.join(home.resp.redirect_chain or [home.url])}", "homepage_fetch")
        b.claim(S, "official_website", home.url, "available", sorted({e, e2}), family="website", key="website",
                confidence=0.99 if v["method"] == "orgnr_on_site" else 0.95, identity_method=v["method"], candidate_source=att["source"])
        if crawl and crawl["brand"].get("public_brand"):  # title segment that names the company (lane 10 precision fix)
            brand = crawl["brand"]["public_brand"]
            span = f"og:site_name={crawl['brand'].get('site_name') or ''}; title={crawl['brand'].get('title') or ''}; brand segment={brand}"
            e3 = b.ev(home.url, "company_owned_website", home.resp.retrieved_at, home.resp.sha256, span[:300], "html_meta")
            b.claim("legal_identity", "public_brand", brand[:120], "available", [e3], family="brand", confidence=0.9)
        if crawl and crawl["brand"].get("self_description"):
            e4 = b.ev(home.url, "company_owned_website", home.resp.retrieved_at, home.resp.sha256, crawl["brand"]["self_description"][:300], "html_meta_description")
            b.claim("legal_identity", "self_description", crawl["brand"]["self_description"][:400], "available", [e4], family="brand", confidence=0.9,
                    note="company-owned promotional copy; not independent")
    else:
        cands = [{"url": a.get("url"), "source": a.get("source"), "verdict": a["verdict"].get("status"), "reasons": a["verdict"].get("reasons")} for a in site.get("attempts", [])]
        state = site["status"] if site["status"] in STATES else "not_available"
        b.claim(S, "official_website", None, state, family="website", key="website",
                note="no candidate proved the exact legal entity" if state == "ambiguous" else "no company-owned website found from registry, email-domain or name-derived candidates",
                candidates=cands[:6])
    # social profiles
    if crawl:
        socials = crawl["social_profiles"]
        for s in socials:
            e = b.ev(s["found_on"], "company_owned_website", s["retrieved_at"], s["sha256"], f"link to {s['url']} on verified site ({s['match']})", "outbound_link_on_verified_site")
            b.claim(S, "social_profile", {"platform": s["platform"], "url": s["url"]}, "available", [e], family="social_profiles", key=f"social:{s['url'].lower()}",
                    confidence=0.95 if s["match"] == "handle_matches_company" else 0.85, match=s["match"])
        if not socials:
            b.claim(S, "social_profile", None, "not_available", family="social_profiles", note="verified site links no company profile")
    else:
        b.claim(S, "social_profile", None, "not_available" if site else "failed", family="social_profiles", note="no verified website to source company-owned profiles from")
    # hiring
    H = "hiring_activity"
    seen_titles = set()
    for j in jobs_nav:
        e = b.ev(j["source_url"], "official_job_feed_nav", j["retrieved_at"], j["sha256"], j["span"], "nav_feed_entry_orgnr_match")
        b.claim(H, "job_posting", {"job_id": j["job_id"], "title": j["title"], "url": j["url"], "published": j["published"], "expires": j["expires"],
                                   "locations": j["locations"], "employer_name": j["employer_name"], "employer_relation": j["employer_relation"]},
                "available", [e], family="hiring", key=j["job_id"], confidence=0.99)
        seen_titles.add(fold(j["title"]))
    own_words = [t for t in tokens(ident.get("name")) if t not in LEGAL_FORMS]
    # main2 fresh8: a careers page listing many positions that mostly do not name the company is a group/chain job
    # portal (norgesgruppen.no: KIWI/Joker/MENY store ads, each a separate employer) -> none of its jobs are published
    by_page: dict[str, list[dict]] = {}
    for j in (crawl or {}).get("jobs", []):
        by_page.setdefault(j.get("found_on") or "", []).append(j)
    distinct = [w for w in own_words if len(w) >= 3] or own_words
    portal_pages = {pg for pg, js in by_page.items() if len(js) >= 5
                    and sum(1 for j in js if set(distinct) & set(tokens(f"{j.get('title')} {j.get('heading') or ''}"))) * 2 < len(js)}
    for j in (crawl or {}).get("jobs", []):
        if (j.get("found_on") or "") in portal_pages:
            continue
        if fold(j["title"]) in seen_titles:
            continue
        if j.get("valid_through") and str(j["valid_through"]) < utc_now()[:10]:
            continue  # main2 fresh6: JobPosting validThrough has passed (heim-maritim.no ads expired 2025-02-03)
        org_words = [t for t in tokens(j.get("hiring_org")) if t not in LEGAL_FORMS]
        if org_words and own_words and not (set(own_words) <= set(org_words) or set(org_words) <= set(own_words)):
            continue  # main2 fresh6: JobPosting hiringOrganization is another company (Heim Skipsservice on heim-maritim.no)
        seen_titles.add(fold(j["title"]))
        e = b.ev(j["found_on"], "company_owned_website", j["retrieved_at"], j["sha256"], j.get("span") or f"{j['title']} -> {j['url']}", j["method"])
        value = {"job_id": j["url"], "title": j["title"], "url": j["url"], "published": j.get("date_posted"), "expires": j.get("valid_through")}
        if j["method"].startswith("free_text"):  # lane 7: free-text ad on the verified site; key = page URL + heading
            value.update(expires=j.get("deadline") if j.get("deadline") != "open_until_filled" else None, deadline=j.get("deadline"),
                         locations=[j["location"]] if j.get("location") else [], heading=j.get("heading"), employer_relation="same_entity_verified_site")
        b.claim(H, "job_posting", value, "available", [e], family="hiring", key=f"site:{j['url']}|{j.get('heading') or j['title']}",
                confidence=0.85 if j["method"].startswith("free_text") else 0.9)
    if not any(c["family"] == "hiring" and c["availability"] == "available" for c in b.claims):
        state = "failed" if nav_state in ("failed", "skipped_deadline") and not crawl else "not_available"
        check = None
        if crawl and crawl.get("hiring_checks"):
            from .site_hiring import hiring_check_summary
            check = hiring_check_summary(crawl["hiring_checks"])
        evs = [b.ev(check["url"], "company_owned_website", check["retrieved_at"], check["sha256"], check.get("span") or f"careers page read: {check['state']}",
                    f"careers_page_check:{check['state']}")] if check else []
        b.claim(H, "job_posting", None, state, evs, family="hiring",
                note=f"no active NAV ad with this organisation number or its subunits (feed: {nav_state})" + ("; careers page checked" if (crawl or {}).get("careers_pages") else "")
                + (f" ({check['state'].replace('_', ' ')})" if check else ""))
    # dated activity
    for a in (crawl or {}).get("activity", []):
        e = b.ev(a["source_url"], "company_owned_website", a["retrieved_at"], a["sha256"], a.get("span") or f"{a['date']} {a['title']}", a["method"])
        b.claim(H, "dated_activity", {"date": a["date"], "title": a["title"], "url": a["url"], "kind": "company_news_or_post", **({"date_basis": a["date_basis"]} if a.get("date_basis") else {})}, "available", [e],
                family="dated_activity", key=f"act:{a['url']}", confidence=0.95, effective_date=a["date"])
    if not any(c["family"] == "dated_activity" and c["availability"] == "available" for c in b.claims):
        b.claim(H, "dated_activity", None, "not_available" if site else "failed", family="dated_activity",
                note="no dated news/posts found on a verified company site" if crawl else "no verified company site to read dated activity from")


# ------------------------------------------------------------------ synthesis
FORM_EN = {"AS": "private limited company (AS)", "ASA": "public limited company (ASA)", "ENK": "sole proprietorship (ENK)",
           "ANS": "general partnership (ANS)", "DA": "partnership with shared liability (DA)", "SA": "cooperative (SA)",
           "STI": "foundation (stiftelse)", "BRL": "housing cooperative (borettslag)", "ESEK": "co-ownership of property (eierseksjonssameie)",
           "NUF": "Norwegian branch of a foreign company (NUF)", "FLI": "association (forening/lag)", "KS": "limited partnership (KS)",
           "SAM": "co-ownership (sameie)", "IKS": "inter-municipal company (IKS)", "KF": "municipal enterprise (KF)", "BA": "company with limited liability (BA)"}


def synthesis(ident: dict, b: Builder, changes: list[dict]) -> dict:
    av = [c for c in b.claims if c["availability"] == "available"]
    by = lambda f: [c for c in av if c["field"] == f]
    used: list[str] = []
    parts: list[str] = []

    def cite(cs: list[dict]) -> str:
        used.extend(c["id"] for c in cs)
        return "[" + ",".join(c["id"] for c in cs) + "]"

    name = ident.get("name") or "This entity"
    form = FORM_EN.get(str(ident.get("legal_form") or ""), ident.get("legal_form_description") or ident.get("legal_form") or "entity")
    muni = (ident.get("business_address") or {}).get("kommune") or (ident.get("business_address") or {}).get("poststed")
    ind = (ident.get("industry") or {}).get("beskrivelse") if isinstance(ident.get("industry"), dict) else None
    article = "an" if form[:1].lower() in "aeiou" else "a"
    s = f"{name} is {article} {form}" + (f" registered in {str(muni).title()}" if muni else "") + (f" (industry: {ind})" if ind else "")
    s += (f", founded {ident['founded']}" if ident.get("founded") else "")
    emp = by("registered_employees")
    if emp:
        s += f", with {emp[0]['value']} registered employee(s)"
    s += f" {cite(by('legal_name') + by('industry') + by('founded_date') + emp)}."
    brand = by("public_brand")
    if brand and fold(brand[0]["value"]).strip() and not set(fold(name).split()) <= set(fold(brand[0]["value"]).split()):
        s += f" It presents itself online as \"{brand[0]['value'][:80]}\" {cite(brand)}."
    parts.append(s)
    act = by("stated_activity")
    if act:
        parts.append(f"Stated activity: {str(act[0]['value'])[:220]} {cite(act[:1])}.")
    if ident.get("deleted"):
        parts.append(f"Warning: the live register reports this entity as deleted ({ident['deleted']}) {cite(by('deleted_from_register'))}.")
    if ident.get("bankrupt") or ident.get("liquidating"):
        parts.append(f"Warning: the register flags this entity as {'bankrupt' if ident.get('bankrupt') else 'under liquidation'} {cite(by('bankrupt') + by('under_liquidation'))}.")
    latest = [c for c in av if c.get("family") == "financials" and c.get("latest")]
    if latest:
        period = latest[0].get("reporting_period", "")
        vals = {c["field"]: c for c in latest}
        bits = [f"{lbl} {money(vals[f]['value'])}" for f, lbl in (("revenue", "revenue"), ("operating_result", "operating result"), ("annual_result", "annual result"), ("equity", "equity")) if f in vals]
        parts.append(f"Latest filed accounts ({period.split('/')[-1][:4]}): " + ", ".join(bits) + f" {cite(list(vals.values()))}.")
        prev = [c for c in av if c.get("family") == "financials" and c["field"] == "revenue" and not c.get("latest")]
        if "revenue" in vals and prev:
            p = sorted(prev, key=lambda c: c.get("reporting_period", ""), reverse=True)[0]
            try:
                old, new = float(p["value"]), float(vals["revenue"]["value"])
                if old > 0:
                    parts.append(f"Revenue changed {100 * (new - old) / old:+.0f}% versus {p['reporting_period'].split('/')[-1][:4]} {cite([p, vals['revenue']])}.")
            except (TypeError, ValueError):
                pass
        eq = vals.get("equity")
        if eq and isinstance(eq["value"], (int, float)) and eq["value"] < 0:
            parts.append(f"Equity is negative in the latest filing {cite([eq])}.")
    roles = by("role")
    lead = [c for c in roles if c["value"].get("role_code") in ("DAGL", "LEDE", "KOMP", "INNH")]
    if lead:
        parts.append("Leadership: " + "; ".join(f"{c['value']['role']}: {c['value']['name'] or c['value']['organisation_number']}" for c in lead[:3]) + f" {cite(lead[:3])}.")
    locs = by("registered_workplace")
    if locs:
        parts.append(f"{len(locs)} registered workplace(s) {cite(locs[:3])}.")
    site = by("official_website")
    if site:
        parts.append(f"Verified website {site[0]['value']} ({site[0].get('identity_method')}) {cite(site)}.")
    jobs = by("job_posting")
    if jobs:
        parts.append(f"Hiring: {len(jobs)} active posting(s), e.g. \"{jobs[0]['value']['title']}\" {cite(jobs[:3])}.")
    acts = sorted(by("dated_activity"), key=lambda c: c["value"]["date"], reverse=True)
    if acts:
        parts.append(f"Latest dated activity {acts[0]['value']['date']}: \"{acts[0]['value']['title'][:90]}\" {cite(acts[:1])}.")
    ratings = sorted(by("official_rating"), key=lambda c: c["value"]["date"], reverse=True)
    if ratings:  # lane 13
        r = ratings[0]["value"]
        parts.append(f"Official food-safety rating (Mattilsynet smilefjes) for {r['establishment']}: {r['meaning']} on {r['date']} {cite(ratings[:1])}.")
    socials = by("social_profile")
    if socials:
        parts.append("Company-owned profiles: " + ", ".join(sorted({c['value']['platform'] for c in socials})) + f" {cite(socials)}.")
    unknown = []
    if not site:
        unknown.append("no verified company website")
    if not jobs:
        unknown.append("no active job postings found")
    if not acts:
        unknown.append("no dated public activity found")
    if not latest:
        unknown.append("no annual-accounts figures available")
    if unknown:
        parts.append("Unknown or not found: " + "; ".join(unknown) + ".")
    if changes is not None:
        material = [c for c in changes if c.get("material")]
        if material:
            parts.append("Changes since previous run: " + "; ".join(f"{c['change_type']} {c['field']}" for c in material[:5]) + ".")
        else:
            parts.append("No material changes since the previous run.")
    return {"text": " ".join(p for p in parts if p), "supported_claim_ids": sorted(set(used), key=lambda x: int(x.split("-")[1])), "unknowns": unknown,
            "method": "deterministic_template_v1 (only cites available claims; no model)"}


# ------------------------------------------------------------------ refresh
CHANGE_TYPES = {"role": ("new_role", "removed_role"), "registered_workplace": ("new_location", "closed_location"), "job_posting": ("new_job", "closed_job"),
                "dated_activity": ("new_activity", None), "social_profile": ("new_profile", "removed_profile"), "official_website": ("new_website", "website_unavailable"),
                "official_rating": ("new_rating", "rating_no_longer_listed")}
MATERIAL_FIELDS = {"legal_name", "legal_form", "business_address", "bankrupt", "under_liquidation", "role", "registered_workplace", "official_website",
                   "job_posting", "revenue", "annual_result", "equity", "industry", "registered_employees", "official_rating"}


def refresh_diff(prev_env: dict | None, b: Builder) -> list[dict]:
    if not prev_env:
        return []
    old = {c["key"]: c for c in prev_env.get("claims", []) if c.get("availability") == "available"}
    new = {c["key"]: c for c in b.claims if c["availability"] == "available"}
    old_ev = {e["id"]: e for e in prev_env.get("evidence", [])}
    changes = []
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        field = (n or o)["field"]
        if o and n and json.dumps(canonical(o["value"]), sort_keys=True, ensure_ascii=False) == json.dumps(canonical(n["value"]), sort_keys=True, ensure_ascii=False):
            continue
        if field in FIN_FIELDS and not n:
            continue  # an older period scrolling out of the 3-record window is not a change
        if o and n:
            ctype = "changed_value"
        elif n:
            ctype = CHANGE_TYPES.get(field, ("new_value", None))[0]
            if field in FIN_FIELDS:
                ctype = "new_filing"
        else:
            ctype = CHANGE_TYPES.get(field, (None, "removed_value"))[1] or "no_longer_observed"
        changes.append({"change_type": ctype, "field": field, "key": k, "old_value": o["value"] if o else None, "new_value": n["value"] if n else None,
                        "old_evidence": [old_ev.get(i) for i in (o or {}).get("evidence_ids", [])], "new_evidence_ids": (n or {}).get("evidence_ids", []),
                        "material": field in MATERIAL_FIELDS})
    return changes


def carry_forward(prev_env: dict | None, b: Builder, failed_families: set[str]) -> int:
    """A failed refresh keeps the last supported value (marked stale) instead of erasing it."""
    if not prev_env or not failed_families:
        return 0
    old_ev = {e["id"]: e for e in prev_env.get("evidence", [])}
    have = {c["key"] for c in b.claims if c["availability"] == "available"}
    n = 0
    for c in prev_env.get("claims", []):
        if c.get("availability") == "available" and c.get("family") in failed_families and c["key"] not in have:
            ids = []
            for i in c.get("evidence_ids", []):
                e = old_ev.get(i)
                if e:
                    ids.append(b.ev(e["source_url"], e["source_class"], e["retrieved_at"], e["content_sha256"], e.get("claim_span"), (e.get("extraction_method") or "") + "+carried_forward", e.get("reporting_period")))
            b.claim(c["section"], c["field"], c["value"], "available", ids, family=c["family"], key=c["key"], confidence=c.get("confidence"),
                    note="carried forward from previous snapshot because this refresh failed", stale=True, first_observed=c.get("first_observed") or prev_env.get("run", {}).get("completed_at"))
            n += 1
    return n


def wikidata_sites(fetcher: Fetcher, orgs: list[str]) -> dict[str, list[str]]:
    """Official-website (P856) candidates for items carrying a Norwegian organisation number (P2333). CC0 data;
    used only as crawl candidates — publication still needs on-site proof."""
    import urllib.parse
    out: dict[str, list[str]] = {}
    for i in range(0, len(orgs), 200):
        values = " ".join(f'"{o}"' for o in orgs[i:i + 200])
        query = f"SELECT ?org ?site WHERE {{ VALUES ?org {{ {values} }} ?item wdt:P2333 ?org . ?item wdt:P856 ?site }}"
        resp = fetcher.get("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query), respect_robots=False,
                           accept="application/sparql-results+json", timeout=40)
        if not resp.ok:
            continue
        try:
            for row in json.loads(resp.body)["results"]["bindings"]:
                out.setdefault(row["org"]["value"], []).append(row["site"]["value"])
        except (ValueError, KeyError):
            continue
    return out


DEFAULT_STRATEGIES = {"name_guess", "email_domain", "no_domain_legal_name", "crawl", "nav", "nav_homepage", "nav_containment", "brreg_events", "ted", "cc_domains", "site_hiring", "site_news", "social_plus", "follow_redirects",
                      "state_aid", "smilefjes", "fin_licences", "www_swap", "tls_aia"}  # lanes 13 and 14 (main2 promotion)
TRANSIENT_MARKERS = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "host_unreachable", "timed out", "TimeoutError", "Connection reset", "RemoteDisconnected")


def rate_limited(attempt: dict) -> bool:
    """lane 14 polite_429: a candidate refused with 429/503 (asked or held back during a cool-down)."""
    v = attempt.get("verdict") or {}
    return v.get("status") in ("failed", "blocked") and any(m in " ".join(v.get("reasons") or []) for m in ("HTTP 429", "HTTP 503"))


def transient(attempt: dict) -> bool:
    v = attempt.get("verdict") or {}
    return v.get("status") in ("failed", "blocked") and any(m in " ".join(v.get("reasons") or []) for m in TRANSIENT_MARKERS)


# ------------------------------------------------------------------ orchestration
def compact_state(st: dict) -> None:
    """Lane 9 (memory): once a company's discovery and crawl are done, drop parsed page trees. Envelopes only read the
    selected homepage's URL, hash, retrieval time and redirect chain, and each attempt's url/source/verdict; keeping a
    BeautifulSoup tree per candidate page for every company until the end peaked at ~1 GB RSS for 1,000 companies."""
    site = st.get("site") or {}
    selected = site.get("selected")
    for a in site.get("attempts") or []:
        if a is not selected:
            a.pop("pages", None)
    for p in (selected or {}).get("pages") or []:
        for attr, empty in (("soup", None), ("links", []), ("text", ""), ("footer_text", ""), ("jsonld", [])):
            if hasattr(p, attr):
                setattr(p, attr, empty)


class Agent:
    def __init__(self, *, run_id: str, budget_s: float, company_cap_s: float = 150, workers: int = 12, modules: set[str] | None = None,
                 strategies: set[str] | None = None, nav_lookback_days: int = 120, events_since: str = "2018-01-01", ted_since: str = "2021-01-01", log=print):
        self.run_id = run_id
        self.t0 = time.monotonic()
        self.deadline = self.t0 + budget_s
        self.margin = min(120.0, max(8.0, 0.08 * budget_s))  # reserved for NAV confirmation + envelope writing
        self.company_cap_s = company_cap_s
        self.workers = workers
        self.modules = modules or {"registry_live", "financials", "roles", "group", "locations", "website", "hiring"}
        self.strategies = strategies if strategies is not None else set(DEFAULT_STRATEGIES)
        self.fetcher = Fetcher(deadline=self.deadline - self.margin)
        self.fetcher.allow_unverified_tls = "tls_fallback" in (strategies if strategies is not None else DEFAULT_STRATEGIES)
        self.fetcher.enable_lane14(tls_aia="tls_aia" in self.strategies, polite_429="polite_429" in self.strategies)  # lane 14
        self.nav = NavIndex(Fetcher(deadline=self.deadline - self.margin / 2, per_host_interval=0.5), lookback_days=nav_lookback_days) if "hiring" in self.modules and "nav" in self.strategies else None
        self.events_since = events_since
        self.events_fetcher = Fetcher(deadline=self.deadline - self.margin, per_host_interval=0.1)  # data.brreg.no API: 10 req/s cap
        self.ted = TedAwards(deadline=self.deadline - self.margin, since=ted_since) if "ted" in self.strategies else None  # lane 3
        self.smilefjes = (SmilefjesIndex(Fetcher(deadline=self.deadline - self.margin, per_host_interval=0.5), self.deadline - self.margin)
                          if "smilefjes" in self.strategies else None)  # lane 13: Mattilsynet inspection ratings
        self.aid_fetcher = Fetcher(deadline=self.deadline - self.margin, per_host_interval=0.25) if "state_aid" in self.strategies else None  # lane 13
        self.rcn = RcnProjects(Fetcher(deadline=self.deadline - self.margin), self.deadline - self.margin) if "rcn_projects" in self.strategies else None  # lane 13
        self.fin_fetcher = Fetcher(deadline=self.deadline - self.margin, per_host_interval=0.25) if "fin_licences" in self.strategies else None  # lane 13
        self.log = log
        self.today = utc_now()[:10]

    def phase_a(self, org: str, reg: dict | None) -> dict:
        state: dict[str, Any] = {"org": org, "errors": [], "started": time.monotonic()}
        f = self.fetcher.child(self.company_cap_s)
        if not valid_orgnr(org):  # lane 9: 8 digits, bad check digit... -> terminal not_found without network calls
            state.update(official={}, invalid_number=True, elapsed_a=0.0)
            return state
        official_modules = self.modules & {"registry_live", "financials", "roles", "group", "locations"}
        try:
            # lane 9: register calls share the company's cap and the global deadline (a slow BRREG used to hold a
            # worker for up to 6 modules x 3 attempts x 20 s, past the deadline)
            recs, metrics = fetch_official_modules(org, official_modules, fetcher=lambda url: fetch_json(url, deadline=f.deadline))
            state["official"] = recs
            state["official_requests"] = len(metrics)
        except Exception as exc:
            state["errors"].append({"step": "official", "error": f"{type(exc).__name__}: {exc}"[:300]})
            state["official"] = {}
        live = (state["official"].get("registry_live") or {})
        if live.get("status") == "not_found" and not reg:  # lane 9: a subunit (underenhet) number given as input
            sub = fetch_json(BRREG_SUBUNIT.format(org=org), deadline=f.deadline)
            if sub.status == 200 and isinstance(sub.body, dict):
                state["subunit_record"] = {"source_url": sub.url, "retrieved_at": sub.retrieved_at, "content_sha256": sub.content_sha256, "body": sub.body}
        state["ident_probe"] = self._probe_identity(org, reg, live)
        if "brreg_events" in self.strategies:  # lane 3: official dated register events by organisation number
            try:
                state["registry_events"] = fetch_update_events(self.events_fetcher.child(min(30.0, self.company_cap_s)), org, self.events_since)
            except Exception as exc:
                state["registry_events"] = {"state": "failed", "events": [], "requests": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
        if self.smilefjes:  # lane 13: official inspection ratings verified by the orgnr printed on the page
            try:
                subs = ((state["official"].get("locations") or {}).get("value") or {}).get("locations") or []
                places = [(state["ident_probe"].get("business_address"), state["ident_probe"].get("name"))] + [(l.get("address"), l.get("name")) for l in subs]
                state["smilefjes"] = fetch_company_ratings(self.smilefjes.fetcher.child(min(40.0, self.company_cap_s)), self.smilefjes, org, places,
                                                           {l["organisation_number"]: l.get("name") for l in subs if l.get("organisation_number")})
            except Exception as exc:
                state["smilefjes"] = {"state": "failed", "establishments": [], "requests": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
        if "website" in self.modules and f.remaining() > 15:
            try:
                prof = state["ident_probe"]
                if "follow_redirects" in self.strategies:  # E18: verify the target of a redirecting guessed domain
                    prof = {**prof, "_follow_redirects": True}
                if "social_plus" in self.strategies:  # E17: Organization sameAs + sitewide numeric Facebook page
                    prof = {**prof, "_social_plus": True}
                if "article_dates_plus" in self.strategies:
                    prof = {**prof, "_article_dates_plus": True}
                if "name_variants" in self.strategies:
                    prof = {**prof, "_name_variants": True}
                if "site_hiring" in self.strategies:  # lane 7
                    prof = {**prof, "_site_hiring": True}
                if "page_source_orgnr" in self.strategies:
                    prof = {**prof, "_allow_page_source_orgnr": True}
                if "shell_read" in self.strategies:  # lane 14: frame/refresh forwards and embedded JSON text on empty homepages
                    prof = {**prof, "_shell_read": True}
                if "www_swap" in self.strategies:  # lane 14: apex <-> www. when one name is unreachable
                    prof = {**prof, "_www_swap": True}
                if "site_news" in self.strategies:  # lane 8: dated posts from news sections, listings and sitemaps
                    prof = site_news_profile(prof, state["official"])
                if "no_domain_legal_name" in self.strategies:
                    prof = {**prof, "_allow_no_domain_legal_name": True}
                if "email_domain" not in self.strategies:
                    prof = {**prof, "email": None}
                extra = []
                if "subunit_sites" in self.strategies:
                    subunits = ((state["official"].get("locations") or {}).get("value") or {}).get("locations") or []
                    for l in subunits:
                        hp = normalize_homepage(l.get("website"))
                        if hp:
                            extra.append((hp, "registry_subunit"))
                    prof = {**prof, "subunit_emails": [l.get("email") for l in subunits if l.get("email")][:5]}
                if "wikidata" in self.strategies:
                    extra += [(u, "wikidata") for u in self.wikidata.get(org, [])]
                late = list_candidates(prof, limit=2) if "cc_domains" in self.strategies else []  # lane 5
                site = discover_website(f, prof, extra_candidates=extra, guess="name_guess" in self.strategies, late_candidates=late)
                state["site"] = site
                if site["status"] == "verified" and "crawl" in self.strategies:
                    state["crawl"] = crawl_verified_site(f, prof, site["selected"], today=self.today)
            except Exception as exc:
                state["errors"].append({"step": "website", "error": f"{type(exc).__name__}: {exc}"[:300], "trace": traceback.format_exc()[-600:]})
        compact_state(state)
        state["elapsed_a"] = round(time.monotonic() - state["started"], 2)
        return state

    def _lane13_fetch(self, org: str) -> dict:
        """Lane 13: register lookups that need only the organisation number (Støtteregisteret awards, Finanstilsynet
        licences). They run in a small background pool beside phase A, so they add no time to the per-company path."""
        out: dict[str, Any] = {}
        for key, fetcher, fn, empty in (("state_aid", self.aid_fetcher, fetch_state_aid, "awards"), ("fin_licences", self.fin_fetcher, fetch_fin_licences, "licences")):
            if fetcher is None:
                continue
            try:
                out[key] = fn(fetcher.child(30.0), org)
            except Exception as exc:
                out[key] = {"state": "failed", empty: [], "requests": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
        return out

    def _collect_lane13(self, futures: dict, states: dict) -> None:
        for org, fut in futures.items():
            left = self.deadline - time.monotonic() - self.margin / 2
            try:
                res = fut.result(timeout=max(0.05, left))
            except Exception as exc:
                fut.cancel()
                why = "not reached before the global deadline" if left <= 0.05 or type(exc).__name__ == "TimeoutError" else f"{type(exc).__name__}: {exc}"[:200]
                res = {k: {"state": "failed", e: [], "requests": [], "error": why}
                       for k, f, e in (("state_aid", self.aid_fetcher, "awards"), ("fin_licences", self.fin_fetcher, "licences")) if f is not None}
            states.setdefault(org, {"org": org, "errors": [], "official": {}}).update(res)

    def _probe_identity(self, org: str, reg: dict | None, live: dict) -> dict:
        v = live.get("value") or {}
        raw = (reg or {}).get("raw") or {}
        return {"organisation_number": org, "name": v.get("name") or (reg or {}).get("name"), "website": v.get("website") or (reg or {}).get("website"),
                "business_address": v.get("business_address") or ({"adresse": [raw.get("forretningsadresse.adresse")], "postnummer": raw.get("forretningsadresse.postnummer")} if raw else None),
                "email": raw.get("epostadresse") or (v.get("raw") or {}).get("epostadresse"),
                "phones": [x for x in (raw.get("telefon"), raw.get("mobil"), (v.get("raw") or {}).get("telefon"), (v.get("raw") or {}).get("mobil")) if x]}

    def run(self, orgs: list[str], registry: dict[str, dict], previous: dict[str, dict]) -> tuple[list[dict], dict]:
        started_at = utc_now()
        if not orgs:  # lane 9: an empty batch must not wait minutes for the NAV index
            self.nav = None
            self.ted = None
        if self.nav:
            self.nav.start()
        if self.ted:
            self.ted.start(orgs)
        if self.smilefjes:
            self.smilefjes.start()
        if self.rcn:
            self.rcn.start()
        lane13_futures: dict = {}
        if self.aid_fetcher or self.fin_fetcher:
            l13_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lane13")
            lane13_futures = {org: l13_pool.submit(self._lane13_fetch, org) for org in orgs}
            l13_pool.shutdown(wait=False)
        self.wikidata = wikidata_sites(self.fetcher, orgs) if "wikidata" in self.strategies and "website" in self.modules else {}
        states: dict[str, dict] = {}
        pool = ThreadPoolExecutor(max_workers=self.workers)
        futures = {pool.submit(self.phase_a, org, registry.get(org)): org for org in orgs}
        pending = set(futures)
        last_log = time.monotonic()
        while pending:
            left = self.deadline - time.monotonic() - self.margin
            if left <= 0:
                break
            done, pending = wait(pending, timeout=min(left, 10), return_when=FIRST_COMPLETED)
            for fut in done:
                org = futures[fut]
                try:
                    states[org] = fut.result()
                except Exception as exc:
                    states[org] = {"org": org, "errors": [{"step": "phase_a", "error": f"{type(exc).__name__}: {exc}"[:300]}], "official": {}}
            if time.monotonic() - last_log > 30:
                self.log(f"[{self.run_id}] phase A {len(states)}/{len(orgs)} done, {round(time.monotonic() - self.t0)}s, requests {self.fetcher.stats()['requests']}, nav {self.nav.status if self.nav else None}")
                last_log = time.monotonic()
        timed_out = [futures[f] for f in pending]
        for org in timed_out:
            states[org] = {"org": org, "errors": [{"step": "phase_a", "error": "global deadline reached before this company finished"}], "official": {}, "deadline_hit": True}
        pool.shutdown(wait=False, cancel_futures=True)
        self.log(f"[{self.run_id}] phase A complete {round(time.monotonic() - self.t0)}s; timed out {len(timed_out)}")
        # phase A2: one retry pass for site candidates that failed transiently (rate limits, 5xx, unreachable, timeouts)
        if ("retry_transient" in self.strategies or "polite_429" in self.strategies) and "website" in self.modules:
            retryable = transient if "retry_transient" in self.strategies else rate_limited
            retry = [o for o in orgs if not states[o].get("deadline_hit") and (states[o].get("site") or {}).get("status") != "verified"
                     and any(retryable(a) for a in (states[o].get("site") or {}).get("attempts", []))]
            if retry and self.deadline - time.monotonic() - self.margin > 120:
                if "retry_transient" in self.strategies:
                    time.sleep(20.0)  # back-off before re-asking the same hosts
                else:  # lane 14: wait until the refusing backends' cool-downs are over (at most 120 s, budget permitting)
                    cool = self.fetcher.cooldown_left([a["url"] for o in retry for a in states[o]["site"]["attempts"] if rate_limited(a)])
                    time.sleep(max(0.0, min(cool, 120.0, self.deadline - time.monotonic() - self.margin - 120)))
                self.fetcher.forget_hosts([a["url"] for o in retry for a in states[o]["site"]["attempts"] if retryable(a)])
                def phase_a2(org: str) -> None:
                    st = states[org]
                    f = self.fetcher.child(60)
                    prof = {**st["ident_probe"], "_allow_no_domain_legal_name": "no_domain_legal_name" in self.strategies,
                            "_site_hiring": "site_hiring" in self.strategies, "_social_plus": "social_plus" in self.strategies,
                            "_follow_redirects": "follow_redirects" in self.strategies,
                            "_shell_read": "shell_read" in self.strategies, "_www_swap": "www_swap" in self.strategies}  # lane 7, E17, E18, lane 14
                    if "email_domain" not in self.strategies:
                        prof["email"] = None
                    if "site_news" in self.strategies:
                        prof = site_news_profile(prof, st.get("official") or {})
                    site = discover_website(f, prof, guess="name_guess" in self.strategies)
                    if site["status"] == "verified":
                        st["site"] = site
                        if "crawl" in self.strategies:
                            st["crawl"] = crawl_verified_site(f, prof, site["selected"], today=self.today)
                        compact_state(st)
                with ThreadPoolExecutor(max_workers=8) as p4:
                    list(p4.map(lambda o: (time.monotonic() < self.deadline - self.margin) and phase_a2(o), retry))
                self.log(f"[{self.run_id}] retry pass: {len(retry)} companies, now verified {sum(1 for o in retry if (states[o].get('site') or {}).get('status') == 'verified')}")
        # phase B: NAV
        nav_jobs: dict[str, list[dict]] = {o: [] for o in orgs}
        nav_state = "not_run"
        if self.nav:
            self.nav.ready.wait(max(0, self.deadline - time.monotonic() - self.margin))
            nav_state = self.nav.status.get("state", "failed") if self.nav.ready.is_set() else "skipped_deadline"
            self.log(f"[{self.run_id}] NAV index {self.nav.status}")
            if self.nav.ready.is_set() and self.nav.by_name:
                tasks = []
                for org in orgs:
                    st = states[org]
                    names = [st.get("ident_probe", {}).get("name") or (registry.get(org) or {}).get("name")]
                    orgmap = {org: "same_entity"}
                    for l in ((st.get("official", {}).get("locations") or {}).get("value") or {}).get("locations") or []:
                        if l.get("organisation_number"):
                            orgmap[l["organisation_number"]] = "registered_subunit"
                            names.append(l.get("name"))
                    seen_uuids = set()
                    for ad in self.nav.candidates([n for n in names if n], containment="nav_containment" in self.strategies):
                        tasks.append((org, ad, orgmap))
                        seen_uuids.add(ad["uuid"])
                    for link in (st.get("crawl") or {}).get("nav_links") or []:  # lane 7: NAV ads linked from the verified site
                        if link["uuid"] not in seen_uuids:
                            tasks.append((org, {"uuid": link["uuid"], "title": link["anchor"], "feed_url": f"{NAV_BASE}/api/v1/feedentry/{link['uuid']}",
                                                "linked_from": link["found_on"]}, orgmap))
                with ThreadPoolExecutor(max_workers=4) as p2:
                    for org, res in zip([t[0] for t in tasks], p2.map(lambda t: self.nav.confirm(t[1], t[2]) if time.monotonic() < self.deadline - self.margin / 2 else {"state": "skipped_deadline"}, tasks)):
                        st = states[org]
                        st.setdefault("nav_attempts", []).append({k: res.get(k) for k in ("state", "uuid", "employer_orgnr", "error")})
                        if res.get("state") == "confirmed" and any(j.get("job_id") == res.get("job_id") for j in nav_jobs[org]):
                            continue
                        if res.get("state") == "confirmed":
                            nav_jobs[org].append(res)
                self.log(f"[{self.run_id}] NAV candidates {len(tasks)}, confirmed {sum(len(v) for v in nav_jobs.values())}")
        # phase C: employer homepage from confirmed NAV ads
        if "nav_homepage" in self.strategies and "website" in self.modules:
            todo = [o for o in orgs if nav_jobs[o] and (states[o].get("site") or {}).get("status") != "verified"]
            def phase_c(org: str) -> None:
                st = states[org]
                f = self.fetcher.child(60)
                extra = []
                for j in nav_jobs[org]:
                    hp = str(j.get("employer_homepage") or "").strip()
                    if hp and j.get("employer_relation") in ("same_entity", "registered_subunit"):
                        extra.append((hp if hp.startswith("http") else "https://" + hp, "registry_email"))
                if not extra:
                    return
                site = discover_website(f, {**st.get("ident_probe", {}), "website": None, "email": None}, extra_candidates=extra, guess=False)
                if site["status"] == "verified":
                    site["selected"]["source"] = "nav_ad_employer_homepage"
                    st["site"] = site
                    if "crawl" in self.strategies:
                        cprof = {**st["ident_probe"], "_site_hiring": "site_hiring" in self.strategies, "_social_plus": "social_plus" in self.strategies}
                        st["crawl"] = crawl_verified_site(f, site_news_profile(cprof, st.get("official") or {}) if "site_news" in self.strategies else cprof, site["selected"], today=self.today)
                    compact_state(st)
            with ThreadPoolExecutor(max_workers=8) as p3:
                list(p3.map(lambda o: (time.monotonic() < self.deadline - self.margin / 2) and phase_c(o), todo))
        if self.ted:
            self.ted.ready.wait(max(0, self.deadline - time.monotonic() - self.margin))
            self.log(f"[{self.run_id}] TED awards {self.ted.status}")
        # main2: register modules that failed transiently (HTTP 5xx bursts, timeouts) get one more try before the
        # envelopes are written (smoke 5452840: two companies failed on a brief BRREG HTTP 500 burst)
        retry_off = [o for o in orgs if any(isinstance(r, dict) and r.get("status") == "source_error"
                                            for r in ((states.get(o) or {}).get("official") or {}).values())]
        if retry_off and self.deadline - time.monotonic() - self.margin > 30:
            def refetch(org: str) -> None:
                st = states[org]
                bad = {m for m, r in st["official"].items() if isinstance(r, dict) and r.get("status") == "source_error"}
                stop = min(time.monotonic() + 60, self.deadline - self.margin / 2)
                try:
                    recs, _ = fetch_official_modules(org, bad, fetcher=lambda url: fetch_json(url, deadline=stop))
                except Exception:
                    return
                for m, r in recs.items():
                    if isinstance(r, dict) and r.get("status") != "source_error":
                        st["official"][m] = r
            with ThreadPoolExecutor(max_workers=4) as px:
                list(px.map(refetch, retry_off))
            self.log(f"[{self.run_id}] register retry: {len(retry_off)} companies re-asked")
        if lane13_futures:
            self._collect_lane13(lane13_futures, states)
        # phase D: envelopes
        envelopes = []
        for org in orgs:
            try:
                envelopes.append(self.envelope(org, states.get(org) or {"official": {}, "errors": []}, registry.get(org), nav_jobs.get(org, []), nav_state, previous.get(org), started_at))
            except Exception as exc:
                envelopes.append(self.failed_envelope(org, registry.get(org), started_at, f"{type(exc).__name__}: {exc}", traceback.format_exc()[-800:]))
        report = {"nav": self.nav.status if self.nav else None, "ted": ({**self.ted.status, "request_log": self.ted.requests[-20:]} if self.ted else None), "smilefjes": ({**self.smilefjes.status, "fetch": self.smilefjes.fetcher.stats()} if self.smilefjes else None),
                  "state_aid": ({"states": dict(Counter((states.get(o) or {}).get("state_aid", {}).get("state", "missing") for o in orgs)), "fetch": self.aid_fetcher.stats()} if self.aid_fetcher else None),
                  "rcn_projects": (self.rcn.status if self.rcn else None),
                  "fin_licences": ({"states": dict(Counter((states.get(o) or {}).get("fin_licences", {}).get("state", "missing") for o in orgs)), "fetch": self.fin_fetcher.stats()} if self.fin_fetcher else None), "timed_out_companies": timed_out, "fetch": self.fetcher.stats(),
                  "nav_fetch": self.nav.fetcher.stats() if self.nav else None, "wall_seconds": round(time.monotonic() - self.t0, 1)}
        return envelopes, report

    def failed_envelope(self, org: str, reg: dict | None, started_at: str, error: str, trace: str = "") -> dict:
        b = Builder(org)
        ident = {}
        try:
            ident = add_identity(b, reg, None)
        except Exception:
            pass
        if not ident:
            b.claim("legal_identity", "legal_name", None, "failed", family="registry", note=error[:200] or "entity not found in the registry snapshot or live API")
        now = utc_now()
        return {"organisation_number": org, "run_id": self.run_id, "agent_version": AGENT_VERSION, "state": "failed", "terminal_status": "failed",
                "run": {"run_id": self.run_id, "started_at": started_at, "completed_at": now, "terminal_status": "failed"},
                "identity": ident, "sections": {s: {"availability": "failed"} for s in ("legal_identity", "annual_accounts", "leadership_workplaces", "website_profiles", "hiring_activity")},
                "claims": b.claims, "evidence": b.evidence, "summary": {"text": "", "supported_claim_ids": [], "unknowns": ["run failed for this company"]},
                "changes": [], "errors": [{"step": "envelope", "error": error[:400], "trace": trace}],
                "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}}

    def envelope(self, org: str, st: dict, reg: dict | None, jobs: list[dict], nav_state: str, prev: dict | None, started_at: str) -> dict:
        b = Builder(org)
        off = st.get("official") or {}
        live = off.get("registry_live")
        if live and live.get("status") != "available":
            live = None
        ident = add_identity(b, reg, live)
        if not ident:
            live_rec = off.get("registry_live") or {}
            if st.get("invalid_number") or live_rec.get("status") == "not_found":
                why = ("not a valid Norwegian organisation number (9 digits with a mod-11 check digit)" if st.get("invalid_number")
                       else "organisation number not found in registry snapshot or live register")
            else:  # lane 9: the register could not be reached (network, 5xx, deadline): that is a failure, not "not found"
                reason = live_rec.get("note") or ("global deadline reached" if st.get("deadline_hit") else "register not reached")
                env = self.failed_envelope(org, None, started_at, f"register lookup failed ({reason}); entity identity unknown")
                env["errors"] += st.get("errors", [])
                return env
            sub = st.get("subunit_record")
            if sub:
                body = sub["body"]
                parent = body.get("overordnetEnhet")
                why = f"this number is a registered subunit (underenhet) of organisation {parent}, not a main entity; research the parent number"
            env = self.failed_envelope(org, None, started_at, why)
            env["state"] = env["terminal_status"] = env["run"]["terminal_status"] = "not_found"
            for c in env["claims"]:
                c["availability"] = "not_available"
            env["summary"] = {"text": why[:1].upper() + why[1:] + ".", "supported_claim_ids": [], "unknowns": [why]}
            if sub:
                sb = Builder(org)
                sb.claims, sb.evidence = env["claims"], env["evidence"]
                for field, key in (("subunit_name", "navn"), ("parent_organisation_number", "overordnetEnhet")):
                    if body.get(key):
                        e = sb.ev(sub["source_url"], "official_registry_live", sub["retrieved_at"], sub["content_sha256"], f"{key}={body.get(key)}", "brreg_subunit_field")
                        sb.claim("legal_identity", field, body.get(key), "available", [e], family="registry", key=field,
                                 note="the input number is a registered subunit (underenhet), not a main entity")
                env["identity"] = {"organisation_number": org, "name": body.get("navn"), "registered_as": "subunit (underenhet)",
                                   "parent_organisation_number": parent}
            env["sections"] = {s: {"availability": "not_applicable"} for s in env["sections"]}
            env["sections"]["legal_identity"] = {"availability": "not_available"}
            env["errors"] += st.get("errors", [])
            return env
        add_financials(b, off.get("financials") if "financials" in self.modules else {"status": "not_fetched"})
        add_roles_locations(b, off.get("roles"), off.get("locations"), off.get("group"))
        if "brreg_events" in self.strategies:
            add_registry_event_claims(b, st.get("registry_events") or {"state": "failed", "events": [], "error": "step did not run (time budget)"}, off.get("roles"), org)
        if self.ted and self.ted.ready.is_set():
            add_ted_claims(b, self.ted, org)
        if self.aid_fetcher:
            add_state_aid_claims(b, st.get("state_aid"), org)
        if self.fin_fetcher:
            add_fin_licence_claims(b, st.get("fin_licences"), org)
        if self.rcn:
            add_rcn_claims(b, self.rcn.lookup(org), org)
        if self.smilefjes:
            add_smilefjes_claims(b, st.get("smilefjes") or {"state": "failed", "establishments": [], "error": "step did not run (time budget)"})
        if "website" in self.modules or "hiring" in self.modules:
            add_footprint(b, st.get("site") if "website" in self.modules else {"status": "not_available", "attempts": []}, st.get("crawl"), jobs, nav_state, ident)
        failed_families = {c["family"] for c in b.claims if c["availability"] == "failed"}
        carried = carry_forward(prev, b, failed_families)
        changes = refresh_diff(prev, b)
        sections = {}
        for sec in ("legal_identity", "annual_accounts", "leadership_workplaces", "website_profiles", "hiring_activity") + (("ratings_reviews",) if self.smilefjes else ()):
            states = [c["availability"] for c in b.claims if c["section"] == sec]
            sections[sec] = {"availability": "available" if "available" in states else next((s for s in ("failed", "blocked", "ambiguous", "not_available", "not_applicable") if s in states), "not_available"),
                             "claims": sum(1 for s in states if s == "available")}
        summary = synthesis(ident, b, changes if prev else None)
        # lane 9: a source we could not reach (register module, NAV feed, deadline) makes the envelope partial; a company's
        # own website being down is a finding about the company and keeps it completed
        family_module = {"financials": "financials", "roles": "roles", "workplaces": "locations", "group": "group", "hiring": "hiring",
                         "website": "website", "social_profiles": "website", "dated_activity": "website"}
        degraded = sorted({c["family"] for c in b.claims if c["availability"] == "failed"
                           and family_module.get(c["family"], "registry_live") in self.modules  # a module switched off is not a failure
                           and (c["family"] != "website" or "did not run" in str(c.get("note") or ""))})
        any_failed = bool(st.get("deadline_hit")) or bool(st.get("errors")) or bool(degraded)
        terminal = "partial" if any_failed else "completed"
        now = utc_now()
        ops_requests = (st.get("official_requests") or 0) + sum(len(a.get("requests", [])) for a in (st.get("site") or {}).get("attempts", [])) + len((st.get("crawl") or {}).get("crawl_log", [])) + len((st.get("registry_events") or {}).get("requests", [])) + len((st.get("smilefjes") or {}).get("requests", [])) + len((st.get("state_aid") or {}).get("requests", [])) + len((st.get("fin_licences") or {}).get("requests", []))
        return {
            "organisation_number": org, "run_id": self.run_id, "agent_version": AGENT_VERSION, "state": terminal, "terminal_status": terminal,
            "run": {"run_id": self.run_id, "started_at": started_at, "completed_at": now, "terminal_status": terminal},
            "identity": {k: v for k, v in ident.items() if k not in ("email", "phones")},
            "sections": sections, "claims": b.claims, "evidence": b.evidence, "summary": summary,
            "changes": changes, "refresh": {"first_snapshot": not prev, "previous_run_id": (prev or {}).get("run_id"), "previous_completed_at": ((prev or {}).get("run") or {}).get("completed_at"),
                                             "carried_forward_claims": carried, "material_changes": sum(1 for c in changes if c.get("material"))},
            "errors": st.get("errors", []) + ([{"step": "sources", "error": "not reached for: " + ", ".join(degraded)}] if degraded else []),
            "attempts": {"website_candidates": [{"url": a.get("url"), "source": a.get("source"), "verdict": {k: v for k, v in a["verdict"].items() if k in ("status", "method", "reasons", "other_orgnrs")}}
                                                for a in (st.get("site") or {}).get("attempts", [])],
                         "crawl_log": (st.get("crawl") or {}).get("crawl_log", []), "nav": st.get("nav_attempts", [])},
            "operations": {"requests": ops_requests, "runtime_ms": int(1000 * (st.get("elapsed_a") or 0)), "third_party_cost_usd": 0},
        }
