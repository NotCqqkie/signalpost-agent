"""main2 fresh-run audit regressions: tenant/group pages and server default pages are not the company's own site."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.footprint import Page, tenant_or_group_page, verify_site  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402


def page(html: str, url: str) -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z"), "homepage")


def attempt(pages, method):
    return {"pages": pages, "verdict": {"status": "verified", "method": method}}


class TenantGroupTests(unittest.TestCase):
    def test_subpath_page_on_group_site_with_other_orgnr(self):
        html = ("<html><body><h1>FLOW Elektro Oslo Vest</h1><footer>FLOW Elektro Oslo Vest, Grini Naeringspark 3, 1361 Osteras "
                "Org nr: 927 992 086 | FLOW Group AS org.nr. 964 982 953</footer></body></html>")
        prof = {"organisation_number": "927992086", "name": "FLOW ELEKTRO OSLO VEST AS"}
        self.assertIsNotNone(tenant_or_group_page(attempt([page(html, "https://flow-elektro.no/oslo-vest")], "orgnr_on_site"), prof))
        # the same page at the site root is the company's own site
        self.assertIsNone(tenant_or_group_page(attempt([page(html.replace("FLOW Group AS org.nr. 964 982 953", ""), "https://flowoslovest.no/")], "orgnr_on_site"), prof))

    def test_subpath_name_only_match_on_chain_portal(self):
        html = "<html><head><title>Servicegrossisten More - TP Engros og Sunnmore Engros - sg.no</title></head><body>x</body></html>"
        prof = {"organisation_number": "843298702", "name": "TP-ENGROS AS"}
        self.assertIsNotNone(tenant_or_group_page(attempt([page(html, "https://www.sg.no/sg-more/")], "registry_listed_name"), prof))

    def test_own_site_naming_a_sister_company_is_kept(self):
        # a sibling-entity rule was tried and dropped: vollenbatservice.no names Vollen Motor AS and is the company's own site
        html = "<html><body><footer>Vollen Batservice AS - samarbeid med Vollen Motor AS</footer></body></html>"
        prof = {"organisation_number": "914874319", "name": "VOLLEN BATSERVICE AS"}
        self.assertIsNone(tenant_or_group_page(attempt([page(html, "https://www.vollenbatservice.no/")], "registry_listed_name"), prof))

    def test_language_root_is_not_a_subpath(self):
        html = "<html><head><title>NTT DATA Business Solutions Norge</title></head><body>x</body></html>"
        prof = {"organisation_number": "992036079", "name": "NTT DATA BUSINESS SOLUTIONS AS"}
        self.assertIsNone(tenant_or_group_page(attempt([page(html, "https://nttdata-solutions.com/no/")], "registry_listed_name"), prof))

    def test_root_page_file_is_not_a_subpath(self):
        html = "<html><head><title>Hardanger Elektro AS</title></head><body>tlf 56 55 55 55</body></html>"
        prof = {"organisation_number": "914398614", "name": "HARDANGER ELEKTRO AS"}
        self.assertIsNone(tenant_or_group_page(attempt([page(html, "https://hardangerelektro.no/home.php")], "name_and_phone"), prof))

    def test_server_default_page_is_placeholder(self):
        html = "<html><head><title>Index of /</title></head><body><h1>Index of /</h1><a href='php.ini'>php.ini</a> Proudly Served by LiteSpeed Web Server at revenka.no Port 443</body></html>"
        v = verify_site({"organisation_number": "998633869", "name": "REVENKA AS"}, [page(html, "https://revenka.no/")], "registry_email")
        self.assertEqual(v["status"], "not_available")


if __name__ == "__main__":
    unittest.main()


class NameBasedRiskTests(unittest.TestCase):
    def test_parent_title_and_address_name(self):
        from norway_company_agent.footprint import name_based_risk
        deas = "<html><head><title>DEAS Gruppen AS || en ledende entreprenor i Innlandet</title></head><body>Dokka Entreprenor AS Anlegg Innlandet AS</body></html>"
        self.assertIsNotNone(name_based_risk(attempt([page(deas, "https://www.deas-gruppen.no/")], "registry_listed_name"),
                                             {"organisation_number": "986652469", "name": "DOKKA ENTREPRENOR AS"}))
        west = "<html><head><title>Home - Westcontrol</title></head><body>Westcontrol Berget AS, Semsvegen 51, Notodden</body></html>"
        self.assertIsNotNone(name_based_risk(attempt([page(west, "https://westcontrol.com/")], "registry_listed_name"),
                                             {"organisation_number": "917349150", "name": "SEMSVEGEN 51 AS"}))
        fit = "<html><head><title>Fit Solutions AS</title></head><body>Siden er under oppbygging</body></html>"
        self.assertIsNone(name_based_risk(attempt([page(fit, "https://fitsolutions.no/")], "registry_listed_name"),
                                          {"organisation_number": "931696505", "name": "FITSOLUTIONS AS"}))
        own = "<html><head><title>Hjem - Hansen Bygg AS</title></head><body>x</body></html>"
        self.assertIsNone(name_based_risk(attempt([page(own, "https://hansenbygg.no/")], "registry_listed_name"),
                                          {"organisation_number": "912345602", "name": "HANSEN BYGG AS"}))

    def test_namesake_in_register(self):
        import json
        import norway_company_agent.http as http
        from norway_company_agent.footprint import namesake_in_register
        body = {"_embedded": {"enheter": [{"organisasjonsnummer": "920175821", "navn": "AUTO SHINE AS"},
                                          {"organisasjonsnummer": "830038442", "navn": "AUTO-SHINE AS"},
                                          {"organisasjonsnummer": "926341855", "navn": "AUTO-SHINE BILPLEIE AS"}]}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            self.assertIsNotNone(namesake_in_register({"organisation_number": "830038442", "name": "AUTO-SHINE AS"}))
            body["_embedded"]["enheter"] = body["_embedded"]["enheter"][1:]
            self.assertIsNone(namesake_in_register({"organisation_number": "830038442", "name": "AUTO-SHINE AS"}))
        finally:
            http.fetch_json = orig


class SharedDomainAndAbroadJobTests(unittest.TestCase):
    def test_shared_service_domain_needs_orgnr(self):
        from norway_company_agent.footprint import name_based_risk
        html = "<html><head><title>Gabler</title></head><body>Pensjonskasser: Skagerak Energi Pensjonskasse, BKK pensjonskasse</body></html>"
        a = {"pages": [page(html, "https://gabler.no/")], "domain": "gabler.no", "verdict": {"status": "verified", "method": "registry_listed_name"}}
        self.assertIsNotNone(name_based_risk(a, {"organisation_number": "979320485", "name": "SKAGERAK ENERGI PENSJONSKASSE"}))
        kiwi = {"pages": [page("<html><head><title>KIWI</title></head><body>KIWI Norge AS</body></html>", "https://kiwi.no/")], "domain": "kiwi.no",
                "verdict": {"status": "verified", "method": "registry_listed_name"}}
        self.assertIsNone(name_based_risk(kiwi, {"organisation_number": "997366646", "name": "KIWI NORGE AS"}))

    def test_jobposting_abroad_is_dropped(self):
        from norway_company_agent.footprint import jobs_from_page
        def jp(country):
            return ('<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"Production Engineer",'
                    '"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"X","addressCountry":"' + country + '"}}]}</script></head><body>x</body></html>')
        self.assertEqual(jobs_from_page(page(jp("FI"), "https://careers.nordec.com/jobs/1"), "nordec.com"), [])
        self.assertEqual(len(jobs_from_page(page(jp("NO"), "https://careers.nordec.com/jobs/2"), "nordec.com")), 1)


class AbroadFreeTextTests(unittest.TestCase):
    def test_free_text_not_read_on_abroad_jobposting_page(self):
        from norway_company_agent.site_hiring import job_abroad
        html = ('<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"Production Engineer",'
                '"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"FI"}}}</script></head>'
                '<body><h1>Production Engineer</h1><a>Apply here</a></body></html>')
        self.assertTrue(job_abroad(page(html, "https://careers.nordec.com/jobs/8670059")))
        self.assertFalse(job_abroad(page(html.replace('"FI"', '"NO"'), "https://careers.nordec.com/jobs/1")))
        self.assertFalse(job_abroad(page("<html><body><h1>Ledig stilling: Elektriker</h1></body></html>", "https://firma.no/jobb")))


class Fresh4RoundTests(unittest.TestCase):
    def test_global_group_handles_for_norwegian_subsidiary(self):
        from norway_company_agent.footprint import social_from_pages
        html = ('<html><body><footer><a href="https://www.facebook.com/OMV">f</a><a href="https://x.com/omv">x</a>'
                '<a href="https://www.instagram.com/omvnorge">i</a></footer></body></html>')
        out = social_from_pages([page(html, "https://www.omv.no/")], {"name": "OMV NORGE AS"}, "omv.no")
        self.assertEqual([s["url"] for s in out], ["https://instagram.com/omvnorge"])
        ld = '<script type="application/ld+json">{"@type":"Organization","sameAs":["https://www.facebook.com/OMV","https://x.com/omv"]}</script>'
        out = social_from_pages([page('<html><head>' + ld + '</head><body>x</body></html>', "https://www.omv.no/")], {"name": "OMV NORGE AS", "_social_plus": True}, "omv.no")
        self.assertEqual(out, [])  # the sameAs fallback must not bring the group handles back
        regional = '<html><body><footer><a href="https://facebook.com/connectmidt">f</a></footer></body></html>'
        self.assertEqual(len(social_from_pages([page(regional, "https://connectmidt.no/")], {"name": "CONNECT MIDT-NORGE"}, "connectmidt.no")), 1)

    def test_title_names_sister_entity_in_register(self):
        import norway_company_agent.http as http
        from norway_company_agent.footprint import title_entity_in_register
        body = {"_embedded": {"enheter": [{"organisasjonsnummer": "981932218", "navn": "RETAILPARTNER AS"}]}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            a = {"pages": [page("<html><head><title>Retailpartner AS – Spesialister</title></head><body>x</body></html>", "https://retailpartner.no/")],
                 "verdict": {"method": "name_and_address"}}
            self.assertIsNotNone(title_entity_in_register(a, {"organisation_number": "995172062", "name": "RETAILPARTNER EIENDOM AS"}))
            own = {"pages": [page("<html><head><title>Hjem - Hansen Bygg AS</title></head><body>x</body></html>", "https://hansenbygg.no/")],
                   "verdict": {"method": "registry_listed_name"}}
            self.assertIsNone(title_entity_in_register(own, {"organisation_number": "912345602", "name": "HANSEN BYGG AS"}))
        finally:
            http.fetch_json = orig


class NotPostTypeTests(unittest.TestCase):
    def test_gallery_and_slider_pages_are_not_posts(self):
        from norway_company_agent.site_news import article_item
        html = '<html><head><meta property="article:published_time" content="2025-05-01T10:00:00+02:00"></head><body><h1>Galleri 2</h1></body></html>'
        self.assertIsNone(article_item(page(html, "https://www.fotograf-bjorkhaug.no/rl_gallery/galleri-2"), "2026-11-06", post_like_url=True))
        self.assertIsNotNone(article_item(page(html.replace("Galleri 2", "Ny fotoutstilling"), "https://www.fotograf-bjorkhaug.no/nyheter/utstilling"), "2026-11-06", post_like_url=True))


class Fresh5RoundTests(unittest.TestCase):
    def test_registry_name_must_be_a_phrase(self):
        from norway_company_agent.footprint import verify_site
        scattered = "<html><head><title>Inmarsat.com</title></head><body><footer>Solutions NexusWave Fleet Xpress Inmarsat Partners portal</footer></body></html>"
        v = verify_site({"organisation_number": "964823138", "name": "INMARSAT SOLUTIONS AS"}, [page(scattered, "https://www.inmarsat.com/")], "registry")
        self.assertNotEqual(v["status"], "verified")
        phrase = "<html><head><title>Inmarsat Solutions</title></head><body><footer>Inmarsat Solutions AS, Oslo</footer></body></html>"
        v = verify_site({"organisation_number": "964823138", "name": "INMARSAT SOLUTIONS AS"}, [page(phrase, "https://www.inmarsat.no/")], "registry")
        self.assertEqual(v["status"], "verified")

    def test_demo_titles_and_generic_board_links(self):
        from norway_company_agent.footprint import is_demo_item
        from norway_company_agent.site_hiring import board_ad_jobs
        self.assertTrue(is_demo_item({"title": "Velkommen til WordPress", "url": "https://x.no/2024/01/01/velkommen/"}))
        self.assertTrue(is_demo_item({"title": "This is the title of your first post", "url": "https://x.no/post/first"}))
        html = '<html><body><a href="https://www.finn.no/job/ad/478243669">Stillingsannonsen på finn</a><a href="https://www.finn.no/job/ad/1">Elektriker</a></body></html>'
        self.assertNotIn("Stillingsannonsen på finn", [j["title"] for j in board_ad_jobs(page(html, "https://pganorway.no/jobb"))])

    def test_namesake_whose_name_ends_with_ours(self):
        import norway_company_agent.http as http
        from norway_company_agent.footprint import namesake_in_register
        body = {"_embedded": {"enheter": [{"organisasjonsnummer": "989041096", "navn": "FOTOGRAF GRINNA AS"}]}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            self.assertIsNotNone(namesake_in_register({"organisation_number": "929139887", "name": "GRINNA AS"}, site_text=" home page fotograf grinna as om oss "))
            self.assertIsNone(namesake_in_register({"organisation_number": "929139887", "name": "GRINNA AS"}, site_text=" grinna as konsulent "))
            # a sister/child entity in the company's own registered group is not a namesake (atico.no, ATICO SOLOR AS)
            self.assertIsNone(namesake_in_register({"organisation_number": "929139887", "name": "GRINNA AS", "_group_orgnrs": ["989041096"]},
                                                   site_text=" home page fotograf grinna as om oss "))
        finally:
            http.fetch_json = orig


class PhraseVariantsTests(unittest.TestCase):
    def test_connector_words_and_body_phrase(self):
        from norway_company_agent.footprint import verify_site
        body = "<p>Vi bygger hus og hytter i hele regionen, med lang erfaring og fornoyde kunder.</p>"
        v = verify_site({"organisation_number": "1", "name": "EIE & FJERMESTAD AS"},
                        [page("<html><head><title>Hjem - Eie og Fjermestad</title></head><body>" + body + "</body></html>", "https://ef-bygg.no/")], "registry")
        self.assertEqual(v["status"], "verified")
        v = verify_site({"organisation_number": "1", "name": "TUBES INTERNATIONAL NORGE AS"},
                        [page("<html><head><title>Slanger</title></head><body><p>Tubes International Norge AS leverer slanger og koblinger.</p>" + body + "</body></html>", "https://tubes-international.no/")], "name_guess")
        v = verify_site({"organisation_number": "1", "name": "TUBES INTERNATIONAL NORGE AS"},
                        [page("<html><head><title>Global</title></head><body><p>Country branches: Tubes International Norge AS, Polska, Czech.</p>" + body + "</body></html>", "https://tubes-international.com/")], "registry")
        self.assertEqual(v["status"], "ambiguous")


class ForeignHandleTests(unittest.TestCase):
    def test_foreign_country_handle_is_a_sister_channel(self):
        from norway_company_agent.footprint import social_from_pages
        html = ('<html><body><footer><a href="https://www.instagram.com/enviroprocess_sweden">i</a>'
                '<a href="https://www.facebook.com/enviroprocessnorge">f</a></footer></body></html>')
        out = social_from_pages([page(html, "https://enviroprocess.com/nb/")], {"name": "ENVIROPROCESS NORWAY AS"}, "enviroprocess.com")
        self.assertEqual([s["url"] for s in out], ["https://facebook.com/enviroprocessnorge"])


class ProximityNameTests(unittest.TestCase):
    def test_name_words_close_together_but_not_scattered(self):
        from norway_company_agent.footprint import verify_site
        body = "<p>Vi tar vare paa kattene dine mens du er bortreist, med god plass og trygg omsorg hele aaret.</p>"
        own = "<html><head><title>Pusehuset Kattehotell</title></head><body>" + body + "<footer>Pusehuset Kattehotell i Malvik</footer></body></html>"
        v = verify_site({"organisation_number": "916850433", "name": "PUSEHUSET MALVIK KATTEHOTELL AS"}, [page(own, "https://pusehusetkattehotell.no/")], "registry")
        self.assertEqual(v["status"], "verified")
        church = "<html><head><title>Kirkko Norjassa</title></head><body>" + body + "<footer>Den finske menigheten i Norge</footer></body></html>"
        v = verify_site({"organisation_number": "871245762", "name": "DEN FINSKE MENIGHETEN I NORGE"}, [page(church, "https://www.finskekirken.no/")], "registry")
        self.assertEqual(v["status"], "verified")


class Fresh6RoundTests(unittest.TestCase):
    def test_portal_redirect_target_and_job_rules(self):
        from norway_company_agent.footprint import tenant_or_group_page, jobs_from_page
        from norway_company_agent.site_hiring import board_ad_jobs
        a = {"pages": [page("<html><body>Ullernaasen Boligsameie org.nr 912345602</body></html>", "https://vibbo.no/ullernasen/om")],
             "verdict": {"status": "verified", "method": "orgnr_on_site"}}
        self.assertIsNotNone(tenant_or_group_page(a, {"organisation_number": "912345602", "name": "ULLERNÅSEN BOLIGSAMEIE"}))
        jp = ('<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"Sveiser","hiringOrganization":{"@type":"Organization","name":"Heim Skipsservice"}}'
              '</script></head><body>x</body></html>')
        self.assertEqual(jobs_from_page(page(jp, "https://www.heim-maritim.no/karriere/sveiser"), "heim-maritim.no")[0]["hiring_org"], "Heim Skipsservice")
        html = '<html><body><a href="https://www.finn.no/job/ad/466750225">Gå til annonse</a><a href="https://happii.teamtailor.com/jobs/1">Meld interesse</a></body></html>'
        self.assertEqual(board_ad_jobs(page(html, "https://invicta.no/jobb")), [])


class ClosedAdTests(unittest.TestCase):
    def test_filled_position_page_yields_no_job(self):
        from norway_company_agent.site_hiring import free_text_jobs
        html = ("<html><body><h1>Industriell ingeniør</h1><p>Meld interesse. Stillingen er besatt.</p>"
                "<p>Send søknad innen 1. desember.</p></body></html>")
        jobs, check = free_text_jobs(page(html, "https://www.happii.no/jobs/ingenior"), "2026-11-06")
        self.assertEqual(jobs, [])


class ExpiredJsonLdJobTests(unittest.TestCase):
    def test_expired_jobposting_not_published(self):
        from norway_company_agent.agent import Builder, add_footprint
        b = Builder("912345602")
        crawl = {"jobs": [{"title": "Sveiser", "url": "https://x.no/karriere/sveiser", "date_posted": "2025-02-03", "valid_through": "2025-02-03",
                           "method": "jsonld_jobposting", "found_on": "https://x.no/karriere/sveiser", "retrieved_at": "t", "sha256": "h", "hiring_org": "X"}],
                 "brand": {}, "social_profiles": [], "activity": [], "careers_pages": [], "hiring_checks": []}
        site = {"status": "verified", "selected": {"verdict": {"method": "orgnr_on_site", "proof_url": "https://x.no/", "proof_retrieved_at": "t",
                "proof_sha256": "h", "span": "x"}, "pages": [page("<html><body>x</body></html>", "https://x.no/")], "source": "registry"}}
        add_footprint(b, site, crawl, [], "complete", {"name": "X AS"})
        self.assertFalse(any(c["family"] == "hiring" and c["availability"] == "available" for c in b.claims))


class ConditionalClosedPhraseTests(unittest.TestCase):
    def test_privacy_note_is_not_a_closed_ad(self):
        from norway_company_agent.site_hiring import NONE_RE
        self.assertIsNone(NONE_RE.search("soknaden slettes automatisk 6 maneder etter at stillingen er besatt"))
        self.assertIsNotNone(NONE_RE.search("meld interesse stillingen er besatt om stillingen"))


class Fresh7RoundTests(unittest.TestCase):
    def test_two_digit_year_deadline(self):
        from norway_company_agent.site_hiring import parse_deadline
        self.assertEqual(parse_deadline("30. september 26", "2026-11-06"), "2026-09-30")

    def test_suffix_only_ignores_exact_namesake(self):
        import norway_company_agent.http as http
        from norway_company_agent.footprint import namesake_in_register
        body = {"_embedded": {"enheter": [{"organisasjonsnummer": "1", "navn": "BREDLAND"}, {"organisasjonsnummer": "982952352", "navn": "LARS THOMAS BREDLAND"}]}}
        orig = http.fetch_json
        http.fetch_json = lambda url, **k: http.FetchResult(url, 200, 1, 1, body)
        try:
            self.assertIsNone(namesake_in_register({"organisation_number": "987579080", "name": "BREDLAND AS"}, suffix_only=True, site_text=" bredland as tomrer "))
            self.assertIsNotNone(namesake_in_register({"organisation_number": "987579080", "name": "BREDLAND AS"}, suffix_only=True, site_text=" lars thomas bredland "))
        finally:
            http.fetch_json = orig


class Fresh8RoundTests(unittest.TestCase):
    def test_chain_job_portal_page_not_published(self):
        from norway_company_agent.agent import Builder, add_footprint
        def job(t):
            return {"title": t, "url": "https://www.norgesgruppen.no/karriere/ledige-stillinger#" + t, "heading": t, "method": "free_text_heading_apply_cue",
                    "found_on": "https://www.norgesgruppen.no/karriere/ledige-stillinger", "retrieved_at": "t", "sha256": "h", "deadline": None}
        crawl = {"jobs": [job(t) for t in ("KIWI Stavsjø søker låseansvarlig", "Joker Klingenberg søker butikkmedarbeider", "Joker Røa søker butikkmedarbeider",
                                           "Daglig leder Esso Dampsaga", "Joker Svarttjern søker butikkmedarbeider")],
                 "brand": {}, "social_profiles": [], "activity": [], "careers_pages": [], "hiring_checks": []}
        site = {"status": "verified", "selected": {"verdict": {"method": "orgnr_on_site", "proof_url": "https://www.norgesgruppen.no/", "proof_retrieved_at": "t",
                "proof_sha256": "h", "span": "x"}, "pages": [page("<html><body>x</body></html>", "https://www.norgesgruppen.no/")], "source": "registry"}}
        b = Builder("819731322")
        add_footprint(b, site, crawl, [], "complete", {"name": "NORGESGRUPPEN ASA"})
        self.assertFalse(any(c["family"] == "hiring" and c["availability"] == "available" for c in b.claims))
        b2 = Builder("997366646")
        crawl2 = {**crawl, "jobs": [job(f"KIWI {s} søker butikkmedarbeider") for s in ("Stavsjø", "Hovden", "Bondi", "Lilleby", "Vogtsgate")]}
        add_footprint(b2, site, crawl2, [], "complete", {"name": "KIWI NORGE AS"})
        self.assertEqual(sum(1 for c in b2.claims if c["family"] == "hiring" and c["availability"] == "available"), 5)


class ControllerTests(unittest.TestCase):
    def test_other_controller_on_privacy_page(self):
        from norway_company_agent.footprint import other_controller
        p = page("<html><body>Behandlingsansvarlig: CUT'N GO HAMAR AS Aslak Bolts gate 48 2316 HAMAR</body></html>", "https://cutngo.no/personvern")
        self.assertIsNotNone(other_controller([p], {"name": "CUT'N GO HAUGESUND AS"}))
        own = page("<html><body>1. Behandlingsansvarlig Learnlink AS Organisasjonsnummer: 916 810 393</body></html>", "https://learnlink.no/personvern")
        self.assertIsNone(other_controller([own], {"name": "LEARNLINK AS"}))
