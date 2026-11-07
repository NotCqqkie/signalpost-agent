"""Hiring from a verified company site (lane 7; lane 6 mechanism 2).

Identity is already proven by the website gate in `footprint.py`; this module only finds careers pages on that
site and reads open positions from them. Publication rules:
  * careers pages are found by whole path segments (`/ledig-stilling`, `/karriere`, `/jobb-hos-oss`) or by short
    anchor texts ("Ledig stilling", "Jobb hos oss") - never by substrings, so `bestilling`, `utstilling` and
    `ledige-lokaler` do not count;
  * a position is published from free text only when the page carries an apply cue (søknadsfrist, send søknad,
    apply ...), no "ingen ledige stillinger" phrase, the heading names a position, and any stated deadline has
    not passed; the key is page URL + heading;
  * structured JobPosting data on the site is read as before; off-site applicant-tracking boards are read only when
    the board page itself prints the organisation number (a bare ATS link is never a claim);
  * a careers page that says no positions are open yields a `none_open` check with the phrase as evidence.
Hook: `crawl_careers(...)`, called from `footprint.crawl_verified_site` when the profile carries `_site_hiring`.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import date
from typing import Any, Callable

from .footprint import ATS_HOSTS, NORWAY, Page, fold, iso_date, job_countries, jobs_from_page, orgnr_pattern, same_site, tokens, type_set, walk_json

# whole path segment tokens (after folding and splitting on non-alphanumerics)
PATH_TOKENS = {"karriere", "karrierer", "career", "careers", "jobb", "jobs", "job", "stilling", "stillinger", "ledigstilling",
               "ledigestillinger", "vacancies", "vacancy", "rekruttering", "jobbmuligheter", "karrieremuligheter", "stillingsannonse",
               "stillingsannonser", "jobbsoknad", "jobbtilbud", "jobbe", "ansettelse"}
PATH_PREFIXES = ("ledigstilling", "ledigestilling", "jobbhososs", "jobbehososs", "jobbmulighet", "karrieremulighet")
# tokens that turn a matching path into something else (rooms, flats, ordering, exhibitions)
DENY_TOKENS = {"bestilling", "utstilling", "forestilling", "innstilling", "lokale", "lokaler", "kontor", "kontorer", "bolig", "boliger",
               "leilighet", "leiligheter", "plass", "plasser", "timer", "time", "ordre", "hytte", "hytter", "tomt", "tomter", "rom"}
ANCHOR_RE = re.compile(
    r"\b(ledige? stilling(er)?|stilling(er)? ledig|ledige jobber|stillinger|jobbe? (hos|i|for|med) (oss|\w+)|karriere\w*|careers?|jobs|jobb|"
    r"vacanc(y|ies)|vi ansetter|vi soker( etter)?( deg| folk| flere)?|work (with|for|at) us|join (us|our team|the team)|rekruttering|"
    r"jobbmuligheter|ledig jobb)\b")
NONE_RE = re.compile(
    r"(ingen|ikke noen|ingen aktuelle) (ledige|utlyste|apne|aktive) (stillinger|jobber|stilling)|har (for tiden|for oyeblikket|na|dessverre)( dessverre)? ingen (ledige|utlyste)|"
    r"ingen stillinger (er )?(ledige|utlyst)|for tiden ingen ledige|ingen ledige|har (vi )?ikke (noen )?(ledige|utlyste) stillinger|"
    r"(do not|don't|dont) (currently )?have (any )?(open|available|vacant) (positions|vacancies|roles|jobs)|"
    r"(no|not any) (open|current|available) (positions|vacancies|openings|jobs)|there are (currently )?no (open )?(positions|vacancies|openings|jobs)|"
    # main2 fresh6: a filled or expired ad page (happii.no "Stillingen er besatt", finn "Fristen er utlopt")
    # not when conditional: "soknaden slettes 6 maneder etter at stillingen er besatt" (eurospar-moa.no privacy note)
    r"(?<!etter at )(?<!nar )(?<!for )(?<!til )stillingen er (besatt|tilsatt|ikke lenger ledig)|(?<!etter at )(soknads)?fristen er (utlopt|passert)|"
    r"(this )?position (has been|is) (filled|closed)")
APPLY_RE = re.compile(
    r"soknadsfrist|send (oss )?(din |en )?soknad|soknad (med cv )?(sendes|merkes)|sok (pa )?(stillingen|jobben|her|na)|send (oss )?(din )?cv|"
    r"soknad og cv|cv og soknad|soknad med cv|tiltredelse|apply (now|here|for this|for the)|application deadline|send (your|an) application|"
    r"how to apply|soknader sendes|sende soknad|soknad kan sendes|sok stillingen|ansok|apply for (this )?(job|position)")
OPEN_APPLICATION_RE = re.compile(r"\b(apen|aapen|open|spontan) (soknad|application)\b")
HEADING_PREFIX_RE = re.compile(r"^(ledig stilling|ledige stilling|stillingsannonse|vi soker etter|vi soker|ny stilling|stilling ledig|vacancy|we are hiring|we're hiring|vi ansetter)\b[\s:\-\u2013\u2014!]*", re.I)
# a heading that names a position: ends in a role noun or is a known job-title word
ROLE_SUFFIXES = ("ingenior", "leder", "elektriker", "montor", "konsulent", "radgiver", "medarbeider", "tekniker", "operator", "sjafor",
                 "larling", "selger", "assistent", "sykepleier", "fagarbeider", "snekker", "tomrer", "utvikler", "developer", "engineer",
                 "manager", "mekaniker", "arbeider", "lege", "terapeut", "fysioterapeut", "kokk", "servitor", "frisor", "arkitekt",
                 "designer", "planlegger", "koordinator", "sekretaer", "regnskapsforer", "advokat", "revisor", "lagermedarbeider",
                 "maler", "rorlegger", "murer", "maskinforer", "laerer", "pedagog", "barnehagelaerer", "assistant", "technician",
                 "specialist", "spesialist", "analytiker", "analyst", "trainee", "vikar", "kontrollor", "inspektor", "renholder", "kranforer", "truckforer",
                 "tannlege", "tannhelsesekretaer", "veterinaer", "farmasoyt", "apoteker", "sveiser", "platearbeider", "blikkenslager",
                 "installator", "prosjektleder", "kundeveileder", "kundebehandler", "radgivere", "medarbeidere", "officer", "director",
                 "direktor", "sjef", "formann", "baker", "konditor", "slakter", "butikkmedarbeider", "ekspeditor", "sjaafor",
                 "lastebilsjafor", "bilmekaniker", "skipper", "matros", "maskinist", "styrmann", "kaptein", "fisker", "gartner",
                 "anleggsgartner", "dyrepleier", "miljoarbeider", "vernepleier", "helsefagarbeider", "ergoterapeut", "psykolog",
                 "kiropraktor", "osteopat", "naprapat", "jurist", "okonom", "controller", "salgssjef", "daglig leder", "student",
                 "sommerjobb", "sommervikar", "deltid", "heltid", "programmerer", "konstruktor", "tegner", "byggeleder", "anleggsleder")
GENERIC_HEADINGS = re.compile(r"^(vare|om|kontakt|ledige stillinger|ledig stilling|stillinger|karriere|jobb hos oss|jobbe hos oss|apen soknad|"
                              r"aapen soknad|open application|careers?|jobs|vi soker deg|hvem er vi|hvorfor|nyheter|tjenester|send soknad|"
                              r"soknad|sok her|meld deg|abonner|nyhetsbrev|folg oss|del|relaterte|siste|les mer|ansatte|medarbeidere|"
                              r"vare ansatte|vare medarbeidere|kontaktperson|sporsmal|praktisk informasjon|om stillingen|om oss|vi tilbyr|"
                              r"arbeidsoppgaver|kvalifikasjoner|egenskaper|personlige egenskaper|vi onsker|onskede kvalifikasjoner)\b")
MONTHS = {"januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
          "november": 11, "desember": 12, "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "october": 10,
          "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "oct": 10,
          "nov": 11, "des": 12, "dec": 12}
DEADLINE_RE = re.compile(r"(soknadsfrist|frist|deadline|application deadline)\b[^0-9a-z]{0,6}(?P<val>\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|\d{1,2}\.?\s+[a-z]+\.?\s+\d{4}|[^.!?\n]{0,40}?\d{4}|\d{1,2}\.?\s+[a-z]+|snarest|fortlopende|lopende|asap)"
                         r"|\b(innen|senest|before)\s+(?P<val2>\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|\d{1,2}\.?\s+[a-z]+\s+\d{2,4})\b")
LOCATION_RE = re.compile(r"\b(Arbeidssted|Arbeidsted|Sted|Lokasjon|Location|Kontorsted|Oppmøtested)\s*[:\-]\s*(?P<loc>[A-ZÆØÅ][\wÆØÅæøå\-]+(?:(?:[ ,/]+|\s+og\s+)[A-ZÆØÅ][\wÆØÅæøå\-]+){0,6})")
LOCATION_STOP = {"Søk", "Søknadsfrist", "Stillingstype", "Frist", "Kontakt", "Ansettelsesform", "Stilling", "Fast", "Heltid", "Deltid", "Apply",
                 "Deadline", "Les", "Tiltredelse", "Arbeidsgiver"}
# individual ads on job boards, linked from the careers page of the verified site (the board itself is never fetched)
BOARD_AD_RE = re.compile(r"^https?://(www\.)?(finn\.no/(job/\S*ad|\S*finnkode=)\S*\d{6,}|"
                         r"jobbnorge\.no/(ledige-stillinger/stilling|en/available-jobs/job)/\d+|[\w.-]*webcruiter\.(no|com)/\S*(advert|job)\S*\d{4,}|"
                         r"[\w.-]*\.(teamtailor\.com|recman\.no|jobylon\.com)/jobs?/\S*\d{3,})", re.I)
HEADINGS = ("h1", "h2", "h3", "h4")
# NAV ads linked from the verified site are confirmed through NAV's official feed (status ACTIVE + employer orgnr), not read
NAV_AD_RE = re.compile(r"^https?://(www\.)?arbeidsplassen\.nav\.no/stillinger/stilling/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def nav_ad_links(pages: list[Page]) -> list[dict]:
    out: dict[str, dict] = {}
    for p in pages:
        for url, text, _ in p.links:
            m = NAV_AD_RE.match(url)
            if m and m.group("uuid").lower() not in out:
                out[m.group("uuid").lower()] = {"uuid": m.group("uuid").lower(), "found_on": p.url, "anchor": text[:160]}
    return list(out.values())[:10]


def is_careers_path(url: str) -> bool:
    path = fold(urllib.parse.urlparse(url).path)
    toks = re.findall(r"[a-z0-9]+", path)
    if any(t in DENY_TOKENS for t in toks):
        return False
    if any(seg in ("jobber", "ledige-jobber") for seg in path.split("/")):  # 'jobber' only as a whole segment, not '/slik-jobber-vi'
        return True
    return any(t in PATH_TOKENS or t.startswith(PATH_PREFIXES) for t in toks)


def is_careers_anchor(text: str) -> bool:
    t = " ".join(tokens(text))
    if not t or len(t) > 40 or re.search(r"bestill|utstill|lokale|bolig|leilighet", t):
        return False
    return bool(ANCHOR_RE.search(t))


def careers_links(pages: list[Page], domain: str, limit: int = 2) -> list[str]:
    """Same-site careers URLs from the pages already fetched (home + identity pages), best first."""
    scored: dict[str, int] = {}
    for p in pages:
        for url, text, _ in p.links:
            if not same_site(url, domain) or urllib.parse.urlparse(url).path.count("/") > 4:
                continue
            key = url.split("#")[0]
            by_path, by_text = is_careers_path(url), is_careers_anchor(text)
            if not (by_path or by_text):
                continue
            rank = 0 if (by_path and by_text) else (1 if by_text else 2)
            if re.search(r"ledig|stilling|vacanc", fold(text) + " " + fold(urllib.parse.urlparse(url).path)):
                rank -= 1
            scored[key] = min(rank, scored.get(key, 99))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: (kv[1], len(kv[0])))[:limit]]


def parse_deadline(val: str, today: str) -> str | None:
    v = fold(val)
    if re.search(r"snarest|fortlopende|lopende|asap", v):
        return "open_until_filled"
    m = re.search(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})", v)
    try:
        if m:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))).isoformat()
        m = re.search(r"(\d{1,2})\.?\s+([a-z]+)\.?\s+(\d{4}|\d{2})\b", v)
        if m and m.group(2) in MONTHS:  # main2: "innen 30. september 26" (two-digit year, hepro.no)
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, MONTHS[m.group(2)], int(m.group(1))).isoformat()
        m = re.match(r"\s*(\d{1,2})\.?\s+([a-z]+)\b(?!\.?\s*\d)", v)
        if m and m.group(2) in MONTHS:
            # E50 (main2 fresh15): "Søknadsfrist: 20. Oktober" without a year: this year, or next year when that
            # date would be more than half a year past
            t = date.fromisoformat(today)
            d = date(t.year, MONTHS[m.group(2)], int(m.group(1)))
            return (d if (t - d).days <= 183 else date(t.year + 1, d.month, d.day)).isoformat()
    except ValueError:
        return None
    return iso_date(val)


def heading_level(node: Any) -> int:
    return int(node.name[1]) if node is not None and node.name in HEADINGS else 9


def section_text(heading: Any, limit: int = 4000) -> str:
    """Text after a heading up to the next heading of the same or higher level."""
    level = heading_level(heading)
    out: list[str] = []
    size = 0
    for el in heading.next_elements:
        name = getattr(el, "name", None)
        if name in HEADINGS and heading_level(el) <= level:
            break
        if name is None:
            s = str(el).strip()
            if s and not any(par.name in ("script", "style", "noscript", "template") for par in el.parents if par is not None):
                out.append(s)
                size += len(s)
                if size > limit:
                    break
    return " ".join(" ".join(out).split())


def position_title(raw: str) -> str | None:
    """The position named by a heading, or None if the heading is a section label, not a job."""
    text = " ".join(raw.split()).strip(" :-–—!")
    if not text or len(text) > 120:
        return None
    f = " ".join(tokens(text))
    m = HEADING_PREFIX_RE.match(f)
    if m:
        drop = len(m.group(0).split())
        words = text.split()
        while words and drop > 0:
            if re.search(r"\w", words[0]):
                drop -= 1
            words.pop(0)
        rest = " ".join(words)
        rest = re.sub(r"^(en|ei|et|flere|ny|nye|a|an)\s+", "", rest.strip(" :-–—!"), flags=re.I).strip(" :-–—!")
        rf = " ".join(tokens(rest))
        if not rf or len(rf.split()) > 10 or rf in ("deg", "deg som", "folk", "flere", "nye medarbeidere", "medarbeidere", "kollegaer", "ny kollega", "kollega"):
            return None
        return rest[:120]
    m2 = re.match(r"^(?P<t>.{3,80}?)\s+s(ø|o)kes\b", text, re.I)  # 'Tømrer med fagbrev søkes til Fribo Bygg'
    if m2 and not GENERIC_HEADINGS.match(" ".join(tokens(m2.group("t")))):
        return re.sub(r"^(en|ei|et|ny|nye)\s+", "", m2.group("t").strip(" :-–—!"), flags=re.I)[:120]
    if GENERIC_HEADINGS.match(f) or len(f.split()) > 9:
        return None
    words = f.split()
    if any(w.endswith(ROLE_SUFFIXES) for w in words) or any(s in f for s in (" daglig leder", "daglig leder")):
        return text[:120]
    return None


STALE_DAYS = 183
POSTED_RE = re.compile(r"(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.](20\d\d)(?![\d/.])")


def stale_before(today: str) -> str:
    return date.fromordinal(date.fromisoformat(today).toordinal() - STALE_DAYS).isoformat()


def posted_date(page: Page, section_folded: str) -> str | None:
    """Publication date of an ad page: structured metadata first, else a day-first date in the ad's own section that is
    not a deadline or start date."""
    for block in page.jsonld:
        for node in walk_json(block):
            d = iso_date(node.get("datePosted") or node.get("datePublished")) if isinstance(node, dict) else None
            if d:
                return d
    meta = page.soup.select_one('meta[property="article:published_time"], meta[itemprop="datePublished"]')
    if meta is not None and iso_date(meta.get("content")):
        return iso_date(meta.get("content"))
    for m in POSTED_RE.finditer(section_folded[:600]):
        if re.search(r"(frist|innen|senest|tiltredelse|oppstart|start|fra|before|deadline)\W{0,12}$", section_folded[max(0, m.start() - 30):m.start()]):
            continue
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            continue
    return None


GENERIC_ROLES = {"sommerjobb", "sommervikar", "trainee", "student", "studenter", "vikar", "larling", "larlinger", "deltid", "heltid",
                 "sommerstudent", "internship", "praktikant", "ekstrahjelp", "tilkallingsvikar"}
CONDITIONAL_RE = re.compile(r"(\b(hvis|dersom|om|if|when|nar)\b[^.!?]{0,40}|\bhar vi\s*|\bhar vi (for tiden|akkurat na|na)\s*|\?\s*)$")


def none_statement(text_folded: str):
    """First 'no open positions' statement that is not a conditional ('har vi ingen ...', 'hvis det ikke er ...', 'Ingen ...?')."""
    for m in NONE_RE.finditer(text_folded):
        before = text_folded[max(0, m.start() - 50):m.start()]
        after = text_folded[m.end():m.end() + 30]
        if CONDITIONAL_RE.search(before) or re.match(r"[^.!]{0,25}\?", after):
            continue
        return m
    return None


def job_abroad(page: Page) -> bool:
    """The page carries structured JobPosting data and every posting is located outside Norway."""
    countries = [job_countries(n) for b in page.jsonld for n in walk_json(b) if "JobPosting" in type_set(n)]
    return bool(countries) and all(c and not (c & NORWAY) for c in countries)


def free_text_jobs(page: Page, today: str) -> tuple[list[dict], dict]:
    """Open positions in a careers page's own text, with a check record explaining the outcome."""
    text_f = fold(page.text)
    check = {"url": page.url, "sha256": page.resp.sha256, "retrieved_at": page.resp.retrieved_at}
    none = none_statement(text_f)
    if none:
        return [], {**check, "state": "none_open", "span": text_f[max(0, none.start() - 80):none.end() + 80]}
    apply = APPLY_RE.search(text_f)
    if not apply:
        state = "open_application_only" if OPEN_APPLICATION_RE.search(text_f) else "no_apply_cue"
        return [], {**check, "state": state, "span": None}
    jobs: list[dict] = []
    expired = 0
    named = [(h, position_title(h.get_text(" ", strip=True))) for h in page.soup.find_all(HEADINGS)]
    named = [(h, t) for h, t in named if t]
    for h, title in named:
        raw = h.get_text(" ", strip=True)
        sect = section_text(h)
        sf = fold(sect)
        # the section under this heading (or, for a single-ad page, the page itself) must carry the apply cue
        single_ad = len({fold(t) for _, t in named}) == 1
        if not (APPLY_RE.search(sf) or (single_ad and apply)):
            continue
        if none_statement(sf) or OPEN_APPLICATION_RE.search(fold(title)):
            continue
        if fold(title) in GENERIC_ROLES and not DEADLINE_RE.search(sf):
            continue  # 'Sommerjobb', 'Lærling' alone name a programme, not an opening, unless a deadline is given
        dl = DEADLINE_RE.search(sf) or (DEADLINE_RE.search(text_f) if single_ad else None)
        deadline = parse_deadline(dl.group("val") or dl.group("val2"), today) if dl else None
        if deadline and deadline != "open_until_filled" and deadline < today:
            expired += 1
            continue
        posted = posted_date(page, sf)
        if not (deadline and deadline != "open_until_filled") and posted and posted < stale_before(today):
            expired += 1  # no future deadline and the ad was posted more than STALE_DAYS ago
            continue
        loc = LOCATION_RE.search(sect) or (LOCATION_RE.search(page.text) if single_ad else None)
        cue = APPLY_RE.search(sf) or apply
        span_src = sf if APPLY_RE.search(sf) else text_f
        location = None
        if loc:
            kept = []
            for w in loc.group("loc").split():
                if w.strip(" ,/") in LOCATION_STOP:
                    break
                kept.append(w)
            location = re.sub(r"\s+,", ",", " ".join(kept)).strip(" ,/") or None
        jobs.append({"title": title, "url": page.url, "heading": " ".join(raw.split())[:200], "deadline": deadline, "posted": posted,
                     "location": location, "method": "free_text_heading_apply_cue",
                     "span": f"{' '.join(raw.split())[:120]} | {span_src[max(0, cue.start() - 60):cue.end() + 60]}"})
    seen, unique = set(), []
    for j in jobs:
        k = fold(j["title"])
        if k not in seen:
            seen.add(k)
            unique.append(j)
    if unique:
        return unique[:15], {**check, "state": "positions_found", "span": unique[0]["span"]}
    return [], {**check, "state": "expired_only" if expired else "no_position_heading", "span": None}


def board_ad_expired(page: Page, today: str) -> bool:
    """A job-board ad page that says it has expired, or whose printed application deadline has passed."""
    text = fold(page.text)
    if re.search(r"fristen er utlopt|annonsen er utlopt|annonsen er ikke lenger aktiv|stillingen er besatt|this ad is no longer active", text):
        return True
    m = DEADLINE_RE.search(text)
    d = parse_deadline(m.group("val"), today) if m else None
    return bool(d and d not in ("open_until_filled",) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < today)


def board_ad_jobs(page: Page) -> list[dict]:
    """Links to individual job-board ads on the careers page, with the position title as anchor text."""
    out = []
    for url, text, a in page.links:
        if not BOARD_AD_RE.match(url) or "open-application" in url.lower() or "apen-soknad" in fold(url):
            continue
        title = re.sub(r"\s*[|\-–]\s*(finn\.no|finn|nav|arbeidsplassen|jobbnorge|webcruiter)\s*$", "", text, flags=re.I).strip()
        tf = " ".join(tokens(title))
        if not tf or len(tf.split()) > 16 or GENERIC_HEADINGS.match(tf) or OPEN_APPLICATION_RE.search(tf) or re.match(
                r"^(se|les|sok|apply|finn|her|link|lenke|klikk|go to|view|see|stillingsannonse\w*|annonse\w*|ledige stillinger|meld interesse|ga til|gaa til|vis annonse|apply now)\b", tf):
            continue
        context = " ".join(a.parent.get_text(" ", strip=True).split())[:300] if a.parent is not None else title
        out.append({"title": title[:200], "url": url, "heading": title[:200], "deadline": None, "location": None,
                    "method": "careers_page_link_to_board_ad", "span": f"{context[:200]} -> {url}"})
    return out[:15]


def detail_links(page: Page, domain: str, limit: int = 4) -> list[str]:
    """Links from a careers index to individual ads on the same site (under the careers path or a job-like path)."""
    base = urllib.parse.urlparse(page.url).path.rstrip("/")
    out = []
    for url, text, _ in page.links:
        if not same_site(url, domain) or len(text) < 6:
            continue
        path = urllib.parse.urlparse(url).path.rstrip("/")
        if path == base or url.split("#")[0].rstrip("/") == page.url.rstrip("/"):
            continue
        under = bool(base) and path.startswith(base + "/")
        named = position_title(text) is not None
        if under or (is_careers_path(url) and named):
            out.append((0 if named else 1, url.split("#")[0]))  # links that name a position first
    out.sort(key=lambda x: x[0])
    return list(dict.fromkeys(u for _, u in out))[:limit]


def ats_jobs(page: Page, get_page: Callable[[str, str], Page | None], org: str) -> list[dict]:
    """Off-site ATS board linked from the careers page: read JobPosting data only if the board prints the org number."""
    for url, _, _ in page.links:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not any(a in host for a in ATS_HOSTS):
            continue
        board = get_page(url, "careers_ats")
        if board is None or not re.search(orgnr_pattern(org), board.resp.text()):
            return []
        jobs = jobs_from_page(board, "")
        for j in jobs:
            j["method"] = "ats_board_jsonld_orgnr_on_board"
        return jobs
    return []


EMPLOYER_RE = re.compile(r"(?i:arbeidsgiver|employer|selskap)\s*:?\s*((?:[A-ZÆØÅ0-9][\w&.'’\-]*\s+){0,5}?[A-ZÆØÅ0-9][\w&.'’\-]*)\s+(?:AS|ASA)\b")


def other_employer(page: Page, name: str | None) -> str | None:
    """E46 (main2 fresh14): a job ad page that names its employer as another company ("Arbeidsgiver: Netpower WEB
    Solutions AS" on netpower.no, the group site of Netpower IT Solutions AS) is that company's position."""
    own = "".join(t for t in tokens(name or "") if t not in ("as", "asa"))
    if not own:
        return None
    for m in EMPLOYER_RE.finditer(page.text):
        other = "".join(t for t in tokens(m.group(1)) if t not in ("as", "asa"))
        if other and other != own:
            return m.group(1).strip() + " AS"
    return None


def crawl_careers(pages: list[Page], domain: str, get_page: Callable[[str, str], Page | None], *, org: str, today: str,
                  max_pages: int = 2, max_details: int = 6, name: str | None = None) -> dict:
    """Find careers pages on a verified site and read open positions. Returns {"jobs", "checks", "careers_pages"}."""
    jobs: list[dict] = []
    other_ad_pages: set[str] = set()
    checks: list[dict] = []
    careers_pages: list[Page] = []
    # pages already fetched (home, identity pages) can themselves be ads or careers pages
    queue = careers_links(pages, domain, max_pages)
    visited: set[str] = set()

    def read(page: Page, from_detail: bool = False) -> None:
        if job_abroad(page):  # main2: the page's own JobPosting places the job outside Norway (group careers site)
            checks.append({"url": page.url, "state": "positions_abroad_only", "span": "JobPosting jobLocation outside Norway", "retrieved_at": page.resp.retrieved_at, "sha256": page.resp.sha256})
            return
        found = [dict(j) for j in jobs_from_page(page, domain)]
        for j in found:
            j.setdefault("span", f"{j['title']} -> {j['url']}")
        ft, check = free_text_jobs(page, today)
        if check["state"] != "none_open":
            boards = board_ad_jobs(page)
            ft += boards
            if boards and check["state"] != "positions_found":
                check = {**check, "state": "positions_found", "span": boards[0]["span"]}
        if found:
            check = {**check, "state": "positions_found", "span": found[0]["span"]}
        checks.append(check)
        for j in found + ft:
            j.update(found_on=page.url, sha256=page.resp.sha256, retrieved_at=page.resp.retrieved_at, from_detail=from_detail)
            jobs.append(j)

    for url in queue:
        if url.rstrip("/") in visited:
            continue
        visited.add(url.rstrip("/"))
        page = get_page(url, "careers")
        if not page:
            continue
        visited.add(page.url.rstrip("/"))
        careers_pages.append(page)
        read(page)
        if checks[-1]["state"] != "none_open":
            for durl in detail_links(page, domain, max_details):
                if durl.rstrip("/") in visited:
                    continue
                visited.add(durl.rstrip("/"))
                d = get_page(durl, "careers_detail")
                if d:
                    read(d, True)
                    if other_employer(d, name):
                        other_ad_pages.add(d.url)
            if not any(j["found_on"] == page.url for j in jobs):
                for j in ats_jobs(page, get_page, org):
                    j.update(found_on=page.url, sha256=page.resp.sha256, retrieved_at=page.resp.retrieved_at, span=f"{j['title']} (board prints org number)")
                    jobs.append(j)
    # an explicit "no open positions" statement on any careers page overrides free-text matches elsewhere on the site
    if any(c["state"] == "none_open" for c in checks):
        jobs = [j for j in jobs if j["method"].startswith(("jsonld", "ats_board"))]
    # one claim per position: the individual ad page wins over its line on the index page
    by_title: dict[str, dict] = {}
    for j in jobs:
        k = fold(j["title"])
        if k not in by_title or (j.get("from_detail") and not by_title[k].get("from_detail")):
            by_title[k] = j
    by_title = {k: j for k, j in by_title.items() if j.get("found_on") not in other_ad_pages}  # E46
    return {"jobs": list(by_title.values())[:30], "checks": checks, "careers_pages": [p.url for p in careers_pages],
            "nav_links": nav_ad_links(list(pages) + careers_pages)}


def hiring_check_summary(checks: list[dict]) -> dict | None:
    """The check to cite when no position is published: an explicit 'none open' statement wins."""
    for state in ("none_open", "expired_only", "open_application_only", "no_position_heading", "no_apply_cue"):
        for c in checks:
            if c.get("state") == state:
                return c
    return None
