"""Dated activity: licences in Finanstilsynet's virksomhetsregister (register of licensed entities), by organisation number.

Source: Finanstilsynet's open API for the register, https://api.finanstilsynet.no/registry/v2 (no key; described at
https://www.finanstilsynet.no/analyser-og-statistikk/api-for-apne-data/ as "opne API for verksemdsregisteret"; no
licence or rate limit is stated there; the swagger default limit is 1000 requests/60 s). robots.txt on the API host is
404 (no rules); the agent's Fetcher still checks it. Throttle: ≤ 4 requests/s.

Method: `GET /legal-entities/search?query=<orgnr>`; continue only if a returned entity's `organisationNumber` equals
the input exactly; then `GET /legal-entities?ids=<legalEntityId>` for its licences. A licence is published only when
`licensedEntity.legalEntityId` is that entity (agents of other firms are not attributed). Each licence has a type
(e.g. "Regnskapsselskap" / External accounting firm, estate agency, insurance agency, bank) and `registeredDate`.
"""
from __future__ import annotations

import json
from typing import Any

BASE = "https://api.finanstilsynet.no/registry/v2"
PUBLIC_URL = "https://www.finanstilsynet.no/virksomhetsregisteret/"
SOURCE_CLASS = "official_licence_register"
MAX_PER_COMPANY = 10


def _json(r) -> Any:
    try:
        return json.loads(r.body)
    except ValueError:
        return None


def fetch_fin_licences(fetcher, org: str) -> dict:
    """{"state": checked|failed, "licences": [...], "requests": [...]} for one organisation number."""
    requests = []
    url = f"{BASE}/legal-entities/search?query={org}&pageSize=10&page=1"
    r = fetcher.get(url, accept="application/json", timeout=15, max_bytes=2_000_000)
    requests.append({"url": url, "status": r.status, "error": r.error})
    d = _json(r) if r.ok else None
    if not isinstance(d, dict) or not isinstance(d.get("legalEntities"), list):
        return {"state": "failed", "licences": [], "requests": requests, "error": r.error or f"HTTP {r.status}"}
    hits = [e for e in d["legalEntities"] if str(e.get("organisationNumber")) == org and e.get("legalEntityId") is not None]
    if not hits:
        return {"state": "checked", "licences": [], "requests": requests}
    ent = hits[0]
    url2 = f"{BASE}/legal-entities?ids={ent['legalEntityId']}"
    r2 = fetcher.get(url2, accept="application/json", timeout=15, max_bytes=5_000_000)
    requests.append({"url": url2, "status": r2.status, "error": r2.error})
    d2 = _json(r2) if r2.ok else None
    if not isinstance(d2, dict):
        return {"state": "failed", "licences": [], "requests": requests, "error": r2.error or f"HTTP {r2.status}"}
    out = []
    for e in d2.get("legalEntities") or []:
        if str(e.get("organisationNumber")) != org or e.get("legalEntityId") != ent["legalEntityId"]:
            continue
        for lic in e.get("licences") or []:
            if (lic.get("licensedEntity") or {}).get("legalEntityId") != ent["legalEntityId"] or not lic.get("registeredDate"):
                continue
            lt = lic.get("licenceType") or {}
            name = lt.get("name") or {}
            out.append({"code": lt.get("code"), "name_en": name.get("english"), "name_no": name.get("norwegian"), "date": str(lic["registeredDate"])[:10],
                        "provider_type": lic.get("serviceProviderType"), "entity_name": e.get("name"), "finanstilsynet_id": e.get("finanstilsynetId")})
    uniq = {(x["code"], x["date"]): x for x in out}
    lic_list = sorted(uniq.values(), key=lambda x: (x["date"], x["code"] or ""), reverse=True)
    return {"state": "checked", "licences": lic_list, "requests": requests, "retrieved_at": r2.retrieved_at, "sha256": r2.sha256, "url": url2,
            "entity_id": ent["legalEntityId"]}


def add_fin_licence_claims(b: Any, res: dict | None, org: str) -> int:
    res = res or {"state": "not_run", "licences": []}
    n = 0
    for x in res.get("licences", [])[:MAX_PER_COMPANY]:
        label = x["name_en"] or x["name_no"] or x["code"]
        title = f"Licence registered with Finanstilsynet (financial supervisory authority): {label}" + (f" ({x['name_no']})" if x["name_no"] and x["name_no"] != label else "") + f", registered {x['date']}"
        span = (f"organisationNumber={org}; name={x['entity_name']}; finanstilsynetId={x['finanstilsynet_id']}; licenceType.code={x['code']}; "
                f"licenceType.name={x['name_no']}/{x['name_en']}; registeredDate={x['date']}")
        e = b.ev(res["url"], SOURCE_CLASS, res.get("retrieved_at"), res.get("sha256"), span, "finanstilsynet_registry_orgnr")
        b.claim("hiring_activity", "dated_activity", {
            "date": x["date"], "title": title[:300], "url": PUBLIC_URL, "kind": "official_licence", "event_type": "licence_registered",
            "licence_code": x["code"], "licence": label, "source": "Finanstilsynet virksomhetsregisteret",
            "date_basis": "registeredDate of the licence in the register"},
            "available", [e], family="dated_activity", key=f"finanstilsynet:{x['code']}:{x['date']}", confidence=0.98, effective_date=x["date"])
        n += 1
    return n
