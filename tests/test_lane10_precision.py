"""Regression tests from the lane 10 precision audit of C3-scale (each case is a real published error or a real
correct claim that must survive). Labels: eval/labels/lane10-precision/labels-C3-scale.jsonl."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.footprint import (Page, activity_from_article, activity_from_feed, discover_website,  # noqa: E402
                                             public_brand, self_description)
from norway_company_agent.net import Response  # noqa: E402

TODAY = "2026-11-06"


def page(url: str, html: str) -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html", "2026-11-06T01:20:00Z"), "article")


def test_brand_is_the_segment_naming_the_company():
    cases = [  # (site_name, title, legal name, domain, expected)
        ("", "Forside | Hasvold AS", "HASVOLD AS", "hasvold.no", "Hasvold AS"),
        ("", "Hjem - Skancke AS", "SKANCKE AS", "skancke.no", "Skancke AS"),
        ("", "Startside - Jaeger-Liteseat AS", "JAEGER-LITESEAT AS", "jaeger-liteseat.no", "Jaeger-Liteseat AS"),
        ("", "Hver dag forbedrer vi hverdagen - Norconsult", "NORCONSULT NORGE AS", "norconsult.no", "Norconsult"),
        ("", "Ditt bilverksted i Rogaland og Hordaland | Voll Motor", "VOLL MOTOR AS", "vollmotor.no", "Voll Motor"),
        ("", "100-Meterskogen barnehage | Vi vil sette spor", "100-METERSKOGEN BARNEHAGE AS", "100meterskogen.no", "100-Meterskogen barnehage"),
        ("", "Arne Hansen Installasjon AS - Arne Hansen Installasjon din lokale elektriker på Hamar og Brumunddal",
         "ARNE HANSEN INSTALLASJON AS", "arnehansen.no", "Arne Hansen Installasjon AS"),
        ("Adams Express", "Adams Express", "AS ADAMS EXPRESS", "adams.no", "Adams Express"),  # correct claim kept
        ("", "Taraxa – Plant-Based Natural Rubber for a Microplastic-Free Future", "TARAXA AS", "taraxa.no", "Taraxa"),
    ]
    for site_name, title, name, domain, want in cases:
        assert public_brand(site_name, title, name, domain) == want, (title, want)


def test_brand_not_published_for_products_portals_and_slugs():
    assert public_brand("Visual Registration System", "", "HARDANGER ELEKTRO AS", "hardangerelektro.no") is None
    # (the Vibbo portal brand is stopped earlier: portal tenant pages never become the company site, see below)
    assert public_brand("tannlegedanielsen", "", "TANNLEGE DANIELSEN AS", "tannlegedanielsen.no") is None
    assert public_brand("tannlegedanielsen", "Tannlege Danielsen | Velkommen", "TANNLEGE DANIELSEN AS", "tannlegedanielsen.no") == "Tannlege Danielsen"
    assert public_brand("", "Hjem", "SKANCKE AS", "skancke.no") is None


def test_self_description_rejects_name_only_and_placeholders():
    assert self_description("Lille Arkana", "LILLE ARKANA AS", "Lille Arkana") is None
    assert self_description("Butikken bruker programvare fra PrestaShop", "AREALCONSULT AS", None) is None
    d = "På Tunge hundesenter har siden 1986 bygget opp vårt lille hundeparadis"
    assert self_description(d, "TUNGE HUNDESENTER AS", "Tunge Hundesenter AS") == d


def test_feed_skips_wordpress_pages():
    rss = b"""<?xml version="1.0"?><rss><channel>
    <item><title>Sjokkb\xc3\xb8lgebehandling</title><link>https://www.aktivklinikken.no/tjenester/behandlingsmetoder/sjokkbolgebehandling/</link>
      <pubDate>Sun, 08 Dec 2013 15:12:29 +0000</pubDate><guid isPermaLink="false">http://www.aktivklinikken.no/?page_id=887</guid></item>
    <item><title>Nytt tilbud</title><link>https://www.aktivklinikken.no/nytt-tilbud/</link>
      <pubDate>Mon, 02 Mar 2026 10:00:00 +0000</pubDate><guid isPermaLink="false">http://www.aktivklinikken.no/?p=1201</guid></item>
    </channel></rss>"""
    items = activity_from_feed(Response("f", "f", 200, rss, "application/rss+xml", ""), "aktivklinikken.no", TODAY)
    assert [i["title"] for i in items] == ["Nytt tilbud"]


def test_article_title_is_headline_not_site_logo():
    html = """<html><head><title>Key Arkitekter</title><meta property="og:site_name" content="KEY">
    <script type="application/ld+json">{"@type":"BlogPosting","headline":"KJØPMANNSGATA UNG KUNST","datePublished":"2021-09-25T10:00:00Z"}</script>
    </head><body><h1 class="logo">KEY</h1><h1 class="entry-title"></h1></body></html>"""
    it = activity_from_article(page("https://www.keyarkitekter.no/kjetils-blogg/2021/9/25/x", html), TODAY)
    assert it["title"] == "KJØPMANNSGATA UNG KUNST" and it["date"] == "2021-09-25"


def test_article_date_equal_to_fetch_day_is_render_time():
    html = """<html><head><title>Heinrich Blaufränkisch 2021</title>
    <script type="application/ld+json">{"@type":"BlogPosting","headline":"Heinrich Blaufränkisch 2021","datePublished":"2026-11-06 05:11:08"}</script>
    </head><body><h1>Heinrich Blaufränkisch 2021</h1></body></html>"""
    assert activity_from_article(page("https://www.oslowineschool.no/blog/x", html), TODAY) is None


def test_portal_tenant_page_is_not_the_company_site():
    class NoFetch:
        def remaining(self):
            return 100.0

        def get(self, *a, **k):
            raise AssertionError("portal page must not be fetched as a candidate")
    site = discover_website(NoFetch(), {"name": "GAMLEHAGEN BORETTSLAG", "website": "www.vibbo.no/gamlehagen"}, guess=False)
    assert site["status"] == "ambiguous" and "portal" in site["attempts"][0]["verdict"]["reasons"][0]

class OsloDateTests(unittest.TestCase):
    def test_feed_timestamps_use_the_norwegian_calendar_date(self):
        from norway_company_agent.footprint import iso_date
        self.assertEqual(iso_date("Mon, 28 Sep 2026 23:55:44 GMT"), "2026-09-29")  # nexusmaler.no shows "29. sep"
        self.assertEqual(iso_date("Sat, 07 Mar 2026 22:33:06 GMT"), "2026-03-07")
        self.assertEqual(iso_date("2026-07-22T22:21:00Z"), "2026-07-23")
        self.assertEqual(iso_date("2026-07-22T10:00:00+0200"), "2026-07-22")
        self.assertEqual(iso_date("2017-05-05T23:30:00"), "2017-05-05")  # no offset: local time as written
        self.assertEqual(iso_date("05.01.2026"), "2026-01-05")

class WordPressDemoTests(unittest.TestCase):
    def test_wp_rest_posts_with_lorem_ipsum_excerpt_are_dropped(self):
        import json as _json
        from norway_company_agent.footprint import activity_from_wp
        from norway_company_agent.net import Response
        posts = [{"date": "2021-03-23T19:42:58", "link": "https://eprod.no/2021/03/23/simplicity/", "title": {"rendered": "Simplicity is the ultimate sophistication."},
                  "excerpt": {"rendered": "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit</p>"}},
                 {"date": "2026-10-01T09:00:00", "link": "https://eprod.no/2026/10/01/ny-maskin/", "title": {"rendered": "Ny maskin i drift"},
                  "excerpt": {"rendered": "<p>Vi har tatt i bruk en ny maskin.</p>"}}]
        r = Response("https://eprod.no/wp-json/wp/v2/posts", "https://eprod.no/wp-json/wp/v2/posts", 200, _json.dumps(posts).encode(), "application/json", "2026-11-06T00:00:00Z")
        self.assertEqual([i["title"] for i in activity_from_wp(r, "eprod.no", "2026-11-06")], ["Ny maskin i drift"])

def test_m2_audit_fixes():
    from norway_company_agent.footprint import activity_from_wp, broader_brand_site
    import json
    # Lorem-ipsum WordPress REST posts are theme demo content (eprod.no)
    posts = [{"link": "https://eprod.no/2021/03/23/simplicity/", "date_gmt": "2021-03-23T10:00:00", "title": {"rendered": "Simplicity is the ultimate sophistication."},
              "excerpt": {"rendered": "<p>Lorem ipsum dolor sit amet, consectetur</p>"}},
             {"link": "https://eprod.no/2024/05/01/ny-lydrigg/", "date_gmt": "2024-05-01T10:00:00", "title": {"rendered": "Ny lydrigg"}, "excerpt": {"rendered": "<p>Vi har kjøpt</p>"}}]
    items = activity_from_wp(Response("u", "u", 200, json.dumps(posts).encode(), "application/json", ""), "eprod.no", TODAY)
    assert [i["title"] for i in items] == ["Ny lydrigg"]
    # feed attachments (kunstbanken.no programme PDF)
    rss = b"""<?xml version="1.0"?><rss><channel><item><title>Program Kunstfestivalen nr. 5</title>
      <link>https://kunstbanken.no/wp-content/uploads/2026/10/Kunstfestivalen-nr-5-program-1.pdf</link><pubDate>Tue, 13 Oct 2026 10:00:00 +0000</pubDate></item>
      <item><title>Program Kunstfestivalen nr. 5</title><link>https://kunstbanken.no/program-kunstfestivalen-nr-5-2/</link><pubDate>Wed, 21 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
    items = activity_from_feed(Response("f", "f", 200, rss, "application/rss+xml", ""), "kunstbanken.no", TODAY)
    assert [i["url"] for i in items] == ["https://kunstbanken.no/program-kunstfestivalen-nr-5-2/"]
    # URL-only meta description (elcam.no)
    assert self_description("https://www.elcamrtt.no/", "ELCAM RTT AS", None) is None
    # group brand site from a listed-domain prefix (ecura.no) vs a company's own site on a shorter domain (fonhus.no)
    def att(domain, title, site_name=""):
        html = f'<html><head><title>{title}</title><meta property="og:site_name" content="{site_name}"></head><body>x</body></html>'
        return {"domain": domain, "verdict": {"status": "verified", "method": "name_and_phone"}, "pages": [page(f"https://{domain}/", html)]}
    assert broader_brand_site(att("ecura.no", "Ecura | Ecura.no"), {"name": "ECURA CARE AS"})
    assert not broader_brand_site(att("fonhus.no", "Fønhus Maskin | Leverandør av maskiner og utstyr til trelastindustrien"), {"name": "FØNHUS MASKIN AS"})
    assert not broader_brand_site(att("skag3rak.no", "Skag3rak Arkitekter AS | Arkitekter i Kristiansand og Oslo", "Skag3rak Arkitekter"), {"name": "SKAG3RAK ARKITEKTER AS"})
    assert not broader_brand_site(att("os-teknikk.no", "O.S Teknikk AS – Rørleggermester", "O.S Teknikk AS"), {"name": "O.S TEKNIKK AS"})


def test_news_index_is_not_an_article():
    from norway_company_agent.footprint import is_section_index
    assert is_section_index("https://eprod.no/news/") and is_section_index("https://x.no/no/nyheter") and is_section_index("https://x.no/aktuelt/")
    assert not is_section_index("https://x.no/nyheter/ny-avtale-med-kommunen/") and not is_section_index("https://x.no/2024/05/01/nyhetsbrev-mai-2024-med-mer/")

class ContactLineDescriptionTests(unittest.TestCase):
    def test_contact_line_is_not_a_self_description(self):
        from norway_company_agent.footprint import self_description
        self.assertIsNone(self_description("FLYT, EN LIVSSTIL AS | Sagaveien 92 | 1555 SON |  | Epost: post@flytenlivsstil.no", "FLYT, EN LIVSSTIL AS", "FLYT"))
        d = "Fair Deal er innkjøpspartner for bedrifter. Vi bistår våre kunder med å skaffe varer."
        self.assertEqual(self_description(d, "FAIR-DEAL AS", "Fairdeal"), d)
