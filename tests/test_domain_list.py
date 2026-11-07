import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.domain_list import SOURCE, list_candidates, load_labels, name_variants  # noqa: E402
from norway_company_agent.footprint import Page, verify_site  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402


def page(html: str, url: str) -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z"), "homepage")


class NameVariantTests(unittest.TestCase):
    def test_prefixes_initials_and_dropped_words(self):
        self.assertIn("proact", name_variants("PROACT IT NORGE AS"))
        self.assertIn("nks-helsehus", name_variants("N.K.S. HELSEHUS AKERSHUS AS"))
        self.assertIn("salmakerengh", name_variants("SALMAKER ERIK ENGH AS"))
        self.assertNotIn("andersen", name_variants("ANDERSEN & BJØRDAL EIENDOM DA"))  # common first word
        self.assertIn("moeller", name_variants("MØLLER AS"))

    def test_generic_single_words_are_not_candidates(self):
        self.assertNotIn("bygg", name_variants("BYGG SERVICE AS"))
        self.assertNotIn("oslo", name_variants("OSLO TRANSPORT AS"))
        self.assertTrue(all(len(v.replace("-", "")) >= 4 for v in name_variants("AB AS")))

    def test_candidates_only_from_list_and_never_repeat_fixed_guesses(self):
        labels = frozenset({"proact", "proactitnorge", "concept2"})
        c = list_candidates({"name": "PROACT IT NORGE AS"}, labels=labels)
        self.assertEqual(c, [("https://proact.no/", SOURCE)])  # proactitnorge.no is already a fixed guess
        self.assertEqual(list_candidates({"name": "UNKNOWN THING AS"}, labels=labels), [])
        self.assertEqual(list_candidates({"name": "PROACT IT NORGE AS"}, labels=frozenset()), [])

    def test_shipped_list_loads(self):
        labels = load_labels()
        self.assertGreater(len(labels), 100000)
        self.assertIn("equinor", labels)


class GateUnchangedTests(unittest.TestCase):
    """A listed-domain candidate is not a declared source: brand name alone never publishes."""
    PROFILE = {"organisation_number": "923609016", "name": "PROACT IT NORGE AS", "business_address": {"adresse": ["Gate 1"], "postnummer": "0150"}}

    def test_brand_site_without_proof_is_not_verified(self):
        v = verify_site(self.PROFILE, [page("<html><title>Proact IT Norge</title><body>Proact group, storage and cloud services for the Nordics.</body></html>", "https://proact.no/")], SOURCE)
        self.assertNotEqual(v["status"], "verified")

    def test_other_orgnr_on_brand_site_is_ambiguous(self):
        v = verify_site(self.PROFILE, [page("<html><title>Proact</title><body>Proact IT Norge AS. <footer>Proact Group AB org.nr 556494-3446 | Org.nr 915 665 721</footer></body></html>", "https://proact.no/")], SOURCE)
        self.assertNotEqual(v["status"], "verified")

    def test_own_orgnr_verifies(self):
        v = verify_site(self.PROFILE, [page("<html><title>Proact</title><body>x<footer>Proact IT Norge AS org.nr 923 609 016</footer></body></html>", "https://proact.no/")], SOURCE)
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["method"], "orgnr_on_site")

    def test_legal_name_alone_on_listed_no_domain_does_not_verify(self):
        prof = {**self.PROFILE, "_allow_no_domain_legal_name": True}
        v = verify_site(prof, [page("<html><title>Proact IT Norge AS</title><body>Welcome to our long page about storage and cloud.</body></html>", "https://proact.no/")], SOURCE)
        self.assertNotEqual(v["status"], "verified")


if __name__ == "__main__":
    unittest.main()
