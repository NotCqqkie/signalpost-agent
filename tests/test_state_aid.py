"""Offline tests for the Støtteregisteret state-aid connector (lane 13)."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent import state_aid  # noqa: E402
from norway_company_agent.agent import Builder  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402

ORG = "961342317"


def award(num, date, status="stoettetiltaksstatus.registrert", recipient=ORG, amount=878.71, instrument="stoetteinstrumenttype.skatteEllerAvgiftsFritak", scheme="1000029830"):
    return {"stoettetiltaksnummer": num, "status": status, "tildelingsdato": date, "mottattDato": date, "stoetteinstrument": instrument,
            "tildeltBeloep": {"beloep": amount, "valuta": "NOK"}, "tilknyttetStoetteordning": scheme,
            "rolle": [{"type": "rolletype.stoettegiver", "rolleinnehaverVirksomhet": {"organisasjonsnummer": "974761076", "navn": "SKATTEETATEN"}},
                      {"type": "rolletype.stoettemottaker", "rolleinnehaverVirksomhet": {"organisasjonsnummer": recipient, "navn": "X AS"}}]}


class FakeFetcher:
    user_agent = "test"

    def remaining(self):
        return 100.0

    def _throttle(self, host):
        pass

    def _count(self, host, n):
        pass


SCHEME = {"stoetteordning": [{"stoettetiltaksnummer": "1000029830", "navn": [{"tekst": "Regionalt DA 2026-", "spraak": "NOR"},
                                                                          {"tekst": "Regionally differentiated social security contributions 2026-", "spraak": "ENG"}]}]}


def run_with(payload, status=200):
    def fake_post(fetcher, url, body, timeout):
        data = SCHEME if "stoetteordning" in url else payload
        raw = json.dumps(data).encode()
        return Response(url, url, status, raw if status == 200 else b"", "application/json", "2026-11-06T06:00:00Z", 5, None if status == 200 else f"HTTP {status}")
    state_aid._SCHEMES.clear()
    with mock.patch.object(state_aid, "_post", side_effect=fake_post) as p, mock.patch.object(state_aid.time, "sleep"):
        res = state_aid.fetch_state_aid(FakeFetcher(), ORG)
        sent = json.loads(p.call_args_list[0][0][2])
    return res, sent


class StateAidTests(unittest.TestCase):
    def test_query_is_exact_recipient_orgnr(self):
        _, sent = run_with({"stoettetildeling": []})
        self.assertEqual(sent, [{"field": "STOETTEMOTTAKER_ORGNR", "value": ORG, "matchType": "EXACT_MATCH"}])

    def test_only_registered_awards_for_this_recipient(self):
        res, _ = run_with({"stoettetildeling": [award("1", "2026-09-07"), award("2", "2026-10-05"), award("3", "2026-10-06", status="stoettetiltaksstatus.feilregistrert"),
                                                award("4", "2026-10-07", recipient="111111111")]})
        self.assertEqual(res["state"], "checked")
        self.assertEqual([a["number"] for a in res["awards"]], ["2", "1"])  # newest first, 3 and 4 dropped

    def test_claims_carry_date_amount_giver_and_evidence(self):
        res, _ = run_with({"stoettetildeling": [award("2", "2026-10-05", amount=14136.5), award("1", "2026-09-07")]})
        b = Builder(ORG)
        self.assertEqual(state_aid.add_state_aid_claims(b, res, ORG), 1)
        c = b.claims[0]
        self.assertEqual((c["family"], c["value"]["date"], c["key"]), ("dated_activity", "2026-10-05", "stotte:974761076:1000029830"))
        self.assertIn("Regionally differentiated social security contributions 2026- — tax or duty exemption of NOK 14 136.50 from SKATTEETATEN, awarded 2026-10-05", c["value"]["title"])
        self.assertIn("(2 awards under this scheme since 2026-09-07)", c["value"]["title"])
        self.assertEqual((c["value"]["awards_in_scheme"], c["value"]["first_award_date"]), (2, "2026-09-07"))
        self.assertEqual(c["value"]["amount"], {"beloep": 14136.5, "valuta": "NOK"})
        ev = b.evidence[0]
        self.assertIn(f"stoettemottaker.organisasjonsnummer={ORG}", ev["claim_span"])
        self.assertTrue(ev["source_url"].startswith(state_aid.API))

    def test_awards_sharing_the_latest_date_are_all_listed(self):
        # audit finding (BAMA EIENDOM, Enova): 5 awards on one day, amounts differ; one arbitrary amount misled
        res, _ = run_with({"stoettetildeling": [award(str(i), "2024-11-05", amount=a, instrument="stoetteinstrumenttype.tilskudd")
                                                for i, a in enumerate([3040000.0, 6080000.0, 3040000.0, 6080000.0, 3040000.0])]})
        b = Builder(ORG)
        state_aid.add_state_aid_claims(b, res, ORG)
        v = b.claims[0]["value"]
        self.assertIn("5 grant awards (NOK 3 040 000 ×3, NOK 6 080 000 ×2) from SKATTEETATEN, all awarded 2024-11-05", v["title"])
        self.assertNotIn("since", v["title"])
        self.assertIsNone(v["amount"])
        self.assertEqual(len(v["amounts_on_date"]), 5)
        self.assertIn("5 awards on 2024-11-05", b.evidence[0]["claim_span"])

    def test_http_error_is_failed_without_claims(self):
        res, _ = run_with({}, status=503)
        self.assertEqual(res["state"], "failed")
        b = Builder(ORG)
        self.assertEqual(state_aid.add_state_aid_claims(b, res, ORG), 0)
        self.assertEqual(b.claims, [])

    def test_one_claim_per_scheme_capped(self):
        res, _ = run_with({"stoettetildeling": [award(str(i), f"2026-01-{i + 1:02d}", scheme=str(5000 + i % 12)) for i in range(25)]})
        b = Builder(ORG)
        self.assertEqual(state_aid.add_state_aid_claims(b, res, ORG), state_aid.MAX_PER_COMPANY)
        self.assertEqual(b.claims[0]["value"]["date"], "2026-01-25")
        self.assertEqual(len({c["key"] for c in b.claims}), state_aid.MAX_PER_COMPANY)


if __name__ == "__main__":
    unittest.main()
