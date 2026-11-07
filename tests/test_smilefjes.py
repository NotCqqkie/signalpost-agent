"""Offline tests for the Mattilsynet smilefjes connector (lane 13)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402
from norway_company_agent.smilefjes import SmilefjesIndex, add_smilefjes_claims, fetch_company_ratings, parse_page  # noqa: E402

BLOCK = ('<div class="p-1 text-center" data-select_element_id="{tid}_TilsynAvtale" data-selected_class="x">'
         '<div class="w-8 my-2" title="Spisestedet har fått {grade}."><svg viewBox="0 0 1 1"><path d="m0"></path></svg></div>'
         '<div class="text-xs">{d}</div></div>')


def page(orgnr="972256242", blocks=(("Z2510080856038980240MTRRE", "blidt smilefjes", "08.10.25"), ("Z2509101820033640239PBBXS", "strekmunn", "10.09.25"),
                                    ("Z2001021249442600239JLFLM", "sur munn", "04.09.19"))):
    return ("<html><h1 class=\"x\">Napoli Restaurant og Pizzeria</h1><div>Orgnr. " + orgnr + "</div><p>Tilsynsresultater:</p>"
            + "".join(BLOCK.format(tid=t, grade=g, d=d) for t, g, d in blocks) + "<p>Siste tilsynsresultat: 08.10.2025</p></html>")


URL = "https://smilefjes.mattilsynet.no/spisested/trondheim/napoli.Z1912271118015873140YYLWD/"
INDEX = {"index": {}, "lookup": [["/spisested/trondheim/napoli.Z1912271118015873140YYLWD/", "Napoli Restaurant og Pizzeria", "Kongens gate 32", "", "7012", "Trondheim", [["0", "2025-10-08"]]],
                                 ["/spisested/oslo/other.Z1/", "Other Cafe", "Storgata 1", "", "0155", "Oslo", []]]}


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def remaining(self):
        return 100.0

    def get(self, url, **kw):
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None:
            return Response(url, url, 404, b"", retrieved_at="2026-11-06T06:00:00Z")
        return Response(url, url, 200, body.encode(), "text/html; charset=utf-8", retrieved_at="2026-11-06T06:00:00Z")


def ready_index():
    idx = SmilefjesIndex(None, deadline=1e12)
    idx.load_json(INDEX)
    idx.status = {"state": "complete"}
    idx.ready.set()
    return idx


def test_parse_page_reads_orgnr_dates_and_grades():
    p = parse_page(page())
    assert p["orgnr"] == "972256242"
    assert p["name"] == "Napoli Restaurant og Pizzeria"
    assert [(i["date"], i["grade_text"]) for i in p["inspections"]] == [("2025-10-08", "blidt smilefjes"), ("2025-09-10", "strekmunn"), ("2019-09-04", "sur munn")]
    assert p["latest_date"] == "2025-10-08"


def test_candidates_by_address_and_name():
    idx = ready_index()
    assert idx.candidates([({"adresse": ["Kongens gate 32"], "postnummer": "7012"}, "X AS")]) == [URL]
    assert idx.candidates([({"adresse": ["Annen vei 3"], "postnummer": "7012"}, "Napoli Restaurant og Pizzeria AS")]) == [URL]
    assert idx.candidates([({"adresse": ["Kongens gate 32"], "postnummer": "7013"}, "Y")]) == []


def test_publishes_only_when_page_orgnr_is_company_or_subunit():
    idx = ready_index()
    places = [({"adresse": ["Kongens gate 32"], "postnummer": "7012"}, "Holding AS")]
    ok = fetch_company_ratings(FakeFetcher({URL: page()}), idx, "999999999", places, {"972256242": "NAPOLI AVD"})
    assert ok["state"] == "checked" and len(ok["establishments"]) == 1 and ok["establishments"][0]["relation"] == "registered_subunit"
    other = fetch_company_ratings(FakeFetcher({URL: page(orgnr="111111111")}), idx, "999999999", places, {"972256242": "NAPOLI AVD"})
    assert other["state"] == "checked" and other["establishments"] == []


def test_claims_rating_and_dated_inspections():
    idx = ready_index()
    res = fetch_company_ratings(FakeFetcher({URL: page()}), idx, "972256242", [({"adresse": ["Kongens gate 32"], "postnummer": "7012"}, "N")], {})
    b = Builder("972256242")
    assert add_smilefjes_claims(b, res) == 4
    rating = [c for c in b.claims if c["family"] == "ratings_reviews"]
    assert len(rating) == 1 and rating[0]["availability"] == "available"
    assert rating[0]["value"]["rating"] == "smiling" and rating[0]["value"]["date"] == "2025-10-08" and rating[0]["value"]["inspections_listed"] == 3
    acts = [c for c in b.claims if c["family"] == "dated_activity"]
    assert {c["value"]["rating"] for c in acts} == {"smiling", "straight_mouth", "sad"}
    assert all(b.evidence[int(c["evidence_ids"][0].split("-")[1]) - 1]["source_url"] == URL for c in acts)
    assert all("Orgnr. 972256242" in e["claim_span"] for e in b.evidence)


def test_not_available_and_failed_states():
    b = Builder("1")
    add_smilefjes_claims(b, {"state": "checked", "establishments": [], "candidates": 0})
    assert b.claims[0]["availability"] == "not_available" and b.claims[0]["family"] == "ratings_reviews"
    b2 = Builder("1")
    idx = SmilefjesIndex(None, deadline=1e12)
    idx.status = {"state": "failed", "error": "HTTP 500"}
    idx.ready.set()
    add_smilefjes_claims(b2, fetch_company_ratings(FakeFetcher({}), idx, "1", [], {}))
    assert b2.claims[0]["availability"] == "failed"


CSV_TEXT = "".join(line + chr(10) for line in [
    "tilsynsobjektid;orgnummer;navn;adrlinje1;adrlinje2;postnr;poststed;tilsynid;sakref;status;dato;total_karakter;tilsynsbesoektype;tema1_no",
    "Z1912271118015873140YYLWD_Tilsynsobjekt;972256242;Napoli Restaurant og Pizzeria;Kongens gate 32;;7012;Trondheim;Z2510080856038980240MTRRE_TilsynAvtale;1;0;08102025;0;1;x",
    "Z1912271118015873140YYLWD_Tilsynsobjekt;972256242;Napoli Restaurant og Pizzeria;Kongens gate 32;;7012;Trondheim;Z2509101820033640239PBBXS_TilsynAvtale;1;0;10092025;2;0;x",
    "ZOTHER_Tilsynsobjekt;111111111;Other;Storgata 1;;0155;Oslo;ZT3_TilsynAvtale;1;0;01012024;3;0;x"])


def csv_index():
    idx = SmilefjesIndex(None, deadline=1e12)
    assert idx.load_csv(CSV_TEXT)
    idx.mode = "csv"
    idx.status = {"state": "complete"}
    idx.ready.set()
    return idx


def test_csv_mode_matches_subunit_orgnr_without_requests():
    idx = csv_index()
    f = FakeFetcher({})
    res = fetch_company_ratings(f, idx, "999999999", [], {"972256242": "NAPOLI AVD"})
    assert f.calls == [] and res["state"] == "checked" and len(res["establishments"]) == 1
    est = res["establishments"][0]
    assert est["relation"] == "registered_subunit" and est["url"].endswith("/spisested/Z1912271118015873140YYLWD/")
    b = Builder("999999999")
    assert add_smilefjes_claims(b, res) == 3
    rating = [c for c in b.claims if c["family"] == "ratings_reviews"][0]
    assert rating["value"]["rating"] == "smiling" and rating["value"]["date"] == "2025-10-08"
    acts = sorted(c["value"]["date"] + c["value"]["rating"] + c["value"]["visit"] for c in b.claims if c["family"] == "dated_activity")
    assert acts == ["2025-09-10straight_mouthordinary inspection", "2025-10-08smilingfollow-up inspection"]
    assert all(e["source_url"].endswith("/api/tilsyn.csv") and "orgnummer=972256242" in e["claim_span"] for e in b.evidence)


def test_csv_mode_no_match_is_not_available():
    res = fetch_company_ratings(FakeFetcher({}), csv_index(), "999999999", [], {"123456789": "X"})
    b = Builder("999999999")
    add_smilefjes_claims(b, res)
    assert [c["availability"] for c in b.claims] == ["not_available"]


def test_load_csv_rejects_other_content():
    assert not SmilefjesIndex(None, deadline=1e12).load_csv("<html>not csv</html>")


class SmilefjesTests(unittest.TestCase):
    pass


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        setattr(SmilefjesTests, _name, staticmethod(_fn))

if __name__ == "__main__":
    unittest.main()
