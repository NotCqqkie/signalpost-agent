"""Dated activity: Research Council of Norway (Forskningsrådet) funded projects, keyed by the responsible organisation's number.

Source: Forskningsrådet's own open-data repository, dataset `soknader2` ("Søknader og prosjekter"),
https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv (~55 MB CSV, one row
per application/project with `organisasjonsnummer` of the project-responsible organisation). Licence NLOD 2.0
(https://data.norge.no/datasets/d23bbbfa-fdad-4dab-b31b-4daadbfa3299). raw.githubusercontent.com/robots.txt is 404
(no rules); downloaded once per run in a background thread. Prosjektbanken pages (robots.txt disallows AI crawlers) are
only linked for people, never fetched.

Published: only rows whose `organisasjonsnummer` equals the input, with `prosjektfase` "Bevilgning" (granted, running)
or "Avsluttet" (completed) and `tildelt_belop` > 0. Rejected or pending applications are not published. The dated
event is the application date (`soknadsdato`, exact day); project start/end years and the granted amount are copied
verbatim. Most recent MAX_PER_COMPANY projects per company.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from typing import Any

URL = "https://raw.githubusercontent.com/Forskningsradet/open-data/main/datasets/soknader2/dataset.csv"
PUBLIC_URL = "https://prosjektbanken.forskningsradet.no/"
SOURCE_CLASS = "official_research_funding_register"
FUNDED = {"Bevilgning": "granted (running)", "Avsluttet": "completed"}
MAX_PER_COMPANY = 10


def _amount(v: str) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


class RcnProjects:
    def __init__(self, fetcher, deadline: float):
        self.fetcher = fetcher
        self.deadline = deadline
        self.ready = threading.Event()
        self.status: dict[str, Any] = {"state": "not_started"}
        self.by_org: dict[str, list[dict]] = {}
        self.meta: dict[str, Any] = {}

    def start(self) -> None:
        threading.Thread(target=self._load, name="rcn-projects", daemon=True).start()

    def _load(self) -> None:
        started = time.monotonic()
        try:
            self.status = {"state": "running"}
            r = self.fetcher.get(URL, timeout=120, max_bytes=200_000_000, accept="text/csv,text/plain,*/*;q=0.5")
            if not r.ok:
                self.status = {"state": "failed", "error": r.error or f"HTTP {r.status}"}
                return
            n = self.load_csv(r.body.decode("utf-8", errors="replace"))
            if not n:
                self.status = {"state": "failed", "error": "dataset unparseable or empty"}
                return
            self.meta = {"retrieved_at": r.retrieved_at, "sha256": r.sha256}
            self.status = {"state": "complete", "rows": n, "organisations": len(self.by_org), "sha256": r.sha256, "retrieved_at": r.retrieved_at,
                           "seconds": round(time.monotonic() - started, 1)}
        except Exception as exc:
            self.status = {"state": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            self.ready.set()

    def load_csv(self, text: str) -> int:
        csv.field_size_limit(10_000_000)
        rd = csv.DictReader(io.StringIO(text, newline=""))
        need = {"prosjektnummer", "prosjekttittel", "soknadsdato", "prosjektfase", "prosjektstart", "prosjektslutt", "organisasjonsnummer", "tildelt_belop"}
        if not rd.fieldnames or not need <= set(rd.fieldnames):
            return 0
        n = 0
        for row in rd:
            n += 1
            org = (row.get("organisasjonsnummer") or "").strip()
            amt = _amount(row.get("tildelt_belop"))
            date = (row.get("soknadsdato") or "")[:10]
            if len(org) != 9 or not org.isdigit() or row.get("prosjektfase") not in FUNDED or not amt or amt <= 0 or len(date) != 10:
                continue
            self.by_org.setdefault(org, []).append({
                "project": row["prosjektnummer"].strip(), "title": (row.get("prosjekttittel") or "").strip(), "date": date, "phase": row["prosjektfase"],
                "start": (row.get("prosjektstart") or "").strip(), "end": (row.get("prosjektslutt") or "").strip(), "amount": amt,
                "activity": (row.get("aktivitet") or "").strip(), "responsible": (row.get("prosjektansvarlig_navn") or "").strip()})
        for lst in self.by_org.values():
            lst.sort(key=lambda p: (p["date"], p["project"]), reverse=True)
        return n

    def lookup(self, org: str) -> dict:
        if not self.ready.is_set():
            self.ready.wait(max(0.0, min(90.0, self.deadline - time.monotonic())))
        if self.status.get("state") != "complete":
            return {"state": "failed", "projects": [], "error": f"rcn dataset {self.status.get('state')}: {self.status.get('error')}"}
        return {"state": "checked", "projects": self.by_org.get(org, []), **self.meta}


def _nok(x: float) -> str:
    s = f"{x:,.2f}".replace(",", " ")
    return "NOK " + (s[:-3] if s.endswith(".00") else s)


def add_rcn_claims(b: Any, res: dict | None, org: str) -> int:
    res = res or {"state": "not_run", "projects": []}
    n = 0
    for p in res.get("projects", [])[:MAX_PER_COMPANY]:
        years = f"{p['start']}–{p['end']}" if p["start"] and p["end"] and p["start"] != p["end"] else (p["start"] or p["end"])
        title = (f"Research Council of Norway funded project {p['project']} \"{p['title'] if len(p['title']) <= 110 else p['title'][:109].rstrip() + '…'}\" ({FUNDED[p['phase']]}; {_nok(p['amount'])} granted"
                 + (f"; project {years}" if years else "") + f"; application dated {p['date']})")
        span = (f"prosjektnummer={p['project']}; organisasjonsnummer={org}; prosjektansvarlig_navn={p['responsible']}; soknadsdato={p['date']}; "
                f"prosjektfase={p['phase']}; prosjektstart={p['start']}; prosjektslutt={p['end']}; tildelt_belop={p['amount']}")
        e = b.ev(URL, SOURCE_CLASS, res.get("retrieved_at"), res.get("sha256"), span, "rcn_open_data_orgnr")
        b.claim("hiring_activity", "dated_activity", {
            "date": p["date"], "title": title[:300], "url": PUBLIC_URL + f"project/FORISS/{p['project']}", "kind": "public_research_funding",
            "event_type": "research_project_funded", "project_number": p["project"], "project_title": p["title"], "phase": p["phase"],
            "granted_nok": p["amount"], "project_years": years, "programme": p["activity"], "source": "Forskningsrådet open data (soknader2)",
            "licence": "NLOD 2.0", "date_basis": "application date (soknadsdato) of a project that was granted funding"},
            "available", [e], family="dated_activity", key=f"forskning:{p['project']}", confidence=0.97, effective_date=p["date"])
        n += 1
    return n
