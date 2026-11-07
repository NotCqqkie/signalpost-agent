import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.fetch_repair import embedded_json_text, forward_target  # noqa: E402
from norway_company_agent.footprint import try_candidate  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402


class PageFetcher:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def remaining(self):
        return 100.0

    def get(self, url, **kw):
        self.asked.append(url)
        if url in self.pages:
            return Response(url, url, 200, self.pages[url].encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z", redirect_chain=[url])
        return Response(url, url, 404, b"", "text/html", "2026-11-06T00:00:00Z", error="HTTP 404")


PROF = {"organisation_number": "912345602", "name": "MEMBRAN ENTREPRENØR AS", "business_address": {"adresse": ["Kaivegen 3"], "postnummer": "6300"}}
FRAMESET = ('<html><head><title>membran entreprenor</title></head><frameset rows="*"><frame src="https://membran-entrepren&oslash;r.no" '
            'name="loopiaForwardFrame"><noframes><body><script>document.location.href = \'https://membran-entrepren&oslash;r.no\'</script></body></noframes></frameset></html>')
TARGET = "https://xn--membran-entreprenr-w4b.no/"
REAL = "<html><body><h1>Membran Entreprenør AS</h1><p>Vi legger tak og membraner i hele Romsdal.</p><footer>Org.nr. 912 345 602</footer></body></html>"


class ForwardTargetTests(unittest.TestCase):
    def test_registrar_frame_forward_to_idn_domain(self):
        self.assertEqual(forward_target(FRAMESET, "https://membran-entreprenor.no/"), TARGET)

    def test_meta_refresh_relative(self):
        self.assertEqual(forward_target('<meta http-equiv="refresh" content="0; url=/no/hjem.html">', "https://x.no/"), "https://x.no/no/hjem.html")

    def test_parking_lander_and_captcha_not_followed(self):
        self.assertIsNone(forward_target('<script>window.onload=function(){window.location.href="/lander"}</script>', "https://latinum.com/"))
        self.assertIsNone(forward_target('<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F">', "https://osm.no/"))

    def test_nothing_to_follow(self):
        self.assertIsNone(forward_target("<html><body><div id=root></div><script src=/a.js></script></body></html>", "https://a.no/"))
        self.assertIsNone(forward_target('<script>location.replace("https://a.no/")</script>', "https://a.no/"))


class EmbeddedJsonTests(unittest.TestCase):
    def test_next_data_strings(self):
        html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"footer":"Fjord Aqua AS, org.nr. 912 345 602",'
                '"img":"/static/a.png","url":"https://cdn.x/y","k":"x","css":"a{b:c;};;"}}}</script>')
        self.assertEqual(embedded_json_text(html), "Fjord Aqua AS, org.nr. 912 345 602")

    def test_invalid_json_and_other_scripts_ignored(self):
        self.assertEqual(embedded_json_text('<script type="application/json">{oops</script><script>var a = "Fjord Aqua AS";</script>'), "")


class TryCandidateTests(unittest.TestCase):
    def test_frame_forward_followed_with_flag_and_judged_like_a_redirect(self):
        f = PageFetcher({"https://membran-entreprenor.no/": FRAMESET, TARGET: REAL})
        a = try_candidate(f, {**PROF, "_shell_read": True, "_follow_redirects": True}, "https://membran-entreprenor.no/", "name_guess")
        self.assertEqual(a["verdict"]["status"], "verified")
        self.assertEqual(a["verdict"]["method"], "orgnr_on_site")
        self.assertEqual(a["domain"], "xn--membran-entreprenr-w4b.no")
        self.assertTrue(any(r.get("kind") == "shell_forward" for r in a["requests"]))

    def test_forward_to_other_domain_without_strong_proof_stays_ambiguous(self):
        weak = "<html><body><h1>Membran Entreprenør</h1><p>Tak og membraner, ring oss for et tilbud i dag.</p></body></html>"
        f = PageFetcher({"https://membran-entreprenor.no/": FRAMESET, TARGET: weak})
        a = try_candidate(f, {**PROF, "_shell_read": True, "_follow_redirects": True}, "https://membran-entreprenor.no/", "name_guess")
        self.assertNotEqual(a["verdict"]["status"], "verified")

    def test_flag_off_unchanged(self):
        f = PageFetcher({"https://membran-entreprenor.no/": FRAMESET, TARGET: REAL})
        a = try_candidate(f, {**PROF, "_follow_redirects": True}, "https://membran-entreprenor.no/", "name_guess")
        self.assertEqual(a["verdict"]["status"], "ambiguous")
        self.assertEqual(f.asked, ["https://membran-entreprenor.no/"])

    def test_json_text_shell_verified_by_orgnr(self):
        shell = ('<html><head><title>Fjord</title></head><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">'
                 '{"props":{"footer":"Membran Entreprenør AS - Kaivegen 3, 6300 Åndalsnes - org.nr. 912 345 602"}}</script></body></html>')
        f = PageFetcher({"https://membran-entreprenor.no/": shell})
        a = try_candidate(f, {**PROF, "_shell_read": True}, "https://membran-entreprenor.no/", "name_guess")
        self.assertEqual(a["verdict"]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
