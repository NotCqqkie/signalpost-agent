"""Hiring from NAV arbeidsplassen's official job-vacancy feed (pam-stilling-feed.nav.no).

Access: NAV publishes a rotating *public* token at /api/publicToken for anyone ("Alle kan bruke tenesta",
https://arbeidsplassen.nav.no/vilkar-api); it is fetched at run time and is not tied to any entrant account.
Terms honoured: only ads whose latest feed status is ACTIVE are published, each claim links to the NAV ad page,
contact persons are never copied.

Method: page the feed from `lookback_days` ago (summaries carry businessName and status only), keep the latest
status per ad, index ACTIVE ads by normalised employer name. For each company the legal name and its registered
subunit names are looked up; candidate ads are confirmed by fetching the feed entry and checking that
`employer.orgnr` equals the company's organisation number or one of its registered subunits.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

from .footprint import fold
from .net import Fetcher

BASE = "https://pam-stilling-feed.nav.no"
CONTAINMENT_STOP = {"og", "i", "av", "for", "the", "and", "as", "asa"}
WEAK = {"holding", "invest", "eiendom", "service", "drift", "norge", "norway", "nordic", "group", "gruppen", "consulting", "bygg", "transport"}
SUFFIXES = {"as", "asa", "sa", "ans", "da", "enk", "nuf", "ks", "iks", "sf", "avd", "avdeling"}


def name_key(name: Any) -> str:
    toks = re.findall(r"[a-z0-9]+", fold(name))
    while toks and toks[-1] in SUFFIXES:
        toks.pop()
    return " ".join(toks)


class NavIndex:
    def __init__(self, fetcher: Fetcher, lookback_days: int = 120, max_pages: int = 600):
        self.fetcher = fetcher
        self.lookback_days = lookback_days
        self.max_pages = max_pages
        self.ready = threading.Event()
        self.token: str | None = None
        self.by_name: dict[str, list[dict]] = {}
        self.status: dict[str, Any] = {"state": "not_started"}
        self.thread: threading.Thread | None = None
        self._tok_index: dict[str, set[str]] | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._build, name="nav-index", daemon=True)
        self.thread.start()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _build(self) -> None:
        started = time.monotonic()
        try:
            self.status = {"state": "building"}
            r = self.fetcher.get(BASE + "/api/publicToken", respect_robots=False, accept="text/plain")
            if not r.ok:
                self.status = {"state": "failed", "error": f"public token: {r.error or r.status}"}
                return
            lines = [l.strip() for l in r.text().splitlines() if l.strip()]
            self.token = next((l for l in lines if l.count(".") == 2 and " " not in l), None)
            if not self.token:
                self.status = {"state": "failed", "error": "public token not found in response"}
                return
            since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
            url = BASE + "/api/v1/feed"
            headers = {**self._headers(), "If-Modified-Since": format_datetime(since, usegmt=True)}
            latest: dict[str, dict] = {}
            pages = 0
            while url and pages < self.max_pages:
                if self.fetcher.remaining() < 120:
                    self.status = {"state": "partial", "error": "deadline", "pages": pages}
                    break
                resp = self.fetcher.get(url, respect_robots=False, accept="application/json", headers=headers, timeout=30, max_bytes=20_000_000)
                if not resp.ok:
                    resp = self.fetcher.get(url, respect_robots=False, accept="application/json", headers=headers, timeout=30, max_bytes=20_000_000)
                    if not resp.ok:
                        self.status = {"state": "partial", "error": f"page {pages}: {resp.error or resp.status}", "pages": pages}
                        break
                body = json.loads(resp.body)
                pages += 1
                for item in body.get("items") or []:
                    entry = item.get("_feed_entry") or {}
                    uuid = entry.get("uuid") or item.get("id")
                    if uuid:
                        latest[uuid] = {"uuid": uuid, "status": entry.get("status"), "title": entry.get("title") or item.get("title"),
                                        "businessName": entry.get("businessName"), "municipal": entry.get("municipal"),
                                        "modified": entry.get("sistEndret") or item.get("date_modified"), "feed_url": BASE + str(item.get("url") or "")}
                next_url = body.get("next_url")
                items = body.get("items") or []
                url = BASE + next_url if next_url and items else None
                headers = self._headers()
            active = [v for v in latest.values() if v.get("status") == "ACTIVE"]
            for ad in active:
                self.by_name.setdefault(name_key(ad.get("businessName")), []).append(ad)
            self.status = {**{"state": "complete"}, **self.status} if self.status.get("state") == "building" else self.status
            self.status.update(pages=pages, ads_seen=len(latest), active_ads=len(active), employers=len(self.by_name),
                               since=since.isoformat(), build_seconds=round(time.monotonic() - started, 1))
            if self.status.get("state") == "building":
                self.status["state"] = "complete"
        except Exception as exc:
            self.status = {"state": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            self.ready.set()

    def candidates(self, names: list[str], containment: bool = False) -> list[dict]:
        """Exact normalised-name matches; with `containment`, also employer names whose tokens contain every token
        of a company/subunit name that has at least one distinctive token (precision comes from the orgnr check)."""
        out: dict[str, dict] = {}
        for n in names:
            key = name_key(n)
            if len(key) < 3:
                continue
            for ad in self.by_name.get(key, []):
                out[ad["uuid"]] = ad
            if containment:
                toks = [t for t in key.split() if t not in CONTAINMENT_STOP]
                strong = [t for t in toks if len(t) >= 4 and t not in WEAK]
                if not strong or len("".join(toks)) < 6:
                    continue
                if self._tok_index is None:
                    self._tok_index = {}
                    for k in self.by_name:
                        for t in set(k.split()):
                            self._tok_index.setdefault(t, set()).add(k)
                keys = set.intersection(*[self._tok_index.get(t, set()) for t in toks])
                # reverse containment: a short employer name ("Scania", "NEXANS") whose distinctive tokens all occur
                # in the company name ("NORSK SCANIA AS", "NEXANS NORWAY AS")
                name_toks = set(toks)
                for t in strong:
                    for k in self._tok_index.get(t, set()):
                        ktoks = [x for x in k.split() if x not in CONTAINMENT_STOP]
                        if ktoks and len(ktoks) <= 3 and set(ktoks) <= name_toks and any(len(x) >= 4 and x not in WEAK for x in ktoks):
                            keys.add(k)
                for k in sorted(keys)[:20]:
                    for ad in self.by_name[k][:10]:
                        out[ad["uuid"]] = ad
        return list(out.values())

    def confirm(self, ad: dict, orgnrs: dict[str, str]) -> dict:
        """Fetch the feed entry; return a job record when employer.orgnr is the company or one of its subunits."""
        resp = self.fetcher.get(ad["feed_url"], respect_robots=False, accept="application/json", headers=self._headers(), timeout=20)
        if not resp.ok:
            return {"state": "failed", "error": resp.error or f"HTTP {resp.status}", "uuid": ad["uuid"]}
        try:
            body = json.loads(resp.body)
        except ValueError:
            return {"state": "failed", "error": "invalid json", "uuid": ad["uuid"]}
        if body.get("status") != "ACTIVE":
            return {"state": "inactive", "uuid": ad["uuid"]}
        content = body.get("ad_content") or {}
        employer = content.get("employer") or {}
        orgnr = re.sub(r"\D", "", str(employer.get("orgnr") or ""))
        if orgnr not in orgnrs:
            return {"state": "other_employer", "uuid": ad["uuid"], "employer_orgnr": orgnr}
        locations = [", ".join(filter(None, [l.get("city") or l.get("municipal"), l.get("county")])) for l in content.get("workLocations") or []]
        return {
            "state": "confirmed",
            "job_id": f"nav:{ad['uuid']}",
            "title": (content.get("title") or ad.get("title") or "")[:200],
            "url": content.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{ad['uuid']}",
            "employer_name": employer.get("name"),
            "employer_orgnr": orgnr,
            "employer_relation": orgnrs[orgnr],
            "employer_homepage": employer.get("homepage"),
            "published": (content.get("published") or "")[:10] or None,
            "expires": (content.get("expires") or "")[:10] or None,
            "application_due": content.get("applicationDue"),
            "locations": [l for l in locations if l][:5],
            "occupation": [c.get("level2") for c in content.get("occupationCategories") or [] if c.get("level2")][:2],
            "source_url": ad["feed_url"],
            "sha256": resp.sha256,
            "retrieved_at": resp.retrieved_at,
            "span": f"employer.orgnr={orgnr}; employer.name={employer.get('name')}; status=ACTIVE",
        }
