"""Offline tests for the Finanstilsynet licence connector (lane 13)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.fin_licences import add_fin_licence_claims, fetch_fin_licences  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402

ORG = "991937099"


def lic(code, date, owner=106372, en="External accounting firm", no="Regnskapsselskap"):
    return {"licensedEntity": {"legalEntityId": owner}, "licenceType": {"code": code, "name": {"norwegian": no, "english": en}},
            "registeredDate": f"{date}T00:00:00", "serviceProviderType": "Licensed"}


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        for k, v in self.pages.items():
            if k in url:
                return Response(url, url, 200, json.dumps(v).encode(), "application/json", "2026-11-06T06:00:00Z")
        return Response(url, url, 500, b"", "", "2026-11-06T06:00:00Z", 1, "HTTP 500")


SEARCH = {"legal-entities/search": {"legalEntities": [{"legalEntityId": 106372, "organisationNumber": ORG, "name": "1+1 REGNSKAP AS"}]}}
DETAIL = {"legal-entities?ids=106372": {"legalEntities": [{"legalEntityId": 106372, "organisationNumber": ORG, "name": "1+1 REGNSKAP AS",
                                                           "finanstilsynetId": "FT00076199",
                                                           "licences": [lic("REGS", "2007-12-10"), lic("FORAGNT", "2020-01-01", owner=999, en="Agent of another firm")]}]}}


class FinLicenceTests(unittest.TestCase):
    def test_exact_orgnr_and_own_licences_only(self):
        res = fetch_fin_licences(FakeFetcher({**SEARCH, **DETAIL}), ORG)
        self.assertEqual(res["state"], "checked")
        self.assertEqual([(x["code"], x["date"]) for x in res["licences"]], [("REGS", "2007-12-10")])
        b = Builder(ORG)
        self.assertEqual(add_fin_licence_claims(b, res, ORG), 1)
        c = b.claims[0]
        self.assertEqual((c["family"], c["value"]["date"], c["key"]), ("dated_activity", "2007-12-10", "finanstilsynet:REGS:2007-12-10"))
        self.assertIn("External accounting firm (Regnskapsselskap), registered 2007-12-10", c["value"]["title"])
        self.assertIn(f"organisationNumber={ORG}", b.evidence[0]["claim_span"])

    def test_search_hit_for_other_number_is_ignored(self):
        f = FakeFetcher({"legal-entities/search": {"legalEntities": [{"legalEntityId": 1, "organisationNumber": "991937098"}]}})
        res = fetch_fin_licences(f, ORG)
        self.assertEqual((res["state"], res["licences"]), ("checked", []))
        self.assertEqual(len(f.calls), 1)

    def test_failure_states(self):
        self.assertEqual(fetch_fin_licences(FakeFetcher({}), ORG)["state"], "failed")
        self.assertEqual(fetch_fin_licences(FakeFetcher(SEARCH), ORG)["state"], "failed")


if __name__ == "__main__":
    unittest.main()
