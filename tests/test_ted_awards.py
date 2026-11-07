"""Offline tests for the TED contract-award connector (lane 3)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.ted_awards import TedAwards, add_ted_claims, build_query, match_notice  # noqa: E402

ORG = "984024290"


def notice(pub: str, ids: list[str], countries: list[str], date: str = "2024-02-27Z", winners: list[int] | None = None) -> dict:
    """As the Search API returns it: winner-identifier = every tenderer; winner-name = winners only (default: all)."""
    names = [f"Tenderer {i} AS" for i in range(len(ids))]
    won = [names[i] for i in (range(len(ids)) if winners is None else winners)]
    return {"publication-number": pub, "publication-date": date, "notice-type": "can-standard", "notice-title": {"eng": f"Norway – Works – {pub}"},
            "buyer-name": {"eng": ["Kristiansund kommune"]}, "winner-identifier": ids, "winner-country": countries, "winner-name": {"eng": won},
            "organisation-identifier-tenderer": ids, "organisation-name-tenderer": {"eng": names}}


def run(pages: list, orgs: list[str]) -> TedAwards:
    calls = []

    def post(body):
        calls.append(body)
        p = pages[min(len(calls) - 1, len(pages) - 1)]
        return (200, json.dumps(p).encode(), None) if p is not None else (503, b"", "HTTP 503")
    t = TedAwards(deadline=time.monotonic() + 30, interval_s=0, post=post)
    t.calls = calls
    t._run(orgs)
    return t


def test_query_has_both_spellings_and_date():
    q = build_query([ORG], "2021-01-01")
    assert '984024290' in q and '"984 024 290"' in q and "publication-date>=20210101" in q


def test_match_requires_norwegian_aligned_winner():
    assert match_notice(notice("1-2024", ["984 024 290"], ["NOR"]), {ORG}) == [ORG]
    assert match_notice(notice("2-2024", [ORG], ["FRA"]), {ORG}) == []  # same digits, another country's identifier
    assert match_notice(notice("3-2024", [ORG, "1"], ["NOR"]), {ORG}) == []  # cannot align -> not published
    assert match_notice(notice("4-2024", ["NO984024290MVA"], ["NOR"]), {ORG}) == [ORG]


def test_lost_bid_is_not_a_win():
    # TED 699963-2024 shape: three tenderers in winner-identifier, only Nasta AS in winner-name
    n = notice("699963-2024", ["914523621", ORG, "933442691"], ["NOR"] * 3, winners=[0])
    assert match_notice(n, {ORG}) == [] and match_notice(n, {"914523621"}) == ["914523621"]
    assert match_notice(notice("5-2024", [ORG, "1"], ["NOR", "NOR"], winners=[1]), {ORG}) == []
    unaligned = notice("6-2024", [ORG], ["NOR"])
    unaligned["organisation-name-tenderer"] = {"eng": []}
    assert match_notice(unaligned, {ORG}) == []  # cannot tie the number to a winner name: not published
    assert match_notice(notice("7-2024", [ORG], ["NOR"]) | {"winner-name": {"eng": ["tenderer 0 as"]}}, {ORG}) == [ORG]  # case-insensitive


def test_batch_run_claims_and_dedupe():
    page = {"notices": [notice("120822-2024", [ORG], ["NOR"]), notice("120822-2024", [ORG], ["NOR"]),
                        notice("10384-2024", ["955880447", ORG], ["NOR", "NOR"], "2024-01-08Z")], "totalNoticeCount": 3}
    t = run([page], [ORG, "915665721"])
    assert t.status["state"] == "complete" and t.checked == {ORG, "915665721"}
    assert [a["publication_number"] for a in t.by_org[ORG]] == ["120822-2024", "10384-2024"]
    b = Builder(ORG)
    assert add_ted_claims(b, t, ORG) == 2
    c = b.claims[0]
    assert c["family"] == "dated_activity" and c["value"]["date"] == "2024-02-27" and c["value"]["url"].endswith("/120822-2024")
    assert "© European Union" in c["value"]["attribution"] and b.evidence[0]["content_sha256"]
    assert add_ted_claims(Builder("915665721"), t, "915665721") == 0


def test_failed_source_is_partial_not_published():
    t = run([None], [ORG])
    assert t.status["state"] == "partial" and t.status["failed_batches"] == 1 and not t.by_org and not t.checked


def test_pagination_stops_at_total():
    p1 = {"notices": [notice(f"{i}-2025", [ORG], ["NOR"]) for i in range(100)], "totalNoticeCount": 101}
    p2 = {"notices": [notice("999-2025", [ORG], ["NOR"])], "totalNoticeCount": 101}
    t = run([p1, p2], [ORG])
    assert len(t.calls) == 2 and t.calls[1]["page"] == 2 and len(t.by_org[ORG]) == 10  # capped per company
