"""Official food-safety inspection ratings (Mattilsynet "smilefjes") keyed by organisation number.

Source: Mattilsynet's public smilefjes site, https://smilefjes.mattilsynet.no/ (dataset "Smilefjestilsyn på
serveringssteder", licence CC BY 4.0, https://data.norge.no/datasets/288aa74c-e3d3-492e-9ede-e71503b3bfd9; every
claim carries the attribution "Kilde: Mattilsynet"). smilefjes.mattilsynet.no/robots.txt is 404 (no rules; checked
2026-11-06). The dataset's CSV host data.mattilsynet.no has `Disallow: /` and is NOT used; the catalogued
hotell.difi.no distribution no longer resolves.

Method A (primary, `csv`): one background GET of the site's daily export `/api/tilsyn.csv` (~16 MB, columns as the
dataset: tilsynsobjektid, orgnummer, navn, address, tilsynid, dato ddmmyyyy, total_karakter 0-3, tilsynsbesoektype).
Rows are published for a company when `orgnummer` is the company's own number or one of its BRREG-registered
subunits (smilefjes records carry the serving place's underenhet number). Zero per-company requests.

Method B (fallback when the export is unavailable; identity is never inferred from names):
1. One background GET of the site's own search index `/search/index/nb.json` (~6 MB; establishment URL, name,
   street, postcode, inspection dates). It carries no organisation numbers, so it only yields *candidates*.
2. Per company, candidates are establishments whose (postcode, street) or (postcode, name) equals the company's
   registered business address or one of its registered subunits (BRREG underenheter, already fetched).
3. Each candidate page is fetched; it prints "Orgnr. <9 digits>". A rating is published only when that number is
   the company's own organisation number or one of its registered subunits. Otherwise nothing is published.

Claims: one `ratings_reviews` claim per verified establishment (latest grade, date, count of inspections) and one
`dated_activity` claim per inspection (most recent MAX_INSPECTIONS per establishment). Grades are copied from the
page's own wording ("Spisestedet har fått blidt smilefjes." etc.) and mapped to Mattilsynet's published scale.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from typing import Any

from .evidence import utc_now

BASE = "https://smilefjes.mattilsynet.no"
INDEX = BASE + "/search/index/nb.json"
CSV = BASE + "/api/tilsyn.csv"
# total_karakter -> the page's wording (0 = no breaches, 1 = minor breaches: both "blidt smilefjes")
KARAKTER_TEXT = {"0": "blidt smilefjes", "1": "blidt smilefjes", "2": "strekmunn", "3": "sur munn"}
VISIT = {"0": "ordinary inspection", "1": "follow-up inspection"}
SOURCE_CLASS = "official_inspection_register"
ATTRIBUTION = "Kilde: Mattilsynet (smilefjes), CC BY 4.0"
SCHEME = "Mattilsynet smilefjes (official food-safety inspection of a serving establishment)"
MAX_CANDIDATES = 8  # pages fetched per company at most
MAX_INSPECTIONS = 5  # dated inspections published per establishment

# page wording -> (grade code, English meaning per https://www.mattilsynet.no/mat-og-drikke/forbrukere/smilefjesordningen)
GRADES = {
    "blidt smilefjes": ("smiling", "smiling face: no breaches or only minor breaches found"),
    "strekmunn": ("straight_mouth", "straight mouth: breaches found that require follow-up"),
    "sur munn": ("sad", "sad face: serious breaches found (or follow-up not done)"),
    "surt smilefjes": ("sad", "sad face: serious breaches found (or follow-up not done)"),
}

_FORMS = re.compile(r"\b(as|asa|da|ans|sa|ba|enk|nuf|ks|avd|avdeling)\b\.?")


def _norm(s: Any) -> str:
    return re.sub(r"[^0-9a-zæøåäöüé]", "", str(s or "").lower())


def _norm_name(s: Any) -> str:
    return _norm(_FORMS.sub(" ", str(s or "").lower()))


def _street(addr: dict | None) -> str:
    lines = (addr or {}).get("adresse") or []
    return _norm(lines[0] if lines else "")


class SmilefjesIndex:
    """Background download of the public search index, then per-company candidate lookup and page verification."""

    def __init__(self, fetcher, deadline: float):
        self.fetcher = fetcher
        self.deadline = deadline
        self.ready = threading.Event()
        self.status: dict[str, Any] = {"state": "not_started"}
        self.by_addr: dict[tuple[str, str], list[str]] = {}
        self.by_name: dict[tuple[str, str], list[str]] = {}
        self.entries: dict[str, dict] = {}
        self.mode = None  # "csv" (export keyed by orgnr) or "pages" (search index + page verification)
        self.by_org: dict[str, dict[str, dict]] = {}

    def start(self) -> None:
        threading.Thread(target=self._load, name="smilefjes-index", daemon=True).start()

    def _load(self) -> None:
        started = time.monotonic()
        try:
            self.status = {"state": "running"}
            r = self.fetcher.get(CSV, timeout=90, max_bytes=80_000_000, accept="text/csv,*/*;q=0.5")
            if r.ok and self.load_csv(r.text(), r):
                self.mode = "csv"
                self.status = {"state": "complete", "mode": "csv", "organisations": len(self.by_org), "sha256": r.sha256, "retrieved_at": r.retrieved_at,
                               "seconds": round(time.monotonic() - started, 1)}
                return
            csv_error = r.error or f"HTTP {r.status}" if not r.ok else "export unparseable"
            r = self.fetcher.get(INDEX, timeout=60, max_bytes=40_000_000, accept="application/json")
            if not r.ok:
                self.status = {"state": "failed", "error": r.error or f"HTTP {r.status}"}
                return
            self.load_json(json.loads(r.body))
            self.mode = "pages"
            self.status = {"state": "complete", "mode": "pages", "csv_error": csv_error, "entries": len(self.entries), "sha256": r.sha256, "retrieved_at": r.retrieved_at,
                           "seconds": round(time.monotonic() - started, 1)}
        except Exception as exc:
            self.status = {"state": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            self.ready.set()

    def load_csv(self, text: str, resp: Any = None) -> bool:
        lines = text.splitlines()
        if not lines or not lines[0].startswith("tilsynsobjektid;orgnummer;"):
            return False
        head = lines[0].split(";")
        col = {h: i for i, h in enumerate(head)}
        need = ("tilsynsobjektid", "orgnummer", "navn", "adrlinje1", "postnr", "poststed", "tilsynid", "dato", "total_karakter", "tilsynsbesoektype")
        if any(k not in col for k in need):
            return False
        meta = {"evidence_url": CSV, "retrieved_at": getattr(resp, "retrieved_at", None) or utc_now(), "sha256": getattr(resp, "sha256", None)}
        for line in lines[1:]:
            f = line.split(";")
            if len(f) < len(head):
                continue
            org, obj, d = f[col["orgnummer"]].strip(), f[col["tilsynsobjektid"]].split("_")[0], f[col["dato"]].strip()
            k = f[col["total_karakter"]].strip()
            if not re.fullmatch(r"\d{9}", org) or not re.fullmatch(r"\d{8}", d) or k not in KARAKTER_TEXT:
                continue
            est = self.by_org.setdefault(org, {}).setdefault(obj, {
                "url": f"{BASE}/spisested/{obj}/", "orgnr": org, "establishment": f[col["navn"]].strip(),
                "address": {"street": f[col["adrlinje1"]].strip(), "postcode": f[col["postnr"]].strip(), "place": f[col["poststed"]].strip()},
                "inspections": [], **meta})
            tid = f[col["tilsynid"]].split("_")[0]
            date = f"{d[4:]}-{d[2:4]}-{d[:2]}"
            est["inspections"].append({"inspection_id": tid, "date": date, "grade_text": KARAKTER_TEXT[k], "karakter": k,
                                       "visit": VISIT.get(f[col["tilsynsbesoektype"]].strip()),
                                       "span": f"tilsynsobjektid={obj}; orgnummer={org}; navn={f[col['navn']].strip()}; tilsynid={tid}; dato={d}; total_karakter={k}"})
        return bool(self.by_org)

    def load_json(self, d: dict) -> None:
        for row in d.get("lookup") or []:
            if not isinstance(row, list) or len(row) < 6:
                continue
            path, name, a1, _a2, pc, place = row[:6]
            if not str(path).startswith("/spisested/"):
                continue
            url = BASE + str(path)
            self.entries[url] = {"name": name, "street": a1, "postcode": pc, "place": place}
            self.by_addr.setdefault((str(pc), _norm(a1)), []).append(url)
            if _norm_name(name):
                self.by_name.setdefault((str(pc), _norm_name(name)), []).append(url)

    def candidates(self, places: list[tuple[dict | None, str | None]]) -> list[str]:
        """places: (registered address, registered name) of the company and each subunit."""
        out: list[str] = []
        for addr, name in places:
            pc = str((addr or {}).get("postnummer") or "")
            if not pc:
                continue
            keys = [self.by_addr.get((pc, _street(addr))) if _street(addr) else None,
                    self.by_name.get((pc, _norm_name(name))) if _norm_name(name) else None]
            for lst in keys:
                for u in lst or []:
                    if u not in out:
                        out.append(u)
        return out


def parse_page(text: str) -> dict:
    """Organisation number, establishment name/address and every inspection (id, date, grade) on a smilefjes page."""
    m = re.search(r"Orgnr\.\s*(\d{9})", text)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    name = html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else None
    inspections = []
    for b in re.finditer(r'data-select_element_id="([^"]+?)_TilsynAvtale".*?title="Spisestedet har f[^"]*?tt ([^"]+?)\.".*?<div class="text-xs">(\d{2})\.(\d{2})\.(\d{2})</div>',
                         text, re.S):
        tid, grade_text, dd, mm, yy = b.groups()
        grade_text = html.unescape(grade_text).strip().lower()
        if tid in {i["inspection_id"] for i in inspections}:
            continue
        inspections.append({"inspection_id": tid, "date": f"20{yy}-{mm}-{dd}", "grade_text": grade_text})
    latest = re.search(r"Siste tilsynsresultat:\s*(\d{2})\.(\d{2})\.(\d{4})", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    return {"orgnr": m.group(1) if m else None, "name": name, "inspections": inspections,
            "latest_date": f"{latest.group(3)}-{latest.group(2)}-{latest.group(1)}" if latest else None}


def fetch_company_ratings(fetcher, index: SmilefjesIndex | None, org: str, places: list[tuple[dict | None, str | None]],
                          subunits: dict[str, str]) -> dict:
    """Return {"state", "establishments": [...], "requests": [...]} for one company.

    subunits: registered subunit organisation number -> name. Publication requires the page's Orgnr to be `org` or a key of `subunits`."""
    if index is None:
        return {"state": "not_run", "establishments": [], "requests": []}
    if not index.ready.is_set():
        index.ready.wait(max(0.0, min(60.0, index.deadline - time.monotonic())))
    if index.status.get("state") != "complete":
        return {"state": "failed", "establishments": [], "requests": [], "error": f"smilefjes index {index.status.get('state')}: {index.status.get('error')}"}
    allowed = {org: "same_entity", **{s: "registered_subunit" for s in subunits}}
    if index.mode == "csv":
        out = []
        for num, rel in allowed.items():
            for est in (index.by_org.get(num) or {}).values():
                if est["inspections"]:
                    out.append({**est, "relation": rel, "subunit_name": subunits.get(num), "method": "smilefjes_export_orgnr_match"})
        return {"state": "checked", "establishments": out, "requests": [], "candidates": len(allowed)}
    cands = index.candidates(places)
    out, requests, failed = [], [], 0
    for url in cands[:MAX_CANDIDATES]:
        if fetcher.remaining() < 5:
            failed += 1
            break
        r = fetcher.get(url, timeout=15, max_bytes=3_000_000)
        requests.append({"url": url, "status": r.status, "error": r.error})
        if not r.ok:
            failed += 1
            continue
        p = parse_page(r.text())
        rel = allowed.get(p["orgnr"] or "")
        if not rel or not p["inspections"]:
            continue
        out.append({"url": url, "orgnr": p["orgnr"], "relation": rel, "establishment": p["name"] or index.entries.get(url, {}).get("name"),
                    "subunit_name": subunits.get(p["orgnr"]), "address": index.entries.get(url), "inspections": p["inspections"],
                    "latest_date": p["latest_date"], "retrieved_at": r.retrieved_at, "sha256": r.sha256, "evidence_url": url, "method": "smilefjes_page_orgnr_match"})
    state = "checked" if not failed else ("partial" if out else "failed")
    return {"state": state, "establishments": out, "requests": requests, "candidates": len(cands),
            "note": f"no Mattilsynet smilefjes establishment at the registered addresses carries this organisation number or a registered subunit's ({len(cands)} candidate pages checked)"}


def add_smilefjes_claims(b: Any, res: dict | None) -> int:
    """Rating + dated inspection claims; a not_available/failed rating claim when the check found nothing."""
    res = res or {"state": "not_run", "establishments": []}
    n = 0
    for est in res.get("establishments") or []:
        insp = sorted(est["inspections"], key=lambda i: (i["date"], i["inspection_id"]), reverse=True)
        latest = insp[0]
        code, meaning = GRADES.get(latest["grade_text"], ("unrecognised", latest["grade_text"]))
        who = f"{est['establishment']} (Orgnr. {est['orgnr']}" + (", registered subunit" if est["relation"] == "registered_subunit" else "") + ")"
        method = est.get("method") or "smilefjes_page_orgnr_match"
        src = est.get("evidence_url") or est["url"]
        span = latest.get("span") or (f"Orgnr. {est['orgnr']}; {est['establishment']}; {len(insp)} inspections; latest {latest['date']}: "
                                      f"\"Spisestedet har fått {latest['grade_text']}.\"")
        e = b.ev(src, SOURCE_CLASS, est["retrieved_at"], est["sha256"], span, method)
        b.claim("ratings_reviews", "official_rating", {
            "scheme": SCHEME, "rating": code, "rating_text": latest["grade_text"], "meaning": meaning, "date": latest["date"],
            "establishment": est["establishment"], "establishment_orgnr": est["orgnr"], "relation": est["relation"],
            "inspections_listed": len(insp), "url": est["url"], "evidence_source": src, "source": "Mattilsynet", "attribution": ATTRIBUTION},
            "available", [e], family="ratings_reviews", key=f"smilefjes:{est['url']}", confidence=0.98, effective_date=latest["date"])
        n += 1
        for i in insp[:MAX_INSPECTIONS]:
            icode, imeaning = GRADES.get(i["grade_text"], ("unrecognised", i["grade_text"]))
            ie = b.ev(src, SOURCE_CLASS, est["retrieved_at"], est["sha256"],
                      i.get("span") or f"Orgnr. {est['orgnr']}; inspection {i['inspection_id']} dated {i['date']}: \"Spisestedet har fått {i['grade_text']}.\"",
                      method)
            b.claim("hiring_activity", "dated_activity", {
                "date": i["date"], "title": f"Mattilsynet food-safety inspection at {who}: {imeaning}"[:300], "url": est["url"],
                "kind": "official_inspection", "event_type": "food_safety_inspection", "rating": icode, "source": "Mattilsynet",
                "attribution": ATTRIBUTION, **({"visit": i["visit"]} if i.get("visit") else {}),
                "date_basis": "inspection date (dato) in Mattilsynet's smilefjes export" if i.get("span") else "inspection date shown on the Mattilsynet smilefjes page"},
                "available", [ie], family="dated_activity", key=f"smilefjes:{i['inspection_id']}", confidence=0.97, effective_date=i["date"])
            n += 1
    if not res.get("establishments"):
        st = res.get("state")
        if st in ("checked", "partial"):
            b.claim("ratings_reviews", "official_rating", None, "not_available", family="ratings_reviews", key="smilefjes",
                    note=res.get("note") or f"no Mattilsynet smilefjes inspection record carries this organisation number or a registered subunit's ({res.get('candidates', 0)} numbers checked)")
        elif st == "failed":
            b.claim("ratings_reviews", "official_rating", None, "failed", family="ratings_reviews", key="smilefjes",
                    note=str(res.get("error") or "smilefjes pages could not be fetched")[:200])
    return n
