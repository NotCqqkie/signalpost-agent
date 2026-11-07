import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.footprint import crawl_verified_site  # noqa: E402
from test_site_news import ARTICLE, HOME, LISTING, TODAY, FakeFetcher, page  # noqa: E402


class SubPathTests(unittest.TestCase):
    def test_site_on_sub_path_keeps_only_posts_under_it(self):
        # E32 (main2 fresh6 audit): ys.no/medlemsfordeler/ is YS FORDEL AS's page; ys.no/nyheter is the union's news
        pages = {"https://firma.no/nyheter": LISTING, "https://firma.no/fagstoff/uten-dato": ARTICLE}
        root = crawl_verified_site(FakeFetcher(pages), {"name": "FIRMA AS", "_site_news": True},
                                   {"pages": [page(HOME)], "domain": "firma.no"}, today=TODAY)
        self.assertEqual(len(root["activity"]), 3)
        sub = crawl_verified_site(FakeFetcher(pages), {"name": "FIRMA AS", "_site_news": True},
                                  {"pages": [page(HOME, "https://firma.no/avdeling/")], "domain": "firma.no"}, today=TODAY)
        self.assertEqual(sub["activity"], [])


if __name__ == "__main__":
    unittest.main()
