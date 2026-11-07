"""Lane 9: a slow, failing or unreachable register, corrupt side inputs and pipeline crashes still give exactly one
honest terminal envelope per input, within the time budget."""
from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import norway_company_agent.http as http  # noqa: E402
from norway_company_agent.agent import Agent  # noqa: E402
from norway_company_agent.sampling import iter_bulk  # noqa: E402

A = "923609016"


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    h = Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x", code, "x", h, io.BytesIO(b""))


def test_fetch_json_respects_deadline(monkeypatch):
    calls = []

    def slow(req, timeout=None):
        calls.append(timeout)
        time.sleep(min(timeout, 5))
        raise TimeoutError("slow")
    monkeypatch.setattr(http.urllib.request, "urlopen", slow)
    t = time.monotonic()
    r = http.fetch_json("https://data.brreg.no/x", deadline=time.monotonic() + 3)
    assert time.monotonic() - t < 4.5 and r.status == 0 and r.error
    assert all(c <= 3 for c in calls)


def test_fetch_json_past_deadline_makes_no_request(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request expected")))
    r = http.fetch_json("https://data.brreg.no/x", deadline=time.monotonic() + 0.5)
    assert r.status == 0 and r.error == "deadline"


def test_fetch_json_429_backs_off_then_succeeds(monkeypatch):
    seq = [_http_error(429, "1"), _http_error(503)]
    sleeps = []

    class Ok:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        if seq:
            raise seq.pop(0)
        return Ok()
    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    r = http.fetch_json("https://data.brreg.no/x")
    assert r.status == 200 and r.body == {"ok": True}
    assert sleeps and all(s >= 2.0 for s in sleeps)


def test_fetch_json_400_not_retried(monkeypatch):
    n = []
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda req, timeout=None: n.append(1) or (_ for _ in ()).throw(_http_error(400)))
    r = http.fetch_json("https://data.brreg.no/x")
    assert len(n) == 1 and r.error == "HTTP 400"


def _agent() -> Agent:
    return Agent(run_id="t", budget_s=60, modules={"registry_live", "financials", "roles"}, strategies=set(), log=lambda m: None)


def test_unreachable_register_is_failed_not_not_found():
    ag = _agent()
    st = {"official": {"registry_live": {"status": "source_error", "note": "URLError"}}, "errors": []}
    env = ag.envelope(A, st, None, [], "not_run", None, "2026-11-06T00:00:00Z")
    assert env["state"] == "failed" and "URLError" in env["errors"][0]["error"]


def test_404_is_not_found_and_invalid_number_skips_network(monkeypatch):
    ag = _agent()
    env = ag.envelope(A, {"official": {"registry_live": {"status": "not_found"}}, "errors": []}, None, [], "not_run", None, "x")
    assert env["state"] == "not_found" and all(c["availability"] == "not_available" for c in env["claims"])
    import norway_company_agent.agent as agent_mod
    monkeypatch.setattr(agent_mod, "fetch_official_modules", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network for invalid numbers")))
    st = ag.phase_a("92360901", None)
    env = ag.envelope("92360901", st, None, [], "not_run", None, "x")
    assert env["state"] == "not_found" and "mod-11" in env["errors"][0]["error"]


def test_subunit_input_names_its_parent():
    ag = _agent()
    st = {"official": {"registry_live": {"status": "not_found"}}, "errors": [],
          "subunit_record": {"source_url": "https://data.brreg.no/enhetsregisteret/api/underenheter/973861883", "retrieved_at": "t", "content_sha256": "h",
                             "body": {"organisasjonsnummer": "973861883", "navn": "EQUINOR ASA AVD X", "overordnetEnhet": A}}}
    env = ag.envelope("973861883", st, None, [], "not_run", None, "x")
    assert env["state"] == "not_found" and env["identity"]["parent_organisation_number"] == A
    parent = [c for c in env["claims"] if c["field"] == "parent_organisation_number"][0]
    assert parent["availability"] == "available" and parent["evidence_ids"]
    assert "subunit" in env["summary"]["text"] and "failed" not in env["summary"]["text"]


def test_empty_batch_does_not_start_nav_or_ted():
    ag = Agent(run_id="t", budget_s=60, log=lambda m: None)
    t = time.monotonic()
    envs, report = ag.run([], {}, {})
    assert envs == [] and time.monotonic() - t < 5 and report["nav"] is None


def test_iter_bulk_reads_plain_and_gzip_csv(tmp_path):
    header = '"organisasjonsnummer";"navn";"organisasjonsform.kode"\n'
    row = f'"{A}";"EQUINOR ASA";"ASA"\n'
    (tmp_path / "b.csv").write_text(header + row, encoding="utf-8")
    with gzip.open(tmp_path / "b.csv.gz", "wt", encoding="utf-8") as f:
        f.write(header + row)
    for name in ("b.csv", "b.csv.gz"):
        assert [r["organisation_number"] for r in iter_bulk(tmp_path / name)] == [A]


def _load_batch_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rcb", ROOT / "scripts" / "run_competition_batch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_read_previous_skips_corrupt_lines(tmp_path):
    p = tmp_path / "prev.jsonl"
    p.write_bytes(b'{"organisation_number": "923609016", "claims": [{"x": 1}, {"key": "k", "field": "f", "availability": "available"}]}\n'
                  b'{truncated\n\n[1,2]\n{"organisation_number": 5}\n{"organisation_number": "986389180", "claims": "oops"}\n')
    prev = _load_batch_module().read_previous(str(p))
    assert set(prev) == {"923609016", "986389180"}
    assert len(prev["923609016"]["claims"]) == 1 and prev["986389180"]["claims"] == []


def _run_patched(tmp_path, patch: str, extra: list[str], env_extra: dict | None = None, timeout: float = 60):
    inp = tmp_path / "in.jsonl"
    inp.write_text(f'{{"organisation_number": "{A}"}}\n{{"organisation_number": "986389180"}}\n', encoding="utf-8")
    out = tmp_path / "out.jsonl"
    code = (f"import sys, runpy, time; sys.path.insert(0, {str(ROOT / 'src')!r}); import norway_company_agent.agent as m; {patch}; "
            f"sys.argv = ['x', '--organisations', {str(inp)!r}, '--output', {str(out)!r}] + {extra!r}; "
            f"runpy.run_path({str(ROOT / 'scripts' / 'run_competition_batch.py')!r}, run_name='__main__')")
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env_extra or {})})
    envs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()] if out.exists() else []
    return p, envs, time.monotonic() - t


def test_pipeline_crash_still_emits_every_envelope(tmp_path):
    p, envs, _ = _run_patched(tmp_path, "m.Agent.run = lambda self, *a, **k: 1 / 0", ["--modules", "registry_live", "--strategies", "none"])
    assert [e["organisation_number"] for e in envs] == [A, "986389180"]
    assert all(e["state"] == "failed" for e in envs) and "ZeroDivisionError" in envs[0]["errors"][0]["error"]
    assert p.returncode == 0


def test_watchdog_writes_envelopes_when_pipeline_hangs(tmp_path):
    p, envs, wall = _run_patched(tmp_path, "m.Agent.run = lambda self, *a, **k: time.sleep(120)",
                                 ["--modules", "registry_live", "--strategies", "none", "--time-budget-seconds", "3"],
                                 {"SIGNALPOST_WATCHDOG_GRACE_SECONDS": "2"})
    assert wall < 30 and p.returncode == 0
    assert [e["organisation_number"] for e in envs] == [A, "986389180"] and all(e["state"] == "failed" for e in envs)


def test_ssrf_guard_defers_to_configured_proxy_when_dns_fails(monkeypatch):
    import socket
    import pytest
    import norway_company_agent.website as w

    def no_dns(*a, **k):
        raise socket.gaierror(11001, "no dns")
    monkeypatch.setattr(w.socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(w.urllib.request, "getproxies", lambda: {})
    with pytest.raises(ValueError):
        w.assert_public_url("https://example.no/")
    monkeypatch.setattr(w.urllib.request, "getproxies", lambda: {"https": "http://proxy.internal:3128"})
    monkeypatch.setattr(w.urllib.request, "proxy_bypass", lambda h: False)
    w.assert_public_url("https://example.no/")  # proxy resolves
    with pytest.raises(ValueError):
        w.assert_public_url("https://localhost/")  # local names stay blocked


def test_name_guesses_go_through_proxy_when_local_dns_fails(monkeypatch):
    import socket
    import norway_company_agent.footprint as fp
    import norway_company_agent.website as w

    def no_dns(*a, **k):
        raise socket.gaierror(11001, "no dns")
    tried = []
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(fp, "try_candidate", lambda f, prof, url, source: tried.append(url) or {"url": url, "source": source, "verdict": {"status": "not_available", "reasons": ["x"]}})

    class F:
        def remaining(self):
            return 100.0
    monkeypatch.setattr(w.urllib.request, "getproxies", lambda: {})
    r = fp.discover_website(F(), {"name": "HELGEVOLD AS"}, guess=True)
    assert not tried and all("does not resolve" in a["verdict"]["reasons"][0] for a in r["attempts"])
    monkeypatch.setattr(w.urllib.request, "getproxies", lambda: {"https": "http://proxy.internal:3128"})
    monkeypatch.setattr(w.urllib.request, "proxy_bypass", lambda h: False)
    fp.discover_website(F(), {"name": "HELGEVOLD AS"}, guess=True)
    assert tried and any("helgevold" in u for u in tried)


def test_unreached_sources_make_envelope_partial_but_site_down_does_not():
    ag = _agent()
    reg = {"organisation_number": A, "name": "EQUINOR ASA", "raw": {"navn": "EQUINOR ASA", "organisasjonsform.kode": "ASA"},
           "bulk_sha256": "h", "bulk_retrieved_at": "t"}
    off = {"registry_live": {"status": "source_error"}, "financials": {"status": "source_error", "note": "TimeoutError"},
           "roles": {"status": "source_error"}, "locations": {"status": "source_error"}}
    env = ag.envelope(A, {"official": off, "errors": []}, reg, [], "not_run", None, "x")
    assert env["state"] == "partial" and any(e["step"] == "sources" and "financials" in e["error"] for e in env["errors"])
    ok = {"registry_live": {"status": "source_error"}, "financials": {"status": "not_found", "source_url": "u"},
          "roles": {"status": "available", "source_url": "u", "value": {"roles": []}}, "locations": {"status": "not_found"}}
    ag2 = Agent(run_id="t", budget_s=60, modules={"registry_live", "financials", "roles", "website"}, strategies=set(), log=lambda m: None)
    site_down = {"status": "failed", "attempts": [{"url": "https://x.no/", "source": "registry", "verdict": {"status": "failed", "reasons": ["HTTP 503"]}}]}
    env2 = ag2.envelope(A, {"official": ok, "errors": [], "site": site_down}, reg, [], "complete", None, "x")
    assert env2["state"] == "completed"


def test_bulk_fallback_has_live_types_and_refresh_sees_no_false_change():
    from norway_company_agent.agent import Builder, add_identity, refresh_diff
    raw = {"navn": "SANDNES ELEKTRISKE AS", "organisasjonsform.kode": "AS", "konkurs": "false", "underAvvikling": "true", "antallAnsatte": "10",
           "forretningsadresse.adresse": "Strandgata 21", "forretningsadresse.postnummer": "4307", "forretningsadresse.poststed": "SANDNES",
           "forretningsadresse.kommune": "SANDNES", "forretningsadresse.kommunenummer": "1108", "forretningsadresse.land": "Norge",
           "forretningsadresse.landkode": "NO", "stiftelsesdato": "2013-01-01"}
    b = Builder("810034882")
    ident = add_identity(b, {"raw": raw, "name": raw["navn"], "bulk_sha256": "h", "bulk_retrieved_at": "t"}, None)
    assert ident["bankrupt"] is False and ident["liquidating"] is True and ident["employees"] == 10
    live_addr = {"land": "Norge", "landkode": "NO", "postnummer": "4307", "poststed": "SANDNES", "adresse": ["Strandgata 21"], "kommune": "SANDNES", "kommunenummer": "1108"}
    assert ident["business_address"] == live_addr
    prev = {"claims": [{"key": "bankrupt", "field": "bankrupt", "value": False, "availability": "available", "evidence_ids": []},
                       {"key": "registered_employees", "field": "registered_employees", "value": 10, "availability": "available", "evidence_ids": []},
                       {"key": "business_address", "field": "business_address", "value": live_addr, "availability": "available", "evidence_ids": []}],
            "evidence": []}
    assert [c for c in refresh_diff(prev, b) if c["field"] in ("bankrupt", "registered_employees", "business_address")] == []
    moved = {**live_addr, "adresse": ["Langgata 1"]}
    prev["claims"][2]["value"] = moved
    assert [c["field"] for c in refresh_diff(prev, b) if c["field"] == "business_address"] == ["business_address"]  # real change still seen


def test_compact_state_keeps_what_envelopes_need():
    from norway_company_agent.agent import Builder, add_footprint, compact_state
    from norway_company_agent.footprint import Page
    from norway_company_agent.net import Response
    html = b"<html><head><title>Helgevold AS</title></head><body><a href='/kontakt'>Kontakt</a><footer>Org.nr 986 389 180</footer></body></html>"
    resp = Response("https://helgevold.com/", "https://helgevold.com/", 200, html, "text/html; charset=utf-8", "2026-11-06T00:00:00Z",
                    redirect_chain=["https://helgevold.com/"])
    sha = resp.sha256
    sel = {"url": "https://helgevold.com/", "source": "name_guess", "pages": [Page(resp, "home")],
           "verdict": {"status": "verified", "method": "orgnr_on_site", "proof_url": "https://helgevold.com/", "proof_retrieved_at": "t",
                       "proof_sha256": sha, "span": "Org.nr 986 389 180"}}
    other = {"url": "https://helgevold.no/", "source": "name_guess", "pages": [Page(resp, "home")], "verdict": {"status": "not_available", "reasons": ["x"]}}
    st = {"site": {"status": "verified", "selected": sel, "attempts": [other, sel]}}
    compact_state(st)
    assert "pages" not in other and sel["pages"][0].soup is None and sel["pages"][0].links == []
    b = Builder("986389180")
    add_footprint(b, st["site"], None, [], "complete", {})
    site = [c for c in b.claims if c["field"] == "official_website"][0]
    assert site["availability"] == "available" and site["value"] == "https://helgevold.com/"
    assert any(e["content_sha256"] == sha for e in b.evidence)


def test_switched_off_module_does_not_make_envelope_partial():
    ag = _agent()  # modules: registry_live, financials, roles (no locations/group)
    reg = {"organisation_number": A, "name": "EQUINOR ASA", "raw": {"navn": "EQUINOR ASA"}, "bulk_sha256": "h", "bulk_retrieved_at": "t"}
    off = {"registry_live": {"status": "available", "source_url": "u", "value": {"name": "EQUINOR ASA", "raw": {"navn": "EQUINOR ASA"}}},
           "financials": {"status": "not_found", "source_url": "u"}, "roles": {"status": "available", "source_url": "u", "value": {"roles": []}}}
    env = ag.envelope(A, {"official": off, "errors": []}, reg, [], "not_run", None, "x")
    assert env["state"] == "completed"


def test_unsupported_accounts_layout_is_not_available_not_failed(monkeypatch):
    # Regnskapsregisteret answers HTTP 500 "oppstillingsplan som ikke er stottet (SKADE)" for insurers, banks, pension
    # funds, non-profits (fresh-1000 run: 4 envelopes were partial because of it)
    from norway_company_agent.agent import add_financials
    from norway_company_agent.agent import Builder
    from norway_company_agent.official import fetch_official_modules
    body = b'{"status":"500","message":"Regnskapet inneholder en oppstillingsplan som ikke er stottet (SKADE)"}'
    calls = []
    def fake(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError("https://x", 500, "x", Message(), io.BytesIO(body))
    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    recs, _ = fetch_official_modules("987268069", {"financials"})
    assert recs["financials"]["status"] == "unsupported_layout" and len(calls) == 1
    b = Builder("987268069")
    add_financials(b, recs["financials"])
    assert [c["availability"] for c in b.claims] == ["not_available"] and b.claims[0]["evidence_ids"]


def test_fetch_json_500_burst_backs_off_then_succeeds(monkeypatch):
    # main2: smoke of 5452840 lost two companies to a brief BRREG HTTP 500 burst (0.4 s + 0.8 s back-off was too short)
    seq = [_http_error(500), _http_error(500), _http_error(502)]
    sleeps = []

    class Ok:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        if seq:
            raise seq.pop(0)
        return Ok()
    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    r = http.fetch_json("https://data.brreg.no/enhetsregisteret/api/enheter/932457113")
    assert r.status == 200 and len(sleeps) == 3 and sum(sleeps) >= 10


def test_register_source_error_is_retried_before_envelopes(monkeypatch):
    import norway_company_agent.agent as agent_mod
    from norway_company_agent.official import fetch_official_modules as real
    calls = []

    def flaky(org, modules, fetcher=None):
        calls.append(sorted(modules))
        if len(calls) == 1:  # phase A: the register answers HTTP 500
            return {m: {"status": "source_error", "note": "HTTP 500", "source_url": "u"} for m in modules}, []
        return {m: {"status": "available", "value": {"name": "EQUINOR ASA", "organisation_number": A}, "source_url": "u",
                    "retrieved_at": "t", "content_sha256": "h"} for m in modules}, []
    monkeypatch.setattr(agent_mod, "fetch_official_modules", flaky)
    ag = Agent(run_id="t", budget_s=120, modules={"registry_live"}, strategies=set(), log=lambda m: None)
    envs, _ = ag.run([A], {}, {})
    assert len(calls) == 2 and calls[1] == ["registry_live"]
    assert envs[0]["state"] != "failed"
