import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.footprint import Page, crawl_verified_site  # noqa: E402
from norway_company_agent.net import Response  # noqa: E402
from norway_company_agent.site_news import article_item, find_dates, listing_items, news_sections  # noqa: E402

TODAY = "2026-11-06"


def page(html: str, url: str = "https://firma.no/", kind: str = "homepage") -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z"), kind)


class FakeFetcher:
    """Serves fixed pages; everything else is a 404. No robots or sitemaps."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def remaining(self) -> float:
        return 100.0

    def get(self, url, **kw):
        self.requested.append(url)
        if url in self.pages:
            return Response(url, url, 200, self.pages[url].encode(), "text/html; charset=utf-8", "2026-11-06T00:00:00Z")
        return Response(url, url, 404, b"", "text/html", "2026-11-06T00:00:00Z", error="HTTP 404")

    def sitemaps_from_robots(self, base):
        return []


HOME = """<html><head><title>Firma AS</title></head><body>
<nav><a href="/">Hjem</a><a href="/tjenester">Tjenester</a><a href="/nyheter">Nyheter</a>
<a href="/nyhetsbrev">Meld deg på nyhetsbrev</a><a href="/kontakt">Kontakt</a></nav>
<main><h1>Velkommen</h1></main></body></html>"""

LISTING = """<html><head><title>Nyheter</title></head><body><nav><a href="/nyheter/ikke-en-post">Meny lenke</a></nav><main>
<div class="post"><a href="/fagstoff/ny-maskin-levert"><h3>Ny maskin levert til Bergen</h3></a><span>4/6/26</span></div>
<div class="post"><h3>Vi feirer 20 år</h3><p>12. mars 2025</p><a href="/fagstoff/vi-feirer-20-ar">Les mer</a></div>
<div class="post"><a href="/fagstoff/uten-dato">En sak uten dato i listen</a></div>
<a href="/fagstoff/category/maskiner">Kategori maskiner</a>
</main></body></html>"""

ARTICLE = """<html><head><title>En sak uten dato | Firma</title></head><body><main><article>
<h1>En sak uten dato i listen</h1><p class="entry-meta">Publisert 02.10.2026 av Kari</p><p>Tekst.</p>
</article></main></body></html>"""


class DateTests(unittest.TestCase):
    def test_day_first_and_month_names(self):
        self.assertEqual(find_dates("4/6/26")[0][0], "2026-06-04")
        self.assertEqual(find_dates("04.06.2026")[0][0], "2026-06-04")
        self.assertEqual(find_dates("4. juni 2026")[0][0], "2026-06-04")
        self.assertEqual(find_dates("3 des. 2024")[0][0], "2024-12-03")
        self.assertEqual(find_dates("June 4, 2026")[0][0], "2026-06-04")
        self.assertEqual(find_dates("12/25/2025")[0][0], "2025-12-25")  # second part > 12: month-first

    def test_non_dates_ignored(self):
        for text in ("tlf 22.33.44.55", "kl 12.30", "1.000.000", "v1.2.3", "31.02.2026", "mai 2024", "Org.nr 912 345 678"):
            self.assertEqual(find_dates(text), [], text)


class SectionTests(unittest.TestCase):
    def test_anchor_text_finds_section_and_skips_newsletter(self):
        home = page('<a href="/hva-skjer">Aktuelt</a><a href="/nyhetsbrev">Nyhetsbrev</a><a href="/om">Om oss</a>')
        self.assertEqual(news_sections([home], "firma.no"), ["https://firma.no/hva-skjer"])

    def test_fragment_anchor_is_not_a_section(self):
        home = page('<a href="/om-skolen#aktuelt">Aktuelt</a><a href="/om-skolen/aktuelt/elevkontrakt">Elevkontrakt</a>')
        self.assertEqual(news_sections([home], "firma.no"), [])

    def test_multiword_segments(self):
        from norway_company_agent.site_news import is_post_url
        self.assertEqual(news_sections([page('<a href="/siste-nytt/">Siste</a>')], "firma.no"), ["https://firma.no/siste-nytt/"])
        self.assertTrue(is_post_url("https://firma.no/siste-nytt/det-koster-a-ansette-feil/", "firma.no", []))
        self.assertTrue(is_post_url("https://firma.no/uutiset/-/news/210177743/", "firma.no", []))
        self.assertFalse(is_post_url("https://firma.no/nyheter/2024", "firma.no", []))

    def test_path_segment_without_text(self):
        home = page('<a href="/blogg/"><img src="x.png"></a>')
        self.assertEqual(news_sections([home], "firma.no"), ["https://firma.no/blogg/"])


class ListingTests(unittest.TestCase):
    def test_one_post_one_date_per_container(self):
        p = page(LISTING, "https://firma.no/nyheter", "news_index")
        items, undated = listing_items(p, "firma.no", ["/nyheter"], TODAY)
        got = {i["url"]: (i["date"], i["title"]) for i in items}
        self.assertEqual(got["https://firma.no/fagstoff/ny-maskin-levert"], ("2026-06-04", "Ny maskin levert til Bergen"))
        self.assertEqual(got["https://firma.no/fagstoff/vi-feirer-20-ar"], ("2025-03-12", "Vi feirer 20 år"))  # generic link text -> heading
        self.assertEqual(undated, ["https://firma.no/fagstoff/uten-dato"])
        self.assertNotIn("https://firma.no/nyheter/ikke-en-post", got)  # nav link
        self.assertFalse(any("category" in u for u in got))

    def test_container_with_two_posts_gives_no_date(self):
        html = '<main><div><a href="/blogg/post-en">Første innlegg her</a><a href="/blogg/post-to">Andre innlegg her</a><span>01.02.2026</span></div></main>'
        items, undated = listing_items(page(html, "https://firma.no/blogg", "news_index"), "firma.no", ["/blogg"], TODAY)
        self.assertEqual(items, [])
        self.assertEqual(len(undated), 2)

    def test_title_drops_date_read_time_and_read_more(self):
        html = ('<main><div><a href="/fagstoff/pigge-fjell">Guide 4/6/26 · 8 min lesetid Pigging av fjell: metode og maskinvalg</a></div>'
                '<div><a href="/fagstoff/kunde">Read more 26. mai 2026 · Slik måler du kundetilfredshet</a></div></main>')
        items, _ = listing_items(page(html, "https://firma.no/fagstoff", "news_index"), "firma.no", ["/fagstoff"], TODAY)
        titles = sorted(i["title"] for i in items)
        self.assertEqual(titles, ["Guide Pigging av fjell: metode og maskinvalg", "Slik måler du kundetilfredshet"])

    def test_future_dates_dropped(self):
        html = '<main><div><a href="/aktuelt/kurs-neste-ar">Kurs neste år for alle</a><span>01.02.2027</span></div></main>'
        items, _ = listing_items(page(html, "https://firma.no/aktuelt", "news_index"), "firma.no", ["/aktuelt"], TODAY)
        self.assertEqual(items, [])


class ArticleTests(unittest.TestCase):
    def test_visible_date_on_post(self):
        it = article_item(page(ARTICLE, "https://firma.no/fagstoff/uten-dato", "article"), TODAY, post_like_url=True)
        self.assertEqual((it["date"], it["title"]), ("2026-10-02", "En sak uten dato i listen"))

    def test_non_post_page_never_dated(self):
        html = "<main><h1>Om oss</h1><p>Oppdatert 02.10.2026</p></main>"
        self.assertIsNone(article_item(page(html, "https://firma.no/om-oss", "article"), TODAY, post_like_url=False))

    def test_jsonld_article_without_news_path(self):
        html = ('<script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2025-05-01T08:00:00+02:00"}</script>'
                "<h1>Sommeråpent hele juli</h1>")
        it = article_item(page(html, "https://firma.no/sommerapent", "article"), TODAY, post_like_url=False)
        self.assertEqual((it["date"], it["method"]), ("2025-05-01", "jsonld_article"))

    def test_render_time_stamp_is_not_a_publish_date(self):
        # Shopify-style blogs stamp datePublished with the time of our own request
        html = ('<script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2026-11-06 01:11:18"}</script>'
                "<h1>Black Week</h1>")
        self.assertIsNone(article_item(page(html, "https://firma.no/blog/black-week", "article"), TODAY, post_like_url=True))

    def test_webpage_date_only_under_news_path(self):
        html = '<script type="application/ld+json">{"@type":"WebPage","datePublished":"2024-10-01T07:12:53+00:00"}</script><h1>Field Results</h1>'
        self.assertIsNone(article_item(page(html, "https://firma.no/field-results/", "article"), TODAY, post_like_url=True))
        it = article_item(page(html, "https://firma.no/nyheter/field-results/", "article"), TODAY, post_like_url=True, news_path=True)
        self.assertEqual(it["date"], "2024-10-01")

    def test_labelled_dates_and_basis(self):
        html = ("<main><h1>Elevundersøkelsen 2026</h1><p>Tekst om undersøkelsen.</p><p>SIST OPPDATERT 26. oktober 2026</p>"
                "<p>LES OGSÅ Velkommen til nytt skoleår! 21. august 2026</p></main>")
        it = article_item(page(html, "https://firma.no/om-skolen/aktuelt/elevundersokelsen-2026", "article"), TODAY, post_like_url=True)
        self.assertIsNone(it)  # main2: a "last updated" stamp alone is not a publication date
        html = "<main><h1>Ny daglig leder</h1><p>Sist endret 3.11.2026</p><p>Publisert 01.10.2026 av Ola</p></main>"
        it = article_item(page(html, "https://firma.no/nyheter/ny-leder", "article"), TODAY, post_like_url=True)
        self.assertEqual((it["date"], it["date_basis"]), ("2026-10-01", "published"))

    def test_ctime_date(self):
        self.assertEqual(find_dates("Muutto Thu Jul 30 10:00:00 EEST 2020"), [("2020-07-30", "Thu Jul 30 10:00:00 EEST 2020")])

    def test_breadcrumb_marks_flat_post(self):
        from norway_company_agent.site_news import breadcrumb_in_section
        html = ('<div class="breadcrumbs"><a href="/">Forside</a> › <a href="/fagstoff">Fagstoff</a> › Alle var enige</div>'
                "<main><h1>Alle var enige</h1></main>")
        self.assertTrue(breadcrumb_in_section(page(html, "https://firma.no/gevinstlekkasje", "article"), ["/fagstoff"]))
        self.assertFalse(breadcrumb_in_section(page(html, "https://firma.no/gevinstlekkasje", "article"), ["/nyheter"]))

    def test_undated_post_gives_nothing(self):
        html = "<main><article><h1>Ny nettside</h1><p>Velkommen.</p></article></main>"
        self.assertIsNone(article_item(page(html, "https://firma.no/nyheter/ny-nettside", "article"), TODAY, post_like_url=True))


class HookTests(unittest.TestCase):
    def test_flag_off_keeps_old_behaviour_flag_on_adds_items(self):
        pages = {"https://firma.no/nyheter": LISTING, "https://firma.no/fagstoff/uten-dato": ARTICLE}
        home = page(HOME)
        on = crawl_verified_site(FakeFetcher(pages), {"name": "FIRMA AS", "_site_news": True}, {"pages": [home], "domain": "firma.no"}, today=TODAY)
        dates = {a["url"]: a["date"] for a in on["activity"]}
        self.assertEqual(dates, {"https://firma.no/fagstoff/ny-maskin-levert": "2026-06-04", "https://firma.no/fagstoff/vi-feirer-20-ar": "2025-03-12",
                                 "https://firma.no/fagstoff/uten-dato": "2026-10-02"})
        self.assertTrue(all(a.get("span") and a.get("sha256") and a.get("source_url") for a in on["activity"]))
        off = crawl_verified_site(FakeFetcher(pages), {"name": "FIRMA AS"}, {"pages": [page(HOME)], "domain": "firma.no"}, today=TODAY)
        self.assertEqual(off["activity"], [])  # old path: news index found but children are under /fagstoff, not /nyheter

    def test_group_brand_site_publishes_no_posts(self):
        from norway_company_agent.site_news import group_brand_siblings, site_news_profile
        group = {"children": [{"navn": "NORCONSULT ASA", "organisasjonsnummer": "1", "children": [
            {"navn": "NORCONSULT NORGE AS", "organisasjonsnummer": "962392687"}, {"navn": "NORCONSULT DIGITAL AS", "organisasjonsnummer": "2"},
            {"navn": "NORCONSULT DANMARK A/S", "organisasjonsnummer": "3"}, {"navn": "NORCONSULT EIENDOM AS", "organisasjonsnummer": "4"}]}]}
        self.assertEqual(group_brand_siblings("NORCONSULT NORGE AS", "962392687", group),
                         ["NORCONSULT ASA", "NORCONSULT DANMARK A/S", "NORCONSULT DIGITAL AS"])  # property company not counted
        self.assertEqual(group_brand_siblings("EAASNES AS", "5", {"navn": "EAASNES HOLDING AS", "organisasjonsnummer": "6"}), [])
        prof = site_news_profile({"name": "NORCONSULT NORGE AS", "organisation_number": "962392687"}, {"group": {"status": "available", "value": group}})
        pages = {"https://firma.no/nyheter": LISTING, "https://firma.no/fagstoff/uten-dato": ARTICLE}
        fetcher = FakeFetcher(pages)
        out = crawl_verified_site(fetcher, prof, {"pages": [page(HOME)], "domain": "firma.no"}, today=TODAY)
        self.assertEqual(out["activity"], [])
        self.assertTrue(any(l.get("url") == "site_news:group_brand_site" for l in out["crawl_log"]))

    def test_demo_posts_filtered(self):
        listing = '<main><div><a href="/blogg/hello-world">Hello world!</a><time datetime="2024-01-01">1. jan 2024</time></div></main>'
        home = page('<nav><a href="/blogg">Blogg</a></nav>')
        on = crawl_verified_site(FakeFetcher({"https://firma.no/blogg": listing}), {"_site_news": True}, {"pages": [home], "domain": "firma.no"}, today=TODAY)
        self.assertEqual(on["activity"], [])


if __name__ == "__main__":
    unittest.main()
