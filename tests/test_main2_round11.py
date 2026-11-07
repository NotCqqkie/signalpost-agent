import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_site_news import page  # noqa: E402


class Round11Tests(unittest.TestCase):
    def test_site_shows_address_of_longer_namesake(self):
        # E36: korenoptikk.no is Koren Optikk Trondheim AS's shop (Sondre gate 23, 7010 Trondheim), not KOREN OPTIKK AS (Rissa)
        import norway_company_agent.http as http
        from norway_company_agent.footprint import namesake_in_register
        body = {"_embedded": {"enheter": [
            {"organisasjonsnummer": "997084101", "navn": "KOREN OPTIKK TRONDHEIM AS",
             "forretningsadresse": {"adresse": ["Søndre gate 23"], "postnummer": "7010", "poststed": "TRONDHEIM"}}]}}
        prof = {"organisation_number": "990879753", "name": "KOREN OPTIKK AS",
                "business_address": {"adresse": ["Kuben Kjøpesenter"], "postnummer": "7100", "poststed": "RISSA"}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            self.assertIsNotNone(namesake_in_register(prof, site_text=" koren optikk as briller sondre gate 23 7010 trondheim "))
            self.assertIsNone(namesake_in_register(prof, site_text=" koren optikk as briller kuben 7100 rissa "))
        finally:
            http.fetch_json = orig

    def test_cjk_page(self):
        # E37: datingsolutions.net (registry-declared) now serves a Chinese site
        from norway_company_agent.footprint import cjk_page
        zh = page("<html><head><title>低代码平台</title></head><body><p>" + "全国服务热线工作时间市场合作技术咨询上海北京江苏" * 5
                  + " marketing@datingsolutions.net</p></body></html>", "https://www.datingsolutions.net/")
        self.assertTrue(cjk_page(zh))
        no = page("<html><body><p>Datingsolutions AS utvikler apper for dating. Kontakt oss i Stavanger.</p></body></html>")
        self.assertFalse(cjk_page(no))


    def test_deleted_same_name_predecessor_number(self):
        # E38: kaasboll-boats.no prints its deleted same-name predecessor's number
        import norway_company_agent.http as http
        from norway_company_agent.footprint import deleted_same_name
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, {"navn": "KAASBØLL BOATS AS", "slettedato": "2021-12-06"})
        try:
            self.assertTrue(deleted_same_name("986529152", "KAASBØLL BOATS AS"))
            self.assertFalse(deleted_same_name("986529152", "KAASBØLL MARINE AS"))
            http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, {"navn": "KAASBØLL BOATS AS"})
            self.assertFalse(deleted_same_name("986529152", "KAASBØLL BOATS AS"))  # active: a real other owner
        finally:
            http.fetch_json = orig


    def test_group_listing_is_not_own_site(self):
        # E39: selvaageiendom.no lists Tjuvholmen F2 Naering AS among its property companies
        from norway_company_agent.footprint import verify_site
        rows = "".join(f"<li>Tjuvholmen F{i} Naering AS {n}</li>" for i, n in ((1, "988 420 050"), (3, "988 419 958"), (7, "994 286 021"), (8, "923 609 016")))
        html = "<html><head><title>Selvaag Eiendom</title></head><body><ul>" + rows + "<li>Tjuvholmen F2 Naering AS 987 658 681</li></ul></body></html>"
        prof = {"organisation_number": "987658681", "name": "TJUVHOLMEN F2 NÆRING AS"}
        v = verify_site(prof, [page(html, "https://www.selvaageiendom.no/")], "registry")
        self.assertEqual(v["status"], "ambiguous")
        own = html.replace("<title>Selvaag Eiendom</title>", "<title>Tjuvholmen Naering</title>")
        self.assertEqual(verify_site(prof, [page(own, "https://www.tjuvholmen.no/")], "registry")["status"], "verified")


    def test_jobposting_abroad_by_title(self):
        # main2 fresh12: careers.tieto.com JobPosting "... in Solna, Sweden" without a jobLocation country
        import json as _json
        from norway_company_agent.footprint import jobs_from_page
        def jp(title, url):
            ld = _json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": title, "url": url})
            return page('<html><head><script type="application/ld+json">' + ld + "</script></head><body><h1>Jobb</h1></body></html>", "https://careers.tieto.com/")
        self.assertEqual(jobs_from_page(jp("Dispute Analyst", "https://careers.tieto.com/job/dispute-analyst-in-solna-sweden-jid-3234"), "tieto.com"), [])
        self.assertEqual(len(jobs_from_page(jp("Konsulent Oslo", "https://careers.tieto.com/job/konsulent-oslo-norway"), "tieto.com")), 1)
        self.assertEqual(len(jobs_from_page(jp("Elektriker", "https://firma.no/jobb/elektriker"), "firma.no")), 1)


    def test_foreign_tld_and_group_listing(self):
        from norway_company_agent.footprint import name_based_risk, verify_site
        # E41: idainfront.no forwards to the Swedish company's idainfront.se
        se = page("<html><head><title>Ida Infront</title></head><body><p>Ida Infront AS</p></body></html>", "https://www.idainfront.se/")
        att = {"url": "https://www.idainfront.no/", "source": "registry", "pages": [se], "domain": "idainfront.se",
               "verdict": {"status": "verified", "method": "registry_listed_name"}}
        self.assertIn("foreign country domain", name_based_risk(att, {"organisation_number": "988393568", "name": "IDA INFRONT AS"}) or "")
        # E42: our number in a contact list of several group entities marks the verdict as a group listing
        rows = "".join(f"<li>Tieto {c} AS Business ID: {n}</li>" for c, n in (("Sweden", "988 420 050"), ("Finland", "988 419 958"),
                                                                                 ("Denmark", "994 286 021"), ("Banktech", "923 609 016")))
        html = "<html><head><title>Tieto</title></head><body><ul>" + rows + "<li>Tieto Norway AS Business ID: 933012867</li></ul></body></html>"
        v = verify_site({"organisation_number": "933012867", "name": "TIETO NORWAY AS"}, [page(html, "https://www.tieto.com/en/")], "registry")
        self.assertEqual(v["status"], "verified")
        self.assertTrue(v.get("group_listing"))


    def test_name_only_inside_a_web_address_is_not_a_name_match(self):
        # E43: radar-technology.com (registry-declared) now a casino site whose footer says "(c) www.radar-technology.com"
        from norway_company_agent.footprint import verify_site
        prof = {"organisation_number": "912590291", "name": "RADAR TECHNOLOGY AS"}
        casino = page("<html><head><title>Beste casino bonuser</title></head><body><p>Spill casino</p><footer>© www.radar-technology.com</footer></body></html>",
                      "https://www.radar-technology.com/")
        self.assertNotEqual(verify_site(prof, [casino], "registry")["status"], "verified")
        own = page("<html><head><title>Radar Technology AS</title></head><body><p>Teknisk radgivning</p><footer>© Radar Technology AS</footer></body></html>",
                   "https://www.radar-technology.com/")
        self.assertEqual(verify_site(prof, [own], "registry")["status"], "verified")


if __name__ == "__main__":
    unittest.main()
