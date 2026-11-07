import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.footprint import try_candidate, www_swapped  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402

PROF = {"organisation_number": "912345602", "name": "PBM EIENDOM AS", "business_address": {"adresse": ["Kaivegen 3"], "postnummer": "6300"}}
SITE = "<html><body><h1>PBM Eiendom AS</h1><p>Utleie av næringslokaler i Molde sentrum.</p><footer>Org.nr. 912 345 602</footer></body></html>"


class Net:
    """URLs in `pages` answer 200; URLs in `dead` fail like an unreachable host / wrong certificate."""

    def __init__(self, pages, dead):
        self.pages, self.dead, self.asked = pages, dead, []

    def remaining(self):
        return 100.0

    def get(self, url, **kw):
        self.asked.append(url)
        if url in self.pages:
            return Response(url, url, 200, self.pages[url].encode(), "text/html; charset=utf-8", "t", redirect_chain=[url])
        if url in self.dead:
            return Response(url, url, 0, retrieved_at="t", error=self.dead[url])
        return Response(url, url, 404, b"", "text/html", "t", error="HTTP 404")


class WwwSwapTests(unittest.TestCase):
    def test_swapped(self):
        self.assertEqual(www_swapped("https://www.ks-zoo.no/"), "https://ks-zoo.no/")
        self.assertEqual(www_swapped("https://pbmeiendom.no/"), "https://www.pbmeiendom.no/")
        self.assertIsNone(www_swapped("https://shop.pbmeiendom.no/"))

    def test_unreachable_apex_tries_www_with_flag(self):
        n = Net({"https://www.pbmeiendom.no/": SITE}, {"https://pbmeiendom.no/": "host_unreachable", "http://pbmeiendom.no/": "host_unreachable"})
        a = try_candidate(n, {**PROF, "_www_swap": True}, "https://pbmeiendom.no/", "registry_email")
        self.assertEqual(a["verdict"]["status"], "verified")
        self.assertEqual(a["verdict"]["www_swap"], "https://www.pbmeiendom.no/")

    def test_certificate_for_other_name(self):
        err = "URLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'www.x.no'."
        n = Net({"https://pbmeiendom.no/": SITE}, {"https://www.pbmeiendom.no/": err, "http://www.pbmeiendom.no/": "host_unreachable"})
        a = try_candidate(n, {**PROF, "_www_swap": True}, "https://www.pbmeiendom.no/", "registry")
        self.assertEqual(a["verdict"]["status"], "verified")

    def test_robots_refusal_and_http_errors_are_not_swapped(self):
        n = Net({"https://www.pbmeiendom.no/": SITE}, {"https://pbmeiendom.no/": "robots_disallowed"})
        a = try_candidate(n, {**PROF, "_www_swap": True}, "https://pbmeiendom.no/", "registry_email")
        self.assertEqual(a["verdict"]["status"], "blocked")
        self.assertNotIn("https://www.pbmeiendom.no/", n.asked)
        n = Net({"https://www.pbmeiendom.no/": SITE}, {})
        a = try_candidate(n, {**PROF, "_www_swap": True}, "https://pbmeiendom.no/", "registry_email")  # 404
        self.assertNotIn("https://www.pbmeiendom.no/", n.asked)

    def test_name_guesses_are_not_swapped(self):
        n = Net({"https://www.pbmeiendom.no/": SITE}, {"https://pbmeiendom.no/": "host_unreachable", "http://pbmeiendom.no/": "host_unreachable"})
        a = try_candidate(n, {**PROF, "_www_swap": True}, "https://pbmeiendom.no/", "name_guess")
        self.assertEqual(a["verdict"]["status"], "failed")
        self.assertNotIn("https://www.pbmeiendom.no/", n.asked)

    def test_flag_off_unchanged(self):
        n = Net({"https://www.pbmeiendom.no/": SITE}, {"https://pbmeiendom.no/": "host_unreachable", "http://pbmeiendom.no/": "host_unreachable"})
        a = try_candidate(n, PROF, "https://pbmeiendom.no/", "registry_email")
        self.assertEqual(a["verdict"]["status"], "failed")
        self.assertNotIn("https://www.pbmeiendom.no/", n.asked)


if __name__ == "__main__":
    unittest.main()
