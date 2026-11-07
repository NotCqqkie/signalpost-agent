"""Regression tests from the lane 10b audit of 1,000 never-seen companies (run lane10b-fresh-596e73f on m2b 596e73f).
Each case is a real published error (or a real correct claim that must survive). Labels:
eval/labels/lane10b-fresh-audit/labels-fresh.jsonl."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.footprint import (PRODUCT_FEED, Page, find_orgnrs, group_named_site, in_scope, is_demo_item,  # noqa: E402
                                             is_section_index, public_brand, self_description, site_scope, social_from_pages,
                                             try_candidate, verify_site)
from norway_company_agent.net import Response  # noqa: E402
from norway_company_agent.site_news import find_dates  # noqa: E402


def page(url: str, html: str, kind: str = "homepage") -> Page:
    return Page(Response(url, url, 200, html.encode(), "text/html", "2026-11-06T07:00:00Z"), kind)


class Fetcher:
    def __init__(self, pages: dict[str, str], redirects: dict[str, str] | None = None):
        self.pages, self.redirects = pages, redirects or {}

    def remaining(self) -> float:
        return 100.0

    def sitemaps_from_robots(self, base):
        return []

    def get(self, url, **kw):
        final = self.redirects.get(url, url)
        if final in self.pages:
            return Response(url, final, 200, self.pages[final].encode(), "text/html; charset=utf-8", "2026-11-06T07:00:00Z", redirect_chain=[url, final])
        return Response(url, final, 404, b"", "text/html", "2026-11-06T07:00:00Z", error="HTTP 404")


# ------------------------------------------------------------------ website identity (wrong-company websites)
def test_vat_form_number_is_an_owner_number():
    # brettel.no: footer prints only the sister's VAT number; BRETTEL AS (916249918) shares address and e-mail
    found, ctx = find_orgnrs("Brettel | Økonomi AS atle@brettel.no NO958234864MVA - Harevegen 39A, 7021 Trondheim")
    assert "958234864" in ctx
    assert "958234864" in find_orgnrs("Org: NO 958 234 864 MVA")[1]
    prof = {"organisation_number": "916249918", "name": "BRETTEL AS", "business_address": {"adresse": ["Harevegen 39A"], "postnummer": "7021"}}
    html = ("<html><head><title>Brettel AS</title></head><body><footer>Brettel AS · Brettel | Økonomi AS atle@brettel.no "
            "NO958234864MVA - Harevegen 39A, 7021 Trondheim</footer></body></html>")
    v = verify_site(prof, [page("https://www.brettel.no/", html)], "name_guess")
    assert v["status"] == "ambiguous" and v["other_orgnrs"] == ["958234864"]


def test_our_number_only_in_a_secondary_role_on_the_privacy_page():
    # nordstugard.com: site owner Nordstu AS (925412139); NORDSTU GÅRD AS (925355542) only for farm-shop purchases
    prof = {"organisation_number": "925355542", "name": "NORDSTU GÅRD AS"}
    home = page("https://nordstugard.com/", "<html><head><title>Nordstu Gård, Koppang</title></head><body>Overnatting, festsal og gårdsbutikk</body></html>")
    priv = page("https://nordstugard.com/personvern.html",
                "<html><body>Gjelder nordstugard.com og all kontakt med gården Nordstu AS · org.nr. 925 412 139. Kjøper du varer i "
                "gårdsbutikken er Nordstu Gård AS, org.nr. 925 355 542, behandlingsansvarlig for det kjøpet.</body></html>", "identity")
    v = verify_site(prof, [home, priv], "name_guess")
    assert v["status"] == "ambiguous" and v["other_orgnrs"] == ["925412139"]
    # a company whose own number comes first on its privacy page stays verified
    priv2 = page("https://firma.no/personvern", "<html><body>Behandlingsansvarlig er Firma AS, org.nr. 925 355 542. Databehandler: "
                                                "Webbyrå AS, org.nr. 925 412 139.</body></html>", "identity")
    assert verify_site(prof, [home, priv2], "name_guess")["method"] == "orgnr_on_site"
    # a shared site listing several companies as equals keeps working (framokonomi.no: Oslo, Vest, Trondheim companies)
    fram = {"organisation_number": "931467662", "name": "FRAM ØKONOMI AS"}
    fram_home = page("https://www.framokonomi.no/", "<html><head><title>Fram Økonomi</title></head><body>Regnskap, økonomi og rådgivning i Oslo, Stavanger og Trondheim</body></html>")
    fram_priv = page("https://www.framokonomi.no/personvern", "<html><body>Nettsiden representerer ulike selskaper under Fram Økonomi: Fram Økonomi Oslo AS "
                     "(Org nr: 931 740 296) F&L Advisory AS (Org nr: 934 266 072) Fram Økonomi Vest AS (Org nr: 934 531 191) Trondheim: "
                     "Fram Økonomi AS (Org nr: 931 467 662)</body></html>", "identity")
    assert verify_site(fram, [fram_home, fram_priv], "name_guess")["method"] == "orgnr_on_site"
    # and the number on the homepage itself is always enough
    home2 = page("https://firma.no/", "<html><body><footer>Nordstu Gård AS org.nr. 925 355 542</footer></body></html>")
    assert verify_site(prof, [home2, priv], "name_guess")["method"] == "orgnr_on_site"


def test_group_named_site_is_not_the_subsidiarys_own():
    # kronstein.no via the registry e-mail domain of KRONSTEIN ENTREPRENØR AS: the site is "Kronsteingruppen"
    def att(title, site_name="", method="registry_listed_name", domain="kronstein.no"):
        html = f'<html><head><title>{title}</title><meta property="og:site_name" content="{site_name}"></head><body>x</body></html>'
        return {"domain": domain, "verdict": {"status": "verified", "method": method}, "pages": [page(f"https://{domain}/", html)]}
    assert group_named_site(att("Kronsteingruppen", "Kronsteingruppen"), {"name": "KRONSTEIN ENTREPRENØR AS"})
    assert group_named_site(att("Hjem - Bartec Group", "", domain="bartec.com"), {"name": "BARTEC AS"})
    assert not group_named_site(att("Kronsteingruppen", "Kronsteingruppen", method="orgnr_on_site"), {"name": "KRONSTEIN ENTREPRENØR AS"})
    assert not group_named_site(att("Bilgruppen AS", "Bilgruppen"), {"name": "BILGRUPPEN AS"})  # the company is the group
    assert not group_named_site(att("Skancke AS - Hjem"), {"name": "SKANCKE AS"})


def test_declared_homepage_forwarding_to_a_group_domain_needs_strong_proof():
    # BARTEC AS registers www.bartec-technor.no, which 301s to bartec.com (global group site, title "Home - Bartec")
    prof = {"organisation_number": "980932834", "name": "BARTEC AS", "business_address": {"adresse": ["Vestre Svanholmen 24"], "postnummer": "4313"}}
    f = Fetcher({"https://bartec.com/": "<html><head><title>Home - Bartec</title></head><body>Explosion protection worldwide</body></html>"},
                {"https://www.bartec-technor.no/": "https://bartec.com/"})
    assert try_candidate(f, prof, "https://www.bartec-technor.no/", "registry")["verdict"]["status"] == "ambiguous"
    # the same redirect with the company's own number on the target is kept
    f2 = Fetcher({"https://bartec.com/no/": "<html><head><title>Bartec</title></head><body>Explosion protection for Norway offshore and onshore. <footer>BARTEC AS org.nr. 980 932 834</footer></body></html>"},
                 {"https://www.bartec-technor.no/": "https://bartec.com/no/"})
    assert try_candidate(f2, prof, "https://www.bartec-technor.no/", "registry")["verdict"]["method"] == "orgnr_on_site"
    # forwards chosen by the company itself are kept: a country section of the same brand, a longer or a new name
    # (scale pair: hydac.no -> hydac.com/no-no/, ambio.no -> ambio-water.no, arctic-spas.no -> polarbad.no)
    hy = {"organisation_number": "982956579", "name": "HYDAC AS"}
    f4 = Fetcher({"https://www.hydac.com/no-no/": "<html><head><title>HYDAC AS - Hydraulikk</title></head><body>HYDAC AS leverer hydraulikk og filtrering i Norge.</body></html>"},
                 {"https://www.hydac.no/": "https://www.hydac.com/no-no/"})
    assert try_candidate(f4, hy, "https://www.hydac.no/", "registry")["verdict"]["status"] == "verified"
    am = {"organisation_number": "912345602", "name": "AMBIO AS"}
    f5 = Fetcher({"https://www.ambio-water.no/": "<html><head><title>Ambio AS</title></head><body>Ambio AS leverer vannbehandling til industri og kommuner.</body></html>"},
                 {"https://www.ambio.no/": "https://www.ambio-water.no/"})
    assert try_candidate(f5, am, "https://www.ambio.no/", "registry")["verdict"]["status"] == "verified"
    # a declared homepage on its own domain is unaffected
    f3 = Fetcher({"https://www.bartec-technor.no/": "<html><head><title>Bartec AS</title></head><body>Bartec AS leverer eksplosjonssikkert utstyr fra Sandnes.</body></html>"})
    assert try_candidate(f3, prof, "https://www.bartec-technor.no/", "registry")["verdict"]["status"] == "verified"


# ------------------------------------------------------------------ social profiles
def _social(url: str, links: list[str], name: str, domain: str, scope: str = "", site_name: str = "") -> list[str]:
    html = f'<html><head><meta property="og:site_name" content="{site_name}"></head><body><footer>' + "".join(f'<a href="{u}">x</a>' for u in links) + "</footer></body></html>"
    return [r["url"] for r in social_from_pages([page(url, html)], {"name": name, "_social_plus": True}, domain, scope)]


def test_group_and_foreign_sister_handles_are_dropped():
    got = _social("https://www.rollco.no/", ["https://www.facebook.com/rollconorge", "https://www.instagram.com/rollco_ab",
                                             "https://www.youtube.com/@Rollco-international", "https://www.linkedin.com/company/rollconorge"],
                  "ROLLCO NORGE AS", "rollco.no")
    assert got == ["https://facebook.com/rollconorge", "https://linkedin.com/company/rollconorge"]
    assert _social("https://www.monkberry.no/", ["https://www.linkedin.com/company/monkberry-ab"], "MONKBERRY AS", "monkberry.no") == []
    got = _social("https://bartec.com/", ["https://www.facebook.com/bartecgroup1975", "https://www.youtube.com/@BARTECglobal"], "BARTEC AS", "bartec.com")
    assert got == []
    # a company whose legal name carries the word keeps its handle
    assert _social("https://www.ab-bygg.no/", ["https://www.facebook.com/abbygg"], "AB BYGG AS", "ab-bygg.no") == ["https://facebook.com/abbygg"]


def test_section_site_keeps_only_handles_with_the_legal_name():
    scope = site_scope("https://www.hydac.com/no-no/")
    assert scope == "/no-no/"
    got = _social("https://www.hydac.com/no-no/", ["https://www.facebook.com/HYDAC-As-NorgeNorway-101817689007240", "https://www.facebook.com/HydacGroup",
                                                   "https://www.instagram.com/hydacgroup", "https://www.linkedin.com/company/hydac-as"],
                  "HYDAC AS", "hydac.com", scope)
    assert got == ["https://facebook.com/HYDAC-As-NorgeNorway-101817689007240", "https://linkedin.com/company/hydac-as"]
    hotel = "https://www.strawberry.no/hotell/norge/oslo-airport/clarion-hotel-oslo-airport/"
    got = _social(hotel, ["https://www.facebook.com/strawberryhotels", "https://www.instagram.com/strawberry", "https://www.youtube.com/@strawberryuniverse"],
                  "CLARION HOTEL OSLO AIRPORT AS", "strawberry.no", site_scope(hotel), "Strawberry")
    assert got == []
    got = _social("https://www.howdengroup.com/no-en", ["https://www.instagram.com/howdeninsurance", "https://www.linkedin.com/company/howden-insurance"],
                  "HOWDEN NORWAY AS", "howdengroup.com", site_scope("https://www.howdengroup.com/no-en"))
    assert got == []
    # the same links on a root-domain site still match the domain label (unchanged behaviour)
    assert _social("https://strawberry.no/", ["https://www.instagram.com/strawberry"], "STRAWBERRY AS", "strawberry.no") == ["https://instagram.com/strawberry"]


def test_scope_helpers():
    assert site_scope("https://www.firma.no/") == "" and site_scope("https://www.firma.no/index.html") == ""
    assert in_scope("https://firstvet.com/no/artikler/feber", "/no/") and not in_scope("https://www.hydac.com/en/hydac-press-control/", "/no-no/")
    assert in_scope("https://www.hydac.com/no-no", "/no-no/")


# ------------------------------------------------------------------ site news
def test_evergreen_pages_index_pages_and_builder_defaults_are_not_posts():
    for title, url in [("Om oss", "https://gravferdsbyraene.no/2024/11/29/om-oss/"), ("Personvernerklæring", "https://krageroenergi.no/personvernerklaering/"),
                       ("Elementor #4080", "https://www.ibizaboats.no/elementor-4080/")]:
        assert is_demo_item({"title": title, "url": url}), title
    assert is_section_index("https://www.rakkfallan.no/blog")
    assert not is_demo_item({"title": "Om oss i media: ny avtale", "url": "https://x.no/2025/01/02/ny-avtale/"})
    assert PRODUCT_FEED.search("/WebPages/produkt/rss.aspx") and not PRODUCT_FEED.search("/feed/") and not PRODUCT_FEED.search("/blog-feed.xml")


def test_space_less_month_date_is_parsed_before_teaser_dates():
    # taelektronikk.no listing: "04.november.2026" is the post date; "3. august 2026" is inside the teaser text
    hits = find_dates("04.november.2026 Brady kjøper Honeywell's printer- og skannervirksomhet. Avtalen ble inngått 3. august 2026.")
    assert hits[0][0] == "2026-11-04"
    assert find_dates("23.januar.2023 Besøk oss på Holdbar/Umami i Lillestrøm 14. –16. februar 2023.")[0][0] == "2023-01-23"
    assert find_dates("Publisert 4. november 2026")[0][0] == "2026-11-04"


# ------------------------------------------------------------------ brand and self-description
def test_brand_fixes():
    assert public_brand("Askem AS -", "", "ASKEM AS", "askem.no") == "Askem AS"
    assert public_brand("", "Vinum Merum AS: Premium Wine Importers in Vennesla", "VINUM MERUM AS", "vinum-merum.no") == "Vinum Merum AS"
    assert public_brand("", "Medisinsk Senter, Bedriftshelse- og HMS-tjenester", "MEDISINSK SENTER AS", "medisinsk-senter.no") == "Medisinsk Senter"
    assert public_brand("Nordic Services is under maintenance", "Nordic Services is under maintenance", "NORDIC SERVICES AS", "nordic-services.no") is None
    assert public_brand("Stiftelsen", "Forside - Stiftelsen", "STIFTELSEN NORSK MAT", "stiftelsennorskmat.no") is None
    assert public_brand("FLYT", "FLYT", "FLYT, EN LIVSSTIL AS", "flytenlivsstil.no") == "FLYT"  # scale pair: correct short brand kept
    # unchanged: names that are the domain label
    assert public_brand("Voll Motor", "", "VOLL MOTOR AS", "vollmotor.no") == "Voll Motor"
    assert public_brand("", "Ecura | Ecura.no", "ECURA CARE AS", "ecura.no") == "Ecura"


def test_self_description_placeholders():
    for d in ["Web site created using create-react-app", "Page d'accueil", "We are doing some work on our site. Please come back later. We'll be up and running in no time.",
              "AddressVilbergvegen 71, 2066 Gardermoen"]:
        assert self_description(d, "X AS", None) is None, d
    d = "Norja Seafood leverer sjømat fra Gardermoen til restauranter i hele landet"
    assert self_description(d, "NORJA SEAFOOD AS", "Norja Seafood") == d


def test_redirect_into_a_chain_section_scopes_the_crawl():
    # clarionosloairport.no forwards to the hotel's page on strawberry.no (orgnr printed there): the chain-wide
    # Strawberry profiles in the page chrome are not the hotel company's
    from norway_company_agent.footprint import crawl_verified_site
    hotel = "https://www.strawberry.no/hotell/norge/oslo-airport/clarion-hotel-oslo-airport/"
    html = ('<html><head><title>Clarion Hotel Oslo Airport</title><meta property="og:site_name" content="Strawberry"></head><body>'
            '<footer><a href="https://www.facebook.com/strawberryhotels">f</a><a href="https://www.instagram.com/strawberry">i</a>'
            'Clarion Hotel Oslo Airport AS org.nr. 981 048 911</footer></body></html>')
    attempt = {"url": "https://clarionosloairport.no/", "domain": "strawberry.no", "pages": [page(hotel, html)],
               "verdict": {"status": "verified", "method": "orgnr_on_site"}}
    crawl = crawl_verified_site(Fetcher({}), {"organisation_number": "981048911", "name": "CLARION HOTEL OSLO AIRPORT AS", "_social_plus": True},
                                attempt, today="2026-11-06")
    assert crawl["social_profiles"] == []
    # the same page reached at the chain's own root would be the chain itself (unchanged)
    attempt_root = {**attempt, "url": "https://www.strawberry.no/", "pages": [page("https://www.strawberry.no/", html)]}
    crawl = crawl_verified_site(Fetcher({}), {"organisation_number": "981048911", "name": "STRAWBERRY AS", "_social_plus": True},
                                attempt_root, today="2026-11-06")
    assert [s["url"] for s in crawl["social_profiles"]] == ["https://facebook.com/strawberryhotels", "https://instagram.com/strawberry"]


def test_listing_card_date_inside_the_teaser_is_not_the_post_date():
    from norway_company_agent.site_news import listing_items
    teaser = ("Brady Corporation har fullført oppkjøpet av Honeywells Productivity Solutions and Services (PSS). Oppkjøpet ble "
              "gjennomført 3. august 2026 og omfatter blant annet Honeywells løsninger innen etikettskrivere, strekkodelesere og RFID. Les mer her.")
    html = ('<html><body><main><div class="card"><h3><a href="/aktuelle-saker/brady-kjoper-honeywell">Brady kjøper Honeywell\'s printer- og '
            f'skannervirksomhet</a></h3><p>{teaser}</p></div>'
            '<div class="card"><span>04.mars.2026</span><h3><a href="/aktuelle-saker/gold-partner-hos-realwear">Gold Partner hos RealWear</a></h3>'
            f'<p>{teaser}</p></div></main></body></html>')
    items, undated = listing_items(page("https://www.taelektronikk.no/forside", html), "taelektronikk.no", ["/aktuelle-saker"], "2026-11-06")
    assert [(i["title"], i["date"]) for i in items] == [("Gold Partner hos RealWear", "2026-03-04")]
    assert undated == ["https://www.taelektronikk.no/aktuelle-saker/brady-kjoper-honeywell"]


def test_listing_card_date_right_after_a_long_title_is_kept():
    # drbogesvang.com/informasjon/: "<title> 4. mai 2023 <teaser>" (scale pair: must stay dated)
    from norway_company_agent.site_news import listing_items
    teaser = "Kjære pasienter, Dersom det sendes henvendelser til oss via HelseRespons, hvor det kreves helsefaglig vurdering i besvarelsen regnes dette som e-konsultasjon og det utløser takst."
    html = ('<html><body><main><div class="sak"><a href="/informasjon/sak21642/">Henvendelser via HelseRespons kan utløse takst for e-konsultasjon</a>'
            f' <span>4. mai 2023</span> <p>{teaser}</p></div></main></body></html>')
    items, _ = listing_items(page("https://drbogesvang.com/informasjon/", html), "drbogesvang.com", ["/informasjon"], "2026-11-06")
    assert [(i["date"]) for i in items] == ["2023-05-04"]
