import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.site_news import listing_items  # noqa: E402
from test_site_news import TODAY, page  # noqa: E402


class ListingDateTests(unittest.TestCase):
    def test_permalink_date_wins_and_title_dates_are_not_publication_dates(self):
        # E44 (main2 fresh13): linak.no lists "Bent Jensen går av som CEO i LINAK A/S 1. august 2026" at
        # /nyheter/2026/05/12/...; revisorvest.no cards quote a law's effective date in the teaser
        html = """<html><body><main>
        <div class="card"><a href="/nyheter/2026/05/12/ceo-gar-av/"><h3>Bent Jensen går av som CEO 1. august 2026</h3></a>
          <span>1. august 2026</span></div>
        <div class="card"><a href="/aktuelt/heltid/"><h3>Ny lovregel om rett til heltidsjobb</h3></a><p>4. mars 2026</p></div>
        </main></body></html>"""
        items, _ = listing_items(page(html, "https://firma.no/nyheter/"), "firma.no", ["/nyheter", "/aktuelt"], TODAY)
        dates = {i["url"]: i["date"] for i in items}
        self.assertEqual(dates.get("https://firma.no/nyheter/2026/05/12/ceo-gar-av/"), "2026-05-12")
        self.assertEqual(dates.get("https://firma.no/aktuelt/heltid/"), "2026-03-04")


if __name__ == "__main__":
    unittest.main()
