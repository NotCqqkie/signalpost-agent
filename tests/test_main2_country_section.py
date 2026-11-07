import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.footprint import tenant_or_group_page  # noqa: E402
from test_site_news import page  # noqa: E402


class CountrySectionTests(unittest.TestCase):
    def test_declared_norwegian_country_section_is_own_site(self):
        # E35 (lane 10b note): hydac.no forwards to hydac.com/no-no/ -- the Norwegian company's own country section
        prof = {"organisation_number": "982956579", "name": "HYDAC AS"}
        home = page("<html><body><h1>HYDAC AS</h1><p>Hydraulikk i Norge.</p></body></html>", "https://www.hydac.com/no-no/")
        attempt = {"url": "https://hydac.no/", "source": "registry", "pages": [home], "verdict": {"status": "verified", "method": "registry_listed_name"}}
        self.assertIsNone(tenant_or_group_page(attempt, prof))
        # a guessed candidate, another country's section, or another organisation number keeps the gate
        self.assertIsNotNone(tenant_or_group_page({**attempt, "source": "name_guess"}, prof))
        se = page("<html><body><h1>HYDAC AB</h1></body></html>", "https://www.hydac.com/sv-se/")
        self.assertIsNotNone(tenant_or_group_page({**attempt, "pages": [se]}, prof))
        other = page("<html><body><h1>HYDAC</h1><p>Org.nr. 923 609 016</p></body></html>", "https://www.hydac.com/no-no/")
        self.assertIsNotNone(tenant_or_group_page({**attempt, "pages": [other]}, prof))


if __name__ == "__main__":
    unittest.main()
