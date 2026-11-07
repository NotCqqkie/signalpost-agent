import ssl
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent import fetch_repair as fr  # noqa: E402
from norway_company_agent.net import Fetcher, Response  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "lane14"
LEAF = (DATA / "leina-leaf.der").read_bytes()          # leina.com, issued by Let's Encrypt YR1; server sends no chain
YR1 = (DATA / "yr1.der").read_bytes()                  # intermediate YR1, issued by Root YR
ROOT_YR_CROSS = (DATA / "root-yr-cross.der").read_bytes()  # Root YR cross-signed by ISRG Root X1


def self_signed_root_der() -> bytes:
    for c in ssl.create_default_context().get_ca_certs(binary_form=True):
        if fr.self_issued(c):
            return c
    raise unittest.SkipTest("no self-signed root in this trust store")


class AiaParsingTests(unittest.TestCase):
    def test_ca_issuer_urls_from_real_certificates(self):
        self.assertEqual(fr.ca_issuer_urls(LEAF), ["http://yr1.i.lencr.org/"])
        self.assertEqual(fr.ca_issuer_urls(YR1), ["http://yr.i.lencr.org/"])
        self.assertEqual(fr.ca_issuer_urls(ROOT_YR_CROSS), ["http://x1.i.lencr.org/"])

    def test_no_aia_and_garbage(self):
        self.assertEqual(fr.ca_issuer_urls(b""), [])
        self.assertEqual(fr.ca_issuer_urls(b"\x06\x08\x2b\x06\x01\x05\x05\x07\x30\x02\x86\xff"), [])  # bad length

    def test_self_issued(self):
        self.assertFalse(fr.self_issued(LEAF))
        self.assertFalse(fr.self_issued(YR1))
        self.assertFalse(fr.self_issued(ROOT_YR_CROSS))
        self.assertTrue(fr.self_issued(self_signed_root_der()))
        self.assertFalse(fr.self_issued(b"junk"))

    def test_to_der_accepts_der_and_pem_only(self):
        self.assertEqual(fr._to_der(YR1), YR1)
        self.assertEqual(fr._to_der(ssl.DER_cert_to_PEM_cert(YR1).encode()), YR1)
        self.assertIsNone(fr._to_der(b"<html>not a certificate</html>"))

    def test_base_context_refuses_partial_chains(self):
        ctx = fr.base_context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(ctx.verify_flags & getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0))


class AiaRepairTests(unittest.TestCase):
    def test_follows_issuers_up_to_a_trusted_root_and_adds_intermediates_only(self):
        served = {"http://yr1.i.lencr.org/": YR1, "http://yr.i.lencr.org/": ROOT_YR_CROSS, "http://x1.i.lencr.org/": self_signed_root_der()}
        asked = []

        def fetch(url):
            asked.append(url)
            return served.get(url)
        rep = fr.AiaRepair(fetch, leaf_reader=lambda host: LEAF)
        self.assertTrue(rep.repair("leina.com"))
        self.assertEqual(asked, ["http://yr1.i.lencr.org/", "http://yr.i.lencr.org/", "http://x1.i.lencr.org/"][:len(asked)])
        # YR1 and the cross-signed Root YR are added; the downloaded self-signed ISRG Root X1 is never added
        # (a store that already holds a certificate ignores the duplicate, so count what repair() added)
        self.assertEqual(rep._added, {YR1, ROOT_YR_CROSS})
        self.assertEqual(rep.stats["hosts_repaired"], 1)
        # cached per host: no second download round
        n = len(asked)
        rep.repair("leina.com")
        self.assertEqual(len(asked), n)

    def test_unreachable_issuer_means_no_repair(self):
        rep = fr.AiaRepair(lambda url: None, leaf_reader=lambda host: LEAF)
        self.assertFalse(rep.repair("leina.com"))

    def test_no_leaf_means_no_repair(self):
        rep = fr.AiaRepair(lambda url: YR1, leaf_reader=lambda host: None)
        self.assertFalse(rep.repair("example.no"))

    def test_unusable_issuer_download_ends_repair_without_raising(self):
        # main2 fresh13: "cadata does not contain a certificate" raised out of repair() and failed a company's website step
        junk = bytes([0x30, 0x03, 0x02, 0x01, 0x00])
        rep = fr.AiaRepair(lambda url: junk, leaf_reader=lambda host: LEAF)
        rep._issuer = lambda url: junk  # e.g. a PKCS#7 bundle that passed the DER sniff but holds no certificate
        self.assertFalse(rep.repair("example.no"))

    def test_self_signed_download_is_never_trusted(self):
        root = self_signed_root_der()
        rep = fr.AiaRepair(lambda url: root, leaf_reader=lambda host: LEAF)
        self.assertFalse(rep.repair("leina.com"))
        self.assertEqual(rep._added, set())


class RetryAfterTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(fr.retry_after_seconds("120"), 120.0)
        self.assertIsNone(fr.retry_after_seconds(None))
        self.assertIsNone(fr.retry_after_seconds("soon"))
        now = time.time()
        from email.utils import formatdate
        self.assertAlmostEqual(fr.retry_after_seconds(formatdate(now + 30, usegmt=True), now=now), 30, delta=1.5)
        self.assertEqual(fr.retry_after_seconds(formatdate(now - 30, usegmt=True), now=now), 0.0)

    def test_cooldowns(self):
        c = fr.Cooldowns()
        c.set(["a.no", "ip:1.2.3.4"], 5)
        self.assertGreater(c.wait_needed(["b.no", "ip:1.2.3.4"]), 4)
        self.assertEqual(c.wait_needed(["b.no", "ip:9.9.9.9"]), 0.0)


class ScriptedFetcher(Fetcher):
    """Fetcher whose network is a script of responses; records requests and sleeps."""

    def __init__(self, script, ips):
        super().__init__(per_host_interval=0.0)
        self.script, self.ips, self.sent, self.slept = list(script), ips, [], []

    def _backend_keys(self, host):
        return [host] + (["ip:" + self.ips[host]] if host in self.ips else [])


class PoliteRetryTests(unittest.TestCase):
    def make(self, script, ips=None):
        f = ScriptedFetcher(script, ips or {})
        f.enable_lane14(polite_429=True)

        def raw(url, **kw):
            # honour the cool-down the same way _raw_get does, without the network
            wait = f.cooldowns.wait_needed(f._backend_keys(url.split("/")[2]))
            if wait > 0:
                f.slept.append(round(wait))
                f.cooldowns._until.clear()
            f.sent.append(url)
            return f.script.pop(0)
        f._raw_get = raw
        f.robots_allowed = lambda url, timeout=8.0: True
        return f

    def r(self, status, retry_after=None):
        return Response("u", "u", status, b"", "text/html", "t", error=None if status == 200 else f"HTTP {status}", retry_after=retry_after)

    def test_honours_retry_after_then_retries_once(self):
        f = self.make([self.r(429, "7"), self.r(200)])
        resp = f.get("https://site.no/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(f.sent, ["https://site.no/", "https://site.no/"])
        self.assertEqual(f.slept, [7])

    def test_long_retry_after_is_not_waited_in_line(self):
        f = self.make([self.r(429, "3600")])
        self.assertEqual(f.get("https://site.no/").status, 429)
        self.assertEqual(len(f.sent), 1)

    def test_second_refusal_cools_the_shared_ip(self):
        f = self.make([self.r(429), self.r(429)], ips={"a.no": "34.149.87.45", "b.no": "34.149.87.45"})
        self.assertEqual(f.get("https://a.no/").status, 429)
        self.assertGreater(f.cooldowns.wait_needed(f._backend_keys("b.no")), 100)

    def test_flag_off_keeps_old_behaviour(self):
        f = Fetcher(per_host_interval=0.0)
        self.assertFalse(f.polite_429)
        self.assertIsNone(f.cooldowns)
        self.assertIsNone(f.aia)


class HeaderTests(unittest.TestCase):
    def capture(self, polite):
        import urllib.request
        f = Fetcher(per_host_interval=0.0)
        f.enable_lane14(polite_429=polite)
        seen = {}

        class Opener:
            def open(self, request, timeout):
                seen.update({k.lower(): v for k, v in request.header_items()})
                raise OSError("stop")
        import norway_company_agent.net as net
        old = net._OPENER
        net._OPENER = Opener()
        try:
            f._backend_keys = lambda host: [host]
            import norway_company_agent.website as website
            old_guard = net.assert_public_url
            net.assert_public_url = lambda url: None
            f._raw_get("https://example.no/", timeout=5, max_bytes=10, accept="text/html")
        finally:
            net._OPENER = old
            net.assert_public_url = old_guard
        return seen

    def test_accept_language_only_with_flag_and_ua_unchanged(self):
        on, off = self.capture(True), self.capture(False)
        self.assertEqual(on["accept-language"].split(",")[0], "nb-NO")
        self.assertNotIn("accept-language", off)
        self.assertEqual(on["user-agent"], off["user-agent"])
        self.assertIn("Signalpost", on["user-agent"])


if __name__ == "__main__":
    unittest.main()
