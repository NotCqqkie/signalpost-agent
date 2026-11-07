"""Offline tests for the BRREG register-events connector (lane 3)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402
from norway_company_agent.registry_events import _date, add_registry_event_claims, classify, fetch_update_events, role_group_events  # noqa: E402

ORG = "989259660"


class FakeFetcher:
    def __init__(self, pages: list[dict | None]):
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, **kw) -> Response:
        self.urls.append(url)
        i = min(len(self.urls) - 1, len(self.pages) - 1)
        body = self.pages[i]
        if body is None:
            return Response(url, url, 503, b"", retrieved_at="2026-11-06T00:00:00Z", error="HTTP 503")
        return Response(url, url, 200, json.dumps(body).encode(), "application/json", "2026-11-06T00:00:00Z")


def row(uid: int, date: str, ctype: str = "Endring", changes: list | None = None, org: str = ORG) -> dict:
    r = {"organisasjonsnummer": org, "oppdateringsid": uid, "dato": date, "endringstype": ctype}
    if changes is not None:
        r["endringer"] = changes
    return r


def page(rows: list[dict], total_pages: int = 1) -> dict:
    return {"_embedded": {"oppdaterteEnheter": rows}, "page": {"totalPages": total_pages, "totalElements": len(rows)}}


def test_classify_groups_paths_and_hides_contact_values():
    evs = classify([
        {"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"},
        {"op": "replace", "path": "/naeringskode1/kode", "value": "68.200"},
        {"op": "replace", "path": "/telefon", "value": "+47 99999999"},
        {"op": "replace", "path": "/institusjonellSektorkode/kode", "value": "2100"},
    ])
    kinds = {e["event_type"]: e for e in evs}
    assert set(kinds) == {"annual_accounts_filed", "industry_code_change", "contact_change"}
    assert kinds["annual_accounts_filed"]["details"] == ["latest filed accounts year 2025"]
    assert kinds["annual_accounts_filed"]["raw"] == ["sisteInnsendteAarsregnskap replace = 2025"]  # kept for the evidence span
    assert kinds["industry_code_change"]["details"] == ["industry code 68.200"]
    assert kinds["contact_change"]["details"] == []  # phone numbers are never published
    assert kinds["contact_change"]["raw"] == ["telefon replace (value not published)"]  # path stays verifiable
    addr = classify([{"op": "replace", "path": "/postadresse/kommunenummer", "value": "1804"}, {"op": "replace", "path": "/postadresse/adresse/0", "value": "Postboks 214"}])[0]
    assert addr["details"] == ["municipality no. 1804", "street Postboks 214"]
    gone = classify([{"op": "remove", "path": "/registreringsdatoAntallAnsatteEnhetsregisteret"}])[0]
    assert gone["details"] == ["employee-count registration date (Enhetsregisteret) removed"]
    assert classify([{"op": "add", "path": "/somethingNew", "value": 1}])[0]["event_type"] == "record_change"
    assert classify([{"op": "replace", "path": "/registreringsdatoAntallAnsatteNAVAaregisteret", "value": "2026-10-01"}])[0]["event_type"] == "employee_count_reported"
    assert classify([{"op": "add", "path": "/fravalgRevisjonDato", "value": "2026-05-01"}])[0]["event_type"] == "audit_opt_out"
    removed = classify([{"op": "remove", "path": "/fravalgRevisjonDato"}, {"op": "remove", "path": "/fravalgRevisjonBeslutningsDato"}])[0]
    assert removed["event_type"] == "audit_opt_out_removed" and "removed" in removed["label"] and "ops" not in removed


def test_fetch_keeps_detailed_events_drops_generic_and_foreign_rows():
    f = FakeFetcher([page([
        row(1, "2025-11-13T11:55:30.849+01:00", "Ukjent"),
        row(2, "2026-07-09T20:02:28.827+02:00", changes=[{"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}]),
        row(3, "2026-07-10T10:00:00+02:00", changes=[{"op": "replace", "path": "/navn", "value": "OTHER AS"}], org="999999999"),
        row(4, "2020-01-01T10:00:00+01:00", "Ny"),
    ])])
    res = fetch_update_events(f, ORG)
    assert res["state"] == "available"
    assert [(e["update_id"], e["event_type"]) for e in res["events"]] == [(2, "annual_accounts_filed"), (4, "registered")]
    assert res["events"][0]["date"] == "2026-07-09"  # Norwegian local date of the timestamp
    assert "organisasjonsnummer=989259660" in f.urls[0] and "includeChanges=true" in f.urls[0]


def test_fetch_states_not_available_and_failed_and_paging():
    assert fetch_update_events(FakeFetcher([page([])]), ORG)["state"] == "not_available"
    assert fetch_update_events(FakeFetcher([page([row(1, "2025-11-13T11:55:30+01:00", "Ukjent")])]), ORG)["state"] == "not_available"
    failed = fetch_update_events(FakeFetcher([None]), ORG)
    assert failed["state"] == "failed" and failed["error"]
    f = FakeFetcher([page([row(1, "2026-01-01T10:00:00+01:00", "Ny")], 2),
                     page([row(2, "2026-02-01T10:00:00+01:00", changes=[{"op": "replace", "path": "/antallAnsatte", "value": 5}])], 2)])
    res = fetch_update_events(f, ORG)
    assert len(f.urls) == 2 and "page=1" in f.urls[1] and len(res["events"]) == 2


def roles_rec():
    return {"status": "available", "source_url": f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}/roller",
            "retrieved_at": "2026-11-06T00:00:00Z", "content_sha256": "abc",
            "value": {"roles": [{"name": "Kari Nordmann", "role": "Styrets leder", "role_code": "LEDE", "group_code": "STYR", "group": "Styre",
                                 "last_changed": "2024-03-01", "inactive": False},
                                {"name": "Ola Nordmann", "role": "Daglig leder", "role_code": "DAGL", "group_code": "DAGL", "group": "Daglig leder",
                                 "last_changed": None, "inactive": False}]}}


def test_role_group_events_only_dated_groups():
    evs = role_group_events(roles_rec())
    assert len(evs) == 1 and evs[0]["date"] == "2024-03-01" and evs[0]["label"].startswith("Board")
    assert role_group_events({"status": "source_error"}) == []


def test_claims_have_evidence_dates_and_states():
    res = fetch_update_events(FakeFetcher([page([row(2, "2026-07-09T20:02:28+02:00", changes=[{"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}])])]), ORG)
    b = Builder(ORG)
    n = add_registry_event_claims(b, res, roles_rec(), ORG)
    assert n == 2
    ev_ids = {e["id"] for e in b.evidence}
    for c in b.claims:
        assert c["family"] == "dated_activity" and c["availability"] == "available"
        assert c["effective_date"] == c["value"]["date"] and set(c["evidence_ids"]) <= ev_ids
    acc = next(c for c in b.claims if c["value"]["event_type"] == "annual_accounts_filed")
    ev = next(e for e in b.evidence if e["id"] == acc["evidence_ids"][0])
    assert "oppdateringsid=2" in ev["claim_span"] and ev["content_sha256"] and ev["retrieved_at"]
    assert "sisteInnsendteAarsregnskap replace = 2025" in ev["claim_span"]
    assert acc["value"]["title"] == "Register update: Annual accounts recorded as filed: latest filed accounts year 2025"
    # checked source with zero results -> not_available with evidence; unreachable -> failed
    b2 = Builder(ORG)
    add_registry_event_claims(b2, fetch_update_events(FakeFetcher([page([])]), ORG), None, ORG)
    assert [c["availability"] for c in b2.claims] == ["not_available"] and b2.claims[0]["evidence_ids"]
    b3 = Builder(ORG)
    add_registry_event_claims(b3, fetch_update_events(FakeFetcher([None]), ORG), None, ORG)
    assert [c["availability"] for c in b3.claims] == ["failed"]


def test_idempotent_claim_keys():
    pages = [page([row(2, "2026-07-09T20:02:28+02:00", changes=[{"op": "replace", "path": "/sisteInnsendteAarsregnskap", "value": "2025"}])])]
    keys = []
    for _ in range(2):
        b = Builder(ORG)
        add_registry_event_claims(b, fetch_update_events(FakeFetcher(pages), ORG), roles_rec(), ORG)
        keys.append([c["key"] for c in b.claims])
    assert keys[0] == keys[1]


def test_oslo_date_is_stable_across_offsets():
    # same instants served with different offsets (seen live on 2026-11-06)
    assert _date("2026-05-12T00:33:10.555+02:00") == _date("2026-05-11T22:33:10.555Z") == "2026-05-12"
    assert _date("2026-02-12T23:04:31.689Z") == _date("2026-02-13T00:04:31.689+01:00") == "2026-02-13"
    assert _date("2026-11-02T23:02:00Z") == "2026-11-03"
    assert _date("2026-03-29T00:30:00Z") == "2026-03-29" and _date("2026-10-25T22:30:00Z") == "2026-10-25"  # DST edges
    assert _date("2024-03-01") == "2024-03-01" and _date("x") is None
