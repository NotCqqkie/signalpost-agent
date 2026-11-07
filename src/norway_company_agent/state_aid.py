"""Dated public activity: public support awards in Brønnøysundregistrene's Støtteregisteret, keyed by organisation number.

Source: Registerinfo API for støtteregisteret, `POST https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoettetildeling`
with the filter `STOETTEMOTTAKER_ORGNR` = orgnr (EXACT_MATCH). Catalogued on data.norge.no as the API distribution
of "Støtteregisteret" (Registerenheten i Brønnøysund), licence NLOD 2.0, no key
(https://data.norge.no/en/datasets/f634a11e-ed9c-30f4-916d-2cb1b7114e64/stotteregisteret). The host's robots.txt
resets the connection (checked 2026-11-06), like data.brreg.no; this documented machine API is called like the
kit's official BRREG modules (no robots gate), from its own throttle (≤ 4 requests/s), one request per company.

Each award (støttetildeling) has a number, status, award date (`tildelingsdato`), received date, instrument (grant,
loan, tax relief …), awarded amount and currency, and roles: the giver (støttegiver) and the recipient
(støttemottaker). Only awards with status `registrert` whose recipient role carries exactly the input organisation
number are published; withdrawn, rejected, mis-registered or expired records are not. Amounts are copied verbatim
from the register with their currency, never computed.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .evidence import utc_now
from .net import DeadlineExceeded, Response

API = "https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoettetildeling"
SCHEME_API = "https://stottetiltak-registerinfo-api.app.brreg.no/api/v1/soek/stoetteordning"
PUBLIC_URL = "https://stotte.brreg.no/"
SOURCE_CLASS = "official_state_aid_register"
PAGE_SIZE = 1000  # API maximum; one request covers a recipient
MAX_PER_COMPANY = 10  # support schemes published per company (most recent first)
_SCHEMES: dict[str, str | None] = {}  # scheme number -> English (else Norwegian) name; shared across companies in a run
_SCHEMES_LOCK = threading.Lock()

INSTRUMENT_EN = {
    "tilskudd": "grant", "laan": "loan", "laan_rentefritt": "interest-free loan", "garanti": "guarantee",
    "skatteEllerAvgiftsFritak": "tax or duty exemption", "skatteEllerAvgiftsFordel": "tax or duty advantage",
    "kapitalinnskudd": "capital injection", "rentesubsidie": "interest subsidy", "risikofinansiering": "risk finance",
}


def _code(v: Any) -> str:
    return str(v or "").split(".", 1)[-1]


def _money(b: dict | None) -> str | None:
    if not isinstance(b, dict) or b.get("beloep") is None:
        return None
    amt = b["beloep"]
    txt = f"{amt:,.2f}".replace(",", " ") if isinstance(amt, (int, float)) else str(amt)
    if txt.endswith(".00"):
        txt = txt[:-3]
    return f"{b.get('valuta') or ''} {txt}".strip()


def _role(rec: dict, kind: str) -> list[dict]:
    return [r.get("rolleinnehaverVirksomhet") or {} for r in rec.get("rolle") or [] if _code(r.get("type")) == kind]


def _post(fetcher, url: str, body: bytes, timeout: float) -> Response:
    """JSON POST through the shared Fetcher's per-host throttle, deadline and request counters (Fetcher has no POST)."""
    host = url.split("/")[2]
    if fetcher.remaining() < 3:
        return Response(url, url, 0, retrieved_at=utc_now(), error="deadline")
    try:
        fetcher._throttle(host)
    except DeadlineExceeded:
        return Response(url, url, 0, retrieved_at=utc_now(), error="deadline")
    req = urllib.request.Request(url, data=body, method="POST", headers={"User-Agent": fetcher.user_agent, "Accept": "application/json",
                                                                         "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, min(timeout, fetcher.remaining() - 1))) as r:
            raw = r.read(10_000_000)
            fetcher._count(host, len(raw))
            return Response(url, url, r.status, raw, r.headers.get("content-type", ""), utc_now(), int((time.monotonic() - started) * 1000))
    except urllib.error.HTTPError as exc:
        fetcher._count(host, 0)
        return Response(url, url, exc.code, b"", "", utc_now(), int((time.monotonic() - started) * 1000), f"HTTP {exc.code}")
    except Exception as exc:
        fetcher._count(host, 0)
        return Response(url, url, 0, b"", "", utc_now(), int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:160]}")


def scheme_name(fetcher, sid: str | None) -> str | None:
    """Name of a support scheme (støtteordning), one cached request per distinct scheme per run."""
    if not sid:
        return None
    with _SCHEMES_LOCK:
        if sid in _SCHEMES:
            return _SCHEMES[sid]
    name = None
    r = _post(fetcher, f"{SCHEME_API}?pageSize=5&page=1", json.dumps([{"field": "STOETTETILTAKSNUMMER", "value": sid, "matchType": "EXACT_MATCH"}]).encode(), 15)
    if r.ok:
        try:
            for rec in json.loads(r.body).get("stoetteordning") or []:
                if str(rec.get("stoettetiltaksnummer")) == sid:
                    names = {n.get("spraak"): n.get("tekst") for n in rec.get("navn") or []}
                    name = names.get("ENG") or names.get("NOR") or next(iter(names.values()), None)
        except ValueError:
            name = None
    with _SCHEMES_LOCK:
        if r.ok:
            _SCHEMES[sid] = name
    return name


def fetch_state_aid(fetcher, org: str) -> dict:
    """{"state": checked|failed, "awards": [...], "requests": [...]} for one organisation number."""
    body = json.dumps([{"field": "STOETTEMOTTAKER_ORGNR", "value": org, "matchType": "EXACT_MATCH"}]).encode()
    url = f"{API}?pageSize={PAGE_SIZE}&page=1"
    r = _post(fetcher, url, body, timeout=20)
    if r.status in (0, 429, 500, 502, 503, 504) and r.error != "deadline" and fetcher.remaining() > 10:
        time.sleep(2.0)
        r = _post(fetcher, url, body, timeout=20)
    req = {"url": url, "status": r.status, "error": r.error}
    if not r.ok:
        return {"state": "failed", "awards": [], "requests": [req], "error": r.error or f"HTTP {r.status}"}
    try:
        d = json.loads(r.body)
    except ValueError:
        return {"state": "failed", "awards": [], "requests": [req], "error": "unparseable response"}
    rows = d.get("stoettetildeling")
    if not isinstance(rows, list):
        return {"state": "failed", "awards": [], "requests": [req], "error": "unexpected response shape"}
    awards = []
    for rec in rows:
        if _code(rec.get("status")) != "registrert" or not rec.get("tildelingsdato"):
            continue
        if not any(str(v.get("organisasjonsnummer")) == org for v in _role(rec, "stoettemottaker")):
            continue
        givers = _role(rec, "stoettegiver")
        awards.append({
            "number": str(rec.get("stoettetiltaksnummer")), "date": str(rec["tildelingsdato"])[:10], "received": rec.get("mottattDato"),
            "instrument": _code(rec.get("stoetteinstrument")), "amount": rec.get("tildeltBeloep"),
            "giver": (givers[0].get("navn") if givers else None), "giver_orgnr": (givers[0].get("organisasjonsnummer") if givers else None),
            "scheme_id": rec.get("tilknyttetStoetteordning"), "category": _code((rec.get("eoesRettsligGrunnlag") or {}).get("statsstoettekategori")),
            "record_sha256": hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        })
    awards.sort(key=lambda a: (a["date"], a["number"]), reverse=True)
    for sid in dict.fromkeys(a["scheme_id"] for a in awards if a.get("scheme_id")):
        nm = scheme_name(fetcher, str(sid))
        for a in awards:
            if a.get("scheme_id") == sid:
                a["scheme_name"] = nm
    return {"state": "checked", "awards": awards, "requests": [req], "retrieved_at": r.retrieved_at, "sha256": r.sha256, "query": url,
            "total_rows": len(rows), "truncated": len(rows) >= PAGE_SIZE}


def add_state_aid_claims(b: Any, res: dict | None, org: str) -> int:
    """One dated-activity claim per support scheme and giver (the latest registered award, with the count and date
    range of all of them: recurring awards such as bimonthly payroll-tax relief would otherwise flood the envelope).
    A checked source with no awards adds no claim of its own (add_footprint emits dated_activity not_available)."""
    res = res or {"state": "not_run", "awards": []}
    groups: dict[tuple, list[dict]] = {}
    for a in res.get("awards", []):  # newest first
        groups.setdefault((a.get("giver_orgnr"), a.get("scheme_id") or a["number"]), []).append(a)
    n = 0
    for (giver_org, scheme), lst in list(groups.items())[:MAX_PER_COMPANY]:
        a = lst[0]
        same_day = [x for x in lst if x["date"] == a["date"]]  # several awards can share the latest date
        inst = INSTRUMENT_EN.get(a["instrument"], a["instrument"].replace("_", " ") or "support")
        what = a.get("scheme_name") or (f"support scheme {a.get('scheme_id')}" if a.get("scheme_id") else inst)
        if len(same_day) == 1:
            amount = _money(a.get("amount"))
            core = f"{inst}" + (f" of {amount}" if amount else "") + (f" from {a['giver']}" if a.get("giver") else "") + f", awarded {a['date']}"
        else:
            amounts = [m for m in (_money(x.get("amount")) for x in same_day) if m]
            listed = ", ".join(f"{m} ×{amounts.count(m)}" if amounts.count(m) > 1 else m for m in dict.fromkeys(amounts))
            core = (f"{len(same_day)} {inst} awards" + (f" ({listed})" if listed else "") + (f" from {a['giver']}" if a.get("giver") else "")
                    + f", all awarded {a['date']}")
        title = (f"Public support registered in Støtteregisteret: {what} — {core}"
                 + (f" ({len(lst)} awards under this scheme since {lst[-1]['date']})" if len(lst) > len(same_day) else ""))
        span = (f"stoettetiltaksnummer={a['number']}; status=registrert; tildelingsdato={a['date']}; stoettemottaker.organisasjonsnummer={org}; "
                f"stoetteinstrument={a['instrument']}; stoettegiver={a.get('giver')} ({giver_org}); tilknyttetStoetteordning={a.get('scheme_id')}; "
                + ("tildeltBeloep=" + json.dumps(a.get("amount"), ensure_ascii=False) if len(same_day) == 1 else
                   f"{len(same_day)} awards on {a['date']}: " + ", ".join(f"{x['number']} {json.dumps(x.get('amount'), ensure_ascii=False)}" for x in same_day[:12]))
                + (f"; {len(lst)} registered awards in this scheme: " + ", ".join(f"{x['number']}@{x['date']}" for x in lst[:12]) if len(lst) > len(same_day) else ""))
        e = b.ev(res["query"] + f"#STOETTEMOTTAKER_ORGNR={org}", SOURCE_CLASS, res.get("retrieved_at"), res.get("sha256"), span, "stoetteregister_recipient_orgnr")
        b.claim("hiring_activity", "dated_activity", {
            "date": a["date"], "title": title[:300], "url": PUBLIC_URL, "kind": "public_support_award", "event_type": "state_aid_award",
            "award_number": a["number"], "award_numbers_on_date": [x["number"] for x in same_day], "instrument": a["instrument"],
            "amount": a.get("amount") if len(same_day) == 1 else None, "amounts_on_date": [x.get("amount") for x in same_day], "giver": a.get("giver"), "giver_orgnr": giver_org,
            "scheme_id": a.get("scheme_id"), "scheme_name": a.get("scheme_name"), "awards_in_scheme": len(lst), "first_award_date": lst[-1]["date"],
            "source": "Støtteregisteret (Brønnøysundregistrene)", "licence": "NLOD 2.0", "date_basis": "award date (tildelingsdato) of the latest registered award"},
            "available", [e], family="dated_activity", key=f"stotte:{giver_org}:{scheme}", confidence=0.98, effective_date=a["date"])
        n += 1
    return n
