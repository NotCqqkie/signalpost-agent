"""Synthesis v2 (lane 2b): peer context, decision notes, English industry, self-description filter, brief."""
import copy
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent import peers  # noqa: E402
from norway_company_agent.industry_en import CODE_EN, DIVISION_EN, english_industry  # noqa: E402
from norway_company_agent.synthesis import add_synthesis, build_synthesis, render_text, summary_from_synthesis, validate_synthesis  # noqa: E402
from test_synthesis import envelope  # noqa: E402


def fake_reference(n: int = 200, code: str = "43.210") -> dict:
    """A reference table from synthetic peers: revenue 1..n million, equity ratio 0..99%, growth -50..+49%."""
    rng = random.Random(1)
    sample = []
    for i in range(n):
        rev = (i + 1) * 1e6
        assets = rng.choice([5e5, 5e6, 5e7, 5e8])
        sample.append({"industry_code": code, "records": [
            {"account_type": "SELSKAP", "period": {"tilDato": "2025-12-31"}, "revenue": rev, "operating_result": rev * ((i % 20) - 5) / 100,
             "annual_result": rev * ((i % 10) - 3) / 100, "assets": assets, "equity": assets * (i % 100) / 100},
            {"account_type": "SELSKAP", "period": {"tilDato": "2024-12-31"}, "revenue": rev / (1 + ((i % 100) - 50) / 100)}]})
    universe = [{"industry_code": code, "municipality_number": "4611"}] * 50 + [{"industry_code": "68.200", "municipality_number": "0301"}] * 30
    return peers.build_reference(universe, sample, meta={"sample_size": n})


class PeerTableTests(unittest.TestCase):
    def test_percentile_of_bounds_ties_and_interpolation(self):
        q = [float(p) for p in peers.PCTS]  # value == percentile
        self.assertEqual(peers.percentile_of(-1, q), 2.5)
        self.assertEqual(peers.percentile_of(1000, q), 97.5)
        self.assertEqual(peers.percentile_of(50, q), 50)
        self.assertAlmostEqual(peers.percentile_of(52.5, q), 52.5)
        flat = [0.0] * 10 + [float(i) for i in range(1, 10)]
        self.assertEqual(peers.percentile_of(0, flat), 27.5)  # tied block 5..50 -> middle

    def test_build_reference_groups_and_min_peers(self):
        ref = fake_reference(60)
        g = ref["groups"]
        self.assertIn("all", g)
        self.assertIn("d43", g)
        self.assertEqual(g["d43"]["revenue"]["n"], 60)
        for key, metrics in g.items():
            for st in metrics.values():
                self.assertGreaterEqual(st["n"], peers.MIN_PEERS, key)
                self.assertEqual(len(st["q"]), len(peers.PCTS))
                self.assertEqual(st["q"], sorted(st["q"]))
        self.assertEqual(ref["code_counts"]["43.210"], 50)
        self.assertEqual(ref["division_municipality_counts"]["43|4611"], 50)

    def test_position_falls_back_to_broader_group(self):
        ref = fake_reference(60)
        p = peers.position("revenue", 30e6, "43.210", 5e6, ref)  # band group too small -> division
        self.assertEqual(p["group"], "d43")
        self.assertIsNone(peers.position("revenue", 1.0, "43.210", 5e6, {"groups": {}}))

    def test_bands_and_divisions(self):
        self.assertEqual(peers.band(999_999), 0)
        self.assertEqual(peers.band(1e6), 1)
        self.assertEqual(peers.band(1e8), 3)
        self.assertIsNone(peers.band(None))
        self.assertIsNone(peers.division("00.000"))
        self.assertEqual(peers.section("43"), "F")

    def test_committed_reference_is_loadable_and_documented(self):
        ref = peers.load_reference()
        self.assertIsNotNone(ref, "src/norway_company_agent/peer_reference.json must ship with the agent")
        m = ref["meta"]
        for k in ("sample_size", "sample_with_accounts", "sample_manifest_sha256", "accounts_source", "universe_sha256", "retrieved_from", "retrieved_to"):
            self.assertTrue(m.get(k), k)
        self.assertEqual(m["universe_rows"], 411160)
        self.assertEqual(sum(ref["code_counts"].values()), 411160)


class PeerSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.ref = fake_reference(200)
        self.patch = mock.patch.object(peers, "load_reference", return_value=self.ref)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_peer_sentence_cites_claims_and_reference(self):
        env = envelope()
        syn = build_synthesis(env)
        self.assertEqual(validate_synthesis(env, syn), [])
        s = [x for x in syn["sentences"] if x.get("derived") == "peer_position"]
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["reference_ids"], ["ref-peers"])
        self.assertIn("ref-peers", {r["id"] for r in syn["references"]})
        ind = next(c["id"] for c in env["claims"] if c["field"] == "industry")
        self.assertIn(ind, s[0]["claim_ids"])
        # revenue 461 million is above every synthetic peer (1..200 million)
        self.assertGreaterEqual(s[0]["peer_positions"]["revenue"]["percentile"], 90)
        self.assertIn("top 10%", s[0]["text"])

    def test_market_counts_from_universe(self):
        env = envelope()
        syn = build_synthesis(env)
        m = [x for x in syn["sentences"] if x["topic"] == "market"]
        self.assertEqual(len(m), 1)
        self.assertIn("50 of the 80 entities", m[0]["text"])
        self.assertEqual(m[0]["reference_ids"], ["ref-universe"])

    def test_foreign_currency_compares_ratios_only(self):
        env = envelope()
        for c in env["claims"]:
            if c.get("family") == "financials":
                c["unit"] = "USD"
        s = [x for x in build_synthesis(env)["sentences"] if x.get("derived") == "peer_position"]
        self.assertTrue(s)
        self.assertFalse({"revenue", "annual_result"} & set(s[0]["peer_positions"]))

    def test_negative_equity_not_ranked(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "equity":
                c["value"] = -5e6
        s = [x for x in build_synthesis(env)["sentences"] if x.get("derived") == "peer_position"]
        self.assertNotIn("equity_ratio", s[0]["peer_positions"] if s else {})

    def test_no_reference_no_peer_statement(self):
        with mock.patch.object(peers, "load_reference", return_value=None):
            syn = build_synthesis(envelope())
        self.assertFalse([x for x in syn["sentences"] if x["topic"] in ("peers", "market")])
        self.assertEqual(syn["references"], [])

    def test_unspecified_industry_no_peers(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "industry":
                c["value"] = {"kode": "00.000", "beskrivelse": "Uoppgitt"}
        syn = build_synthesis(env)
        self.assertFalse([x for x in syn["sentences"] if x["topic"] in ("peers", "market")])


class DecisionTests(unittest.TestCase):
    def test_four_audiences_each_point_cited(self):
        env = envelope()
        syn = build_synthesis(env)
        self.assertEqual([b["audience"] for b in syn["decision"]], ["apply", "sell", "partner", "invest"])
        claims = {c["id"]: c for c in env["claims"]}
        for b in syn["decision"]:
            for p in b["points"]:
                self.assertTrue(p["claim_ids"])
                for cid in p["claim_ids"]:
                    self.assertIn(cid, claims)
        self.assertEqual(validate_synthesis(env, syn), [])

    def test_bankruptcy_warning_leads_every_block(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "bankrupt":
                c["value"] = True
        for b in build_synthesis(env)["decision"]:
            self.assertIn("bankrupt", b["points"][0]["text"])

    def test_not_checked_hiring_is_not_claimed(self):
        syn = build_synthesis(envelope())  # job feed not_run in the fixture
        apply = next(b for b in syn["decision"] if b["audience"] == "apply")
        self.assertFalse(any("job ad" in p["text"] for p in apply["points"]))

    def test_validator_catches_bad_decision_point(self):
        env = envelope()
        syn = build_synthesis(env)
        bad = copy.deepcopy(syn)
        bad["decision"][0]["points"][0]["claim_ids"] = ["c-999"]
        bad["decision"][0]["points"].append({"text": "x", "claim_ids": [], "reference_ids": ["ref-nope"]})
        problems = validate_synthesis(env, bad)
        self.assertTrue(any("unknown claim c-999" in p for p in problems))
        self.assertTrue(any("no supporting claim" in p for p in problems))
        self.assertTrue(any("unknown reference ref-nope" in p for p in problems))

    def test_same_person_two_roles_merged(self):
        env = envelope()
        for c in env["claims"]:
            if c["field"] == "role" and c["value"]["role_code"] == "LEDE":
                c["value"]["name"] = "Filippo Ballarin"
        syn = build_synthesis(env)
        lead = next(s for s in syn["sentences"] if s["text"].startswith("Leadership"))
        self.assertIn("managing director and board chair Filippo Ballarin", lead["text"])


class TextTests(unittest.TestCase):
    def test_english_industry(self):
        self.assertEqual(english_industry("43.210"), ("electrical installation", "code"))
        self.assertEqual(english_industry("43.999")[1], "division")
        self.assertEqual(english_industry("00.000"), (None, "none"))
        self.assertGreaterEqual(len(CODE_EN), 119)  # top 120 codes minus 00.000 "unspecified"
        self.assertGreaterEqual(len(DIVISION_EN), 85)
        self.assertIn("electrical installation", build_synthesis(envelope())["headline"])

    def test_self_description_filter(self):
        env = envelope()
        blog = {"id": "c-900", "section": "legal_identity", "field": "self_description", "family": "brand", "key": "self_description",
                "value": "For kvinner med interesse for markedsføring kan spillbransjen være et interessant analyseområde.", "availability": "available",
                "evidence_ids": [env["evidence"][0]["id"]]}
        env["claims"].append(blog)
        self.assertNotIn("spillbransjen", build_synthesis(env)["text"])
        blog["value"] = "Vi leverer elektro til bedrifter på Vestlandet."
        self.assertIn("Vi leverer", build_synthesis(env)["text"])
        blog["value"] = "Helgevold leverer elektro."
        self.assertIn("Helgevold leverer", build_synthesis(env)["text"])

    def test_first_run_says_no_previous_snapshot(self):
        syn = build_synthesis(envelope())
        u = [x for x in syn["unknowns"] if x["topic"] == "changes"]
        self.assertEqual(u[0]["availability"], "not_applicable")
        self.assertIn("first snapshot", syn["brief"])

    def test_brief_structure_and_determinism(self):
        env = envelope()
        a, b = build_synthesis(env), build_synthesis(copy.deepcopy(env))
        self.assertEqual(a["brief"], b["brief"])
        for h in ("Overview:", "Numbers:", "What to know before you decide:", "If you are applying for a job:", "Not verified, and why:"):
            self.assertIn(h, a["brief"])
        self.assertEqual(render_text(a), a["brief"])
        self.assertNotIn("None", a["brief"])

    def test_activity_prefers_substantive_events(self):
        env = envelope(run={"started_at": "2026-11-06T01:00:00Z"})
        ev = env["evidence"][0]["id"]
        env["claims"].append({"id": "c-901", "field": "dated_activity", "family": "dated_activity", "key": "x1", "availability": "available", "evidence_ids": [ev],
                              "value": {"date": "2026-10-01", "title": "Employee count re-reported", "kind": "official_register_event", "event_type": "employee_count_reported"}})
        s = next(x for x in build_synthesis(env)["sentences"] if x["topic"] == "activity_dated")
        self.assertIn("Lederbytte", s["text"])
        self.assertIn("routine register update", s["text"])
        # the newer routine item is not hidden: the sentence says it is not the latest overall (blind-grader finding, round 1)
        self.assertIn("Latest non-routine dated activity", s["text"])
        self.assertIn("the latest on 2026-10-01", s["text"])
        self.assertIn("c-901", s["claim_ids"])

    def test_summary_from_synthesis_opt_in(self):
        env = add_synthesis(envelope(summary={"text": "old template", "supported_claim_ids": ["c-1"]}))
        out = summary_from_synthesis(env)
        self.assertEqual(out["summary"]["text"], env["synthesis"]["brief"])
        self.assertEqual(out["summary"]["template_text"], "old template")
        self.assertEqual(out["summary"]["supported_claim_ids"], env["synthesis"]["supported_claim_ids"])
        self.assertEqual(env["summary"]["text"], "old template")  # input untouched
        self.assertEqual(summary_from_synthesis({"x": 1}), {"x": 1})


if __name__ == "__main__":
    unittest.main()
