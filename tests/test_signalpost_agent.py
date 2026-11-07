import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Agent, Builder, add_financials, read_inputs, refresh_diff  # noqa: E402
from norway_company_agent.footprint import Page, find_orgnrs, guess_domains, mod11_ok, verify_site  # noqa: E402
from norway_company_agent.nav_jobs import name_key  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402


def page(html: str, url: str = "https://example.no/") -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z"), "homepage")


PROFILE = {"organisation_number": "923609016", "name": "EQUINOR ASA", "business_address": {"adresse": ["Forusbeen 50"], "postnummer": "4035"}}


class InputTests(unittest.TestCase):
    def test_dedupe_and_invalid_rows_never_raise(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.jsonl"
            p.write_text('{"organisation_number":"923609016"}\n{"organisation_number":"923 609 016"}\ngarbage\n915665721\n', encoding="utf-8")
            orgs, problems = read_inputs(str(p))
        self.assertEqual(orgs, ["923609016", "915665721"])
        self.assertEqual(len(problems), 2)


class SingleInputTests(unittest.TestCase):
    def test_one_line_jsonl_is_one_record(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "one.jsonl"
            p.write_text('{"organisation_number":"848882542"}' + chr(10), encoding="utf-8")
            self.assertEqual(read_inputs(str(p))[0], ["848882542"])
            p.write_text('["923609016", "915665721"]', encoding="utf-8")
            self.assertEqual(read_inputs(str(p))[0], ["923609016", "915665721"])
            p.write_text('{"organisation_numbers": ["923609016"]}', encoding="utf-8")
            self.assertEqual(read_inputs(str(p))[0], ["923609016"])


class IdentityTests(unittest.TestCase):
    def test_mod11(self):
        self.assertTrue(mod11_ok("923609016"))
        self.assertFalse(mod11_ok("923609017"))

    def test_orgnr_context(self):
        found, ctx = find_orgnrs("Kontakt oss. Org.nr: 923 609 016 MVA. Tlf 51 99 00 00")
        self.assertIn("923609016", found)
        self.assertIn("923609016", ctx)

    def test_orgnr_on_site_verifies(self):
        v = verify_site(PROFILE, [page("<html><title>Equinor</title><body>Lots of text about energy and the company. <footer>Equinor ASA org.nr 923 609 016</footer></body></html>")], "name_guess")
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["method"], "orgnr_on_site")

    def test_other_orgnr_blocks_publication(self):
        v = verify_site(PROFILE, [page("<html><title>Equinor ASA</title><body>Some long text about a different company here. <footer>Equinor ASA Org.nr 915 665 721</footer></body></html>")], "registry")
        self.assertEqual(v["status"], "ambiguous")

    def test_guess_needs_more_than_name(self):
        v = verify_site(PROFILE, [page("<html><title>Equinor ASA</title><body>Some long text about energy production and other things that are relevant.</body></html>")], "name_guess")
        self.assertEqual(v["status"], "ambiguous")

    def test_guess_brand_plus_address_is_not_enough(self):
        prof = {"organisation_number": "918499946", "name": "PYROKNO AS", "business_address": {"adresse": ["Langeveien 16"], "postnummer": "5003"}}
        html = "<html><title>Pyrokno - Brandklassade fönster</title><body>Vi levererar brandklassade fönster och glaspartier i hela Norden. <footer>Pyrokno Norge Säljkontor Langeveien 16 5003 Bergen</footer></body></html>"
        self.assertEqual(verify_site(prof, [page(html)], "name_guess")["status"], "ambiguous")
        html2 = html.replace("Pyrokno Norge", "Pyrokno AS")
        self.assertEqual(verify_site(prof, [page(html2)], "name_guess")["status"], "verified")

    def test_registry_site_with_name_verifies(self):
        v = verify_site(PROFILE, [page("<html><title>Equinor ASA</title><body>Some long text about energy production and other things that are relevant.</body></html>")], "registry")
        self.assertEqual(v["status"], "verified")

    def test_guess_domains(self):
        self.assertEqual(guess_domains("HANSEN BYGG AS"), ["hansenbygg.no", "hansen-bygg.no", "hansenbygg.com"])
        self.assertEqual(guess_domains("AB AS"), [])

    def test_nav_name_key(self):
        self.assertEqual(name_key("Hansen Bygg AS"), name_key("HANSEN BYGG"))


class EnvelopeTests(unittest.TestCase):
    def test_missing_financial_field_is_not_zero(self):
        b = Builder("923609016")
        add_financials(b, {"status": "available", "source_url": "u", "value": {"records": [{"period": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}, "revenue": None, "equity": 0.0}]}})
        rev = [c for c in b.claims if c["field"] == "revenue"][0]
        eq = [c for c in b.claims if c["field"] == "equity"][0]
        self.assertEqual(rev["availability"], "not_available")
        self.assertIsNone(rev["value"])
        self.assertEqual(eq["value"], 0.0)

    def test_identical_refresh_has_no_changes(self):
        b = Builder("923609016")
        b.claim("legal_identity", "legal_name", "X AS", "available", [b.ev("u", "c", "t", "h")], family="registry", key="legal_name")
        prev = {"claims": b.claims, "evidence": b.evidence}
        self.assertEqual(refresh_diff(prev, b), [])

    def test_deadline_still_emits_every_envelope(self):
        agent = Agent(run_id="t", budget_s=1, modules={"registry_live"}, strategies=set())
        envs, _ = agent.run(["923609016", "915665721"], {}, {})
        self.assertEqual([e["organisation_number"] for e in envs], ["923609016", "915665721"])
        self.assertTrue(all(e["state"] in ("completed", "partial", "failed", "not_found") for e in envs))


if __name__ == "__main__":
    unittest.main()


class ActivityTests(unittest.TestCase):
    def test_demo_posts_are_dropped(self):
        from norway_company_agent.footprint import is_demo_item
        self.assertTrue(is_demo_item({"title": "Take the risk", "url": "https://x.no/enjoy/beatum-inquit"}))
        self.assertTrue(is_demo_item({"title": "Hello world!", "url": "https://x.no/2020/01/hello-world"}))
        self.assertFalse(is_demo_item({"title": "Profilering og prospekt", "url": "https://x.no/profilering-og-prospekt"}))


class FootprintPrecisionTests(unittest.TestCase):
    def test_lone_unmatched_social_link_is_not_published(self):
        from norway_company_agent.footprint import social_from_pages
        prof = {"name": "ENGEVIK BYGGSENTER AS"}
        html = '<html><body><footer><a href="https://facebook.com/flisdesign">fb</a><a href="https://instagram.com/engevikbyggsenter">ig</a></footer></body></html>'
        out = social_from_pages([page(html, "https://engevik.no/")], prof, "engevik.no")
        self.assertEqual([s["url"] for s in out], ["https://instagram.com/engevikbyggsenter"])

    def test_social_plus_org_sameas_and_sitewide_numeric_facebook(self):
        from norway_company_agent.footprint import social_from_pages
        prof = {"name": "LUND ELEKTRO AS", "_social_plus": True}
        ld = '<script type="application/ld+json">{"@type":"Organization","name":"Lund Elektro","sameAs":["https://www.facebook.com/people/Lund-Elektro/100053175655882"]}</script>'
        out = social_from_pages([page('<html><head>' + ld + '</head><body>x</body></html>', "https://lundelektro.no/")], prof, "lundelektro.no")
        self.assertEqual([(s["url"], s["match"]) for s in out], [("https://facebook.com/people/Lund-Elektro/100053175655882", "listed_in_site_organization_sameAs")])
        self.assertEqual(social_from_pages([page('<html><head>' + ld + '</head><body>x</body></html>', "https://lundelektro.no/")], {"name": "LUND ELEKTRO AS"}, "lundelektro.no"), [])
        fb = '<html><body><footer><a href="https://www.facebook.com/profile.php?id=100068082211192">Facebook</a></footer></body></html>'
        two = [page(fb, "https://lundgraveservice.no/"), page(fb, "https://lundgraveservice.no/kontakt")]
        out = social_from_pages(two, {"name": "LUND GRAVESERVICE AS", "_social_plus": True}, "lundgraveservice.no")
        self.assertEqual([s["url"] for s in out], ["https://facebook.com/profile.php?id=100068082211192"])
        # only on one of two pages (a partner link in an article) -> not published; a named Facebook link present -> not published
        one = [page(fb, "https://x.no/"), page("<html><body>kontakt</body></html>", "https://x.no/kontakt"), page("<html><body>om</body></html>", "https://x.no/om")]
        self.assertEqual(social_from_pages(one, {"name": "X AS", "_social_plus": True}, "x.no"), [])
        mixed = fb.replace("</footer>", '<a href="https://facebook.com/flisdesign">fb</a></footer>')
        self.assertEqual(social_from_pages([page(mixed, "https://x.no/"), page(mixed, "https://x.no/k")], {"name": "X AS", "_social_plus": True}, "x.no"), [])

    def test_ats_links_are_not_jobs(self):
        from norway_company_agent.footprint import jobs_from_page
        html = '<html><body><a href="https://app.teamtailor.com/companies/x/dashboard">Log in as employee</a></body></html>'
        self.assertEqual(jobs_from_page(page(html, "https://grunt.pro/careers"), "grunt.pro"), [])


class ErrorPageTests(unittest.TestCase):
    def test_soft_404_is_not_a_website(self):
        prof = {"organisation_number": "913463331", "name": "OPPLAND BYGGVASK AS", "business_address": {}}
        html = "<html><title>404 Ikke funnet | Oppland Byggvask AS</title><body>Siden du ser etter ble ikke funnet. Prøv å omformulere søket ditt, eller bruk menyen.</body></html>"
        self.assertEqual(verify_site(prof, [page(html)], "name_guess")["status"], "not_available")


class RefreshTests(unittest.TestCase):
    def test_material_changes_are_typed(self):
        old = Builder("923609016")
        e = old.ev("u", "official_roles", "t0", "h0", "DAGL A")
        old.claim("leadership_workplaces", "role", {"role": "Daglig leder", "name": "A"}, "available", [e], family="roles", key="role:DAGL:A")
        old.claim("legal_identity", "business_address", {"adresse": ["Gate 1"]}, "available", [e], family="registry", key="business_address")
        new = Builder("923609016")
        e2 = new.ev("u", "official_roles", "t1", "h1", "DAGL B")
        new.claim("leadership_workplaces", "role", {"role": "Daglig leder", "name": "B"}, "available", [e2], family="roles", key="role:DAGL:B")
        new.claim("legal_identity", "business_address", {"adresse": ["Gate 2"]}, "available", [e2], family="registry", key="business_address")
        new.claim("hiring_activity", "job_posting", {"job_id": "nav:1", "title": "Chef"}, "available", [e2], family="hiring", key="nav:1")
        changes = {(c["change_type"], c["key"]) for c in refresh_diff({"claims": old.claims, "evidence": old.evidence}, new)}
        self.assertIn(("new_role", "role:DAGL:B"), changes)
        self.assertIn(("removed_role", "role:DAGL:A"), changes)
        self.assertIn(("changed_value", "business_address"), changes)
        self.assertIn(("new_job", "nav:1"), changes)


class ArticleDateTests(unittest.TestCase):
    def test_visible_norwegian_date_after_headline(self):
        from norway_company_agent.footprint import activity_from_article
        html = "<html><title>x</title><body><h1>Videre vekst og økt lønnsomhet</h1><p>Publisert 12. mars 2026</p><p>Tekst om resultatene.</p></body></html>"
        self.assertIsNone(activity_from_article(page(html, "https://n.no/aktuelt/vekst"), "2026-11-06"))
        it = activity_from_article(page(html, "https://n.no/aktuelt/vekst"), "2026-11-06", plus=True)
        self.assertEqual((it["date"], it["method"]), ("2026-03-12", "visible_date_after_headline"))


class FeedDemoTests(unittest.TestCase):
    def test_lorem_ipsum_feed_items_are_dropped(self):
        from norway_company_agent.footprint import activity_from_feed
        xml = ("<rss><channel><item><title>Dream big</title><link>https://eprod.no/dream-big/</link><pubDate>Mon, 01 Mar 2021 10:00:00 +0000</pubDate>"
               "<description>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</description></item>"
               "<item><title>Ny avtale med Statens vegvesen</title><link>https://eprod.no/ny-avtale/</link><pubDate>Tue, 02 Mar 2021 10:00:00 +0000</pubDate>"
               "<description>Vi har signert en ny rammeavtale.</description></item></channel></rss>")
        r = Response("https://eprod.no/feed", "https://eprod.no/feed", 200, xml.encode(), "application/rss+xml", "t")
        self.assertEqual([i["title"] for i in activity_from_feed(r, "eprod.no", "2026-11-06")], ["Ny avtale med Statens vegvesen"])
