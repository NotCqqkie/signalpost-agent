"""Company-owned web footprint: website discovery, exact-entity verification and a bounded crawl.

Publication rule: a website is published only when the fetched site proves the exact legal entity:
  * `orgnr_on_site`   - the entity's own organisation number (mod-11 valid) appears on a fetched page, or
  * `registry_listed_name` - the site is the registry-declared homepage and the full legal name appears in the
                        site's identity text (title, site name, structured data, footer/contact text), and no
                        other organisation number is printed as the site owner, or
  * `name_and_address` - a guessed/secondary candidate whose identity text carries the full legal name and the
                        registered street address with postcode, and no other organisation number.
Everything else is `ambiguous` (candidate kept, not published) or `not_available`.
Profiles, jobs and dated activity are only read from a verified site.
"""
from __future__ import annotations

import functools
import json
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .net import Fetcher, Response
from .website import normalize_social_url

LEGAL_FORMS = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "ks", "iks", "sf", "ba", "bbl", "brl", "stiftelsen", "stiftelse",
               "borettslag", "sameiet", "sameie", "the", "og", "and", "i", "av", "for", "ltd", "limited", "ab", "aps", "gmbh"}
WEAK_TOKENS = {"holding", "invest", "investment", "eiendom", "eiendommer", "consulting", "consult", "service", "services",
               "drift", "norge", "norway", "nordic", "group", "gruppen", "kapital", "capital", "management", "utvikling"}
PARKED = ("domain is for sale", "domain for sale", "domenet er til salgs", "dette domenet", "hugedomains", "parked at",
          "parkert", "under construction", "kommer snart", "coming soon", "this domain", "web hosting", "webhotell",
          "default web site page", "it works!", "welcome to nginx", "her flytter snart", "domene er registrert",
          "domeneshop", "one.com", "hostinger", "plesk",
          # main2 fresh-run audit: server default pages and directory listings (revenka.no)
          "index of /", "proudly served by litespeed", "apache2 ubuntu default", "apache http server test page")
IDENTITY_HINTS = ("kontakt", "contact", "om-oss", "om_oss", "omoss", "about", "personvern", "privacy", "impressum", "vilkar", "terms")
CAREER_HINTS = ("karriere", "career", "jobb", "jobs", "stilling", "ledige", "vacanc", "rekrutter", "join-us", "work-with-us")
NEWS_HINTS = ("nyhet", "news", "aktuelt", "presse", "press", "blogg", "blog", "artikl", "article", "innlegg", "arkiv", "media")
ATS_HOSTS = ("webcruiter.", "jobylon.", "teamtailor.", "recman.", "easycruit.", "hrmanager.", "reachmee.", "jobbnorge.",
             "varbi.", "workday", "smartrecruiters.", "lever.co", "greenhouse.io", "talentech", "cvpartner", "hr-manager.")
FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "online.no", "outlook.com", "outlook.no", "yahoo.com", "yahoo.no", "live.no",
            "live.com", "icloud.com", "me.com", "msn.com", "frisurf.no", "broadpark.no", "start.no", "getmail.no", "altibox.no",
            "lyse.net", "c2i.net", "tele2.no", "telenor.no", "ebnett.no", "haugnett.no", "enivest.net", "kvamnet.no", "tussa.com",
            "mail.com", "protonmail.com", "proton.me", "gmx.com", "gmx.net", "aol.com", "netcom.no", "chello.no", "bluezone.no",
            "hotmail.co.uk", "googlemail.com", "vikenfiber.no", "nktv.no", "tdcnet.no", "ifi.uio.no", "skynet.no", "pc.nu"}
PLATFORM_HANDLES = {"editorx", "wix", "wixcom", "squarespace", "wordpress", "wordpressdotcom", "shopify", "webflow", "mailchimp",
                    "webnode", "jimdo", "godaddy", "hubspot", "teamtailor", "facebook", "instagram", "linkedin", "youtube", "x", "twitter"}
ORG_TYPES = {"organization", "localbusiness", "corporation", "store", "onlinestore", "professionalservice", "homeandconstructionbusiness",
             "automotivebusiness", "foodestablishment", "restaurant", "lodgingbusiness", "medicalbusiness", "educationalorganization", "ngo"}
FB_NUMERIC = re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/profile\.php\?id=(\d{6,20})(?:&|$)", re.I)
# E18: hosts a guessed domain may forward to that are never a company's own site (platforms, registrars, parking)
REDIRECT_DENY = {"facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "google.com", "webhuset.no",
                 "domenebors.no", "domeneshop.no", "one.com", "sedo.com", "dan.com", "afternic.com", "godaddy.com", "hugedomains.com",
                 "wix.com", "wixsite.com", "squarespace.com", "shopify.com", "finn.no", "proff.no", "gulesider.no", "1881.no"}
REDIRECT_PROOFS = ("orgnr_on_site", "name_and_address", "name_and_phone")
# candidate sources that an official/explicit record ties to this organisation number (still need on-site proof)
# Multi-tenant portals where a housing co-op/small firm gets a sub-page (lane 10 audit: vibbo.no/gamlehagen, OBOS's
# Vibbo portal, was published as GAMLEHAGEN BORETTSLAG's website with brand "Vibbo - En tjeneste fra OBOS").
PORTAL_DOMAINS = {"vibbo.no", "obos.no", "usbl.no", "tobb.no", "nbbo.no", "bate.no", "bob.no", "borettslag.net"}
DECLARED_SOURCES = ("registry", "registry_email", "registry_subunit", "wikidata")
SOCIAL_PLATFORMS = ("linkedin", "facebook", "instagram", "x", "youtube", "tiktok")


# ---------------------------------------------------------------- helpers
def fold(text: Any) -> str:
    text = str(text or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def tokens(text: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def core_tokens(name: Any) -> list[str]:
    return [t for t in tokens(name) if t not in LEGAL_FORMS]


# Lane 10 precision audit (C3-scale, 2026-11-06): 19/50 sampled public_brand claims were raw page titles ("Forside |
# Hasvold AS", "Hjem - Skancke AS", "<tagline> - Norconsult"), a product name or a site slug; 2/50 self-descriptions
# were the bare name or a shop-platform placeholder. Brand = the title/site-name segment that names the company.
GENERIC_TITLE_SEGMENTS = {"hjem", "forside", "startside", "hovedside", "home", "homepage", "front page", "start", "velkommen",
                          "welcome", "index", "hjemmeside", "nettside", "nettsted", "website",
                          # an organisation-type word alone is not the name ("Stiftelsen" for STIFTELSEN NORSK MAT, lane 10b)
                          "stiftelsen", "foreningen", "borettslaget", "sameiet", "selskapet", "firmaet", "klinikken", "barnehagen"}
TITLE_SEPARATORS = re.compile(r"\s+[-–—|:·•»~]\s+|\s*[|–—·•»]\s*|(?<=\w)[:,]\s+")
# lane 10b audit: maintenance pages, React/website-builder defaults and French theme defaults as brand/description
MAINTENANCE = re.compile(r"under maintenance|maintenance mode|doing some work on our site|come back later|under vedlikehold|"
                         r"nettsiden er under arbeid|siden er under arbeid", re.I)
PLACEHOLDER_DESCRIPTIONS = re.compile(r"prestashop|just another wordpress|bare enda en wordpress|nok en wordpress|powered by (wix|shopify|wordpress)"
                                      r"|my wordpress blog|this is my site|dette er min side|site description|default description|lorem ipsum"
                                      r"|create-react-app|web site created using|^page d.accueil$|^accueil$|^home ?page$|^startside$", re.I)


def public_brand(site_name: str, title: str, legal_name: str, domain: str) -> str | None:
    """The og:site_name/<title> segment that names the company (legal-name tokens or the domain label), or None."""
    core = [t for t in core_tokens(legal_name) if len(t) >= 2 and t not in WEAK_TOKENS] or [t for t in core_tokens(legal_name) if len(t) >= 2]
    label = re.sub(r"[^a-z0-9]", "", fold(str(domain or "").split(".")[0]))
    for cand in (site_name, title):
        cand = " ".join(str(cand or "").split())
        if not cand:
            continue
        for seg in [x for x in TITLE_SEPARATORS.split(cand) if x and x.strip()]:
            seg = re.sub(r"(?i)^(velkommen til|welcome to)\s+", "", seg.strip(" \"'«»"))
            seg = re.sub(r"[\s\-–—|:·•»~,]+$", "", seg)  # a dangling separator ("Askem AS -")
            if not seg or len(seg) > 60 or fold(seg) in GENERIC_TITLE_SEGMENTS or MAINTENANCE.search(seg):
                continue
            if seg == seg.lower() and " " not in seg:
                continue  # a site slug such as "tannlegedanielsen", not a displayed name
            st = tokens(seg)
            joined = "".join(st)
            hits = sum(1 for t in core if t in st)
            if (core and hits >= max(1, (len(core) + 1) // 2)) or (len(label) >= 3 and len(joined) >= 3 and (label in joined or joined in label)):
                return seg[:120]
    return None


def self_description(description: str, legal_name: str, brand: str | None) -> str | None:
    """The meta description unless it is only the company name or a platform/theme placeholder."""
    d = " ".join(str(description or "").split())
    if not d or PLACEHOLDER_DESCRIPTIONS.search(d) or MAINTENANCE.search(d) or re.fullmatch(r"(https?://|www\.)\S*", d):
        return None
    if re.match(r"(?i)(address|adresse)", d) and re.search(r"\b\d{4}\b", d) and len(tokens(d)) <= 10:
        return None  # an address line, not a description (lane 10b audit: "AddressVilbergvegen 71, 2066 Gardermoen")
    # contact details (e-mail, phone, postcode + place) are not a description of the business (main2 audit: flytison.no)
    contact_free = re.sub(r"\S+@\S+|\+?\d[\d ]{6,}\d|\b\d{4}\s+\w+|\b(e-?post|e-mail|tlf|telefon|mob|adresse|org\.? ?nr)\b:?", " ", d, flags=re.I)
    rest = [t for t in tokens(contact_free) if t not in set(tokens(legal_name)) | set(tokens(brand)) | LEGAL_FORMS]
    street_like = [t for t in rest if re.search(r"(veien|vegen|gata|gaten|gate|vei|veg|plass|bakken|stien)$", t)]
    return d if len(rest) - len(street_like) >= 3 else None


def mod11_ok(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    rest = sum(int(d) * w for d, w in zip(digits, weights)) % 11
    check = 0 if rest == 0 else 11 - rest
    return check != 10 and check == int(digits[8])


ORGNR_RE = re.compile(r"(?<![\d])(\d{3}[  .]?\d{3}[  .]?\d{3})(?![\d])")
ORG_CONTEXT = re.compile(r"(org|foretaks|organisasjons|company|vat|mva|nr|reg\.? ?no|registr|business id)", re.I)  # main2: "Reg No. 820889452" (trigo.no)


def find_orgnrs(text: str) -> tuple[set[str], set[str]]:
    """Return (all mod-11 valid 9-digit numbers, those with an org-number context word just before)."""
    found, contextual = set(), set()
    for m in ORGNR_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not mod11_ok(digits):
            continue
        found.add(digits)
        before = text[max(0, m.start() - 40):m.start()]
        # VAT form "NO958234864MVA" / "NO 958 234 864 MVA" is an organisation number too (lane 10b audit: brettel.no
        # printed only its sister's VAT number, so the name+address match was accepted for the wrong company)
        vat = re.match(r"\s?mva", text[m.end():m.end() + 5], re.I) or re.search(r"(?<![a-z])no\s?$", before, re.I)
        if ORG_CONTEXT.search(before) or vat:
            contextual.add(digits)
    return found, contextual


def first_contextual_orgnr(text: str) -> str | None:
    """The first organisation number on a page that is introduced as one (org.nr / MVA context), in reading order."""
    for m in ORGNR_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if mod11_ok(digits) and digits in find_orgnrs(text[max(0, m.start() - 40):m.end() + 5])[1]:
            return digits
    return None


def span_around(text: str, needle_re: str, width: int = 90) -> str:
    m = re.search(needle_re, text, re.I)
    if not m:
        return ""
    return " ".join(text[max(0, m.start() - width):m.end() + width].split())


def orgnr_pattern(org: str) -> str:
    return rf"{org[:3]}[  .]?{org[3:6]}[  .]?{org[6:]}"


def page_text(soup: BeautifulSoup) -> str:
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def is_html(resp: Response) -> bool:
    return "html" in resp.content_type.lower() or resp.body[:200].lstrip().lower().startswith((b"<!doctype html", b"<html"))


_TLD = None


def registered_domain(url: str) -> str:
    """Registrable domain using tldextract's bundled public-suffix snapshot (no network fetch, deterministic)."""
    global _TLD
    if _TLD is None:
        import tldextract
        _TLD = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    return _TLD(urllib.parse.urlparse(url).hostname or "").top_domain_under_public_suffix.lower()


def same_site(url: str, domain: str) -> bool:
    return registered_domain(url) == domain


def guess_domains(name: str) -> list[str]:
    core = core_tokens(name)
    if not core or len(core) > 4:
        return []
    compact = "".join(core)
    if len(compact) < 4 or compact.isdigit():
        return []
    out = [f"{compact}.no"]
    if len(core) > 1:
        out.append(f"{'-'.join(core)}.no")
    out.append(f"{compact}.com")
    return out


NORDIC_SPELLINGS = ({"ø": "oe", "å": "aa", "æ": "ae"}, {"ø": "o", "å": "aa", "æ": "ae"}, {"ø": "oe", "å": "a", "æ": "ae"},
                    {"ø": "", "å": "", "æ": ""})


def spelling_variants(name: Any) -> list[str]:
    """Compact name variants with the other common ASCII spellings of æ/ø/å (aa, oe, ae, dropped letter)."""
    low = str(name or "").lower()
    if not any(c in low for c in "æøå"):
        return []
    out = []
    for table in NORDIC_SPELLINGS:
        t = unicodedata.normalize("NFKD", low.translate(str.maketrans(table))).encode("ascii", "ignore").decode()
        toks = [x for x in re.findall(r"[a-z0-9]+", t) if x not in LEGAL_FORMS]
        if toks:
            out.append("".join(toks))
    return list(dict.fromkeys(out))


def guess_domain_variants(name: Any) -> list[str]:
    """Extra .no guesses: other æ/ø/å spellings, and the name with 'og' kept (kaffipaahjul.no, luftogvann.no)."""
    core = core_tokens(name)
    if not core or len(core) > 4:
        return []
    out = [f"{v}.no" for v in spelling_variants(name) if len(v) >= 4]
    toks = tokens(name)
    if "og" in toks:
        keep = [t for t in toks if t not in LEGAL_FORMS or t == "og"]
        if len(keep) <= 5:
            out.append("".join(keep) + ".no")
    base = set(guess_domains(name))
    return [d for d in dict.fromkeys(out) if d not in base]


def jsonld_blocks(soup: BeautifulSoup) -> list[Any]:
    blocks = []
    for node in soup.select('script[type="application/ld+json"]'):
        raw = node.string or node.get_text() or ""
        try:
            blocks.append(json.loads(raw))
        except Exception:
            continue
    return blocks


def walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def type_set(node: dict) -> set[str]:
    t = node.get("@type")
    return set(t if isinstance(t, list) else [t]) - {None}


ERROR_TITLE = re.compile(r"(^|[^0-9])404([^0-9]|$)|not found|ikke funnet|finnes ikke|page cannot be found|error 50[0-9]")
WB = '\\b'
NONWORD = '\\W+'
DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")


def iso_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    # A timestamp with a UTC offset is converted to the Europe/Oslo calendar date (what the site shows its readers):
    # "Mon, 28 Sep 2026 23:55:44 GMT" is 29 September in Norway.
    from .registry_events import _date as oslo_date
    if re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$", text):
        iso = re.sub(r"([+-]\d{2})(\d{2})$", r":", text.replace(" ", "T", 1))
        d = oslo_date(iso)
        if d:
            return d
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            t = datetime.strptime(text[:31].strip(), fmt)
            return oslo_date((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat()) or t.date().isoformat()
        except ValueError:
            continue
    m = DATE_RE.search(text)
    if m:
        return m.group(1)
    try:
        return datetime.strptime(text[:10], "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def plausible_date(d: str | None, today: str) -> bool:
    return bool(d) and "2000-01-01" <= d <= today


# ---------------------------------------------------------------- page snapshot
class Page:
    def __init__(self, resp: Response, kind: str):
        self.resp = resp
        self.kind = kind
        self.url = resp.final_url
        html = resp.text()
        self.soup = BeautifulSoup(html, "lxml")
        self.title = self.soup.title.get_text(" ", strip=True)[:300] if self.soup.title else ""
        meta = self.soup.select_one('meta[property="og:site_name"]')
        self.site_name = str(meta.get("content") or "").strip()[:200] if meta else ""
        desc = self.soup.select_one('meta[name="description"], meta[property="og:description"]')
        self.description = str(desc.get("content") or "").strip()[:600] if desc else ""
        self.jsonld = jsonld_blocks(self.soup)
        self.links = []
        for a in self.soup.select("a[href]"):
            href = str(a.get("href") or "").strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            self.links.append((urllib.parse.urljoin(self.url, href), " ".join(a.get_text(" ", strip=True).split())[:160], a))
        footer = self.soup.select("footer, address, [class*=footer], [id*=footer], [class*=contact], [itemprop=address]")
        self.footer_text = " ".join(" ".join(n.get_text(" ", strip=True).split()) for n in footer)[:4000]
        self.feeds = [urllib.parse.urljoin(self.url, str(l.get("href"))) for l in self.soup.select('link[rel=alternate][type*=rss], link[rel=alternate][type*=atom]') if l.get("href")]
        self.text = page_text(self.soup)  # destructive on soup scripts: keep last

    def org_names(self) -> list[str]:
        names = []
        for block in self.jsonld:
            for node in walk_json(block):
                if type_set(node) & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant", "ProfessionalService", "HomeAndConstructionBusiness"}:
                    for key in ("name", "legalName", "alternateName"):
                        if isinstance(node.get(key), str):
                            names.append(node[key])
        return names


def fetch_page(fetcher: Fetcher, url: str, kind: str) -> tuple[Page | None, Response]:
    resp = fetcher.get(url, max_bytes=1_500_000)
    if not resp.ok or not is_html(resp):
        return None, resp
    try:
        return Page(resp, kind), resp
    except Exception as exc:  # malformed markup
        resp.error = f"parse: {type(exc).__name__}"
        return None, resp


# ---------------------------------------------------------------- verification
def verify_site(profile: dict, pages: list[Page], source: str) -> dict:
    org = profile["organisation_number"]
    core = core_tokens(profile.get("name"))
    strong = [t for t in core if t not in WEAK_TOKENS] or core
    reasons: list[str] = []
    all_text = " ".join(p.text for p in pages)
    folded_all = fold(all_text)
    if any(marker in folded_all[:3000] for marker in PARKED) and len(all_text) < 2500:
        return {"status": "not_available", "method": None, "reasons": ["parked or placeholder page"]}
    if pages and ERROR_TITLE.search(fold(pages[0].title)):
        return {"status": "not_available", "method": None, "reasons": ["homepage is an error page (soft 404)"]}
    if len(all_text) < 40:
        if profile.get("_allow_page_source_orgnr"):
            # script-rendered sites (Wix, React) often carry the footer text in embedded JSON: the organisation
            # number in the delivered page source is still company-controlled proof
            for p in pages:
                raw = p.resp.text()
                found_raw, ctx_raw = find_orgnrs(raw)
                if org in found_raw:
                    return {"status": "verified", "method": "orgnr_in_page_source", "proof_url": p.url, "proof_sha256": p.resp.sha256,
                            "proof_retrieved_at": p.resp.retrieved_at, "span": span_around(raw, orgnr_pattern(org), 80),
                            "reasons": ["exact organisation number in the page source of a script-rendered site"]}
        return {"status": "ambiguous", "method": None, "reasons": ["page has no readable text (script shell or empty)"]}
    found, contextual = set(), set()
    proof_page = None
    for p in pages:
        f, c = find_orgnrs(p.text + " " + p.footer_text)
        found |= f
        contextual |= c
        if org in f and proof_page is None:
            proof_page = p
    if proof_page is not None and proof_page is not pages[0] and org not in find_orgnrs(pages[0].text + " " + pages[0].footer_text)[0]:
        # Our number only on an inner page (privacy/terms) that first names another organisation number as the site's
        # owner: we appear in a secondary role (lane 10b audit: nordstugard.com is Nordstu AS's site; NORDSTU GÅRD AS is
        # named only as controller of farm-shop purchases).
        # (a page listing three or more numbers is a shared site of equals, e.g. framokonomi.no's regional companies)
        page_text = proof_page.text + " " + proof_page.footer_text
        first = first_contextual_orgnr(page_text)
        if first and first != org and len(find_orgnrs(page_text)[1]) == 2:
            return {"status": "ambiguous", "method": None, "other_orgnrs": [first],
                    "reasons": ["the site names another organisation number as its owner; ours appears only in a secondary role"]}
    if proof_page is not None and len(find_orgnrs(proof_page.text + " " + proof_page.footer_text)[0] - {org}) >= 4:
        # E39 (main2 fresh11): selvaageiendom.no lists its property companies with their numbers (Tjuvholmen F2 Naering
        # AS 987 658 681 among them); a number in such a list on a site whose title/brand shares no word with our name
        # is a group listing, not the company's own site
        core = {t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS and len(t) > 2 and t not in GENERIC_NAME_WORDS}
        brand = set(tokens(" ".join([pages[0].title, pages[0].site_name, urllib.parse.urlparse(pages[0].url).netloc.split(".")[-2] if "." in urllib.parse.urlparse(pages[0].url).netloc else ""])))
        if core and not core & brand:
            return {"status": "ambiguous", "method": None, "other_orgnrs": sorted(find_orgnrs(proof_page.text)[0] - {org})[:5],
                    "reasons": ["our number appears only in a list of several companies on another company's site (group listing)"]}
    if proof_page is not None:
        span = span_around(proof_page.text, orgnr_pattern(org))
        listing = len(find_orgnrs(proof_page.text + " " + proof_page.footer_text)[0] - {org}) >= 2  # E52 (framti.no: 3 group companies)
        return {"status": "verified", "method": "orgnr_on_site", "proof_url": proof_page.url, "proof_sha256": proof_page.resp.sha256,
                "proof_retrieved_at": proof_page.resp.retrieved_at, "span": span, "reasons": ["exact organisation number printed on the site"],
                **({"group_listing": True} if listing else {})}
    others = sorted(contextual - {org})
    if others and len(others) <= 2:
        # E38 (main2): kaasboll-boats.no still prints the number of its deleted same-name predecessor (KAASBOLL BOATS AS
        # 986529152, deleted 2021); a deleted entity with exactly our name is not another owner
        others = [o for o in others if not deleted_same_name(o, str(profile.get("name") or ""))]
    if others:
        return {"status": "ambiguous", "method": None, "other_orgnrs": others[:5],
                "reasons": ["site prints a different organisation number as owner (parent, sister or namesake)"]}
    identity_parts = []
    for p in pages:
        identity_parts += [p.title, p.site_name, p.footer_text, *p.org_names()]
        copy = re.findall(r"(?:©|&copy;|copyright)[^.|\n]{0,80}", p.text, re.I)
        identity_parts += copy
    # E43 (main2 fresh13): a name that appears only inside a web or e-mail address is not the company naming itself
    # ("© www.radar-technology.com" on a casino site re-using the domain; datingsolutions.net's service@ addresses)
    identity_parts = [strip_addresses(x) for x in identity_parts]
    identity_tokens = set(tokens(" ".join(identity_parts)))
    name_ok = bool(core) and set(core) <= identity_tokens and len("".join(core)) >= 4
    name_proof = None
    connectors = {"og", "and"}
    req = [t for t in core if t not in connectors and t not in COUNTRY_WORDS] or [t for t in core if t not in connectors]
    phrase = " ".join(t for t in core if t not in connectors)

    def text_tokens(text: Any) -> list[str]:
        return [t for t in tokens(text) if t not in connectors and t not in LEGAL_FORMS]

    def has_phrase(text: Any) -> bool:  # the name as a phrase; "Eie og Fjermestad" = "EIE & FJERMESTAD"
        return f" {phrase} " in " " + " ".join(text_tokens(text)) + " "

    def near(text: Any) -> bool:
        """All distinctive name words within a short window of one field (country words optional): "Arctic Spas" for
        ARCTIC SPAS NORGE AS, "Pusehuset Kattehotell Malvik"; not words scattered over a menu (inmarsat.com footer)."""
        tt = text_tokens(text)
        pos = {t: [i for i, x in enumerate(tt) if x == t] for t in set(req)}
        if not all(pos.values()):
            return False
        best = None
        for start in pos[req[0]]:
            lo = hi = start
            for t in set(req) - {req[0]}:
                nearest = min(pos[t], key=lambda i: abs(i - start))
                lo, hi = min(lo, nearest), max(hi, nearest)
            best = hi - lo if best is None else min(best, hi - lo)
        return best is not None and best <= len(set(req)) + 1
    if name_ok:
        for p in pages:
            for part in [p.title, p.site_name, p.footer_text, *p.org_names()]:
                if has_phrase(part) or near(part):  # main2 fresh5: the name, not scattered words (inmarsat.com)
                    name_proof = (p, part)
                    break
            if name_proof:
                break
    if name_proof is None and len(phrase.split()) >= 2 and source not in DECLARED_SOURCES:
        # a multi-word name as a phrase in the page text also proves the name on a name-derived domain
        # (tubes-international.no); not for registry-declared global sites listing country branches (tubes-international.com)
        between = r"[\W_]+(?:(?:og|and)[\W_]+)?"
        for p in pages:
            if has_phrase(p.text):
                name_ok = True
                name_proof = (p, span_around(fold(p.text), WB + between.join(re.escape(t) for t in phrase.split()) + WB, 80) or p.title)
                break
    name_ok = name_ok and name_proof is not None  # tokens spread over several fields are not a name match
    address = profile.get("business_address") or {}
    postcode = str(address.get("postnummer") or "")
    street = " ".join(address.get("adresse") or []) if isinstance(address.get("adresse"), list) else str(address.get("adresse") or "")
    street_tokens = [t for t in tokens(street) if not t.isdigit() and len(t) > 2][:2]
    address_ok = bool(postcode) and postcode in all_text and bool(street_tokens) and all(t in folded_all for t in street_tokens)
    phone_digits = [re.sub(r"\D", "", str(x or ""))[-8:] for x in (profile.get("phones") or [])]
    compact_all = re.sub(r"\D", "", all_text)
    phone_ok = any(len(d) == 8 and d in compact_all for d in phone_digits)
    if source in DECLARED_SOURCES and name_ok and (len(strong) >= 1):
        p, part = name_proof
        return {"status": "verified", "method": "registry_listed_name", "proof_url": p.url, "proof_sha256": p.resp.sha256,
                "proof_retrieved_at": p.resp.retrieved_at, "span": part[:300],
                "reasons": ["registry-declared homepage and full legal name in site identity text"]}
    # Non-registry candidates must show the full legal name *with* its legal-form suffix ("Superprint AS"):
    # a brand token plus a local address also matches a foreign parent's site listing its Norwegian sales office.
    full_phrase = " ".join(tokens(profile.get("name")))
    identity_stream = " " + " ".join(tokens(" ".join(identity_parts) + " " + all_text[:20000])) + " "
    legal_phrase_ok = bool(full_phrase) and len(tokens(profile.get("name"))) > len(core) and f" {full_phrase} " in identity_stream
    if name_ok and legal_phrase_ok and (address_ok or phone_ok):
        p, part = name_proof
        if not address_ok:
            return {"status": "verified", "method": "name_and_phone", "proof_url": p.url, "proof_sha256": p.resp.sha256,
                    "proof_retrieved_at": p.resp.retrieved_at, "span": part[:300],
                    "reasons": ["full legal name and the registry telephone number on the site"]}
        return {"status": "verified", "method": "name_and_address", "proof_url": p.url, "proof_sha256": p.resp.sha256,
                "proof_retrieved_at": p.resp.retrieved_at, "span": part[:300] + " | " + span_around(all_text, re.escape(postcode), 60),
                "reasons": ["full legal name and registered street address with postcode on the site"]}
    if (name_ok and legal_phrase_ok and source == "name_guess" and profile.get("_allow_no_domain_legal_name")
            and pages and registered_domain(pages[0].url).endswith(".no")):
        p, part = name_proof
        phrase_span = span_around(fold(all_text), WB + NONWORD.join(re.escape(t) for t in full_phrase.split()) + WB, 80) or part[:300]
        return {"status": "verified", "method": "legal_name_on_no_domain", "proof_url": p.url, "proof_sha256": p.resp.sha256,
                "proof_retrieved_at": p.resp.retrieved_at, "span": phrase_span,
                "reasons": ["name-derived .no domain (Norid: Norwegian-registered holder) shows the exact legal name with its legal-form suffix"]}
    if name_ok:
        reasons.append("legal name present but no organisation number or registered address")
    elif address_ok:
        reasons.append("registered address present but legal name absent")
    else:
        reasons.append("no exact-entity proof on fetched pages")
    return {"status": "ambiguous", "method": None, "reasons": reasons}


# ---------------------------------------------------------------- discovery
def identity_links(home: Page, domain: str, limit: int = 3) -> list[str]:
    scored: dict[str, int] = {}
    for url, text, _ in home.links:
        if not same_site(url, domain):
            continue
        hay = fold(urllib.parse.urlparse(url).path + " " + text)
        rank = next((i for i, h in enumerate(IDENTITY_HINTS) if h in hay), None)
        if rank is None:
            continue
        clean = url.split("#")[0]
        if clean.rstrip("/") == home.url.rstrip("/"):
            continue
        scored[clean] = min(rank, scored.get(clean, 99))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])))[:limit]]


def read_shell(fetcher: Fetcher, home: "Page", attempt: dict) -> "Page":
    """lane 14 shell_read: a homepage with no readable text either forwards the reader (registrar frame forwarding,
    meta refresh, script redirect; parking landers and bot challenges are not followed) or carries its text in JSON
    the server already sent (Next.js, Wix). The forward target is fetched like a redirect (robots, throttle) and then
    judged by the same redirect rules; embedded JSON strings become the page text used for identity checks."""
    from .fetch_repair import embedded_json_text, forward_target
    raw = home.resp.text()
    target = forward_target(raw, home.url)
    if target:
        page, r = fetch_page(fetcher, target, "homepage")
        attempt["requests"].append({"url": target, "final_url": r.final_url, "status": r.status, "error": r.error, "kind": "shell_forward"})
        if page is not None:
            page.forwarded_from = home.url
            home = page
            raw = home.resp.text()
            if len(home.text) >= 40:
                return home
    extra = embedded_json_text(raw)
    if extra:
        home.text = (home.text + " " + extra).strip()
        home.text_from_json = True
    return home


def www_swapped(url: str) -> str | None:
    """https://www.x.no/ <-> https://x.no/ (lane 14 www_swap); None for other subdomains."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        alt = host[4:]
    elif host == registered_domain(url):
        alt = "www." + host
    else:
        return None
    return f"https://{alt}/"


def try_candidate(fetcher: Fetcher, profile: dict, url: str, source: str) -> dict:
    """Fetch a homepage candidate (+ up to 3 identity pages) and assess exact identity."""
    attempt: dict[str, Any] = {"url": url, "source": source, "requests": []}
    home, resp = fetch_page(fetcher, url, "homepage")
    attempt["requests"].append({"url": url, "final_url": resp.final_url, "status": resp.status, "error": resp.error})
    first = resp
    if home is None and url.startswith("https://") and resp.status == 0 and resp.error and "robots" not in resp.error:
        http_url = "http://" + url[len("https://"):]
        home, resp = fetch_page(fetcher, http_url, "homepage")
        attempt["requests"].append({"url": http_url, "final_url": resp.final_url, "status": resp.status, "error": resp.error})
    if home is None and profile.get("_www_swap") and source in DECLARED_SOURCES and first.status == 0             and first.error not in (None, "robots_disallowed", "deadline"):
        # lane 14: a registry-declared domain under the other usual name (apex <-> www.) when this one is unreachable,
        # has no DNS, or presents a certificate for the other name; fully verified TLS, robots as usual. Declared
        # candidates only: on dead name guesses the extra timeouts cost more than they find (dev-A Kistefos case).
        alt_url = www_swapped(url)
        if alt_url:
            alt_home, alt_resp = fetch_page(fetcher, alt_url, "homepage")
            attempt["requests"].append({"url": alt_url, "final_url": alt_resp.final_url, "status": alt_resp.status, "error": alt_resp.error, "kind": "www_swap"})
            if alt_home is not None:
                home, resp = alt_home, alt_resp
                attempt["www_swap"] = alt_url
    if home is None:
        attempt["verdict"] = {"status": "blocked" if resp.error == "robots_disallowed" else "failed" if resp.status in (0, 429) or resp.status >= 500 else "not_available",
                              "reasons": [resp.error or f"HTTP {resp.status}"]}
        return attempt
    if profile.get("_shell_read") and len(home.text) < 40:
        home = read_shell(fetcher, home, attempt)  # lane 14: follow a frame/refresh forward, read embedded JSON text
    domain = registered_domain(home.url)
    redirected = source not in DECLARED_SOURCES and registered_domain(url) != domain
    if redirected and not (profile.get("_follow_redirects") and domain not in REDIRECT_DENY and not domain.endswith((".gov", ".edu"))):
        attempt["verdict"] = {"status": "ambiguous", "reasons": [f"guessed domain redirects to another domain {domain}"]}
        attempt["pages"] = [home]
        return attempt
    pages = [home]
    verdict = verify_site(profile, pages, source)
    # Keep reading identity pages (contact/about/privacy) unless the organisation number already proved the match:
    # a name-based match is upgraded when the number appears, and withdrawn when another number is printed as owner.
    if verdict["status"] != "not_available" and verdict.get("method") != "orgnr_on_site":
        for link in identity_links(home, domain):
            page, r = fetch_page(fetcher, link, "identity")
            attempt["requests"].append({"url": link, "final_url": r.final_url, "status": r.status, "error": r.error})
            if page and same_site(page.url, domain):
                pages.append(page)
                verdict = verify_site(profile, pages, source)
                if verdict.get("method") == "orgnr_on_site" or verdict.get("other_orgnrs"):
                    break
    if redirected and verdict.get("status") == "verified":
        # E18: a guessed domain that forwards elsewhere proves nothing by itself; the target site must carry strong
        # proof on its own (own organisation number, or legal name with suffix + registered address or phone).
        if verdict.get("method") in REDIRECT_PROOFS:
            verdict["reasons"] = list(verdict.get("reasons") or []) + [f"reached through a redirect from {registered_domain(url)}"]
            verdict["redirected_from"] = url
        else:
            verdict = {"status": "ambiguous", "method": None, "reasons": [f"guessed domain redirects to another domain {domain} without strong proof there"]}
    elif (source in DECLARED_SOURCES and verdict.get("status") == "verified" and verdict.get("method") not in REDIRECT_PROOFS
          and shorter_brand_root(url, home.url)):
        # A declared homepage that forwards to the front page of a shorter brand's domain is the parent's global site
        # (lane 10b audit: bartec-technor.no -> bartec.com, the BARTEC group site, accepted on the brand word "Bartec"
        # alone): the target needs the same strong proof as a redirected guess. Forwards to a country section
        # (hydac.no -> hydac.com/no-no/), a longer or a new name (ambio.no -> ambio-water.no, arctic-spas.no ->
        # polarbad.no) are the company's own choice and are kept.
        verdict = {"status": "ambiguous", "method": None, "reasons": [f"registered homepage forwards to another domain {domain} without strong proof there"]}
    verdict["tls_verified"] = all(getattr(pg.resp, "tls_verified", True) for pg in pages)
    if any(getattr(pg.resp, "tls_repaired", None) for pg in pages):  # lane 14 attribution
        verdict["tls_repaired"] = "aia"
    if attempt.get("www_swap"):  # lane 14 attribution
        verdict["www_swap"] = attempt["www_swap"]
    if getattr(pages[0], "forwarded_from", None) or getattr(pages[0], "text_from_json", False):
        verdict["shell_read"] = "forward" if getattr(pages[0], "forwarded_from", None) else "embedded_json"
    attempt["verdict"] = verdict
    attempt["domain"] = domain
    attempt["pages"] = pages
    return attempt


def shorter_brand_root(url: str, final_url: str) -> bool:
    """True when `url` forwards to the front page of a domain whose label is a strict part of the original label."""
    src = re.sub(r"[^a-z0-9]", "", fold(registered_domain(url).split(".")[0]))
    dst = re.sub(r"[^a-z0-9]", "", fold(registered_domain(final_url).split(".")[0]))
    return bool(dst) and dst != src and dst in src and not urllib.parse.urlparse(final_url).path.strip("/")


def broader_brand_site(attempt: dict, profile: dict) -> bool:
    """A listed-domain candidate verified by name (not orgnr) whose domain AND own site name both omit a distinctive
    word of the legal name is the group's brand site, not the company's (lane 10 audit: ECURA CARE AS -> ecura.no,
    titled "Ecura", listing Ecura BPA / Hjemmetjenester / Bo og Habilitering / Care as its businesses)."""
    if attempt["verdict"].get("method") == "orgnr_on_site" or not attempt.get("pages"):
        return False
    home = attempt["pages"][0]
    strong = [t for t in core_tokens(profile.get("name")) if t not in WEAK_TOKENS and len(t) >= 2]
    label = re.sub(r"[^a-z0-9]", "", fold(str(attempt.get("domain") or "").split(".")[0]))
    if not strong or all(t in label for t in strong):
        return False
    brand = public_brand(home.site_name, home.title, profile.get("name"), attempt.get("domain") or "")
    return bool(brand) and not all(t in tokens(brand) for t in strong)


GROUP_MARKERS = ("gruppen", "group", "konsernet", "konsern")


def group_named_site(attempt: dict, profile: dict) -> bool:
    """A site verified by name (not orgnr) that calls itself "<brand>gruppen"/"<brand> Group" while the legal name has no
    group word is the group's site listing this company among its businesses (lane 10b audit: KRONSTEIN ENTREPRENØR AS
    -> kronstein.no, "Kronsteingruppen", reached through the registry e-mail domain)."""
    if attempt["verdict"].get("method") in ("orgnr_on_site", "orgnr_in_page_source") or not attempt.get("pages"):
        return False
    if any(t.endswith(GROUP_MARKERS) for t in tokens(profile.get("name"))):
        return False
    home = attempt["pages"][0]
    brand = public_brand(home.site_name, home.title, profile.get("name"), attempt.get("domain") or "") or home.site_name
    return any(t.endswith(GROUP_MARKERS) for t in tokens(brand))


ROOTISH_PATHS = {"", "index.php", "index.html", "index.htm", "home", "hjem", "forside", "no", "nb", "nn", "en", "nb-no", "no-nb", "en-gb", "en-us"}


STREET_WORD = re.compile(r"(veien|vegen|gata|gaten|gate|vei|veg|plass|bakken|stien|alle|alleen|torget|brygge|bryggen)$")


@functools.lru_cache(maxsize=1)
def shared_domains() -> frozenset[str]:
    """Domains the register lists as e-mail/homepage for >= 8 entities (scripts/build_shared_domains.py)."""
    import gzip
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "shared_domains.txt.gz"
    try:
        return frozenset(l.strip() for l in gzip.open(p, "rt", encoding="utf-8") if l.strip())
    except OSError:
        return frozenset()


CONTROLLER_RE = re.compile(r"(?i:behandlingsansvarlig|data controller|the controller)\s*(?:er|is|:|-|–)?\s*"
                           r"((?:[A-ZÆØÅ0-9][\w'’&.\-]*\s+){0,5}?[A-ZÆØÅ0-9][\w'’&.\-]*)\s+(?:AS|ASA|As|as)\b")


def other_controller(pages: list, profile: dict) -> str | None:
    """main2 round 8: a privacy page naming another legal entity as data controller ("Behandlingsansvarlig: CUT'N GO
    HAMAR AS" on cutngo.no for CUT'N GO HAUGESUND AS) shows the site is that entity's (chain, franchise, group)."""
    own = "".join(t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS)
    for p in pages:
        for m in CONTROLLER_RE.finditer(p.text):
            other = "".join(t for t in tokens(m.group(1)) if t not in LEGAL_FORMS)
            if other and own and other != own and own not in other and other not in own:
                return f"the site's privacy statement names another legal entity as controller ({m.group(1).strip()} AS)"
    return None


# Nordic sister forms only: a global brand site ("(c) Amundsen Sports Inc") can still be the Norwegian AS's own site
NORDIC_FORMS_RE = r"(ab|aps|oy|oyj)"


def foreign_owner(pages: list, core: list[str]) -> str | None:
    """main2 fresh9 audit: isicom.no prints "(c) IsiCom AB" -- the copyright holder is a foreign company with our name
    (Swedish AB, Danish ApS, Finnish Oy), so a name match cannot prove the Norwegian AS."""
    if not core:
        return None
    rx = re.compile(r"(?:©|copyright|\(c\))\s*(?:\d{4}(?:\s*-\s*\d{4})?\s*)?" + r"[\s\-]*".join(map(re.escape, core))
                    + r"\s+" + NORDIC_FORMS_RE + r"(?![a-z])", re.I)
    for p in pages:
        m = rx.search(fold((p.text + " " + p.footer_text).replace("©", " copyright ")))
        if m:
            return f"the site's copyright holder is a foreign company with this name ({m.group(0).strip()})"
    return None


FOREIGN_TLDS = {"se", "dk", "fi", "de", "pl", "ee", "lv", "lt", "nl", "uk", "fr", "es", "it", "is", "at", "ch", "be", "cz", "ru", "ua", "cn", "jp"}


ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.(?:no|com|net|org|se|dk|fi|io|eu|info|biz|co)\b(?:/\S*)?", re.I)


def strip_addresses(text: Any) -> str:
    """Text without e-mail addresses, URLs and bare domain names."""
    return ADDRESS_RE.sub(" ", str(text or ""))


def cjk_page(page) -> bool:
    """E37 (main2 fresh11): datingsolutions.net, a registry-declared domain, now serves a Chinese site; the name matched
    only inside its e-mail addresses. A page whose letters are mostly CJK is not a Norwegian company's own site."""
    text = (page.title or "") + " " + (page.text or "")
    cjk = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= 40 and cjk / letters >= 0.3


def name_based_risk(attempt: dict, profile: dict) -> str | None:
    """main2 fresh2 audit. A match that rests on the company NAME (not its organisation number) is not accepted when
    (a) the company's name is only a street address ("SEMSVEGEN 51 AS" matched westcontrol.com's footer address), or
    (b) the site's title / og:site_name presents another legal entity ("DEAS Gruppen AS" for its subsidiary Dokka
    Entreprenor AS; "Jaegergruppen AS" for Jaeger Sentrum AS). Returns the reason, or None."""
    if attempt["verdict"].get("method") == "orgnr_on_site" or not attempt.get("pages"):
        return None
    core = [t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS]
    if core and all(STREET_WORD.search(t) or t.isdigit() for t in core):
        return "company name is a street address; only the organisation number can prove the site"
    compact_name = "".join(core)
    controller = other_controller(attempt["pages"], profile)
    if controller:
        return controller
    final_tld = urllib.parse.urlparse(attempt["pages"][0].url).netloc.lower().rsplit(".", 1)[-1]
    if final_tld in FOREIGN_TLDS:
        # E41 (main2 fresh12): idainfront.no forwards to idainfront.se, the Swedish company's site
        return f"the site is on a foreign country domain (.{final_tld}); a name match cannot prove the Norwegian entity"
    if cjk_page(attempt["pages"][0]):
        return "the page is mostly Chinese/Japanese/Korean text (expired or re-used domain); a name match cannot prove a Norwegian company"
    foreign = foreign_owner(attempt["pages"], core)
    if foreign:
        return foreign
    domain = str(attempt.get("domain") or "")
    label = re.sub(r"[^a-z0-9]", "", fold(domain.split(".")[0]))
    if domain in shared_domains() and not (label and label in compact_name):
        return f"{domain} is the e-mail/homepage domain of many register entities (service provider); only the organisation number can prove it"
    home = attempt["pages"][0]
    for label in (home.title, home.site_name):
        tt = tokens(label)
        for i, t in enumerate(tt):
            if t in ("as", "asa") and i > 0:
                phrase = "".join(x for x in tt[max(0, i - 3):i] if x not in LEGAL_FORMS)
                tail = "".join(x for x in tt[max(0, i - 1):i])
                if phrase and tail and compact_name and tail not in compact_name and compact_name not in phrase and phrase not in compact_name:
                    return f"site presents itself as another legal entity ({' '.join(tt[max(0, i - 3):i + 1]).upper()})"
    return None


TITLE_ENTITY = re.compile(r"((?:[A-ZÆØÅ0-9][\w&.'-]*\s+){0,3}[A-ZÆØÅ0-9][\w&.'-]*)\s+(AS|ASA)\b")
COUNTRY_WORDS = {"norge", "norway", "norden", "nordic", "scandinavia", "skandinavia"}


def title_entity_in_register(attempt: dict, profile: dict, deadline: float | None = None) -> str | None:
    """main2 fresh4 audit (retailpartner.no is RETAILPARTNER AS, the sister of RETAILPARTNER EIENDOM AS): when a
    name-based match's title / og:site_name names a legal entity ("X AS", capitalised) other than the company, and the
    register has an active entity with exactly that name, the site is that entity's. One register search."""
    if attempt["verdict"].get("method") == "orgnr_on_site" or not attempt.get("pages"):
        return None
    from .http import fetch_json
    home = attempt["pages"][0]
    own = "".join(t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS)
    core = [t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS]
    if not core:
        return None
    searches = 0
    for label in (home.title, home.site_name):
        for m in TITLE_ENTITY.finditer(str(label or "")):
            words = [w for w in tokens(m.group(1)) if w not in LEGAL_FORMS]
            suffixes = ["".join(words[k:]) for k in range(len(words))]
            if own in suffixes:
                continue  # the title names the company itself ("Hjem - Hansen Bygg AS")
            for k, cand in enumerate(suffixes):
                if not cand.startswith(core[0]) or searches >= 2:
                    continue  # only a sister-style name (same leading word) is checked
                searches += 1
                r = fetch_json("https://data.brreg.no/enhetsregisteret/api/enheter?navn=" + urllib.parse.quote(" ".join(words[k:]) + " " + m.group(2))
                               + "&size=20", deadline=deadline, attempts=2)
                ents = ((r.body or {}).get("_embedded") or {}).get("enheter") or [] if r.status == 200 and isinstance(r.body, dict) else []
                for ent in ents:
                    if (str(ent.get("organisasjonsnummer")) != profile.get("organisation_number") and not ent.get("slettedato")
                            and "".join(t for t in tokens(ent.get("navn")) if t not in LEGAL_FORMS) == cand):
                        return f"site presents itself as another register entity ({ent.get('navn')}, {ent.get('organisasjonsnummer')})"
    return None


GENERIC_NAME_WORDS = {"advokat", "advokatfirma", "advokatfirmaet", "advokatene", "regnskap", "regnskapskontor", "revisjon",
                      "eiendom", "eiendomsselskap", "holding", "invest", "investering", "bygg", "byggservice", "consulting",
                      "konsult", "konsulent", "service", "tjenester", "drift", "utvikling", "norge", "norway", "group",
                      "gruppen", "partner", "partners", "and", "og"}


def deleted_same_name(orgnr: str, name: str) -> bool:
    """True when the register says `orgnr` is a deleted entity whose name equals `name` (one register look-up)."""
    from .http import fetch_json
    r = fetch_json(f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}", attempts=2, timeout=10.0)
    if r.status != 200 or not isinstance(r.body, dict) or not r.body.get("slettedato"):
        return False
    return [t for t in tokens(r.body.get("navn")) if t not in LEGAL_FORMS] == [t for t in tokens(name) if t not in LEGAL_FORMS]


def namesake_in_register(profile: dict, deadline: float | None = None, site_text: str = "", suffix_only: bool = False,
                         domain: str = "") -> str | None:
    """main2 fresh2 audit (auto-shine.no is AUTO SHINE AS of Lillehammer, not AUTO-SHINE AS of Oppaker): another
    active register entity with the same name means a name-only match cannot tell them apart. One register search."""
    from .http import fetch_json
    name = str(profile.get("name") or "")
    compact = "".join(t for t in tokens(name) if t not in LEGAL_FORMS)
    if not compact:
        return None
    query = urllib.parse.quote(" ".join(t for t in tokens(name) if t not in LEGAL_FORMS)[:80])
    r = fetch_json(f"https://data.brreg.no/enhetsregisteret/api/enheter?navn={query}&size=50", deadline=deadline, attempts=2)
    if r.status != 200 or not isinstance(r.body, dict):
        return "register name search unavailable; a name-only match is not accepted"
    for ent in ((r.body.get("_embedded") or {}).get("enheter") or []):
        other = str(ent.get("organisasjonsnummer") or "")
        if other and other != profile.get("organisation_number") and not ent.get("slettedato"):
            other_compact = "".join(t for t in tokens(ent.get("navn")) if t not in LEGAL_FORMS)
            if domain and ent.get("hjemmeside") and registered_domain("https://" + str(ent.get("hjemmeside") or "").split("://")[-1]) == registered_domain("https://" + domain):
                # E47 (main2 fresh14): fontes.no is the declared homepage of FONTES FOUNDATION STI, not of FONTES AS
                return f"the register lists this domain as the homepage of another entity ({ent.get('navn')}, {other})"
            if other_compact == compact and not suffix_only:
                return f"another register entity has the same name ({other}); a name-only match cannot tell them apart"
            # main2 fresh5: grinna.no shows "Fotograf Grinna AS" (another entity whose name ends with GRINNA)
            if other in (profile.get("_group_orgnrs") or []):
                continue  # main2 fresh7: atico.no names ATICO SOLOR AS, a registered subsidiary of ATICO AS
            if other_compact != compact and compact in other_compact and site_text and f" {' '.join(tokens(ent.get('navn')))} " in site_text:
                return f"the site names another register entity ({ent.get('navn')}, {other}) whose name contains this one"
            # E36 (main2 fresh11): korenoptikk.no shows Koren Optikk Trondheim AS's address (Sondre gate 23, 7010), not ours
            if other_compact != compact and compact in other_compact and site_text and not suffix_only:  # not for declared sites (koinonia.no)
                addr = ent.get("forretningsadresse") or {}
                other_pc = str(addr.get("postnummer") or "")
                own_pc = str((profile.get("business_address") or {}).get("postnummer") or "")
                street = " ".join(tokens(" ".join(addr.get("adresse") or [])))
                shows_other = (other_pc and other_pc != own_pc and f" {other_pc} " in site_text) or (len(street) > 6 and f" {street} " in site_text)
                if shows_other and not (own_pc and f" {own_pc} " in site_text):
                    return f"the site shows the address of another register entity ({ent.get('navn')}, {other}) whose name contains this one"
    return None


def tenant_or_group_page(attempt: dict, profile: dict) -> str | None:
    """main2 fresh-run audit. A verified page that is a sub-path page of a site (not its root) where another
    organisation number is printed, or where only a name matched (flow-elektro.no/oslo-vest on the FLOW group site;
    tp-engros.no -> sg.no/sg-more chain portal), is a tenant page of a group or portal site, not the company's own site.
    (A rule for sites naming sibling legal entities was tried and dropped: own sites often name a sister company.)
    Returns the reason, or None."""
    pages = attempt.get("pages") or []
    if not pages:
        return None
    org = profile["organisation_number"]
    method = attempt["verdict"].get("method")
    path = urllib.parse.urlparse(pages[0].url).path.strip("/").lower()
    if registered_domain(pages[0].url) in PORTAL_DOMAINS and path:
        return "the site redirects to a page on a multi-tenant manager portal (main2: ullernaasen.no -> vibbo.no/ullernasen)"
    others = set()
    for p in pages:
        found, _ = find_orgnrs(p.text + " " + p.footer_text)
        others |= found - {org}
    rootish = path in ROOTISH_PATHS or ("/" not in path and re.search(r"\.(php|html?|aspx?|jsp|cfm)$", path) is not None)  # /home.php, /default.aspx
    # E35 (lane 10b note): a register-declared domain forwarding to the group's Norwegian country section
    # (hydac.no -> hydac.com/no-no/, howdengroup.no -> howdengroup.com/no-en/) is the company's own section; the crawl
    # keeps posts and profiles inside that section (site_scope)
    country_section = re.fullmatch(r"(no-no|no-en|en-no|nb-no|no-nb|norway|norge|no_no|nb_no)", path) is not None
    if country_section and not others and attempt.get("source") in ("registry", "registry_email", "registry_subunit"):
        return None
    if not rootish and (others or method != "orgnr_on_site"):
        return "company page under a path of a shared or group site (another organisation number, or no own number, on the site)"
    return None


def discover_website(fetcher: Fetcher, profile: dict, extra_candidates: list[tuple[str, str]] | None = None, guess: bool = True,
                     late_candidates: list[tuple[str, str]] | None = None) -> dict:
    from .website import normalize_homepage
    tried: set[str] = set()
    attempts = []
    candidates: list[tuple[str, str]] = []
    reg = normalize_homepage(profile.get("website"))
    if reg:
        candidates.append((reg, "registry"))
    email = str(profile.get("email") or "")
    if "@" in email:
        edomain = email.rsplit("@", 1)[1].strip().lower()
        if edomain and edomain not in FREEMAIL and not any(edomain.endswith("." + f) for f in FREEMAIL):
            candidates.append((f"https://{edomain}/", "registry_email"))
    for extra_email in profile.get("subunit_emails") or []:
        edomain = str(extra_email).rsplit("@", 1)[-1].strip().lower()
        if "@" in str(extra_email) and edomain and edomain not in FREEMAIL and not any(edomain.endswith("." + f) for f in FREEMAIL):
            candidates.append((f"https://{edomain}/", "registry_subunit"))
    candidates += list(extra_candidates or [])
    if guess:
        candidates += [(f"https://{d}/", "name_guess") for d in guess_domains(profile.get("name"))]
        if profile.get("_name_variants"):
            candidates += [(f"https://{d}/", "name_guess") for d in guess_domain_variants(profile.get("name"))]
    candidates += list(late_candidates or [])  # lane 5: listed-domain name variants, tried after the fixed guesses
    for url, source in candidates:
        try:
            dom = registered_domain(url)
        except Exception:
            continue
        if not dom or dom in tried:
            continue
        if dom in PORTAL_DOMAINS and urllib.parse.urlparse(url).path.strip("/"):
            attempts.append({"url": url, "source": source, "verdict": {"status": "ambiguous", "reasons": ["page on a multi-tenant manager portal, not the company's own site"]}})
            continue
        tried.add(dom)
        if fetcher.remaining() < 10:
            attempts.append({"url": url, "source": source, "verdict": {"status": "failed", "reasons": ["deadline"]}})
            break
        if source == "name_guess":
            import socket
            from .website import _via_proxy
            try:
                socket.getaddrinfo(dom, 443, type=socket.SOCK_STREAM)
            except OSError:
                if _via_proxy("https", dom):  # lane 9: proxy-only egress has no local DNS; let the proxy decide
                    attempt = try_candidate(fetcher, profile, url, source)
                    attempts.append(attempt)
                    if attempt["verdict"]["status"] == "verified" and group_named_site(attempt, profile):
                        attempt["verdict"] = {**attempt["verdict"], "status": "ambiguous", "reasons": ["site names itself as a group (group site, not this company's own)"]}
                    why = (tenant_or_group_page(attempt, profile) or name_based_risk(attempt, profile)) if attempt["verdict"]["status"] == "verified" else None
                    if why:
                        attempt["verdict"] = {**attempt["verdict"], "status": "ambiguous", "reasons": [why]}
                    if attempt["verdict"]["status"] == "verified":
                        return {"status": "verified", "selected": attempt, "attempts": attempts}
                    continue
                attempts.append({"url": url, "source": source, "verdict": {"status": "not_available", "reasons": ["domain does not resolve"]}})
                continue
        attempt = try_candidate(fetcher, profile, url, source)
        attempts.append(attempt)
        if attempt["verdict"]["status"] == "verified" and source == "cc_domain_list" and broader_brand_site(attempt, profile):
            attempt["verdict"] = {**attempt["verdict"], "status": "ambiguous",
                                  "reasons": ["site names itself by a shorter brand than the legal name (group/brand site)"]}
        if attempt["verdict"]["status"] == "verified" and group_named_site(attempt, profile):
            attempt["verdict"] = {**attempt["verdict"], "status": "ambiguous", "reasons": ["site names itself as a group (group site, not this company's own)"]}
        if attempt["verdict"]["status"] == "verified" and profile.get("_tenant_check", True):
            why = tenant_or_group_page(attempt, profile) or name_based_risk(attempt, profile)
            single_word = len([t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS]) == 1
            site_text = " " + " ".join(tokens(" ".join(p.text + " " + p.title + " " + p.footer_text for p in attempt.get("pages") or []))) + " "
            core_words = [t for t in tokens(profile.get("name")) if t not in LEGAL_FORMS]
            if (not why and attempt["verdict"].get("method") == "registry_listed_name" and single_word and source == "registry_email"
                    and core_words[0] not in re.sub(r"[^a-z0-9]", "", fold(dom.split(".")[0]))
                    and f" {core_words[0]} as " not in site_text and f" {core_words[0]} asa " not in site_text):
                # E48 (main2 fresh14): LOSSLESS AS's e-mail domain is its distributor's shop ("Minuendo | Lossless earplugs"):
                # a one-word name on an e-mail-domain site that is not named after it counts only with its legal form
                # (kulturminnehjelp.no, consta.no are named after the company and stay)
                why = "one-word name on the registry e-mail domain's site without the legal form (product or brand word)"
            if not why and attempt["verdict"].get("method") == "registry_listed_name" and single_word:
                # main2 fresh7: "BREDLAND AS" matched "Lars Thomas Bredland" (a separately registered sole proprietor)
                why = namesake_in_register(profile, deadline=getattr(fetcher, "deadline", None), suffix_only=True,
                                           site_text=" " + " ".join(tokens(" ".join(p.text + " " + p.title for p in attempt.get("pages") or []))) + " ",
                                           domain=dom if source not in DECLARED_SOURCES else "")  # E47 only for undeclared candidates (entrack.no)
            if not why and attempt["verdict"].get("method") == "legal_name_on_no_domain":
                why = namesake_in_register(profile, deadline=getattr(fetcher, "deadline", None),
                                           site_text=" " + " ".join(tokens(" ".join(p.text + " " + p.title for p in attempt.get("pages") or []))) + " ",
                                           domain=dom if source not in DECLARED_SOURCES else "")
            if not why:
                why = title_entity_in_register(attempt, profile, deadline=getattr(fetcher, "deadline", None))
            if why:
                attempt["verdict"] = {**attempt["verdict"], "status": "ambiguous", "reasons": [why]}
        if attempt["verdict"]["status"] == "verified":
            return {"status": "verified", "selected": attempt, "attempts": attempts}
    if any(a["verdict"]["status"] == "ambiguous" for a in attempts):
        state = "ambiguous"
    elif reg and any(a["source"] == "registry" and a["verdict"]["status"] in {"failed", "blocked"} for a in attempts):
        state = next(a["verdict"]["status"] for a in attempts if a["source"] == "registry")
    else:
        state = "not_available"
    return {"status": state, "selected": None, "attempts": attempts}


# ---------------------------------------------------------------- crawl of a verified site
FOREIGN_COUNTRY_WORDS = ("sweden", "sverige", "svenska", "denmark", "danmark", "finland", "suomi", "germany", "deutschland", "poland", "polska",
                         "estonia", "latvia", "lithuania", "netherlands", "nederland", "france", "spain", "italy", "iceland", "island")


GROUP_HANDLE_WORDS = ("group", "gruppen", "global", "international", "worldwide")
FOREIGN_FORMS = {"ab", "gmbh", "oy", "oyj", "aps", "ltd", "inc", "llc", "bv", "plc", "sarl", "srl", "ag", "sverige", "sweden",
                 "danmark", "denmark", "suomi", "finland"}


def site_scope(url: str) -> str:
    """The path prefix of a verified site that is a section of a larger domain ("/no-no/" on hydac.com, a hotel page on
    a chain's site), or "" for a site at the domain root."""
    path = urllib.parse.urlparse(url or "").path
    path = re.sub(r"/(index|default|home)\.[a-z]{2,5}$", "/", path, flags=re.I)
    return path.rstrip("/") + "/" if path.strip("/") else ""


def in_scope(url: str, scope: str) -> bool:
    return not scope or (urllib.parse.urlparse(url).path.rstrip("/") + "/").lower().startswith(scope.lower())


def foreign_or_group_handle(raw_handle: str, legal_name: str) -> bool:
    """A handle naming a group/global account or a foreign sister ("HydacGroup", "Rollco-international", "rollco_ab",
    "monkberry-ab") is not this Norwegian company's own profile (lane 10b audit), unless its legal name says the same."""
    toks = tokens(raw_handle)
    legal = tokens(legal_name)
    joined, legal_joined = "".join(toks), "".join(legal)
    if any(w in joined and w not in legal_joined for w in GROUP_HANDLE_WORDS):
        return True
    return any(t in FOREIGN_FORMS and t not in legal for t in toks)


def social_from_pages(pages: list[Page], profile: dict, domain: str, scope: str = "") -> list[dict]:
    """Company-owned profiles = social links on the verified site. Keep a link when it is the only one for its
    platform on the site or its handle matches the legal name, the domain label or the site name; drop share/intent
    links (normalize_social_url) and links that appear only inside article bodies. On a site that is a section of a
    larger domain (`scope`), only links on pages inside the section whose handle carries the legal name count: the
    domain's label and site name belong to the chain or group (lane 10b audit: strawberry.no hotel pages, HYDAC, Howden)."""
    by_platform: dict[str, dict[str, dict]] = {}
    plus = bool(profile.get("_social_plus"))
    fb_numeric: dict[str, dict] = {}
    for p in pages:
        if not in_scope(p.url, scope):
            continue
        hrefs = [(u, "a") for u, _, _ in p.links]
        for block in p.jsonld:
            for node in walk_json(block):
                same = node.get("sameAs")
                org_node = bool({str(t).lower() for t in type_set(node)} & ORG_TYPES)
                for u in (same if isinstance(same, list) else [same]):
                    if isinstance(u, str):
                        hrefs.append((u.strip(), "jsonld_org" if org_node else "jsonld"))
        for u, how in hrefs:
            n = normalize_social_url(u)
            if not n:
                m = FB_NUMERIC.match(u.strip()) if plus and how == "a" else None
                if m:
                    rec = fb_numeric.setdefault(m.group(1), {"platform": "facebook", "url": f"https://facebook.com/profile.php?id={m.group(1)}", "found_on": p.url,
                                                             "sha256": p.resp.sha256, "retrieved_at": p.resp.retrieved_at, "pages": set()})
                    rec["pages"].add(p.url)
                continue
            rec = by_platform.setdefault(n["platform"], {}).setdefault(n["url"].lower(), {**n, "found_on": p.url, "how": how, "count": 0, "sha256": p.resp.sha256, "retrieved_at": p.resp.retrieved_at, "jsonld_org": False})
            rec["count"] += 1
            rec["jsonld_org"] = rec["jsonld_org"] or how == "jsonld_org"
    label = fold(domain.split(".")[0]).replace("-", "")
    name_compact = "".join(core_tokens(profile.get("name")))
    name_forms = {name_compact, *spelling_variants(profile.get("name"))} - {""}
    site_names = {"".join(tokens(p.site_name)) for p in pages if p.site_name}
    # main2 fresh4 audit: for "OMV NORGE AS" the handles omv / omvofficial are the global group's channels, not the
    # Norwegian entity's (regional names such as "CONNECT MIDT-NORGE" are not country qualifiers)
    # main2 fresh5 audit: enviroprocess_sweden on a Norwegian company's site is the Swedish sister's channel
    def foreign_handle(h: str) -> bool:
        return any((h.startswith(w) or h.endswith(w)) and w not in "".join(tokens(profile.get("name"))) for w in FOREIGN_COUNTRY_WORDS)
    global_handles: set[str] = set()
    name_tokens = tokens(profile.get("name"))
    if set(name_tokens) & COUNTRY_WORDS and not re.search(r"-\s*(NORGE|NORWAY)\b", str(profile.get("name") or ""), re.I):
        brand_only = "".join(t for t in name_tokens if t not in COUNTRY_WORDS and t not in LEGAL_FORMS)
        global_handles = {brand_only + x for x in ("", "asa", "group", "global", "official", "company", "com")} if brand_only else set()
    out = []
    for platform, links in by_platform.items():
        def raw(rec):
            return urllib.parse.unquote(urllib.parse.urlparse(rec["url"]).path.strip("/").split("/")[-1])

        def handle(rec):
            return "".join(tokens(raw(rec)))
        links = {k: r for k, r in links.items() if not foreign_or_group_handle(raw(r), profile.get("name"))}
        if scope:
            matched = [r for r in links.values() if handle(r) and any(f == handle(r) or f in handle(r) for f in name_forms)]
        else:
            matched = [r for r in links.values() if handle(r) and (handle(r) == label or label in handle(r) or any(f == handle(r) or f in handle(r) for f in name_forms)
                                                                  or any(s and (s in handle(r) or handle(r) in s) for s in site_names))]
        # Only handles that match the company (legal name, domain label or site name) are published. A lone link
        # whose handle does not match was wrong or a vendor account in half of the audited cases (web builders,
        # partners, parent organisations), so it is not treated as the company's own profile.
        chosen = [r for r in matched if handle(r) not in PLATFORM_HANDLES and handle(r) not in global_handles and not foreign_handle(handle(r))]
        if plus and not chosen and not scope:
            # E17: the site's own structured data names the profile (Organization/LocalBusiness sameAs). A web builder,
            # partner or parent is not listed as the organisation's sameAs; at most one such link per platform.
            listed = [r for r in links.values() if r["jsonld_org"] and handle(r) not in PLATFORM_HANDLES and handle(r) not in global_handles and not foreign_handle(handle(r))]
            if len(listed) == 1:
                out.append({"platform": platform, "url": listed[0]["url"], "found_on": listed[0]["found_on"], "sha256": listed[0]["sha256"],
                            "retrieved_at": listed[0]["retrieved_at"], "match": "listed_in_site_organization_sameAs"})
                continue
        for r in chosen[:2]:
            out.append({"platform": platform, "url": r["url"], "found_on": r["found_on"], "sha256": r["sha256"], "retrieved_at": r["retrieved_at"],
                        "match": "handle_matches_company" if r in matched else "only_link_for_platform_on_verified_site"})
    # E17: a numeric Facebook page (profile.php?id=N has no handle to match) is kept only when it is the site's one
    # Facebook link and it sits in the site-wide chrome (on two or more and at least half of the fetched pages).
    if plus and not scope and len(fb_numeric) == 1 and "facebook" not in by_platform:
        r = next(iter(fb_numeric.values()))
        if len(r["pages"]) >= min(2, len(pages)) and 2 * len(r["pages"]) >= len({p.url for p in pages}):
            out.append({"platform": "facebook", "url": r["url"], "found_on": r["found_on"], "sha256": r["sha256"], "retrieved_at": r["retrieved_at"],
                        "match": "only_facebook_page_sitewide_on_verified_site"})
    return sorted(out, key=lambda r: (r["platform"], r["url"]))


def section_links(pages: list[Page], domain: str, hints: tuple[str, ...], limit: int) -> list[str]:
    scored: dict[str, int] = {}
    for p in pages:
        for url, text, _ in p.links:
            if not same_site(url, domain):
                continue
            path = urllib.parse.urlparse(url).path
            hay = fold(path + " " + text)
            rank = next((i for i, h in enumerate(hints) if h in hay), None)
            if rank is None or path.count("/") > 3:
                continue
            scored[url.split("#")[0]] = min(rank, scored.get(url.split("#")[0], 99))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])))[:limit]]


def job_countries(node: dict) -> set[str]:
    """Country codes/names in a JobPosting's jobLocation (main2 fresh3 audit: careers.nordec.com lists the group's
    Finnish, Swedish and Polish jobs on the Norwegian company's verified site)."""
    out: set[str] = set()
    locs = node.get("jobLocation")
    for loc in (locs if isinstance(locs, list) else [locs]):
        addr = loc.get("address") if isinstance(loc, dict) else None
        for a in (addr if isinstance(addr, list) else [addr]):
            if isinstance(a, dict):
                c = a.get("addressCountry")
                c = c.get("name") if isinstance(c, dict) else c
                if c:
                    out.add(fold(str(c)).strip())
    return out


NORWAY = {"no", "nor", "norway", "norge", "noreg"}
ABROAD_WORDS = {"sweden", "sverige", "denmark", "danmark", "finland", "suomi", "germany", "deutschland", "poland", "polska",
                "estonia", "latvia", "lithuania", "netherlands", "france", "spain", "italy", "iceland", "stockholm", "solna",
                "gothenburg", "goteborg", "copenhagen", "kobenhavn", "helsinki", "tallinn", "riga", "vilnius", "warsaw",
                "brazil", "brasil", "india", "china", "japan", "canada", "mexico", "czechia", "ukraine", "portugal", "austria",
                "switzerland", "belgium", "ireland", "romania", "hungary", "philippines", "singapore", "malaysia", "vietnam"}


def jobs_from_page(page: Page, domain: str) -> list[dict]:
    jobs: dict[str, dict] = {}
    for block in page.jsonld:
        for node in walk_json(block):
            if "JobPosting" in type_set(node):
                title = str(node.get("title") or "").strip()
                url = str(node.get("url") or page.url)
                countries = job_countries(node)
                if countries and not countries & NORWAY:
                    continue  # a position abroad (group careers site), not at this Norwegian company
                place = set(tokens(title + " " + url))
                if not countries and place & ABROAD_WORDS and not place & NORWAY:
                    continue  # main2 fresh12: "... in Solna, Sweden" on careers.tieto.com without a jobLocation country
                org = node.get("hiringOrganization")
                org_name = str((org.get("name") if isinstance(org, dict) else org) or "").strip()
                if title:
                    jobs[url + "|" + title] = {"title": title[:200], "url": url, "date_posted": iso_date(node.get("datePosted")),
                                                "valid_through": iso_date(node.get("validThrough")), "method": "jsonld_jobposting", "hiring_org": org_name[:120]}
    # Links to applicant-tracking systems are not published as postings: in the audit they were vendor, login and
    # "powered by" links, not jobs. Only structured JobPosting data counts.
    return list(jobs.values())[:30]


def activity_from_feed(resp: Response, domain: str, today: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(resp.body)
    except ET.ParseError:
        return items
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda name: next((c for c in node if c.tag.split("}")[-1] == name), None)
        title = (get("title").text if get("title") is not None else "") or ""
        link_node = get("link")
        link = (link_node.text or link_node.get("href") or "") if link_node is not None else ""
        date = None
        for name in ("pubDate", "published", "updated", "date"):
            n = get(name)
            if n is not None and n.text:
                date = iso_date(n.text)
                if date:
                    break
        guid = get("guid")
        if guid is not None and re.search(r"[?&]page_id=\d", guid.text or ""):
            continue  # a static WordPress page listed in the feed (service page), not a post (lane 10 audit: aktivklinikken.no)
        if re.search(r"/wp-content/uploads/|\.(pdf|jpe?g|png|gif|webp|docx?|xlsx?|pptx?|zip|mp3|mp4)(\?|$)", link.strip().lower()):
            continue  # a media attachment listed in the feed, not a post (lane 10 audit: kunstbanken.no programme PDF)
        if title and link and plausible_date(date, today) and same_site(link, domain):
            body = " ".join((n.text or "") for n in node if n.tag.split("}")[-1] in ("description", "encoded", "content", "summary"))
            if filler_text(body):
                continue  # theme demo content (Lorem ipsum body), not company activity
            items.append({"title": " ".join(title.split())[:200], "url": link.strip(), "date": date, "method": "rss_feed"})
    return items


def activity_from_wp(resp: Response, domain: str, today: str) -> list[dict]:
    try:
        posts = json.loads(resp.body)
    except Exception:
        return []
    out = []
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        title = BeautifulSoup(str((p.get("title") or {}).get("rendered") or ""), "lxml").get_text(" ", strip=True)
        excerpt = fold(BeautifulSoup(str((p.get("excerpt") or {}).get("rendered") or ""), "lxml").get_text(" ", strip=True))
        if filler_text(excerpt):
            continue  # theme demo content (Lorem ipsum / Latin filler excerpt), not company activity (lane 5 audit: eprod.no; main2 fresh14 kdtrading.no)
        link, date = str(p.get("link") or ""), iso_date(p.get("date") or p.get("date_gmt"))
        if title and link and plausible_date(date, today) and same_site(link, domain):
            out.append({"title": title[:200], "url": link, "date": date, "method": "wordpress_rest_posts"})
    return out


def activity_from_article(page: Page, today: str, plus: bool = False) -> dict | None:
    date = None
    if filler_text(page.text):
        return None  # main2 fresh9 audit: theme demo page in filler text
    headline = ""
    for block in page.jsonld:
        for node in walk_json(block):
            if type_set(node) & {"NewsArticle", "Article", "BlogPosting", "Report", "PressRelease"}:
                headline = headline or (node.get("headline") if isinstance(node.get("headline"), str) else "")
                date = iso_date(node.get("datePublished"))
                if date:
                    break
        if date:
            break
    method = "jsonld_article"
    if not date:
        meta = page.soup.select_one('meta[property="article:published_time"], meta[name="date"], meta[itemprop="datePublished"]')
        date = iso_date(meta.get("content")) if meta else None
        method = "meta_published_time"
    if not date:
        t = page.soup.select_one("article time[datetime], main time[datetime]")
        date = iso_date(t.get("datetime")) if t else None
        method = "html_time_element"
    # Title: the article headline, never the site name (lane 10 audit: Squarespace logo <h1>KEY</h1> published as the title)
    site = {fold(page.site_name).strip()} - {""}
    h1s = [h.get_text(" ", strip=True) for h in page.soup.select("h1") if "logo" not in " ".join(h.get("class") or []).lower()]
    title = next((t for t in [" ".join(str(headline).split())] + h1s + [page.title] if t and fold(t).strip() not in site), "")[:200]
    if not date and plus:
        date, method = article_date_plus(page, title)
    if not plausible_date(date, today):
        return None
    if date >= today:
        return None  # a page-level publish stamp equal to the fetch day is a render-time value (lane 10: oslowineschool.no)
    return {"title": title, "url": page.url, "date": date, "method": method} if title else None


MONTHS = {"januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
          "oktober": 10, "november": 11, "desember": 12, "january": 1, "february": 2, "march": 3, "may": 5, "june": 6,
          "july": 7, "october": 10, "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "sept": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}
TEXT_DATE = re.compile(r"(?<![0-9])([0-3]?[0-9])[.]?[ ]+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")[.]?[ ]+(20[0-9][0-9])(?![0-9])", re.I)
NUM_DATE = re.compile(r"(?<![0-9.])([0-3]?[0-9])[.]([01]?[0-9])[.](20[0-9][0-9])(?![0-9])")


def article_date_plus(page: "Page", title: str) -> tuple[str | None, str]:
    """Wider publish-date extraction: any JSON-LD datePublished, more meta tags, any <time datetime>, or a visible
    date within 300 characters after the headline (Norwegian/English month names or dd.mm.yyyy)."""
    for block in page.jsonld:
        for node in walk_json(block):
            d = iso_date(node.get("datePublished"))
            if d:
                return d, "jsonld_datepublished_any"
    meta = page.soup.select_one('meta[property="og:article:published_time"], meta[name="pubdate"], meta[name="publish-date"], '
                                'meta[name="dc.date"], meta[name="DC.date.issued"], meta[property="article:published"]')
    if meta and iso_date(meta.get("content")):
        return iso_date(meta.get("content")), "meta_published_time_any"
    t = page.soup.select_one("time[datetime]")
    if t and iso_date(t.get("datetime")):
        return iso_date(t.get("datetime")), "html_time_element_any"
    text = page.text
    at = text.find(title[:40]) if title else -1
    window = text[at + len(title[:40]): at + len(title[:40]) + 300] if at >= 0 else ""
    m = TEXT_DATE.search(window)
    if m:
        day, month, year = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        if 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}", "visible_date_after_headline"
    m = NUM_DATE.search(window)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}", "visible_date_after_headline"
    return None, ""


DEMO_TITLES = {"hello world", "hei verden", "sample page", "eksempelside", "hello world!", "test", "test post", "testinnlegg",
               "velkommen til wordpress", "welcome to wordpress", "this is the title of your first post", "this is your first post",
               # E54 (main2 fresh16): Wix blog template posts (campsoon.no)
               "now you can blog from everywhere", "manage your blog from your live site", "grow your blog community",
               "design a stunning blog", "getting started with wix blog", "travel the world with wix", "how to make a blog"}
LATIN_WORDS = {"lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "quid", "igitur", "ego", "quoque", "semper",
               "enim", "ita", "inquit", "beatum", "dubitamus", "adsumit", "sed", "quae", "nam", "etiam", "vero", "tamen", "quidem", "autem"}


# main2 fresh9 audit: theme demo posts in Latin filler without the words "lorem ipsum" (hijabhus.no "Vivamus vestibulum",
# "Sed pretium, ligula ...") or in Moby-Dick filler text (fjordmegler.no real-estate theme)
LATIN_FILLER = {"pretium", "ligula", "sollicitudin", "laoreet", "viverra", "tortor", "suspendisse", "potenti", "egestas",
                "vulputate", "volutpat", "luctus", "augue", "morbi", "faucibus", "gravida", "praesent", "hendrerit",
                "vestibulum", "vivamus", "pellentesque", "fusce", "donec", "curabitur", "nullam", "aliquam", "phasellus",
                "maecenas", "aenean", "tincidunt", "ullamcorper", "fringilla", "consequat", "elementum", "adipiscing",
                "consectetur", "porttitor", "dignissim", "malesuada", "venenatis", "sodales", "blandit", "lacinia",
                "tristique", "mauris", "interdum", "scelerisque", "bibendum", "posuere", "lectus", "molestie", "nibh",
                "ultrices", "ultricies", "rhoncus", "mattis", "euismod", "condimentum", "sagittis", "efficitur",
                "imperdiet", "convallis", "feugiat", "accumsan", "suscipit"}
FILLER_NAMES = {"queequeg", "ishmael", "pequod"}


def filler_text(text: str) -> bool:
    """Theme/demo placeholder body: lorem ipsum, distinctive Latin filler words, Moby-Dick filler, or a site builder's
    sample-post wording (E54, main2 fresh16: "This is a subtitle for your new post" on informamedica.no)."""
    words = set(tokens(text))
    folded = fold(text)
    return ("lorem ipsum" in folded or "dolor sit amet" in folded or len(words & LATIN_FILLER) >= 4
            or len(words & FILLER_NAMES) >= 1 or any(p in folded for p in BUILDER_SAMPLE_PHRASES))


BUILDER_SAMPLE_PHRASES = ("this is a subtitle for your new post", "this is the excerpt for your", "create a blog post subtitle",
                          "welcome to your blog post", "use this space to connect with your readers")


NOT_POST_TYPES = re.compile(r"^/(rl_gallery|gallery|galleri|gallerier|photogallery|photo-gallery|fotogalleri|bildegalleri|album|albums|attachment|slider|slider_responsive|sliders|product|products|produkt|produkter|vare|varer|shop|ledig-stilling|ledige-stillinger|stilling|stillinger|jobb|jobs|job|karriere|career|careers|vacancies|tag|tags|category|kategori|author|forfatter)(/|$)", re.I)  # main2 fresh4 audit; job ads (fresh14 netpower.no) are hiring, not posts

# Evergreen pages that WordPress themes keep as posts and list in the feed (lane 10b audit: "Om oss" on
# gravferdsbyraene.no, "Personvernerklæring" on krageroenergi.no) and page-builder default titles ("Elementor #4080").
EVERGREEN_TITLES = {"om oss", "om", "about", "about us", "personvern", "personvernerklaering", "personvernerklaering og cookies",
                    "privacy", "privacy policy", "cookies", "cookie policy", "informasjonskapsler", "kontakt", "kontakt oss",
                    "contact", "contact us", "forside", "hjem", "home", "vilkar", "salgsbetingelser", "terms", "terms and conditions"}
PRODUCT_FEED = re.compile(r"(^|/)(produkt|produkter|products?|shop|nettbutikk|varer)(/|\.|$)", re.I)


TAG_ARCHIVE = re.compile(r"/(tag|tags|tagged|tag-arkiv|[a-z0-9]+(?:-[a-z0-9]+)*-tag)(/|$)", re.I)


def is_demo_item(item: dict) -> bool:
    """Theme/demo placeholder posts (WordPress 'Hello world', Latin lorem-ipsum slugs) are not company activity."""
    title = " ".join(tokens(item.get("title")))
    first = " ".join(tokens(re.split(r"\s+[-–|]\s+", str(item.get("title") or ""))[0]))
    if first in DEMO_TITLES:
        return True  # E51 (main2 fresh15): "Hei, verden! - Haugli Eiendom"
    if title in DEMO_TITLES or "lorem ipsum" in title or title in EVERGREEN_TITLES or re.fullmatch(r"elementor \d+", title):
        return True
    if re.fullmatch(r"(test|testing|testinnlegg|demo|draft|utkast)( \d+)?", title):  # main2 fresh14: kdtrading.no "Test 2"
        return True
    if TAG_ARCHIVE.search(urllib.parse.urlparse(item.get("url") or "").path):
        return True  # E58 (main2 fresh19): /blogs/x/tagged/y (Shopify), /blog/tag/y, /mfn-news-tag/ archives
    slug = tokens(urllib.parse.urlparse(item.get("url") or "").path)
    return sum(1 for t in slug if t in LATIN_WORDS or t in LATIN_FILLER) >= 2 or sum(1 for t in tokens(title) if t in LATIN_WORDS or t in LATIN_FILLER) >= 2


def sitemap_urls(fetcher: Fetcher, base: str, domain: str, budget: int = 3) -> list[tuple[str, str | None]]:
    seeds = fetcher.sitemaps_from_robots(base) or [urllib.parse.urljoin(base, "/sitemap.xml")]
    out: list[tuple[str, str | None]] = []
    queue = list(dict.fromkeys(seeds))[:3]
    seen = set()
    while queue and budget > 0:
        sm = queue.pop(0)
        if sm in seen or not same_site(sm, domain):
            continue
        seen.add(sm)
        budget -= 1
        resp = fetcher.get(sm, max_bytes=3_000_000, accept="application/xml,text/xml,*/*")
        if not resp.ok:
            continue
        try:
            root = ET.fromstring(resp.body)
        except ET.ParseError:
            continue
        tag = root.tag.split("}")[-1]
        for node in root:
            loc = next((c.text for c in node if c.tag.split("}")[-1] == "loc"), None)
            lastmod = next((c.text for c in node if c.tag.split("}")[-1] == "lastmod"), None)
            if not loc:
                continue
            if tag == "sitemapindex":
                if any(h in fold(loc) for h in ("post", "news", "nyhet", "artik", "blog")) or len(queue) < 1:
                    queue.insert(0, loc.strip())
            else:
                out.append((loc.strip(), iso_date(lastmod)))
    return out


def is_section_index(url: str) -> bool:
    segs = [x for x in urllib.parse.urlparse(url).path.split("/") if x]
    return len(segs) <= 2 and bool(segs) and any(h in fold(segs[-1]) for h in NEWS_HINTS) and len(fold(segs[-1])) <= 12


def crawl_verified_site(fetcher: Fetcher, profile: dict, attempt: dict, *, today: str, max_articles: int = 4) -> dict:
    pages: list[Page] = list(attempt.get("pages") or [])
    domain = attempt["domain"]
    plus = bool(profile.get("_article_dates_plus"))
    home = pages[0]
    log: list[dict] = []
    # a declared homepage that is a section of a larger domain (hydac.com/no-no/, a hotel page on strawberry.no): posts
    # and profiles outside that section are the group's or chain's (lane 10b audit)
    # (also when another domain forwards into the section: clarionosloairport.no -> strawberry.no/hotell/.../)
    start = attempt.get("url") or home.url
    scope = site_scope(home.url) if (site_scope(start) or registered_domain(start) != registered_domain(home.url)) else ""

    def get_page(url: str, kind: str) -> Page | None:
        if fetcher.remaining() < 8:
            return None
        page, r = fetch_page(fetcher, url, kind)
        log.append({"url": url, "status": r.status, "error": r.error, "kind": kind})
        if page and same_site(page.url, domain):
            pages.append(page)
            return page
        return None

    # careers pages
    jobs: list[dict] = []
    careers_pages = []
    hiring_checks: list[dict] = []
    nav_links: list[dict] = []
    if profile.get("_site_hiring"):  # lane 7: word-boundary careers detection + free-text positions
        from .site_hiring import crawl_careers
        res = crawl_careers(list(pages), domain, get_page, org=profile["organisation_number"], today=today, name=profile.get("name"))
        jobs, hiring_checks, nav_links = res["jobs"], res["checks"], res["nav_links"]
        # E55 (main2 fresh17): FINN ads linked from a careers page stay listed long after their deadline ("Fristen er
        # utløpt" behind zolve.no's and akmekaniske.no's links); read each linked FINN ad once (robots.txt allows
        # /job/) and drop expired ones
        from .site_hiring import board_ad_expired
        finn = [j for j in jobs if j.get("method") == "careers_page_link_to_board_ad" and "finn.no" in urllib.parse.urlparse(j["url"]).netloc][:5]
        for j in finn:
            if fetcher.remaining() < 8:
                break
            ad, r = fetch_page(fetcher, j["url"], "board_ad")
            log.append({"url": j["url"], "status": r.status, "error": r.error, "kind": "board_ad"})
            if ad is not None and board_ad_expired(ad, today):
                jobs = [x for x in jobs if x is not j]
        careers_pages = [p for p in pages if p.url in res["careers_pages"]]
    for url in ([] if profile.get("_site_hiring") else section_links(pages, domain, CAREER_HINTS, 1)):
        page = get_page(url, "careers")
        if page:
            careers_pages.append(page)
            for job in jobs_from_page(page, domain):
                job.update(found_on=page.url, sha256=page.resp.sha256, retrieved_at=page.resp.retrieved_at)
                jobs.append(job)
    # dated activity: RSS/Atom feeds, WordPress REST, then article pages from news section / sitemap
    activity: dict[str, dict] = {}

    def add(item: dict, resp: Response, source_url: str) -> None:
        # a feed/REST item is a post even when its slug looks like an index ("/produktnyhet/" on automasjonsikkerhet.no)
        index = is_section_index(item["url"]) and item.get("method") not in ("rss_feed", "wordpress_rest_posts")
        if is_demo_item(item) or index or not in_scope(item["url"], scope) or NOT_POST_TYPES.search(urllib.parse.urlparse(str(item.get("url") or "")).path):
            return
        key = item["url"].split("#")[0].rstrip("/")
        if key not in activity:
            activity[key] = {**item, "source_url": source_url, "sha256": resp.sha256, "retrieved_at": resp.retrieved_at}

    for feed in list(dict.fromkeys(home.feeds))[:1]:
        if PRODUCT_FEED.search(urllib.parse.urlparse(feed).path):
            log.append({"url": feed, "status": None, "error": "product feed (webshop items, not posts)", "kind": "feed"})
            continue
        if same_site(feed, domain) and fetcher.remaining() > 8:
            r = fetcher.get(feed, max_bytes=2_000_000, accept="application/rss+xml,application/atom+xml,application/xml,*/*")
            log.append({"url": feed, "status": r.status, "error": r.error, "kind": "feed"})
            if r.ok:
                for it in activity_from_feed(r, domain, today)[:10]:
                    add(it, r, feed)
    generator = home.soup.select_one('meta[name="generator"]')
    is_wp = "wp-content" in home.resp.text()[:200_000] or (generator is not None and "wordpress" in str(generator.get("content") or "").lower())
    lang_root = urllib.parse.urlparse(home.url).path.strip("/").lower() in {"nb", "no", "nn", "en", "sv", "se", "da", "dk", "de", "fi", "nb-no", "en-gb", "en-us", "sv-se"}
    lang_prefix = "/" + urllib.parse.urlparse(home.url).path.strip("/").lower() + "/" if lang_root else ""
    if not activity and is_wp and fetcher.remaining() > 8:
        api = urllib.parse.urljoin(home.url, "/wp-json/wp/v2/posts?per_page=10&_fields=link,date,date_gmt,title,excerpt")
        r = fetcher.get(api, max_bytes=1_000_000, accept="application/json")
        log.append({"url": api, "status": r.status, "error": r.error, "kind": "wp_posts"})
        if r.ok:
            for it in activity_from_wp(r, domain, today):
                if lang_prefix and not urllib.parse.urlparse(it["url"]).path.lower().startswith(lang_prefix):
                    continue  # main2: on a site verified at /nb/ only posts under /nb/ (enviroprocess.com: Swedish posts elsewhere)
                add(it, r, api)
    group_site = bool(profile.get("_site_news") and len(profile.get("_group_brand_siblings") or []) >= 3)
    if profile.get("_site_news") and len(activity) < 3 and not group_site:  # lane 8: anchor-text sections, listing dates, sitemap posts
        from .site_news import canonical_key, collect_site_news
        seen = {canonical_key(k) for k in activity}
        for it in collect_site_news(fetcher, pages, domain, today, get_page, log):
            if is_demo_item(it) or is_section_index(it["url"]) or not in_scope(it["url"], scope):
                continue
            if canonical_key(it["url"]) not in seen:
                seen.add(canonical_key(it["url"]))
                activity[it["url"]] = it
    if not activity and not group_site:  # legacy fallback (also after site_news found nothing)
        article_urls: list[str] = []
        news_pages = [p for p in (get_page(u, "news_index") for u in section_links(pages, domain, NEWS_HINTS, 1)) if p]
        for npage in news_pages:
            base_path = urllib.parse.urlparse(npage.url).path.rstrip("/")
            for url, text, _ in npage.links:
                path = urllib.parse.urlparse(url).path.rstrip("/")
                if same_site(url, domain) and base_path and path.startswith(base_path + "/") and len(text) > 12:
                    article_urls.append(url.split("#")[0])
                elif (plus and same_site(url, domain) and len(text) > 12 and path.count("/") >= 2 and path != base_path
                      and any(h in fold(path) for h in NEWS_HINTS)):
                    article_urls.append(url.split("#")[0])
        if not article_urls:
            sm = sitemap_urls(fetcher, home.url, domain)
            log.append({"url": "sitemap", "status": len(sm), "kind": "sitemap"})
            newsy = [(u, d) for u, d in sm if any(h in fold(urllib.parse.urlparse(u).path) for h in NEWS_HINTS) and urllib.parse.urlparse(u).path.count("/") >= 2]
            newsy.sort(key=lambda x: x[1] or "", reverse=True)
            article_urls = [u for u, _ in newsy]
        # a section index ("/news/", "/nyheter/") is a listing, not an article (lane 10 audit: eprod.no/news/ was dated by
        # the first <time> on the listing, i.e. a theme demo post)
        article_urls = [u for u in article_urls if not is_section_index(u)]
        for url in list(dict.fromkeys(article_urls))[:max_articles]:
            page = get_page(url, "article")
            if page:
                it = activity_from_article(page, today, plus=plus)
                if it:
                    add(it, page.resp, page.url)
    home_path = urllib.parse.urlparse(home.url).path.strip("/").lower()
    sub_prefix = lang_prefix or ("" if home_path in ROOTISH_PATHS or re.search(r"\.(php|html?|aspx?|jsp|cfm)$", home_path) else f"/{home_path}/")
    if sub_prefix and activity:  # E32: a site verified on a sub-path (ys.no/medlemsfordeler/, nowa.se/no/) keeps only posts under it
        activity = {k: v for k, v in activity.items() if urllib.parse.urlparse(v.get("url") or k).path.lower().startswith(sub_prefix)}
    if (attempt.get("verdict") or {}).get("group_listing"):
        # E42 (main2 fresh12): the site proves us only by listing our number among several group companies
        # (tieto.com: "Tieto Norway AS ... Business ID: 933012867" next to the group's other entities); its posts,
        # profiles and careers pages are the group's
        log.append({"url": "site:group_listing", "status": len(activity), "kind": "site_news"})
        activity = {}
        jobs = []
    if group_site:  # lane 8: a group brand site's posts cannot be attributed to this legal entity
        log.append({"url": "site_news:group_brand_site", "status": len(activity), "kind": "site_news", "siblings": profile["_group_brand_siblings"][:5]})
        activity = {}
    social = [] if (attempt.get("verdict") or {}).get("group_listing") else social_from_pages(pages, profile, domain, scope)
    _brand = public_brand(home.site_name, home.title, profile.get("name"), domain)
    return {"domain": domain, "pages": [{"url": p.url, "kind": p.kind, "title": p.title, "sha256": p.resp.sha256, "retrieved_at": p.resp.retrieved_at} for p in pages],
            "social_profiles": social, "jobs": jobs, "careers_pages": [p.url for p in careers_pages], "hiring_checks": hiring_checks, "nav_links": nav_links,
            "activity": sorted(activity.values(), key=lambda a: a["date"], reverse=True)[:10],
            "brand": {"title": home.title, "site_name": home.site_name, "description": home.description,
                      "public_brand": _brand, "self_description": self_description(home.description, profile.get("name"), _brand)},
            "crawl_log": log}
