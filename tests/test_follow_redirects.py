import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.footprint import try_candidate  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402


class RedirectFetcher:
    """Serves fixed pages; `redirects` maps a requested URL to the final URL it lands on."""

    def __init__(self, pages: dict[str, str], redirects: dict[str, str]):
        self.pages, self.redirects = pages, redirects

    def remaining(self) -> float:
        return 100.0

    def get(self, url, **kw):
        final = self.redirects.get(url, url)
        if final in self.pages:
            return Response(url, final, 200, self.pages[final].encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z", redirect_chain=[url, final])
        return Response(url, final, 404, b"", "text/html", "2026-11-06T00:00:00Z", error="HTTP 404")


PROF = {"organisation_number": "912345602", "name": "FJORD AQUA AS", "business_address": {"adresse": ["Kaivegen 3"], "postnummer": "6300"}}


class FollowRedirectTests(unittest.TestCase):
    def run_case(self, html: str, flag: bool = True) -> dict:
        f = RedirectFetcher({"https://salmonofthenorth.com/": html}, {"https://fjordaqua.no/": "https://salmonofthenorth.com/"})
        return try_candidate(f, {**PROF, "_follow_redirects": flag}, "https://fjordaqua.no/", "name_guess")["verdict"]

    def test_target_with_own_orgnr_is_verified(self):
        v = self.run_case("<html><head><title>Salmon of the North</title></head><body><footer>Fjord Aqua AS, org.nr. 912 345 602</footer></body></html>")
        self.assertEqual((v["status"], v["method"]), ("verified", "orgnr_on_site"))
        self.assertEqual(v["redirected_from"], "https://fjordaqua.no/")

    def test_flag_off_keeps_rejection(self):
        v = self.run_case("<html><body><footer>Fjord Aqua AS, org.nr. 912 345 602</footer></body></html>", flag=False)
        self.assertEqual(v["status"], "ambiguous")

    def test_target_with_name_only_is_ambiguous(self):
        v = self.run_case("<html><head><title>Fjord Aqua AS</title></head><body><footer>Fjord Aqua AS</footer></body></html>")
        self.assertEqual(v["status"], "ambiguous")

    def test_other_orgnr_on_target_is_ambiguous(self):
        v = self.run_case("<html><body><footer>Salmon Group AS, org.nr. 923 609 016. Fjord Aqua AS</footer></body></html>")
        self.assertEqual(v["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
