"""Offline tests for the Forskningsrådet funded-projects connector (lane 13)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.rcn_projects import RcnProjects, add_rcn_claims  # noqa: E402

HEAD = "prosjektnummer,prosjekttittel,soknadsdato,prosjektfase,prosjektstart,prosjektslutt,prosjektansvarlig_navn,organisasjonsnummer,aktivitet,tildelt_belop"
ROWS = [
    '356039,"ELTERM: Kombinerte Elektriske og Termiske Mikrogrid",2024-10-16 00:00:00,Bevilgning,2025,2029,X AS,962392687,ENERGIFORSKNING,16850000.0',
    "355997,Distributed surveillance,2024-10-16 00:00:00,Avslag,2025,2026,X AS,962392687,Innovasjon,0.0",
    "200118,CREATIVITY,2009-09-16 00:00:00,Avsluttet,2010,2013,X AS,962392687,Nærings-phd,1291875.0",
    "999999,Other company,2020-01-01 00:00:00,Bevilgning,2020,2021,Y AS,111111111,Z,5.0",
    "888888,Pending,2025-01-01 00:00:00,Vurderes,2025,2026,X AS,962392687,Z,100.0",
]


def loaded():
    r = RcnProjects(None, deadline=1e12)
    assert r.load_csv("\n".join([HEAD] + ROWS) + "\n") == 5
    r.status = {"state": "complete"}
    r.ready.set()
    return r


class RcnTests(unittest.TestCase):
    def test_only_funded_projects_of_exact_orgnr(self):
        res = loaded().lookup("962392687")
        self.assertEqual(res["state"], "checked")
        self.assertEqual([p["project"] for p in res["projects"]], ["356039", "200118"])
        self.assertEqual(loaded().lookup("123456789")["projects"], [])

    def test_claims(self):
        b = Builder("962392687")
        self.assertEqual(add_rcn_claims(b, loaded().lookup("962392687"), "962392687"), 2)
        c = b.claims[0]
        self.assertEqual((c["family"], c["value"]["date"], c["key"]), ("dated_activity", "2024-10-16", "forskning:356039"))
        self.assertIn("granted (running); NOK 16 850 000 granted; project 2025–2029; application dated 2024-10-16", c["value"]["title"])
        self.assertIn("organisasjonsnummer=962392687", b.evidence[0]["claim_span"])

    def test_failed_dataset(self):
        r = RcnProjects(None, deadline=1e12)
        r.status = {"state": "failed", "error": "HTTP 404"}
        r.ready.set()
        res = r.lookup("962392687")
        self.assertEqual(res["state"], "failed")
        b = Builder("962392687")
        self.assertEqual(add_rcn_claims(b, res, "962392687"), 0)

    def test_rejects_other_header(self):
        self.assertEqual(RcnProjects(None, deadline=1e12).load_csv("a,b\n1,2\n"), 0)


if __name__ == "__main__":
    unittest.main()
