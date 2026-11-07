"""Lane 7: hiring from verified company sites (site_hiring.py). Fixtures are trimmed copies of real page shapes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Builder, add_footprint  # noqa: E402
from norway_company_agent.footprint import Page  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402
from norway_company_agent.site_hiring import (board_ad_jobs, careers_links, crawl_careers, free_text_jobs, is_careers_anchor,  # noqa: E402
                                              is_careers_path, parse_deadline, position_title)

TODAY = "2026-11-06"


def page(html: str, url: str) -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z"), "careers")


def html(body: str, title: str = "Side") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


AD_FUTURE = html("""<nav><a href="/">Hjem</a></nav><h1>Ledige stillinger</h1>
<h2>Ledig stilling: Elektriker</h2><p>Vi søker en erfaren elektriker. Arbeidssted: Hamar Søknadsfrist: 01.12.2026.
Send søknad og CV til post@x.no</p>
<h2>Prosjektleder bygg</h2><p>Fast stilling. Søknadsfrist: 15. desember 2026. Send din søknad til post@x.no</p>""")
AD_EXPIRED = html("""<h2>Tømrer med fagbrev søkes til Fribo Bygg – 100% fast stilling</h2><p>Er du en dyktig tømrer?
Slik søker du: Send din CV og en kortfattet søknad til oss innen 10.02.2024.</p>""")
NONE_OPEN = html("""<h1>Jobb hos oss</h1><p>Har du lyst til å jobbe hos oss? Vi har for øyeblikket ingen ledige stillinger,
men send gjerne en åpen søknad.</p>""")
OPEN_APP = html("""<h1>Ledig stilling</h1><h3>Åpen søknad</h3><p>Vi er alltid på jakt etter flinke folk. Send oss en åpen søknad.</p>""")
NO_CUE = html("""<h1>Karriere</h1><h2>Kranfører</h2><p>G-sertifikater, gjerne erfaring fra havn.</p><h2>Rigger</h2><p>Erfaring med løft.</p>""")
STALE = html("""<h2>Ledig stilling kunderådgiver</h2><p>12/01/2025 13:05 Vi søker en kunderådgiver. Les hele stillingsbeskrivelse og søk her.</p>""")
FRESH = html("""<h2>Ledig stilling kunderådgiver</h2><p>20/10/2026 13:05 Vi søker en kunderådgiver. Les hele stillingsbeskrivelse og søk her.</p>""")


class CareersDetectionTests(unittest.TestCase):
    def test_path_word_boundaries(self):
        for url in ("https://x.no/bestilling/", "https://x.no/utstillinger/", "https://x.no/om-oss/ledige-lokaler", "https://x.no/kontor",
                    "https://x.no/ordrebestilling.html", "https://x.no/kategori/malsom-etter-bestilling", "https://x.no/slik-jobber-vi"):
            self.assertFalse(is_careers_path(url), url)
        for url in ("https://x.no/ledig-stilling", "https://x.no/karriere/", "https://x.no/om-oss/jobb-hos-oss/", "https://x.no/jobber/",
                    "https://x.no/about-us/career", "https://x.no/ledigestillinger", "https://x.no/jobbmuligheter/"):
            self.assertTrue(is_careers_path(url), url)

    def test_anchor_text(self):
        for t in ("Ledig stilling!", "Jobb hos oss", "Ledige stillinger", "Karriere", "Vi søker"):
            self.assertTrue(is_careers_anchor(t), t)
        for t in ("Bestill time", "Slik jobber vi", "Se utstillingen", "Ledige lokaler", "Les mer om hvordan vi jobber med kvalitet i alle ledd"):
            self.assertFalse(is_careers_anchor(t), t)

    def test_home_anchor_text_is_followed(self):
        home = page(html('<a href="/aktuelt/2026/okt">Ledig stilling!</a><a href="/bestilling">Bestilling</a><a href="https://other.no/jobb">Jobb</a>'),
                    "https://knmaskin.no/")
        self.assertEqual(careers_links([home], "knmaskin.no"), ["https://knmaskin.no/aktuelt/2026/okt"])


class ExtractionTests(unittest.TestCase):
    def test_positions_with_deadline_and_location(self):
        jobs, check = free_text_jobs(page(AD_FUTURE, "https://x.no/ledige-stillinger"), TODAY)
        self.assertEqual(check["state"], "positions_found")
        self.assertEqual([j["title"] for j in jobs], ["Elektriker", "Prosjektleder bygg"])
        self.assertEqual(jobs[0]["deadline"], "2026-12-01")
        self.assertEqual(jobs[0]["location"], "Hamar")
        self.assertEqual(jobs[1]["deadline"], "2026-12-15")

    def test_expired_ad_is_not_published(self):
        jobs, check = free_text_jobs(page(AD_EXPIRED, "https://fribo-bygg.no/ledig-stilling"), TODAY)
        self.assertEqual((jobs, check["state"]), ([], "expired_only"))

    def test_no_positions_phrase(self):
        jobs, check = free_text_jobs(page(NONE_OPEN, "https://x.no/jobb-hos-oss"), TODAY)
        self.assertEqual((jobs, check["state"]), ([], "none_open"))
        self.assertIn("ingen ledige stillinger", check["span"])

    def test_open_application_and_standing_roles_are_not_positions(self):
        self.assertEqual(free_text_jobs(page(OPEN_APP, "https://x.no/ledig-stilling"), TODAY)[0], [])
        self.assertEqual(free_text_jobs(page(NO_CUE, "https://x.no/karriere"), TODAY)[0], [])

    def test_stale_post_without_deadline_is_dropped(self):
        self.assertEqual(free_text_jobs(page(STALE, "https://x.no/ledig-stilling-kunderadgiver/"), TODAY)[0], [])
        jobs, _ = free_text_jobs(page(FRESH, "https://x.no/ledig-stilling-kunderadgiver/"), TODAY)
        self.assertEqual([(j["title"], j["posted"]) for j in jobs], [("kunderådgiver", "2026-10-20")])

    def test_conditional_none_phrase_is_not_a_statement(self):
        from norway_company_agent.site_hiring import none_statement
        self.assertIsNone(none_statement("sjekk utlysninger nedenfor. har vi ingen aktive stillinger akkurat na, tar vi imot en presentasjon"))
        self.assertIsNone(none_statement("ingen aktive stillinger som passer? send oss en soknad"))
        self.assertIsNotNone(none_statement("vi har for oyeblikket ingen ledige stillinger"))

    def test_programme_heading_needs_a_deadline(self):
        p = html("<h2>Sommerjobb</h2><p>Er du student? Hos oss kan du søke om sommerjobb! Send søknad.</p>")
        self.assertEqual(free_text_jobs(page(p, "https://x.no/karriere/studenter"), TODAY)[0], [])

    def test_titles(self):
        self.assertEqual(position_title("Vi søker – en dyktig tømrer!"), "dyktig tømrer")
        for t in ("Ledige stillinger", "Vi søker deg", "Våre rådgivere", "Åpen søknad", "Om stillingen"):
            self.assertIsNone(position_title(t), t)

    def test_deadlines(self):
        self.assertEqual(parse_deadline("snarest", TODAY), "open_until_filled")
        self.assertEqual(parse_deadline("01.12.26", TODAY), "2026-12-01")

    def test_board_ad_links_but_not_vendor_links(self):
        p = page(html('<p>Ledig stilling, Strai Kjøkken AS: <a href="https://www.finn.no/job/ad/477510586">Få en nøkkelrolle i AI | FINN.no</a></p>'
                      '<a href="https://www.teamtailor.com/?utm_content=careers">Career site by Teamtailor</a>'
                      '<a href="https://www.finn.no/job/ad/477510587">Se annonsen</a>'), "https://strai.no/jobb-hos-oss/")
        self.assertEqual([(j["title"], j["url"]) for j in board_ad_jobs(p)], [("Få en nøkkelrolle i AI", "https://www.finn.no/job/ad/477510586")])


class CrawlTests(unittest.TestCase):
    def test_site_level_none_open_overrides_old_detail_pages(self):
        home = page(html('<a href="/ledige-stillinger/">Ledige stillinger</a>'), "https://lundelektro.no/")
        site = {"https://lundelektro.no/ledige-stillinger/": html('<h1>Ledige stillinger</h1><p>Søk Ingen ledige stillinger på dette tidspunktet.</p>'
                                                           '<a href="/ledige-stillinger/service-elektriker/">Service elektriker</a>')}
        got = crawl_careers([home], "lundelektro.no", lambda u, k: page(site[u], u) if u in site else None, org="923609016", today=TODAY)
        self.assertEqual(got["jobs"], [])
        self.assertEqual([c["state"] for c in got["checks"]], ["none_open"])

    def test_nav_links_are_collected_for_feed_confirmation(self):
        home = page(html('<a href="https://arbeidsplassen.nav.no/stillinger/stilling/acc50110-8b72-4ed5-814b-4c9b6e929338">Ledig stilling!</a>'),
                    "https://www.knmaskin.no/")
        got = crawl_careers([home], "knmaskin.no", lambda u, k: None, org="990311684", today=TODAY)
        self.assertEqual(got["jobs"], [])  # the NAV page is never read; the agent checks the ad in NAV's feed (ACTIVE + orgnr)
        self.assertEqual([l["uuid"] for l in got["nav_links"]], ["acc50110-8b72-4ed5-814b-4c9b6e929338"])

    def test_index_and_detail_pages(self):
        home = page(html('<a href="/karriere/">Karriere</a>'), "https://nc.no/")
        site = {"https://nc.no/karriere/": html('<h1>Karriere</h1><a href="/karriere/req-1/">Ingeniør geoteknikk</a>'),
                "https://nc.no/karriere/req-1/": html('<h1>Ingeniør geoteknikk</h1><p>Søknadsfrist: 01.12.2026 Arbeidssted: Bergen Søk på stillingen</p>')}
        got = crawl_careers([home], "nc.no", lambda u, k: page(site[u], u) if u in site else None, org="923609016", today=TODAY)
        self.assertEqual([(j["title"], j["url"], j["location"]) for j in got["jobs"]], [("Ingeniør geoteknikk", "https://nc.no/karriere/req-1/", "Bergen")])


class EnvelopeHookTests(unittest.TestCase):
    def _site(self):
        home = page(html("<h1>X</h1>"), "https://x.no/")
        att = {"verdict": {"proof_url": "https://x.no/", "proof_retrieved_at": "t", "proof_sha256": "h", "span": "923609016", "method": "orgnr_on_site"},
               "pages": [home], "source": "registry"}
        return {"status": "verified", "selected": att}

    def _crawl(self, jobs, checks):
        return {"brand": {}, "social_profiles": [], "activity": [], "jobs": jobs, "careers_pages": ["https://x.no/karriere"], "hiring_checks": checks}

    def test_free_text_job_claim(self):
        b = Builder("923609016")
        job = {"title": "Elektriker", "url": "https://x.no/karriere", "heading": "Ledig stilling: Elektriker", "deadline": "2026-12-01", "location": "Hamar",
               "method": "free_text_heading_apply_cue", "span": "Ledig stilling: Elektriker | soknadsfrist 01.12.2026", "found_on": "https://x.no/karriere",
               "sha256": "h", "retrieved_at": "t"}
        add_footprint(b, self._site(), self._crawl([job], []), [], "complete", {})
        c = [c for c in b.claims if c["family"] == "hiring"]
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["availability"], "available")
        self.assertEqual(c[0]["key"], "site:https://x.no/karriere|Ledig stilling: Elektriker")
        self.assertEqual((c[0]["value"]["expires"], c[0]["value"]["locations"]), ("2026-12-01", ["Hamar"]))
        self.assertTrue(c[0]["evidence_ids"])

    def test_none_open_check_is_cited(self):
        b = Builder("923609016")
        check = {"url": "https://x.no/karriere", "state": "none_open", "span": "vi har ingen ledige stillinger", "sha256": "h", "retrieved_at": "t"}
        add_footprint(b, self._site(), self._crawl([], [check]), [], "complete", {})
        c = [c for c in b.claims if c["family"] == "hiring"][0]
        self.assertEqual(c["availability"], "not_available")
        ev = [e for e in b.evidence if e["id"] in c["evidence_ids"]]
        self.assertEqual(ev[0]["extraction_method"], "careers_page_check:none_open")
        self.assertIn("none open", c["note"])


if __name__ == "__main__":
    unittest.main()
