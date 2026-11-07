import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_site_news import page  # noqa: E402


class Round14Tests(unittest.TestCase):
    def test_job_ad_naming_another_employer(self):
        # E46: netpower.no ads say "Arbeidsgiver: Netpower WEB Solutions AS" for NETPOWER IT SOLUTIONS AS
        from norway_company_agent.site_hiring import other_employer
        ad = page("<html><body><h1>AI-rådgiver</h1><p>Arbeidsgiver: Netpower WEB Solutions AS</p></body></html>", "https://netpower.no/ledig-stilling/ai/")
        self.assertEqual(other_employer(ad, "NETPOWER IT SOLUTIONS AS"), "Netpower WEB Solutions AS")
        own = page("<html><body><h1>Drift</h1><p>Arbeidsgiver: Netpower IT Solutions AS</p></body></html>", "https://netpower.no/ledig-stilling/drift/")
        self.assertIsNone(other_employer(own, "NETPOWER IT SOLUTIONS AS"))

    def test_domain_declared_by_another_entity(self):
        # E47: fontes.no is the declared homepage of FONTES FOUNDATION STI
        import norway_company_agent.http as http
        from norway_company_agent.footprint import namesake_in_register
        body = {"_embedded": {"enheter": [{"organisasjonsnummer": "991635106", "navn": "FONTES FOUNDATION STI", "hjemmeside": "www.fontes.no"}]}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            prof = {"organisation_number": "938253455", "name": "FONTES AS"}
            self.assertIn("homepage of another entity", namesake_in_register(prof, site_text=" fontes as water ", domain="fontes.no") or "")
            self.assertIsNone(namesake_in_register(prof, site_text=" fontes as water ", domain="fontes-as.no"))
        finally:
            http.fetch_json = orig


    def test_round15_deadline_and_demo_title(self):
        from norway_company_agent.footprint import is_demo_item
        from norway_company_agent.site_hiring import parse_deadline
        self.assertEqual(parse_deadline("20. oktober", "2026-11-06"), "2026-10-20")  # E50: passed, no year printed
        self.assertEqual(parse_deadline("15. januar", "2026-11-06"), "2027-01-15")
        self.assertTrue(is_demo_item({"title": "Hei, verden! - Haugli Eiendom", "url": "https://hauglieiendom.no/hei-verden/"}))  # E51


    def test_round17_board_ad_expiry_and_archives(self):
        from norway_company_agent.footprint import NOT_POST_TYPES
        from norway_company_agent.site_hiring import board_ad_expired
        self.assertTrue(board_ad_expired(page("<html><body><h1>Utvikler</h1><p>Fristen er utløpt</p></body></html>", "https://www.finn.no/job/ad/1"), "2026-11-07"))  # E55
        self.assertTrue(board_ad_expired(page("<html><body><p>Søknadsfrist: 24.11.2024</p></body></html>", "https://www.finn.no/job/ad/2"), "2026-11-07"))
        self.assertFalse(board_ad_expired(page("<html><body><p>Søknadsfrist: 30.11.2026</p></body></html>", "https://www.finn.no/job/ad/3"), "2026-11-07"))
        self.assertTrue(NOT_POST_TYPES.search("/tag/ingenior/"))  # E56
        self.assertTrue(NOT_POST_TYPES.search("/category/nyheter/"))
        self.assertTrue(NOT_POST_TYPES.search("/photogallery/image6/"))  # E57 (main2 fresh18: truckit.no)
        from norway_company_agent.footprint import is_demo_item
        for u in ("https://x.no/blogs/news/tagged/easee", "https://x.no/blog/tag/fluefiske/", "https://x.com/mfn-news-tag/mfn-report/"):
            self.assertTrue(is_demo_item({"title": "Nyheter", "url": u}), u)  # E58
        self.assertFalse(is_demo_item({"title": "Vi har fått ny tagging-maskin", "url": "https://x.no/nyheter/ny-tagging-maskin/"}))


if __name__ == "__main__":
    unittest.main()
