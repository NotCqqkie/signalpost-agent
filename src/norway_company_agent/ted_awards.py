"""Dated public activity: EU/EEA public-procurement contract awards (TED) naming the company's organisation number.

Source: TED Search API `POST https://api.ted.europa.eu/v3/notices/search` — anonymous ("The Search API does not
require a key", https://docs.ted.europa.eu/api/latest/index.html); api.ted.europa.eu/robots.txt is 404 (no rules).
Reuse of TED notices is authorised with acknowledgement of the source ("© European Union, https://ted.europa.eu",
Commission Decision 2011/833/EU; https://ted.europa.eu/en/legal-notice); every claim carries that attribution.
The ted.europa.eu notice pages answer automated clients with a bot challenge (HTTP 202, empty body), so they are only
linked for people, never fetched.

Method: one query per batch of up to 50 organisation numbers (both `123456789` and `123 456 789` spellings), notices
published since `since`. A notice is published for a company only when an entry of `winner-identifier` equals its
organisation number (digits only) AND the index-aligned `winner-country` is NOR (a 9-digit number can be another
country's identifier, e.g. a French SIREN). The claim says what the notice says: the company is listed as a winner.

Winner check (lane 10 audit, 2026-11-06): in the Search API `winner-identifier` (and `winner-country`) list *every
tendering party* (it equals `organisation-identifier-tenderer`); only `winner-name` is restricted to winners. 18 of 42
published awards on the C3 scale run were lost bids. So the company's tenderer name (index-aligned
`organisation-name-tenderer`) must also appear in `winner-name`; if names cannot be aligned, nothing is published.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .evidence import utc_now
from .registry_events import _date

API = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = ["publication-number", "publication-date", "notice-type", "notice-title", "buyer-name", "winner-identifier", "winner-country", "winner-name",
          "organisation-identifier-tenderer", "organisation-name-tenderer"]
ATTRIBUTION = "© European Union, https://ted.europa.eu"
BATCH = 50
PAGE_LIMIT = 100
MAX_PAGES = 5
MAX_PER_COMPANY = 10  # most recent awards kept per company


def _spaced(org: str) -> str:
    return f"{org[:3]} {org[3:6]} {org[6:]}"


def build_query(orgs: list[str], since: str) -> str:
    terms = " ".join(orgs + [f'"{_spaced(o)}"' for o in orgs])
    return f"winner-identifier IN ({terms}) AND publication-date>={since.replace('-', '')}"


def _text(v: Any, prefer: tuple[str, ...] = ("eng", "nor", "nob", "nno")) -> str | None:
    if isinstance(v, dict):
        for k in prefer:
            if v.get(k):
                x = v[k]
                return x[0] if isinstance(x, list) else x
        for x in v.values():
            return x[0] if isinstance(x, list) else x
    if isinstance(v, list):
        return str(v[0]) if v else None
    return str(v) if v else None


def _norm_name(s: Any) -> str:
    return re.sub(r"[^0-9a-zæøåäöü]", "", str(s).lower())


def _name_lists(v: Any) -> list[list[str]]:
    if isinstance(v, dict):
        return [x if isinstance(x, list) else [x] for x in v.values()]
    if isinstance(v, list):
        return [v]
    return [[v]] if v else []


def match_notice(n: dict, wanted: set[str]) -> list[str]:
    """Organisation numbers from `wanted` listed as Norwegian winners of notice `n`.

    `winner-identifier` lists all tenderers, so a number counts only when its index-aligned tenderer name is one
    of the notice's `winner-name` entries."""
    ids = n.get("winner-identifier") or []
    countries = n.get("winner-country") or []
    if len(ids) != len(countries):
        return []  # cannot align identifier and country: do not publish
    winners = {_norm_name(x) for lst in _name_lists(n.get("winner-name")) for x in lst} - {""}
    t_ids = [re.sub(r"\D", "", str(x)) for x in (n.get("organisation-identifier-tenderer") or [])]
    t_names = [lst for lst in _name_lists(n.get("organisation-name-tenderer")) if len(lst) == len(t_ids)]
    out = []
    for raw, country in zip(ids, countries):
        org = re.sub(r"\D", "", str(raw))
        if org not in wanted or str(country).upper() not in ("NOR", "NO") or org in out:
            continue
        names = {_norm_name(lst[i]) for lst in t_names for i, t in enumerate(t_ids) if t == org}
        if names & winners:
            out.append(org)
    return out


class TedAwards:
    def __init__(self, deadline: float, since: str = "2021-01-01", interval_s: float = 1.0, user_agent: str | None = None, post=None):
        from .net import USER_AGENT
        self.deadline = deadline
        self.since = since
        self.interval_s = interval_s
        self.user_agent = user_agent or USER_AGENT
        self._post = post or self._http_post
        self.ready = threading.Event()
        self.by_org: dict[str, list[dict]] = {}
        self.checked: set[str] = set()
        self.status: dict[str, Any] = {"state": "not_started"}
        self.requests: list[dict] = []

    def _http_post(self, body: dict) -> tuple[int, bytes, str | None]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=max(2.0, min(45.0, self.deadline - time.monotonic() - 1))) as r:
                return r.status, r.read(20_000_000), None
        except urllib.error.HTTPError as exc:
            return exc.code, b"", f"HTTP {exc.code}"
        except Exception as exc:
            return 0, b"", f"{type(exc).__name__}: {str(exc)[:160]}"

    def start(self, orgs: list[str]) -> None:
        threading.Thread(target=self._run, args=(list(orgs),), name="ted-awards", daemon=True).start()

    def _run(self, orgs: list[str]) -> None:
        started = time.monotonic()
        try:
            self.status = {"state": "running"}
            valid = [o for o in orgs if re.fullmatch(r"\d{9}", o)]
            failed_batches = 0
            last = 0.0
            for i in range(0, len(valid), BATCH):
                chunk = valid[i:i + BATCH]
                wanted = set(chunk)
                query = build_query(chunk, self.since)
                ok = True
                for page in range(1, MAX_PAGES + 1):
                    if time.monotonic() > self.deadline - 5:
                        self.status = {"state": "partial", "error": "deadline"}
                        ok = False
                        break
                    wait = last + self.interval_s - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    last = time.monotonic()
                    body = {"query": query, "fields": FIELDS, "limit": PAGE_LIMIT, "page": page, "paginationMode": "PAGE_NUMBER"}
                    status, raw, err = self._post(body)
                    if status != 200:
                        last = time.monotonic()
                        time.sleep(min(5.0, max(0.0, self.deadline - time.monotonic() - 5)))
                        status, raw, err = self._post(body)
                    retrieved = utc_now()
                    sha = hashlib.sha256(raw).hexdigest() if raw else None
                    self.requests.append({"batch": i // BATCH, "page": page, "status": status, "error": err, "sha256": sha})
                    if status != 200:
                        ok = False
                        break
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        ok = False
                        break
                    notices = d.get("notices") or []
                    for n in notices:
                        for org in match_notice(n, wanted):
                            pub = str(n.get("publication-number") or "")
                            self.by_org.setdefault(org, []).append({
                                "publication_number": pub, "date": _date(n.get("publication-date")), "notice_type": n.get("notice-type"),
                                "title": (_text(n.get("notice-title")) or "")[:240], "buyer": _text(n.get("buyer-name")),
                                "winner_names": (n.get("winner-name") or {}).get("eng") if isinstance(n.get("winner-name"), dict) else None,
                                "url": f"https://ted.europa.eu/en/notice/-/detail/{pub}", "query": query, "page": page,
                                "sha256": sha, "retrieved_at": retrieved,
                                "span": f"publication-number={pub}; winner-identifier contains {org}; winner-country=NOR; tenderer name in winner-name; publication-date={n.get('publication-date')}"})
                    total = int(d.get("totalNoticeCount") or 0)
                    if page * PAGE_LIMIT >= total or not notices:
                        break
                if ok:
                    self.checked.update(chunk)
                else:
                    failed_batches += 1
                    if self.status.get("state") == "partial":
                        break
            for org, lst in self.by_org.items():
                uniq = {a["publication_number"]: a for a in lst}
                self.by_org[org] = sorted(uniq.values(), key=lambda a: (a["date"] or "", a["publication_number"]), reverse=True)[:MAX_PER_COMPANY]
            state = self.status.get("state") if self.status.get("state") == "partial" else ("complete" if not failed_batches else "partial")
            self.status = {**self.status, "state": state, "batches": (len(valid) + BATCH - 1) // BATCH, "failed_batches": failed_batches,
                           "requests": len(self.requests), "companies_with_awards": len(self.by_org), "seconds": round(time.monotonic() - started, 1)}
        except Exception as exc:
            self.status = {"state": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            self.ready.set()


def add_ted_claims(b: Any, ted: TedAwards | None, org: str) -> int:
    if ted is None:
        return 0
    n = 0
    for a in ted.by_org.get(org, []):
        e = b.ev(API + "#query=" + a["query"].replace(" ", "+") + f"&page={a['page']}", "official_procurement_notices_ted", a["retrieved_at"], a["sha256"],
                 a["span"], "ted_search_api_winner_identifier")
        title = f"Listed as a winner in a public contract award notice (TED {a['publication_number']}): {a['title']}" + (f" — buyer: {a['buyer']}" if a.get("buyer") else "")
        b.claim("hiring_activity", "dated_activity", {"date": a["date"], "title": title[:300], "url": a["url"], "kind": "public_procurement_award",
                                                      "event_type": "procurement_award", "notice_type": a["notice_type"], "buyer": a.get("buyer"),
                                                      "source": "TED (Tenders Electronic Daily)", "attribution": ATTRIBUTION},
                "available", [e], family="dated_activity", key=f"ted:{a['publication_number']}", confidence=0.97, effective_date=a["date"])
        n += 1
    return n
