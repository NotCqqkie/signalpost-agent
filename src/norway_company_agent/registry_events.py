"""Dated public activity from Brønnøysundregistrene's official update log, keyed by organisation number.

Source: Enhetsregisteret open API, `GET /enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer=<orgnr>
&dato=<since>&includeChanges=true` (data.brreg.no, licence NLOD 2.0, no key, no quota). Each update has an id, a timestamp, a change type (Ny/Endring/Sletting/
Ukjent) and, since 2025-11-18, the RFC 6902 patch of what changed. data.brreg.no/robots.txt resets the connection
(checked 2026-11-06); this is a documented machine API published for programmatic reuse, called exactly like the
kit's official modules (no robots gate), at most 10 requests/s from one shared throttle. Role groups carry `sistEndret` in the roles
API the agent already fetches, so board/CEO changes cost no extra request.

The legacy announcement pages (w2.brreg.no/kunngjoring/) are disallowed by robots.txt and are not used.

Identity risk: none beyond the input key; the query is by organisation number and every returned row is
re-checked to carry the same number before it is published.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .net import Fetcher

BASE = "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter"
ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
MAX_PAGES = 5
PAGE_SIZE = 100
# Undetailed "record updated" rows (no patch) are not published: some are system-wide (e.g. 2025-11-13 touches every
# entity), so they are not evidence of company activity. Raise to keep the N most recent dates.
MAX_GENERIC = 0

# patch path prefix -> (event_type, English label, publish values?)
PATHS: list[tuple[str, str, str, bool]] = [
    ("/sisteInnsendteAarsregnskap", "annual_accounts_filed", "Annual accounts recorded as filed", True),
    ("/navn", "name_change", "Registered name updated", True),
    ("/organisasjonsform", "legal_form_change", "Legal form changed", True),
    ("/forretningsadresse", "business_address_change", "Business address changed", True),
    ("/postadresse", "postal_address_change", "Postal address changed", True),
    ("/naeringskode", "industry_code_change", "Industry (NACE) code changed", True),
    ("/hjelpeenhetskode", "industry_code_change", "Industry (NACE) code changed", True),
    ("/antallAnsatte", "employee_count_change", "Registered employee count changed", True),
    ("/harRegistrertAntallAnsatte", "employee_count_change", "Registered employee count changed", True),
    ("/registreringsdatoAntallAnsatte", "employee_count_reported", "Employee count re-reported to the register", True),
    ("/fravalgRevisjon", "audit_opt_out", "Decision to opt out of statutory audit registered", True),
    ("/hjemmeside", "website_change", "Registered website changed", True),
    ("/konkurs", "bankruptcy", "Bankruptcy status changed", True),
    ("/underAvvikling", "liquidation", "Liquidation status changed", True),
    ("/underTvangsavviklingEllerTvangsopplosning", "forced_liquidation", "Forced liquidation/dissolution status changed", True),
    ("/registrertIMvaregisteret", "vat_registration_change", "VAT register status changed", True),
    ("/registrertIForetaksregisteret", "business_register_change", "Register of Business Enterprises status changed", True),
    ("/vedtektsdato", "articles_change", "Date of articles of association updated", True),
    ("/vedtektsfestetFormaal", "purpose_change", "Registered purpose changed", False),
    ("/aktivitet", "activity_change", "Registered business activity description changed", False),
    ("/overordnetEnhet", "parent_change", "Parent entity changed", True),
    ("/erIKonsern", "group_membership_change", "Group (konsern) membership flag changed", True),
    ("/kapital", "share_capital_change", "Share capital changed", True),
    ("/epostadresse", "contact_change", "Contact details changed", False),
    ("/telefon", "contact_change", "Contact details changed", False),
    ("/mobil", "contact_change", "Contact details changed", False),
]
FIELD_LABEL = {"sisteInnsendteAarsregnskap": "latest filed accounts year", "navn": "name", "organisasjonsform/kode": "legal form",
               "organisasjonsform/beskrivelse": "legal form", "naeringskode1/kode": "industry code", "naeringskode1/beskrivelse": "industry",
               "naeringskode2/kode": "secondary industry code", "naeringskode2/beskrivelse": "secondary industry",
               "naeringskode3/kode": "third industry code", "naeringskode3/beskrivelse": "third industry", "hjelpeenhetskode": "auxiliary unit code",
               "antallAnsatte": "registered employees", "harRegistrertAntallAnsatte": "employee count registered",
               "registreringsdatoAntallAnsatteEnhetsregisteret": "employee-count registration date (Enhetsregisteret)",
               "registreringsdatoAntallAnsatteNAVAaregisteret": "employee-count report date (NAV Aa-register)",
               "forretningsadresse/adresse": "street", "forretningsadresse/postnummer": "postcode", "forretningsadresse/poststed": "town",
               "forretningsadresse/kommune": "municipality", "forretningsadresse/kommunenummer": "municipality no.", "forretningsadresse/land": "country",
               "postadresse/adresse": "street", "postadresse/postnummer": "postcode", "postadresse/poststed": "town",
               "postadresse/kommune": "municipality",
               "hjemmeside": "website", "konkurs": "bankrupt", "konkursdato": "bankruptcy date", "underAvvikling": "under liquidation",
               "underAvviklingDato": "liquidation date", "underTvangsavviklingEllerTvangsopplosning": "under forced liquidation/dissolution",
               "registrertIMvaregisteret": "in VAT register", "registrertIForetaksregisteret": "in Register of Business Enterprises",
               "vedtektsdato": "articles of association dated", "overordnetEnhet": "parent entity", "erIKonsern": "part of a group (konsern)",
               "kapital/belop": "share capital", "kapital/antallAksjer": "number of shares", "kapital/innfortDato": "capital change registered",
               "kapital/valuta": "currency", "kapital/type": "capital type", "kapital/fulltInnbetaltBelop": "paid-in capital",
               "fravalgRevisjonDato": "audit opt-out date", "fravalgRevisjonBeslutningsDato": "audit opt-out decision date"}
SUBFIELD_LABEL = {"adresse": "street", "postnummer": "postcode", "poststed": "town", "kommune": "municipality", "kommunenummer": "municipality no.",
                  "land": "country", "landkode": "country code", "kode": "code", "beskrivelse": "description"}
REMOVED_LABEL = {"audit_opt_out_removed": "Audit opt-out removed from the register (statutory audit no longer waived)",
                 "contact_change_removed": "Contact details removed", "website_change_removed": "Registered website removed",
                 "employee_count_change_removed": "Registered employee count removed",
                 "employee_count_reported_removed": "Employee-count registration removed from the register"}
IGNORED = ("/institusjonellSektorkode", "/_links", "/maalform", "/registreringsdatoEnhetsregisteret")
ROLE_GROUP_LABEL = {"STYR": "Board", "DAGL": "Chief executive", "REVI": "Auditor", "REGN": "Accountant", "KONT": "Contact person",
                    "DTPR": "General partner", "DTSO": "Partners", "INNH": "Proprietor", "SIGN": "Signature rights", "PROK": "Procuration",
                    "EIKM": "Owner municipality", "FFØR": "Business manager", "REPR": "Norwegian representative"}


def _last_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if month < 12 else datetime(year, 12, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _date(ts: Any) -> str | None:
    """Europe/Oslo calendar date of a timestamp. The API serves the same instant with different offsets on different
    fetches (Z vs +02:00), so the raw date part is not stable; EU DST rule: +02:00 from the last Sunday of March
    01:00 UTC to the last Sunday of October 01:00 UTC, else +01:00. A bare date is returned unchanged."""
    s = str(ts or "").strip()
    if not re.match(r"\d{4}-\d{2}-\d{2}", s):
        return None
    if len(s) <= 10:
        return s[:10]
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if t.tzinfo is None:
        return s[:10]
    u = t.astimezone(timezone.utc)
    start = _last_sunday(u.year, 3).replace(hour=1)
    end = _last_sunday(u.year, 10).replace(hour=1)
    return (u + timedelta(hours=2 if start <= u < end else 1)).date().isoformat()


def classify(changes: list[dict]) -> list[dict]:
    """Group an RFC 6902 patch into labelled events; unknown paths fall back to a generic event naming the fields."""
    out: dict[str, dict] = {}
    unknown: list[str] = []
    for ch in changes or []:
        path = str(ch.get("path") or "")
        if path.startswith(IGNORED):
            continue
        hit = next((p for p in PATHS if path.startswith(p[0])), None)
        if not hit:
            unknown.append(path)
            continue
        _, etype, label, publish = hit
        ev = out.setdefault(etype, {"event_type": etype, "label": label, "details": [], "raw": [], "ops": set()})
        ev["ops"].add(str(ch.get("op")))
        if not publish:
            ev["raw"].append(f"{path.lstrip('/')} {ch.get('op')} (value not published)"[:160])
        if publish:
            val = ch.get("value")
            if isinstance(val, dict):
                val = val.get("kode") and f"{val.get('kode')} {val.get('beskrivelse') or ''}".strip() or ", ".join(str(v) for v in val.values() if isinstance(v, (str, int)))
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val if v)
            field = path.lstrip("/")
            detail = f"{field} {ch.get('op')}" + (f" = {val}" if val not in (None, "") else "")
            ev["raw"].append(detail[:160])
            segs = [x for x in field.split("/") if x and not x.isdigit()]
            key = "/".join(segs)
            name = FIELD_LABEL.get(key) or (f"{SUBFIELD_LABEL[segs[-1]]}" if len(segs) > 1 and segs[-1] in SUBFIELD_LABEL else None) or FIELD_LABEL.get(segs[0] if segs else key) or key.replace("/", " ")
            if isinstance(val, bool):
                val = "yes" if val else "no"
            ev["details"].append((f"{name} removed" if ch.get("op") == "remove" else f"{name} {val}" if val not in (None, "") else f"{name} updated")[:160])
    if unknown and not out:
        out["record_change"] = {"event_type": "record_change", "label": "Register record changed", "details": [f"fields: {', '.join(sorted(set(unknown)))[:200]}"], "raw": [], "ops": set()}
    for ev in out.values():
        if ev.pop("ops") == {"remove"}:  # only removals: the registered fact was withdrawn, not newly registered
            ev["event_type"] += "_removed"
            ev["label"] = REMOVED_LABEL.get(ev["event_type"], ev["label"] + " (entry removed from the register)")
    return list(out.values())


def fetch_update_events(fetcher: Fetcher, org: str, since: str = "2018-01-01") -> dict:
    """Return {state, events, requests:[...]} with state in available / not_available / failed."""
    pages: list[dict] = []
    events: list[dict] = []
    page = 0
    while page < MAX_PAGES:
        url = f"{BASE}?organisasjonsnummer={org}&dato={since}T00:00:00.000Z&includeChanges=true&size={PAGE_SIZE}&page={page}"
        r = fetcher.get(url, respect_robots=False, accept="application/json", timeout=15, max_bytes=3_000_000)
        if not r.ok:
            r = fetcher.get(url, respect_robots=False, accept="application/json", timeout=15, max_bytes=3_000_000)
        pages.append({"url": url, "status": r.status, "error": r.error, "sha256": r.sha256 if r.ok else None, "retrieved_at": r.retrieved_at})
        if not r.ok:
            return {"state": "blocked" if r.error == "robots_disallowed" else "failed", "events": events, "requests": pages,
                    "error": r.error or f"HTTP {r.status}"}
        try:
            body = json.loads(r.body)
        except ValueError:
            return {"state": "failed", "events": events, "requests": pages, "error": "invalid json"}
        for row in (body.get("_embedded") or {}).get("oppdaterteEnheter") or []:
            if re.sub(r"\D", "", str(row.get("organisasjonsnummer") or "")) != org:
                continue  # never publish a row for another entity
            date = _date(row.get("dato"))
            if not date:
                continue
            base = {"update_id": row.get("oppdateringsid"), "date": date, "timestamp": row.get("dato"), "change_type": row.get("endringstype"),
                    "source_url": url, "sha256": r.sha256, "retrieved_at": r.retrieved_at}
            ctype = str(row.get("endringstype") or "")
            if ctype == "Ny":
                events.append({**base, "event_type": "registered", "label": "New entity record published in the Enhetsregisteret update log", "details": []})
            elif ctype in ("Sletting", "Fjernet"):
                events.append({**base, "event_type": "deleted", "label": "Entity deletion published in the Enhetsregisteret update log", "details": []})
            elif row.get("endringer"):
                for ev in classify(row["endringer"]):
                    events.append({**base, **ev})
            else:
                events.append({**base, "event_type": "record_updated", "label": "Register record updated (change details not published by the source)", "details": [], "generic": True})
        total_pages = int(((body.get("page") or {}).get("totalPages")) or 1)
        page += 1
        if page >= total_pages:
            break
    if not events:
        return {"state": "not_available", "events": [], "requests": pages}
    # keep every detailed event; only the most recent few undetailed ones (several rows can share one timestamp)
    generic = [e for e in events if e.get("generic")]
    seen_dates: list[str] = []
    for e in sorted(generic, key=lambda e: e["timestamp"] or "", reverse=True):
        if e["date"] not in seen_dates:
            seen_dates.append(e["date"])
    keep_dates = set(seen_dates[:MAX_GENERIC])
    kept, used = [], set()
    for e in events:
        if e.get("generic"):
            if e["date"] not in keep_dates or e["date"] in used:
                continue
            used.add(e["date"])
        kept.append(e)
    return {"state": "available" if kept else "not_available", "events": kept, "requests": pages,
            **({} if kept else {"note": f"{len(events)} undetailed update(s) only"})}


def role_group_events(roles_rec: dict | None) -> list[dict]:
    """Role groups whose registration was last changed on a date (from the roles payload already fetched)."""
    if not roles_rec or roles_rec.get("status") != "available":
        return []
    groups: dict[str, dict] = {}
    for r in (roles_rec.get("value") or {}).get("roles") or []:
        d = _date(r.get("last_changed"))
        code = r.get("group_code")
        if not d or not code:
            continue
        g = groups.setdefault(code, {"date": d, "group": r.get("group"), "code": code, "members": []})
        if not r.get("inactive"):
            g["members"].append(f"{r.get('role')}: {r.get('name') or r.get('organisation_number')}")
    out = []
    for code, g in groups.items():
        label = ROLE_GROUP_LABEL.get(code, g["group"] or code)
        out.append({"date": g["date"], "event_type": "role_change", "label": f"{label} registration last changed", "group_code": code,
                    "group": g["group"], "details": g["members"][:8], "source_url": roles_rec["source_url"], "sha256": roles_rec.get("content_sha256"),
                    "retrieved_at": roles_rec.get("retrieved_at")})
    return out


def add_registry_event_claims(b: Any, rec: dict | None, roles_rec: dict | None, org: str) -> int:
    """Publish dated_activity claims. Returns the number of available claims added.

    Emits nothing when the connector did not run (rec is None) so the caller's own not_available/failed logic applies.
    """
    H = "hiring_activity"
    n = 0
    events = list((rec or {}).get("events") or []) if rec else []
    for ev in sorted(events, key=lambda e: (e["date"], str(e.get("update_id")), e["event_type"]), reverse=True):
        title = ev["label"] + (": " + "; ".join(ev["details"]) if ev.get("details") else "")
        span = f"oppdateringsid={ev.get('update_id')}; organisasjonsnummer={org}; dato={ev.get('timestamp')}; endringstype={ev.get('change_type')}" + \
               (f"; {'; '.join(ev.get('raw') or ev['details'])}" if ev.get("raw") or ev.get("details") else "")
        e = b.ev(ev["source_url"], "official_registry_updates", ev["retrieved_at"], ev["sha256"], span, "brreg_oppdateringer_orgnr")
        b.claim(H, "dated_activity", {"date": ev["date"], "title": ("Register update: " + title)[:300], "url": ev["source_url"], "kind": "official_register_event",
                                      "date_basis": "Europe/Oslo date of the Enhetsregisteret update-log timestamp",
                                      "event_type": ev["event_type"], "update_id": ev.get("update_id"), "source": "Brønnøysundregistrene Enhetsregisteret update log"},
                "available", [e], family="dated_activity", key=f"brreg_upd:{ev.get('update_id')}:{ev['event_type']}", confidence=0.99,
                effective_date=ev["date"])
        n += 1
    for ev in role_group_events(roles_rec):
        title = ev["label"] + (": " + "; ".join(ev["details"]) if ev["details"] else "")
        e = b.ev(ev["source_url"], "official_roles", ev["retrieved_at"], ev["sha256"],
                 f"rollegruppe {ev['group_code']} ({ev['group']}) sistEndret={ev['date']}", "brreg_roles_sistEndret")
        b.claim(H, "dated_activity", {"date": ev["date"], "title": ("Register update: " + title)[:300], "url": ev["source_url"], "kind": "official_register_event",
                                      "date_basis": "Europe/Oslo date of the Enhetsregisteret update-log timestamp",
                                      "event_type": "role_change", "source": "Brønnøysundregistrene roles register",
                                      "date_basis": "sistEndret of the role group in the roles register"},
                "available", [e], family="dated_activity", key=f"brreg_roles:{ev['group_code']}:{ev['date']}", confidence=0.99,
                effective_date=ev["date"])
        n += 1
    if rec and not n and rec.get("state") in ("not_available", "failed", "blocked"):
        req = (rec.get("requests") or [{}])[-1]
        ids = []
        if rec["state"] == "not_available" and req.get("sha256"):
            ids = [b.ev(req["url"], "official_registry_updates", req.get("retrieved_at"), req.get("sha256"), "oppdaterteEnheter=[] (no updates since lookback)", "brreg_oppdateringer_orgnr")]
        b.claim(H, "dated_activity", None, rec["state"], ids, family="dated_activity", key="brreg_updates",
                note=("official update log checked: no dated register events in the lookback window" if rec["state"] == "not_available"
                      else f"official update log not reached: {rec.get('error')}")[:200])
    return n
