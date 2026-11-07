"""Dated activity from a verified company site (lane 8).

Called from `footprint.crawl_verified_site` when the strategy flag `site_news` is on (profile key `_site_news`).
Only ever runs on a site that already proved the exact legal entity, so every item is company-owned content.

Discovery, cheapest first:
  1. news-like sections linked from the fetched pages, found by anchor text or path segment
     (nyheter, aktuelt, blogg, fagstoff, presse, artikler, news, ...; never "nyhetsbrev"/newsletter);
  2. dated entries on the section listing (and on the homepage when they point into a section): an entry is one
     container holding exactly one post link and a date (`<time datetime>` or a visible date);
  3. post pages without a listing date: JSON-LD datePublished, `article:published_time`, `<time>`, date-classed
     elements, then a visible date next to the `<h1>`;
  4. sitemap URLs that look like posts (news path segment, or listed in a WordPress post sitemap). `lastmod` only
     orders candidates: it is a modification date, so it is never published as the post date.

Dates: ISO, day-first numerics (`4/6/26`, `04.06.2026`, `4-6-2026`), Norwegian and English month names
(`4. juni 2026`, `4 jun. 2026`, `June 4, 2026`). Dates in the future or before 2000 are dropped. No date, no claim.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Callable

from bs4 import Tag

from .footprint import NOT_POST_TYPES, Page, filler_text, fold, is_demo_item, iso_date, plausible_date, same_site, type_set, walk_json

# --------------------------------------------------------------------------- dates
MONTHS = {
    "januar": 1, "january": 1, "jan": 1, "februar": 2, "february": 2, "feb": 2, "mars": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11, "desember": 12, "december": 12, "des": 12, "dec": 12,
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
ISO_RE = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
NUM_RE = re.compile(r"(?<![\d.,/:-])(\d{1,2})([./-])(\d{1,2})\2(20\d\d|\d\d)(?![\d,]|[./-]\d)")
# "4. november 2026", "4 nov. 2026" and the space-less "04.november.2026" (lane 10b audit: taelektronikk.no listings,
# where the unparsed listing date let a date from the teaser text win)
DMY_RE = re.compile(r"(?<![\w.])(\d{1,2})(?:\.\s*|\s+)(" + _MON + r")(?:\.\s*|,?\s+)(20\d\d)(?!\d)", re.I)
MDY_RE = re.compile(r"(?<!\w)(" + _MON + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)(?!\d)", re.I)
# Java/ctime style "Thu Jul 30 10:00:00 EEST 2020" (Liferay portals)
CTIME_RE = re.compile(r"(?<!\w)(?:mon|tue|wed|thu|fri|sat|sun)\s+(" + _MON + r")\s+(\d{1,2})\s+\d\d:\d\d(?::\d\d)?\s+(?:[A-Z]{2,5}\s+)?(20\d\d)(?!\d)", re.I)
# a date introduced by a label; "updated" labels give a last-updated date, not a publication date
LABEL_RE = re.compile(r"(?<!\w)(publisert|publ\.|postet|skrevet|opprettet|published|posted|posted on|dato|date|"
                      r"sist endret|sist oppdatert|oppdatert|endret|updated|last updated|last modified)\s*(?:den|on|:|·|-)?\s*:?\s*$", re.I)
UPDATE_LABELS = ("sist endret", "sist oppdatert", "oppdatert", "endret", "updated", "last updated", "last modified")


def _mk(y: int, m: int, d: int) -> str | None:
    try:
        return _date(y, m, d).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[str, str]]:
    """All dates in `text` as (iso, matched text), in reading order. Numeric dates are read day-first
    (Norwegian convention); a slash date whose second part exceeds 12 is read month-first."""
    found: list[tuple[int, str, str]] = []
    for m in ISO_RE.finditer(text):
        iso = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            found.append((m.start(), iso, m.group(0)))
    for m in NUM_RE.finditer(text):
        a, sep, b, y = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        year = int(y) if len(y) == 4 else 2000 + int(y)
        day, month = a, b
        if sep == "/" and b > 12 and a <= 12:
            day, month = b, a
        iso = _mk(year, month, day)
        if iso:
            found.append((m.start(), iso, m.group(0)))
    for m in DMY_RE.finditer(text):
        iso = _mk(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        if iso:
            found.append((m.start(), iso, m.group(0)))
    for m in MDY_RE.finditer(text):
        iso = _mk(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        if iso:
            found.append((m.start(), iso, m.group(0)))
    for m in CTIME_RE.finditer(text):
        iso = _mk(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        if iso:
            found.append((m.start(), iso, m.group(0)))
    found.sort()
    out, last_end = [], -1
    for start, iso, raw in found:  # drop a match nested in an earlier one (e.g. "Jul 30 ... 2020" inside a ctime date)
        if start >= last_end:
            out.append((iso, raw))
            last_end = start + len(raw)
    return out


def labelled_date(text: str, today: str) -> tuple[str, str, str] | None:
    """First date in `text` introduced by a publish label, else by an update label: (iso, label + date, basis)."""
    text = " ".join(str(text or "").split())
    updated = None
    for iso, raw in find_dates(text):
        at = text.find(raw)
        m = LABEL_RE.search(text[max(0, at - 30): at])
        if m and "2000-01-01" <= iso <= today:
            if m.group(1).lower() not in UPDATE_LABELS:
                return iso, f"{m.group(0).strip()} {raw}", "published"
            updated = updated or (iso, f"{m.group(0).strip()} {raw}", "last_updated")
    return updated


def parse_date(text: Any) -> tuple[str, str] | None:
    hits = find_dates(" ".join(str(text or "").split()))
    return hits[0] if hits else None


# --------------------------------------------------------------------------- sections and post URLs
NEWS_WORDS = re.compile(
    r"(^|[^a-z])(nyheter|nyhet|nyheiter|nytt|siste nytt|aktuelt|aktuelle saker|blogg|blog|news|fagstoff|fagartikler|presse|"
    r"pressemeldinger|press|artikler|artikkel|articles|innlegg|saker|insights|stories|updates|hva skjer|uutiset)([^a-z]|$)")
NEWS_DENY = re.compile(r"nyhetsbrev|newsletter|subscribe|abonner|pamelding|presseb|pressefoto|press kit|presskit|login|logg inn")
SEGMENT_WORDS = ("nyheter", "nyhet", "nyheiter", "aktuelt", "blogg", "blog", "news", "fagstoff", "fagartikler", "presse",
                 "pressemeldinger", "press", "artikler", "artikkel", "articles", "innlegg", "posts", "post", "insights",
                 "siste-nytt", "sistenytt", "hva-skjer", "uutiset", "nyheitar", "aktuelle-saker")
NON_POST_SEGMENTS = {"category", "kategori", "kategorier", "tag", "tags", "stikkord", "emne", "author", "forfatter", "page", "side",
                     "feed", "search", "sok", "arkiv", "archive", "wp-content", "wp-json", "cdn-cgi", "kontakt", "contact"}
GENERIC_TEXT = {"les mer", "les mer her", "read more", "mer", "more", "les", "se mer", "les hele saken", "les artikkelen",
                "continue reading", "fortsett a lese", "les videre", "vis mer", "neste", "forrige", "next", "previous"}
POST_TYPES = {"NewsArticle", "Article", "BlogPosting", "Report", "PressRelease", "SocialMediaPosting", "AnalysisNewsArticle"}


def _segments(url: str) -> list[str]:
    return [fold(s) for s in urllib.parse.urlparse(url).path.split("/") if s]


def _in_chrome(a: Tag) -> bool:
    for parent in a.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in ("nav", "header", "footer"):
            return True
        marker = " ".join(parent.get("class") or []) + " " + str(parent.get("id") or "")
        if re.search(r"(^|[\s_-])(menu|navbar|nav|footer|header|breadcrumb)", marker.lower()):
            return True
    return False


def news_sections(pages: list[Page], domain: str, limit: int = 2) -> list[str]:
    """News-like section URLs linked from the fetched pages, ranked by anchor-text match then path match."""
    scored: dict[str, tuple[int, int]] = {}
    for p in pages:
        for url, text, _ in p.links:
            if urllib.parse.urlparse(url).fragment:
                continue  # "Aktuelt" -> /om-skolen#aktuelt names a block on a page, not a news section
            if not same_site(url, domain):
                continue
            segs = _segments(url)
            if len(segs) > 2 or any(s in NON_POST_SEGMENTS for s in segs):
                continue
            ftext = fold(text)
            path = " ".join(segs).replace("-", " ").replace("_", " ")
            if NEWS_DENY.search(ftext) or NEWS_DENY.search(path):
                continue
            score = None
            if len(ftext) <= 40 and NEWS_WORDS.search(ftext):
                score = 0
            elif segs and (segs[-1] in SEGMENT_WORDS or any(w in segs[-1].replace("_", "-").split("-") for w in SEGMENT_WORDS)):
                score = 1
            if score is None:
                continue
            key = (score, len(segs))
            if url not in scored or key < scored[url]:
                scored[url] = key
    ordered = sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])))
    out: list[str] = []
    for url, _ in ordered:
        norm = url.rstrip("/")
        if norm and all(norm != o.rstrip("/") for o in out):
            out.append(url)
        if len(out) >= limit:
            break
    return out


def is_post_url(url: str, domain: str, section_paths: list[str]) -> bool:
    """A same-site URL that sits below a news section (or under a news path segment) and is not an index page."""
    if not same_site(url, domain):
        return False
    path = urllib.parse.urlparse(url).path.rstrip("/")
    segs = _segments(url)
    if not segs or any(s in NON_POST_SEGMENTS for s in segs) or re.search(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|zip|mp4)$", path, re.I):
        return False
    if any(sp and path.startswith(sp + "/") and len(path) > len(sp) + 2 for sp in section_paths):
        return True
    return any(s in SEGMENT_WORDS for s in segs[:-1]) and len(segs[-1]) >= 4 and not (segs[-1].isdigit() and len(segs[-1]) < 5)


READ_TIME = re.compile(r"(?<!\w)\d+\s*(min(utt(er)?)?|minutes?)\.?(\s*(lesetid|lesing|read|lese))?(?!\w)", re.I)


def _title_for(a: Tag, container: Tag) -> str:
    """Headline of a listing entry: a heading inside the link or entry, else the link text without dates and
    reading-time labels."""
    for scope in (a, container):
        h = scope.find(["h1", "h2", "h3", "h4", "h5"])
        if h:
            text = " ".join(h.get_text(" ", strip=True).split())
            if len(text) >= 8:
                return text[:200]
    text = " ".join(a.get_text(" ", strip=True).split())
    for _, raw in find_dates(text):
        text = text.replace(raw, " ")
    text = " ".join(READ_TIME.sub(" ", text).split())
    text = re.sub(r"(?:\s+[·|•–-])+\s+", " ", f" {text} ").strip()  # separators left between removed parts
    text = re.sub(r"^(read more|les mer|les videre|continue reading)\b", "", text, flags=re.I).strip(" ·|-–•")
    if len(text) >= 12 and fold(text).strip(" .:>»›") not in GENERIC_TEXT:
        return text[:200]
    title = str(a.get("title") or a.get("aria-label") or "").strip()
    return title[:200] if len(title) >= 12 else ""


def _container_date(container: Tag, today: str, anchor: str = "") -> tuple[str, str] | None:
    for t in container.select("time"):
        d = iso_date(t.get("datetime"))
        shown = " ".join(t.get_text(" ", strip=True).split())
        if not d:
            hit = parse_date(shown)
            d = hit[0] if hit else None
        if d and plausible_date(d, today):
            return d, shown or str(t.get("datetime"))
    text = " ".join(container.get_text(" ", strip=True).split())
    anchor = " ".join(anchor.split())
    after_title = text.find(anchor) + len(anchor) + 30 if anchor and anchor in text else -1
    for iso, raw in find_dates(text):
        # a listing card shows its date at the start or the end; a date inside the teaser sentence is about the story
        # (lane 10b audit: taelektronikk.no front page, "Oppkjøpet ble gjennomført 3. august 2026" published as the date)
        at = text.find(raw)
        if plausible_date(iso, today) and (len(text) <= 160 or at <= max(60, after_title) or at + len(raw) >= len(text) - 40):
            return iso, raw
    return None


def _weak_candidate(url: str, text: str, domain: str) -> bool:
    """A content link on a news index that is not below the section path (flat post URLs such as /no/sesongens-nyheter).
    Fetched only after strong candidates, and dated only when the page is marked as an article or its breadcrumb
    links back to the news section."""
    segs = _segments(url)
    if not same_site(url, domain) or not segs or any(s in NON_POST_SEGMENTS for s in segs):
        return False
    if re.search(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|zip|mp4)$", url, re.I):
        return False
    read_more = fold(text).strip(" .:>»›") in GENERIC_TEXT
    return len(segs[-1]) >= 8 and ((segs[-1].count("-") >= 2 and len(text) >= 12) or read_more)


def listing_items(page: Page, domain: str, section_paths: list[str], today: str, *, weak: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Dated entries on a listing page, and the post URLs seen without a date (to fetch next). When `weak` is a list,
    flat content links (see `_weak_candidate`) are appended to it."""
    candidates: dict[str, list[Tag]] = {}
    for url, text, a in page.links:
        url = url.split("#")[0]
        if url.rstrip("/") == page.url.split("#")[0].rstrip("/") or _in_chrome(a):
            continue
        if is_post_url(url, domain, section_paths):
            candidates.setdefault(url, []).append(a)
        elif weak is not None and _weak_candidate(url, text, domain) and url not in weak:
            weak.append(url)
    items: dict[str, dict] = {}
    undated: list[str] = []
    for url, anchors in candidates.items():
        found = None
        for a in anchors:
            node: Tag = a
            for _ in range(5):
                parent = node.parent
                if not isinstance(parent, Tag) or parent.name in ("body", "html", "main"):
                    break
                node = parent
                urls = {urllib.parse.urljoin(page.url, str(x.get("href") or "")).split("#")[0] for x in node.select("a[href]")}
                if len({u for u in urls if u in candidates}) > 1:
                    break  # the container spans several posts: a date here may belong to a neighbour
                hit = _container_date(node, today, a.get_text(" ", strip=True))
                if hit:
                    title = _title_for(a, node)
                    if title:
                        found = (hit, title)
                    break
            if found:
                break
        m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", urllib.parse.urlparse(url).path)
        url_iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        if found and url_iso and plausible_date(url_iso, today) and url_iso != found[0][0]:
            # E44 (main2 fresh13): a dated permalink (/2026/05/12/) is the publication date; a different date in the
            # card is the title's or the teaser's ("... går av som CEO 1. august 2026" on linak.no)
            found = ((url_iso, m.group(0)), found[1])
        elif found and fold(found[0][1]).strip() and fold(found[0][1]).strip() in fold(found[1]):
            found = None  # E44: the only date in the card is part of the title, not a publication date
        if found:
            (iso, raw), title = found
            items[url] = {"title": title, "url": url, "date": iso, "method": "news_listing_date", "date_basis": "published",
                          "span": f"{raw} | {title}"[:300]}
        else:
            undated.append(url)
    return list(items.values()), undated


def iri_to_uri(url: str) -> str:
    """Percent-encode non-ASCII characters (Norwegian letters in slugs) so the URL can be requested."""
    parts = urllib.parse.urlsplit(url)
    try:
        host = parts.netloc.encode("idna").decode("ascii") if not parts.netloc.isascii() else parts.netloc
    except UnicodeError:
        host = parts.netloc
    q = lambda v: urllib.parse.quote(v, safe="/%:@!$&'()*+,;=-._~?")
    return urllib.parse.urlunsplit((parts.scheme, host, q(parts.path), q(parts.query), ""))


def _looks_like_post(page: Page) -> bool:
    # og:type=article is not used: Yoast and others set it on every non-home page. article:published_time is
    # only emitted for posts.
    if page.soup.select_one('meta[property="article:published_time"]'):
        return True
    return any(type_set(node) & POST_TYPES for block in page.jsonld for node in walk_json(block))


def _request_time(value: Any, retrieved_at: str) -> bool:
    """True when a machine-readable publish timestamp lies within 6 hours of our own fetch: the site stamps the
    render time, not the publication time (seen on Shopify-style blogs)."""
    text = str(value or "").strip().replace(" ", "T", 1).replace("Z", "+00:00")
    if not re.match(r"20\d\d-\d\d-\d\dT\d\d:\d\d", text):
        return False
    try:
        published = datetime.fromisoformat(text[:25] if "+" in text[19:] or text[19:20] == "-" else text[:19])
        fetched = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone(timedelta(hours=1)))  # Norwegian local time
    return abs((fetched - published).total_seconds()) < 6 * 3600


def canonical_key(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"


def article_item(page: Page, today: str, *, post_like_url: bool, news_path: bool = False) -> dict | None:
    """One dated item for a post page, or None. A page that is neither marked as an article nor under a news
    path is not a post, whatever dates it carries."""
    if not (post_like_url or _looks_like_post(page)):
        return None
    if NOT_POST_TYPES.search(urllib.parse.urlparse(page.url).path):
        return None  # main2 fresh4 audit: gallery / slider / product custom post types are not news posts
    date, method, raw = None, "", ""
    for block in page.jsonld:
        for node in walk_json(block):
            types = type_set(node)
            if types & POST_TYPES or (news_path and "WebPage" in types):
                d = iso_date(node.get("datePublished"))
                if d and plausible_date(d, today) and not _request_time(node.get("datePublished"), page.resp.retrieved_at):
                    date, method, raw = d, "jsonld_article" if types & POST_TYPES else "jsonld_webpage_post_url", f"datePublished={node.get('datePublished')}"
                    break
        if date:
            break
    if not date:
        meta = page.soup.select_one('meta[property="article:published_time"], meta[itemprop="datePublished"], meta[name="date"], meta[name="publish-date"]')
        d = iso_date(meta.get("content")) if meta else None
        if d and plausible_date(d, today) and not _request_time(meta.get("content"), page.resp.retrieved_at):
            date, method, raw = d, "meta_published_time", f"published_time={meta.get('content')}"
    h1 = page.soup.select_one("h1")
    scope = page.soup.select_one("article") or page.soup.select_one("main") or (h1.parent if h1 else None)
    if not date and scope is not None:
        t = scope.select_one("time[datetime]")
        d = iso_date(t.get("datetime")) if t else None
        if d and plausible_date(d, today) and not _request_time(t.get("datetime"), page.resp.retrieved_at):
            date, method, raw = d, "html_time_element", f"<time datetime={t.get('datetime')}>"
    basis = "published"
    body = " ".join((scope.get_text(" ", strip=True) if scope is not None else page.text).split())[:8000]
    if filler_text(body):
        return None  # main2 fresh9 audit: a theme demo page in filler text (agd-corporation.com/carpenter, altimo.no artikkel-3)
    labelled = labelled_date(body, today) if not date else None
    if labelled and labelled[2] == "published":
        date, method, raw = labelled[0], "labelled_publish_date", labelled[1]
    if not date and scope is not None:
        for el in scope.select('[class*="date"], [class*="dato"], [class*="published"], [class*="meta"], [itemprop="datePublished"]')[:6]:
            text = " ".join(el.get_text(" ", strip=True).split())[:120]
            hit = parse_date(text)
            if hit and plausible_date(hit[0], today):
                lab = labelled_date(text, today)
                date, method, raw = hit[0], "visible_date_element", hit[1]
                if lab and lab[0] == hit[0] and lab[2] == "last_updated":
                    basis, raw = "last_updated", lab[1]
                break
    if not date and h1 is not None:
        # visible date next to the headline: the headline's container text, capped at 300 characters
        near = " ".join((h1.parent or h1).get_text(" ", strip=True).split())[:300]
        hit = parse_date(near)
        if hit and plausible_date(hit[0], today):
            lab = labelled_date(near, today)
            date, method, raw = hit[0], "visible_date_near_headline", hit[1]
            if lab and lab[0] == hit[0] and lab[2] == "last_updated":
                basis, raw = "last_updated", lab[1]
    if not date and labelled:  # only a "last updated" date is shown
        date, method, raw, basis = labelled[0], "labelled_update_date", labelled[1], "last_updated"
    if not date or basis == "last_updated":
        return None  # a "last updated" stamp is not a publication date (main2 audit: static info pages re-stamped today)
    title = " ".join((h1.get_text(" ", strip=True) if h1 else page.title).split())[:200]
    if len(title) < 4:
        return None
    return {"title": title, "url": page.url, "date": date, "method": method, "span": f"{raw} | {title}"[:300], "date_basis": basis}


def breadcrumb_in_section(page: Page, section_paths: list[str]) -> bool:
    """The page's breadcrumb links to a news section (flat post URLs such as /gevinstlekkasje under 'Fagstoff')."""
    crumbs = page.soup.select('[class*="breadcrumb"], [id*="breadcrumb"], nav[aria-label*="read" i], nav[aria-label*="smule" i]')
    for crumb in crumbs[:3]:
        for a in crumb.select("a[href]"):
            path = urllib.parse.urlparse(urllib.parse.urljoin(page.url, str(a.get("href")))).path.rstrip("/")
            if path and path in section_paths:
                return True
    return False


def post_sitemap_urls(fetcher: Any, base: str, domain: str, budget: int = 3) -> list[tuple[str, str | None, bool]]:
    """(url, lastmod, from_post_sitemap) from robots-declared sitemaps or /sitemap.xml; post/news child sitemaps first."""
    seeds = fetcher.sitemaps_from_robots(base) or [urllib.parse.urljoin(base, "/sitemap.xml")]
    queue = list(dict.fromkeys(seeds))[:3]
    seen: set[str] = set()
    out: list[tuple[str, str | None, bool]] = []
    while queue and budget > 0 and fetcher.remaining() > 8:
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
        is_index = root.tag.split("}")[-1] == "sitemapindex"
        postish = bool(re.search(r"(^|[/_-])(post|posts|news|nyhet|nyheter|artik|blog|blogg|aktuelt|fagstoff|presse)", fold(urllib.parse.urlparse(sm).path)))
        children = []
        for node in root:
            loc = next((c.text for c in node if c.tag.split("}")[-1] == "loc"), None)
            lastmod = next((c.text for c in node if c.tag.split("}")[-1] == "lastmod"), None)
            if not loc:
                continue
            if is_index:
                p = fold(urllib.parse.urlparse(loc).path)
                if re.search(r"(^|[/_-])(post|posts|news|nyhet|nyheter|artik|blog|blogg|aktuelt|fagstoff|presse)", p) and "page" not in p.split("/")[-1]:
                    children.insert(0, loc.strip())
            else:
                out.append((loc.strip(), iso_date(lastmod), postish))
        queue = children[:2] + queue
    return out


# --------------------------------------------------------------------------- group brand sites
PASSIVE_GROUP_TOKENS = {"holding", "invest", "investering", "eiendom", "eiendommer", "property", "properties", "kapital", "capital"}


def group_brand_siblings(name: str, organisation_number: str, group_value: Any) -> list[str]:
    """Other operating entities in the registered group whose name carries this company's brand token.
    Three or more mean the site speaks for a group (NORCONSULT NORGE AS on norconsult.no posts group results and
    sister-company news), so a post cannot be attributed to this legal entity."""
    from .footprint import core_tokens

    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for org_key, name_key in (("organisasjonsnummer", "navn"), ("parentOrganisasjonsnummer", "parentNavn")):
                if node.get(name_key):
                    found[str(node.get(org_key) or node[name_key])] = str(node[name_key])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(group_value)
    mine = core_tokens(name)
    if not mine:
        return []
    out = []
    for org, other in found.items():
        toks = core_tokens(other)
        if org == organisation_number or fold(other) == fold(name) or mine[0] not in toks or PASSIVE_GROUP_TOKENS & set(toks):
            continue
        out.append(other)
    return sorted(set(out))


def site_news_profile(profile: dict, official: dict) -> dict:
    """Profile flags for `crawl_verified_site`: `_site_news` on, plus the group-brand siblings from the register."""
    group = official.get("group") or {}
    value = group.get("value") if group.get("status") == "available" else None
    siblings = group_brand_siblings(str(profile.get("name") or ""), str(profile.get("organisation_number") or ""), value) if value else []
    return {**profile, "_site_news": True, "_group_brand_siblings": siblings, "_group_orgnrs": group_orgnrs(value)}


def group_orgnrs(group_value: Any) -> list[str]:
    """Every organisation number in the registered group (parents, children, siblings)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("organisasjonsnummer", "parentOrganisasjonsnummer"):
                if node.get(key):
                    found.add(str(node[key]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(group_value)
    return sorted(found)


# --------------------------------------------------------------------------- driver
def collect_site_news(fetcher: Any, pages: list[Page], domain: str, today: str, get_page: Callable[[str, str], Page | None],
                      log: list[dict], *, max_sections: int = 2, max_articles: int = 5, max_items: int = 10) -> list[dict]:
    """Dated posts from a verified site. Each item: title, url, date, method, span, source_url, sha256, retrieved_at."""
    home = pages[0]
    out: dict[str, dict] = {}

    def keep(item: dict, page: Page) -> None:
        if is_demo_item(item) or not plausible_date(item.get("date"), today):
            return
        key = canonical_key(item["url"])
        if key not in out:
            out[key] = {**item, "source_url": page.url, "sha256": page.resp.sha256, "retrieved_at": page.resp.retrieved_at}

    sections = news_sections(pages, domain, max_sections)
    section_paths = [urllib.parse.urlparse(u).path.rstrip("/") for u in sections]
    log.append({"url": "site_news:sections", "status": len(sections), "kind": "site_news", "sections": sections})
    undated: list[str] = []
    listing_pages = [home]
    for url in sections:
        if fetcher.remaining() < 10:
            break
        p = get_page(iri_to_uri(url), "news_index")
        if p and same_site(p.url, domain):
            listing_pages.append(p)
            final_path = urllib.parse.urlparse(p.url).path.rstrip("/")
            if final_path and final_path not in section_paths:
                section_paths.append(final_path)
    weak: list[str] = []
    for p in listing_pages:
        items, missing = listing_items(p, domain, section_paths, today, weak=weak if p is not home else None)
        for it in items:
            keep(it, p)
        undated += [u for u in missing if u not in undated]
    fetch_queue = [u for u in undated if canonical_key(u) not in out]
    fetch_queue += [u for u in weak if u not in fetch_queue and canonical_key(u) not in out][:3]
    from_sitemap: set[str] = set()
    if len(out) + len(fetch_queue) < 3 and fetcher.remaining() > 15:
        sm = post_sitemap_urls(fetcher, home.url, domain)
        log.append({"url": "site_news:sitemap", "status": len(sm), "kind": "site_news"})
        # strong: in a post sitemap or below a news path; weak: a news word in the slug (dated only if marked as article)
        posts = [(u, lm, (postish and len(_segments(u)) >= 1) or is_post_url(u, domain, section_paths)) for u, lm, postish in sm]
        posts = [(u, lm, strong) for u, lm, strong in posts if strong or (
            _segments(u) and NEWS_WORDS.search(_segments(u)[-1].replace("-", " ")) and not NEWS_DENY.search(_segments(u)[-1]))]
        posts.sort(key=lambda x: (x[2], x[1] or ""), reverse=True)  # lastmod: ordering only
        for u, _, strong in posts:
            if canonical_key(u) not in out and u not in fetch_queue:
                fetch_queue.append(u)
                if strong:
                    from_sitemap.add(u)
    fetched = 0
    for url in fetch_queue:
        if fetched >= max_articles or len(out) >= max_items or fetcher.remaining() < 10:
            break
        fetched += 1
        p = get_page(iri_to_uri(url), "article")
        if not p or not same_site(p.url, domain):
            continue
        news_path = is_post_url(p.url, domain, section_paths) or breadcrumb_in_section(p, section_paths)
        it = article_item(p, today, post_like_url=news_path or url in from_sitemap, news_path=news_path)
        if it:
            keep(it, p)
    return sorted(out.values(), key=lambda a: a["date"], reverse=True)[:max_items]
