"""Deterministic per-company synthesis built only from an envelope's own claims (no model, no network, no keys).

`add_synthesis(envelope)` returns a copy of the envelope with an additive `synthesis` section:

- `headline`: one line for a list view.
- `sentences`: plain-English statements, each with the `claim_ids` and `evidence_ids` that support it. A sentence
  cites `available` claims, or `not_available` claims that carry evidence (a checked source that returned none,
  e.g. "the register lists no subunits"). Derived figures (growth, equity ratio) cite every input claim.
- `unknowns`: what is missing and why, keyed by topic and availability state (`not_available`, `ambiguous`,
  `blocked`, `failed`), each citing the claim that records the gap. Missing is never turned into zero.
- `changes`: material changes since the previous run, in words, with the claims/evidence behind them.
- `text`: the sentences joined; `supported_claim_ids`: every claim id cited anywhere.

The function never raises: a broken envelope yields `synthesis.status == "failed"` with the error, so one bad record
cannot stop a batch. `validate_synthesis` re-checks every citation against the envelope (used by tests and fuzzing).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable

from . import peers
from .industry_en import english_industry

METHOD = ("signalpost-synthesis/2 (deterministic template over the envelope's claims plus a committed peer reference table; "
          "no model, no network)")

# Norwegian legal forms (Enhetsregisteret codes) -> short English description and an article.
LEGAL_FORMS = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "NUF": "Norwegian-registered branch of a foreign company", "BRL": "housing cooperative (borettslag)",
    "ESEK": "condominium owners' association (eierseksjonssameie)", "STI": "foundation (stiftelse)",
    "FLI": "association or club (forening/lag/innretning)", "SA": "cooperative (samvirkeforetak)",
    "DA": "partnership with shared liability (DA)", "ANS": "general partnership (ANS)",
    "VPFO": "securities fund (verdipapirfond)", "SAM": "co-ownership (tingsrettslig sameie)",
    "IKS": "inter-municipal company", "KS": "limited partnership (KS)", "PRE": "shipowning partnership (partsrederi)",
    "PK": "pension fund", "SPA": "savings bank", "ANNA": "other legal entity", "SÆR": "entity established by special law",
    "BBL": "housing cooperative association (boligbyggelag)", "GFS": "mutual insurance company",
    "BA": "company with limited liability (BA)", "SF": "state enterprise (statsforetak)", "KIRK": "Church of Norway body",
    "KF": "municipal enterprise", "SE": "European company (SE)", "ORGL": "organisational unit", "STAT": "state body",
    "FKF": "county municipal enterprise", "KOMM": "municipality", "FYLK": "county", "ADOS": "administrative unit",
    "BO": "estate in administration", "UTLA": "foreign entity", "EOFG": "European economic interest grouping",
}
# Forms where a missing website, job ads or news is normal and should not read as a red flag.
RESIDENTIAL_FORMS = {"BRL", "ESEK", "SAM", "BBL"}
NO_OWNER_FORMS = {"STI", "FLI"}

ROLE_EN = {
    "DAGL": "managing director", "LEDE": "board chair", "NEST": "deputy board chair", "MEDL": "board member",
    "VARA": "deputy board member", "OBS": "board observer", "INNH": "owner (sole proprietor)", "DTPR": "partner (full liability)",
    "DTSO": "partner (shared liability)", "KOMP": "general partner", "FFØR": "business manager", "REPR": "Norwegian representative",
    "KONT": "contact person", "REVI": "auditor", "REGN": "accountant", "BEST": "managing shipowner", "BOBE": "estate administrator",
    "KDEB": "debtor in bankruptcy", "PROK": "holder of procuration", "POFE": "joint procuration", "SIGN": "signature right",
    "SAM": "co-owner", "EIKM": "owner municipality", "KENK": "sole proprietor of business",
}

FIN_LABEL = {
    "revenue": "revenue", "operating_result": "operating result", "profit_before_tax": "profit before tax",
    "annual_result": "net result", "assets": "total assets", "equity": "equity", "debt": "debt",
}

# NACE Rev. 2 / SN2007 division (first two digits) -> broad English sector.
_NACE_SECTIONS = [
    (1, 3, "agriculture, forestry and fishing"), (5, 9, "mining and quarrying"), (10, 33, "manufacturing"),
    (35, 35, "electricity, gas and heating supply"), (36, 39, "water supply, sewerage and waste"), (41, 43, "construction"),
    (45, 47, "wholesale and retail trade"), (49, 53, "transport and storage"), (55, 56, "accommodation and food service"),
    (58, 63, "information and communication"), (64, 66, "finance and insurance"), (68, 68, "real estate"),
    (69, 75, "professional, scientific and technical services"), (77, 82, "administrative and support services"),
    (84, 84, "public administration"), (85, 85, "education"), (86, 88, "health and social work"),
    (90, 93, "arts, entertainment and recreation"), (94, 96, "other service activities"), (97, 98, "household activities"),
    (99, 99, "extraterritorial organisations"),
]
_NACE_SPECIAL = {"97.001": "housing cooperatives and condominiums", "68.209": "housing cooperatives and condominiums",
                 "64.200": "holding company", "64.220": "holding company", "64.210": "financial holding company", "70.100": "head-office activity"}
UNSPECIFIED_NACE = {"00.000", "00.00", "00"}

MAX_TEXT = 220
STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")


# ------------------------------------------------------------------ small helpers
def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _clip(text: Any, n: int = MAX_TEXT) -> str:
    t = " ".join(_s(text).split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _title(name: Any) -> str:
    """Kommune names arrive upper-case; title-case them but keep Norwegian particles readable."""
    t = _s(name).strip()
    return t.title() if t.isupper() else t


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return None


def money(v: Any, unit: Any) -> str:
    x = _num(v)
    cur = _s(unit) or "(currency not stated)"
    if x is None:
        return f"{cur} ?"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e9:
        body = f"{a / 1e9:.1f} billion"
    elif a >= 1e6:
        body = f"{a / 1e6:.1f} million"
    elif a >= 1e4:
        body = f"{a / 1e3:.0f} thousand"
    else:
        body = f"{a:,.0f}".replace(",", " ")
    return f"{cur} {sign}{body}"


def _period_end(c: dict) -> str:
    p = _s(c.get("reporting_period"))
    if "/" in p:
        return p.split("/")[-1]
    key = _s(c.get("key"))
    return key.split("@", 1)[1] if "@" in key else p


def _period_words(c: dict) -> str:
    p = _s(c.get("reporting_period"))
    if "/" in p:
        a, b = p.split("/", 1)
        if a[5:] == "01-01" and b[5:] == "12-31" and a[:4] == b[:4]:
            return b[:4]
        return f"the period {a} to {b}"
    end = _period_end(c)
    return end[:4] if end else "an unstated period"


def nace_sector(code: Any) -> str | None:
    c = _s(code).strip()
    if c in UNSPECIFIED_NACE:
        return None
    if c in _NACE_SPECIAL:
        return _NACE_SPECIAL[c]
    m = re.match(r"(\d{2})", c)
    if not m:
        return None
    d = int(m.group(1))
    for lo, hi, label in _NACE_SECTIONS:
        if lo <= d <= hi:
            return label
    return None


class _Ctx:
    """Indexes the envelope's claims and evidence and collects cited sentences."""

    def __init__(self, env: dict):
        self.env = env
        claims = env.get("claims")
        self.claims = [c for c in claims if isinstance(c, dict) and isinstance(c.get("id"), str)] if isinstance(claims, list) else []
        seen: dict[str, int] = {}
        for c in self.claims:
            seen[c["id"]] = seen.get(c["id"], 0) + 1
        self.claims = [c for c in self.claims if seen[c["id"]] == 1]  # a duplicated claim id cannot be cited unambiguously
        for c in self.claims:  # tolerate malformed citation lists instead of failing the whole synthesis
            if not isinstance(c.get("evidence_ids"), list):
                c["evidence_ids"] = []
        evidence = env.get("evidence")
        self.evidence = {e["id"]: e for e in evidence if isinstance(e, dict) and isinstance(e.get("id"), str)} if isinstance(evidence, list) else {}
        self.by_id = {c.get("id"): c for c in self.claims if c.get("id")}
        ident = env.get("identity")
        self.ident = ident if isinstance(ident, dict) else {}
        self.sentences: list[dict] = []
        self.unknowns: list[dict] = []
        self.cited: set[str] = set()
        self.facts: dict[str, Any] = {}  # values shared between topics (e.g. latest accounts) for the decision block
        self.references: dict[str, dict] = {}  # non-envelope sources (peer table) cited by `reference_ids`

    def avail(self, field: str) -> list[dict]:
        # An "available" claim without a value is inconsistent; it is never stated (and so never printed as None or 0).
        return [c for c in self.claims if c.get("field") == field and c.get("availability") == "available" and c.get("value") is not None]

    def gaps(self, field: str) -> list[dict]:
        return [c for c in self.claims if c.get("field") == field and (c.get("availability") != "available" or c.get("value") is None)]

    def first(self, field: str) -> dict | None:
        a = self.avail(field)
        return a[0] if a else None

    def value(self, field: str) -> Any:
        c = self.first(field)
        return c.get("value") if c else None

    def _ev(self, cs: list[dict]) -> list[str]:
        out: list[str] = []
        for c in cs:
            for i in c.get("evidence_ids") or []:
                if isinstance(i, str) and i in self.evidence and i not in out:
                    out.append(i)
        return out

    def say(self, topic: str, text: str, cs: list[dict], **extra: Any) -> None:
        cs = [c for c in cs if c and c.get("id")]
        if not cs or not text:
            return  # never state anything without a supporting claim
        ids = list(dict.fromkeys(c["id"] for c in cs))
        self.cited.update(ids)
        self.sentences.append({"id": f"s-{len(self.sentences) + 1}", "topic": topic, "text": text, "claim_ids": ids,
                               "evidence_ids": self._ev(cs), **extra})

    def unknown(self, topic: str, text: str, cs: list[dict], availability: str | None = None, expected: bool = False) -> None:
        cs = [c for c in cs if c]
        state = availability or (cs[0].get("availability") if cs else "not_available") or "not_available"
        if state not in STATES:
            state = "failed"  # a malformed state in the envelope is reported as a failure, never as data
        reason = next((_s(c.get("note")) for c in cs if c.get("note")), "")
        item = {"topic": topic, "text": text, "availability": state, "claim_ids": [c["id"] for c in cs if c.get("id")]}
        if reason:
            item["reason"] = _clip(reason, 200)
        if expected:
            item["expected_for_legal_form"] = True
        self.unknowns.append(item)


def _gap_text(subject: str, c: dict | None) -> tuple[str, str]:
    state = (c or {}).get("availability") or "not_available"
    if state not in STATES:
        state = "failed"
    return {
        "ambiguous": (f"{subject}: a candidate was found but could not be confirmed as this company's, so nothing is published", state),
        "blocked": (f"{subject}: the source blocked automated access, so it was not read", state),
        "failed": (f"{subject}: could not be checked in this run (technical failure)", state),
        "not_applicable": (f"{subject}: not applicable to this entity", state),
    }.get(state, (f"{subject}: none found", state))


# ------------------------------------------------------------------ topics
def _identity(x: _Ctx) -> None:
    name_c = x.first("legal_name")
    name = _s(name_c.get("value") if name_c else x.ident.get("name")) or f"Organisation {x.env.get('organisation_number')}"
    form_c = x.first("legal_form")
    code = _s(form_c.get("value") if form_c else "").upper()
    form = LEGAL_FORMS.get(code) or _s(x.ident.get("legal_form_description")) or (code and f"{code} entity")
    orgnr_c = x.first("organisation_number")
    addr_c = x.first("business_address")
    addr = addr_c.get("value") if addr_c and isinstance(addr_c.get("value"), dict) else {}
    place = _title(addr.get("kommune") or addr.get("poststed"))
    country = _s(addr.get("land"))
    founded_c = x.first("founded_date")
    bits = [name]
    cs = [name_c]
    if form:
        bits.append(f"is a {form}" if not form[0].lower() in "aeiou" else f"is an {form}")
        cs.append(form_c)
    else:
        bits.append("is a registered entity")
    if orgnr_c:
        bits.append(f"(organisation number {orgnr_c['value']})")
        cs.append(orgnr_c)
    if place:
        bits.append(f"with its business address in {place}" + (f", {country}" if country and country.lower() not in ("norge", "norway") else ""))
        cs.append(addr_c)
    if founded_c:
        bits[-1] += ","
        bits.append(f"founded {founded_c['value']}")
        cs.append(founded_c)
    x.say("identity", " ".join(bits) + ".", cs)
    brand = x.first("public_brand")
    if brand and _s(brand.get("value")).strip() and _s(brand.get("value")).strip().lower() != name.lower():
        x.say("identity", f"On its verified website it presents itself as \"{_clip(brand['value'], 90)}\".", [brand])


def _activity(x: _Ctx) -> None:
    ind = x.first("industry")
    if ind and isinstance(ind.get("value"), dict) and _s(ind["value"].get("kode")).strip() in UNSPECIFIED_NACE:
        x.say("activity", "The register gives no industry code for it (00.000 \"unspecified\").", [ind])
    elif ind and isinstance(ind.get("value"), dict):
        v = ind["value"]
        code = _s(v.get("kode")).strip()
        en, precision = english_industry(code)
        sector = nace_sector(code)
        label = _s(v.get("beskrivelse"))
        reg = f"registered industry {code}" + (f" \"{label}\"" if label else "")
        if en and precision == "code":
            txt = f"What it does: {en} ({reg})."
        elif en:
            txt = f"What it does: its {reg} is in the {en} division."
        else:
            txt = f"Its {reg}" + (f" ({sector})." if sector else ".")
        x.say("activity", txt, [ind])
        x.facts["industry_en"] = en
    elif ind and ind.get("value"):
        x.say("activity", f"Its registered industry is \"{_clip(ind['value'], 120)}\".", [ind])
    act = x.first("stated_activity")
    if act and _s(act.get("value")).strip():
        x.say("activity", f"Stated purpose in the register (Norwegian): \"{_clip(act['value'])}\"", [act])
    desc = x.first("self_description")
    if desc and _s(desc.get("value")).strip() and _looks_like_self_description(x, _s(desc.get("value"))):
        x.say("activity", f"Its own website says: \"{_clip(desc['value'], 160)}\" (company-owned text, not independent).", [desc])


_FIRST_PERSON = re.compile(r"\b(vi|oss|vår|vårt|våre|we|our|us)\b", re.I)


def _stem_words(text: str) -> set[str]:
    stop = {"as", "asa", "sa", "ba", "da", "ans", "enk", "nuf", "og", "the", "and", "norge", "norway", "holding", "gruppen", "group", "www", "https", "http", "com", "no"}
    return {w for w in re.findall(r"[a-zæøå0-9]{4,}", text.lower()) if w not in stop}


def _looks_like_self_description(x: _Ctx, text: str) -> bool:
    """Keep a website snippet only if it reads as the company describing itself (its name, brand, domain or town,
    or first-person wording); blog or article text scraped from the page is dropped rather than shown as 'what it does'."""
    t = text.lower()
    if _FIRST_PERSON.search(text):
        return True
    names = " ".join(_s(v) for v in (x.value("legal_name"), x.value("public_brand"), x.value("official_website")))
    addr = x.value("business_address")
    if isinstance(addr, dict):
        names += " " + _s(addr.get("poststed")) + " " + _s(addr.get("kommune"))
    return any(w in t for w in _stem_words(names))


def _status(x: _Ctx) -> None:
    br, lq = x.first("bankrupt"), x.first("under_liquidation")
    flags = []
    if br and br.get("value") is True:
        flags.append("bankrupt")
    if lq and lq.get("value") is True:
        flags.append("under liquidation")
    if flags:
        x.say("status", f"Warning: the register flags this entity as {' and '.join(flags)}.", [c for c in (br, lq) if c and c.get("value") is True], flag="warning")
    elif br and lq and br.get("value") is False and lq.get("value") is False:
        x.say("status", "The register shows no bankruptcy or liquidation proceedings.", [br, lq])
    emp = x.first("registered_employees")
    if emp and _num(emp.get("value")) is not None:
        n = int(_num(emp["value"]) or 0)
        x.say("size", f"The Central Coordinating Register lists {n} employee{'s' if n != 1 else ''}.", [emp])
    else:
        gap = (x.gaps("registered_employees") or [None])[0]
        if gap is not None:
            x.unknown("size", "Employee count: none registered (the register only lists it when reported; this is not zero)", [gap])


def _accounts(x: _Ctx) -> None:
    fin = [c for c in x.claims if c.get("family") == "financials" or c.get("field") in FIN_LABEL]
    avail = [c for c in fin if c.get("availability") == "available" and c.get("id") and _num(c.get("value")) is not None]
    form = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    submitted = _s(x.ident.get("latest_submitted_accounts"))
    if not avail:
        gap = fin[0] if fin else None
        txt, st = _gap_text("Annual accounts", gap)
        if not fin:
            txt = "Annual accounts: no figures in this envelope"
        x.unknown("accounts", txt + (f" (the register says accounts for {submitted} were submitted)" if submitted else ""),
                  [gap] if gap else [], st, expected=form in ("ENK", "NUF") and not submitted)
        return
    ends = sorted({_period_end(c) for c in avail}, reverse=True)
    latest_end = ends[0]
    latest = {c["field"]: c for c in avail if _period_end(c) == latest_end}
    # Prefer the claim marked latest (company accounts) if the agent flagged one.
    flagged = [c for c in avail if c.get("latest")]
    if flagged:
        latest_end = _period_end(flagged[0])
        latest = {c["field"]: c for c in avail if _period_end(c) == latest_end}
    any_c = next(iter(latest.values()))
    unit = any_c.get("unit")
    period = _period_words(any_c)
    kind = {"KONSERN": "consolidated group", "SELSKAP": "company"}.get(_s(any_c.get("account_type")).upper(), "")
    order = ["revenue", "operating_result", "annual_result", "equity", "assets", "debt", "profit_before_tax"]
    parts = [f"{FIN_LABEL[f]} {money(latest[f]['value'], latest[f].get('unit') or unit)}" for f in order[:4] if f in latest]
    if parts:
        x.say("accounts", f"Latest filed {kind + ' ' if kind else ''}accounts ({period}): " + ", ".join(parts) + ".",
              [latest[f] for f in order[:4] if f in latest], period=_s(any_c.get("reporting_period")))
    missing = [f for f in ("revenue", "operating_result", "annual_result", "equity") if f not in latest]
    gaps = [c for c in fin if c.get("field") in missing and _period_end(c) == latest_end and c.get("availability") != "available"]
    if missing:
        x.unknown("accounts", f"Not in the filed accounts for {period}: " + ", ".join(FIN_LABEL[f] for f in missing) + " (left blank, not zero)", gaps, "not_available")
    if submitted and latest_end[:4] and submitted > latest_end[:4]:
        x.unknown("accounts", f"The register says accounts for {submitted} were submitted, but the newest figures available here are for {period}", [], "not_available")
    # year-on-year for revenue and net result
    for f in ("revenue", "annual_result"):
        cur = latest.get(f)
        if not cur:
            continue
        older = sorted([c for c in avail if c.get("field") == f and _period_end(c) < latest_end], key=_period_end, reverse=True)
        if not older:
            continue
        prev = older[0]
        a, b = _num(prev["value"]), _num(cur["value"])
        if a is None or b is None:
            continue
        pw = _period_words(prev)
        if f == "revenue":
            if a > 0:
                pct = 100 * (b - a) / a
                trend = "up" if pct > 0.5 else "down" if pct < -0.5 else "flat"
                x.say("accounts", f"Revenue was {trend} {abs(pct):.0f}% on {pw} ({money(a, prev.get('unit') or unit)}).", [prev, cur], derived="growth")
            elif a == 0 and b > 0:
                x.say("accounts", f"Revenue rose from {money(0, unit)} reported for {pw}.", [prev, cur], derived="growth")
        else:
            if (a < 0) != (b < 0):
                x.say("accounts", f"The net result swung from {'a loss' if a < 0 else 'a profit'} in {pw} to {'a loss' if b < 0 else 'a profit'} in {period}.", [prev, cur], derived="sign_change")
    eq, assets = latest.get("equity"), latest.get("assets")
    if eq and _num(eq["value"]) is not None and _num(eq["value"]) < 0:
        x.say("accounts", f"Equity was negative ({money(eq['value'], eq.get('unit') or unit)}) at the end of {period}.", [eq], flag="warning")
    elif eq and assets and (_num(assets["value"]) or 0) > 0:
        ratio = 100 * (_num(eq["value"]) or 0) / (_num(assets["value"]) or 1)
        x.say("accounts", f"Equity ratio {ratio:.0f}% (equity / total assets of {money(assets['value'], assets.get('unit') or unit)}).", [eq, assets], derived="equity_ratio")
    rev, op = latest.get("revenue"), latest.get("operating_result")
    if rev and op and (_num(rev["value"]) or 0) > 0:
        margin = 100 * (_num(op["value"]) or 0) / (_num(rev["value"]) or 1)
        x.say("accounts", f"Operating margin {margin:.1f}% in {period} (operating result / revenue).", [op, rev], derived="operating_margin")
    acct_type = _s(any_c.get("account_type"))
    same_type = [c for c in avail if _s(c.get("account_type")) == acct_type]
    rev_years = sorted({_period_end(c): c for c in same_type if c.get("field") == "revenue"}.items())
    per_year: dict[str, dict] = {}
    for c in sorted(avail, key=lambda c: list(FIN_LABEL).index(c["field"]) if c.get("field") in FIN_LABEL else 99):
        per_year.setdefault(_period_end(c)[:4], c)
    years = sorted(y for y in per_year if y)
    if len(rev_years) >= 3:
        x.say("accounts", "Revenue by filed year: " + ", ".join(f"{money(c['value'], c.get('unit') or unit)} ({_period_words(c)})" for _, c in rev_years) + ".",
              [c for _, c in rev_years], derived="trajectory")
    elif len(years) > 1:
        x.say("accounts", f"Filed figures are available for {', '.join(years)}.", [per_year[y] for y in years])
    prev_end = max((_period_end(c) for c in same_type if _period_end(c) < latest_end), default=None)
    prev = {c["field"]: c for c in same_type if prev_end and _period_end(c) == prev_end}
    x.facts["fin"] = {"latest": latest, "prev": prev, "period": period, "unit": unit, "years": years, "end": latest_end}
    _peer_context(x)


def _peer_context(x: _Ctx) -> None:
    """Where the latest filed figures sit among comparable companies (committed peer table; see peers.py)."""
    fin = x.facts.get("fin") or {}
    latest, prev = fin.get("latest") or {}, fin.get("prev") or {}
    ref = peers.load_reference()
    ind = x.first("industry")
    if not ref or not latest or not ind or not isinstance(ind.get("value"), dict):
        return
    code = _s(ind["value"].get("kode")).strip()
    if peers.division(code) is None:
        return
    nok = all(_s(c.get("unit") or "NOK").upper() == "NOK" for c in latest.values())
    vals = {f: _num(c.get("value")) for f, c in latest.items()}
    pvals = {f: _num(c.get("value")) for f, c in prev.items()}
    m = peers.metrics_from(vals, pvals if pvals else None)
    if not nok:  # peers are NOK figures; only ratios compare across currencies
        m = {k: v for k, v in m.items() if k in ("equity_ratio", "operating_margin", "revenue_growth")}
    inputs = {"revenue": ["revenue"], "annual_result": ["annual_result"], "operating_margin": ["operating_result", "revenue"],
              "equity_ratio": ["equity", "assets"], "revenue_growth": ["revenue"]}
    if (m.get("equity_ratio") or 0) < 0:
        m.pop("equity_ratio")  # negative equity is stated as such; a negative ratio rank adds nothing
    pos = {k: peers.position(k, v, code, vals.get("assets"), ref) for k, v in m.items()}
    pos = {k: p for k, p in pos.items() if p}
    if not pos:
        return
    # Use one peer group for the sentence: the most specific group that covers the most metrics.
    order = {k: i for i, k in enumerate(["d|b", "d", "s", "all"])}
    def kind(g: str) -> str:
        return "d|b" if "|b" in g else "d" if g.startswith("d") else "s" if g.startswith("s") else "all"
    groups: dict[str, list[str]] = {}
    for k, p in pos.items():
        groups.setdefault(p["group"], []).append(k)
    gkey = sorted(groups, key=lambda g: (-len(groups[g]), order[kind(g)]))[0]
    chosen = [k for k in ("revenue", "revenue_growth", "operating_margin", "annual_result", "equity_ratio") if k in groups[gkey]]
    meta = ref.get("meta") or {}
    ref_id = "ref-peers"
    x.references[ref_id] = {
        "id": ref_id, "kind": "peer_reference", "path": "src/norway_company_agent/peer_reference.json",
        "description": (f"Seeded random sample of {meta.get('sample_size')} entities from the frozen 2025-filer universe "
                        f"({meta.get('sample_with_accounts')} with filed accounts), read from Regnskapsregisteret "
                        f"{_s(meta.get('retrieved_from'))[:10]} to {_s(meta.get('retrieved_to'))[:10]}; percentiles per peer group."),
        "source_urls": [meta.get("accounts_source"), meta.get("universe_source")],
        "sample_manifest_sha256": meta.get("sample_manifest_sha256"), "universe_sha256": meta.get("universe_sha256"),
    }
    def fmt(k: str, v: float) -> str:
        if k in ("revenue", "annual_result"):
            return money(v, "NOK")
        return f"{v:+.0f}%" if k == "revenue_growth" else f"{v:.1f}%" if k == "operating_margin" else f"{v:.0f}%"
    parts = []
    for k in chosen:
        p = pos[k]
        parts.append(f"{peers.METRIC_WORDS[k]} {peers.rank_words(p['percentile'])} (peer median {fmt(k, p['median'])})")
    ns = sorted({pos[k]["n"] for k in chosen})
    count = f"{ns[0]}" if len(ns) == 1 else f"{ns[0]}-{ns[-1]}"
    label = pos[chosen[0]]["group_label"]
    cs = [ind] + [latest[f] for k in chosen for f in inputs[k] if f in latest] + [prev[f] for k in chosen if k == "revenue_growth" for f in ("revenue",) if f in prev]
    x.say("peers", f"Against peers ({label}; {count} sampled companies per figure): " + "; ".join(parts) + ".", cs,
          reference_ids=[ref_id], derived="peer_position", peer_group=gkey, peer_label=f"{label}; {count} sampled companies per figure",
          peer_positions={k: {f: pos[k][f] for f in ("percentile", "median", "p25", "p75", "n", "group")} for k in chosen})
    x.facts["peers"] = {k: pos[k] for k in chosen}


def _market(x: _Ctx) -> None:
    """How common this line of business is: counts from the frozen universe (committed table)."""
    ref = peers.load_reference()
    ind = x.first("industry")
    if not ref or not ind or not isinstance(ind.get("value"), dict):
        return
    code = _s(ind["value"].get("kode")).strip()
    d = peers.division(code)
    if not d:
        return
    n_code = (ref.get("code_counts") or {}).get(code)
    total = (ref.get("meta") or {}).get("universe_rows")
    addr_c = x.first("business_address")
    addr = addr_c.get("value") if addr_c and isinstance(addr_c.get("value"), dict) else {}
    muni_nr, muni = _s(addr.get("kommunenummer")), _title(addr.get("kommune"))
    n_local = (ref.get("division_municipality_counts") or {}).get(f"{d}|{muni_nr}") if muni_nr else None
    if not n_code or not total:
        return
    x.references.setdefault("ref-universe", {
        "id": "ref-universe", "kind": "universe_counts", "path": "src/norway_company_agent/peer_reference.json",
        "description": f"Counts over the frozen public universe of {total} entities with 2025 filed accounts.",
        "source_urls": [(ref.get("meta") or {}).get("universe_source")], "universe_sha256": (ref.get("meta") or {}).get("universe_sha256"),
    })
    txt = f"Market context: {n_code:,} of the {total:,} entities in the 2025-filer universe share industry code {code}".replace(",", " ")
    cs = [ind]
    if n_local and muni:
        txt += (f"; {n_local:,} entit{'ies' if n_local != 1 else 'y'} in division {d} {'are' if n_local != 1 else 'is'} registered in {muni}").replace(",", " ")
        cs.append(addr_c)
    x.say("market", txt + ".", cs, reference_ids=["ref-universe"], derived="universe_count")


def _people_name(v: dict) -> str:
    raw = v.get("name")
    n = " ".join(_s(p) for p in raw if p) if isinstance(raw, list) else _s(raw)
    n = n.strip()
    o = _s(v.get("organisation_number")).strip()
    if n and o:
        return f"{n} ({o})"
    return n or (f"organisation {o}" if o else "unnamed")


def _leadership(x: _Ctx) -> None:
    roles = [c for c in x.avail("role") if isinstance(c.get("value"), dict)]
    form = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    if not roles:
        gap = (x.gaps("role") or [None])[0]
        txt, st = _gap_text("Leadership roles", gap)
        x.unknown("leadership", txt, [gap] if gap else [], st)
        return
    by: dict[str, list[dict]] = {}
    for c in roles:
        by.setdefault(_s(c["value"].get("role_code")).upper(), []).append(c)
    lead_parts, cs = [], []
    seen_people: dict[str, int] = {}
    for code in ("INNH", "DAGL", "LEDE", "KOMP", "DTPR", "DTSO", "FFØR", "REPR", "BEST"):
        for c in by.get(code, [])[:2]:
            who, role = _people_name(c["value"]), ROLE_EN.get(code, _s(c["value"].get("role")))
            if who in seen_people:  # same person in two roles: "managing director and board chair X"
                i = seen_people[who]
                lead_parts[i] = lead_parts[i][: -len(who) - 1] + f" and {role} {who}"
            else:
                seen_people[who] = len(lead_parts)
                lead_parts.append(f"{role} {who}")
            cs.append(c)
    if lead_parts:
        x.say("leadership", "Leadership in the register: " + "; ".join(lead_parts) + ".", cs)
    board = by.get("MEDL", []) + by.get("NEST", [])
    if board:
        n = len(board) + len(by.get("LEDE", []))
        x.say("leadership", f"The board has {n} member{'s' if n != 1 else ''} including the chair" if by.get("LEDE") else f"The board has {len(board)} member{'s' if len(board) != 1 else ''}",
              board + by.get("LEDE", []))
        x.sentences[-1]["text"] += "."
    svc = []
    for code in ("REVI", "REGN"):
        for c in by.get(code, [])[:1]:
            svc.append((f"{ROLE_EN[code]}: {_people_name(c['value'])}", c))
    if svc:
        x.say("leadership", "Registered " + "; ".join(t for t, _ in svc) + ".", [c for _, c in svc])
    if "DAGL" not in by and form not in ("ENK", "BRL", "ESEK", "SAM", "FLI", "STI") and "INNH" not in by:
        x.unknown("leadership", "No managing director is registered (not required for every company)", [], "not_available")
    changed = sorted([c for c in roles if _s(c["value"].get("last_changed"))], key=lambda c: c["value"]["last_changed"], reverse=True)
    if changed:
        c = changed[0]
        x.say("leadership", f"Most recent role registration: {ROLE_EN.get(_s(c['value'].get('role_code')).upper(), _s(c['value'].get('role')))} on {c['value']['last_changed']}.", [c])


def _find_node(tree: Any, org: str, depth: int = 0) -> dict | None:
    if depth > 12 or not isinstance(tree, dict):
        return None
    if _s(tree.get("organisasjonsnummer")) == org:
        return tree
    for ch in tree.get("children") or []:
        hit = _find_node(ch, org, depth + 1)
        if hit:
            return hit
    return None


def _count(tree: dict, depth: int = 0) -> int:
    if depth > 12:
        return 0
    return sum(1 + _count(ch, depth + 1) for ch in tree.get("children") or [] if isinstance(ch, dict))


def _group(x: _Ctx) -> None:
    org = _s(x.env.get("organisation_number"))
    g = x.first("group_structure")
    if not g or not isinstance(g.get("value"), dict):
        gap = (x.gaps("group_structure") or [None])[0]
        parent = _s(x.ident.get("parent_organisation_number"))
        if parent:
            pc = x.first("parent_organisation_number")
            if pc:
                x.say("group", f"The register lists parent organisation {parent} (group relationship).", [pc], relationship="parent")
                return
        if gap is not None and gap.get("availability") == "not_available":
            x.unknown("group", "Group structure: none published in the register (the entity may still have owners)", [gap])
        elif gap is not None:
            txt, st = _gap_text("Group structure", gap)
            x.unknown("group", txt, [gap], st)
        return
    tree = g["value"]
    node = _find_node(tree, org) or (tree if _s(tree.get("organisasjonsnummer")) in ("", org) else None)
    top_name, top_nr = _s(tree.get("navn")), _s(tree.get("organisasjonsnummer"))
    if node is None:
        x.say("group", f"Part of the group headed by {top_name} ({top_nr}) according to the register's group data.", [g], relationship="member")
        return
    parent_nr = _s(node.get("parentOrganisasjonsnummer"))
    if parent_nr and parent_nr != org:
        share = _s(node.get("grunnlag"))
        rel = _s((node.get("knytningsform") or {}).get("beskrivelse")) if isinstance(node.get("knytningsform"), dict) else ""
        txt = f"Group relationship: subsidiary{(' (' + share + ' owned)') if share else ''} of {_s(node.get('parentNavn'))} ({parent_nr})"
        if top_nr and top_nr not in (parent_nr, org):
            txt += f"; the group's top company is {top_name} ({top_nr})"
        x.say("group", txt + (f" [{rel}]." if rel else "."), [g], relationship="subsidiary")
    n_direct = len([c for c in node.get("children") or [] if isinstance(c, dict)])
    n_all = _count(node)
    if n_direct:
        names = ", ".join(_s(c.get("navn")) for c in (node.get("children") or [])[:3] if isinstance(c, dict))
        more = f" and {n_all - n_direct} further down the chain" if n_all > n_direct else ""
        x.say("group", f"Group relationship: parent company of {n_direct} direct subsidiar{'ies' if n_direct != 1 else 'y'}{more}, e.g. {names}.", [g], relationship="parent")


def _places(x: _Ctx) -> None:
    locs = [c for c in x.avail("registered_workplace") if isinstance(c.get("value"), dict)]
    if locs:
        munis = []
        for c in locs:
            a = c["value"].get("address") if isinstance(c["value"].get("address"), dict) else {}
            m = _title(a.get("kommune") or a.get("poststed"))
            if m and m not in munis:
                munis.append(m)
        where = f" in {', '.join(munis[:5])}" + (f" and {len(munis) - 5} more municipalities" if len(munis) > 5 else "") if munis else ""
        x.say("places", f"It has {len(locs)} registered workplace{'s' if len(locs) != 1 else ''} (subunit{'s' if len(locs) != 1 else ''}){where}.", locs)
        return
    gaps = x.gaps("registered_workplace")
    checked = [c for c in gaps if c.get("availability") == "not_available" and c.get("evidence_ids")]
    if checked:
        x.say("places", "The register lists no separate workplaces (subunits); activity is registered at the main address.", checked[:1], kind="checked_absence")
    elif gaps:
        txt, st = _gap_text("Registered workplaces", gaps[0])
        x.unknown("places", txt, gaps[:1], st)


def _online(x: _Ctx) -> None:
    form = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    expected = form in RESIDENTIAL_FORMS
    site = x.first("official_website")
    if site:
        how = {"orgnr_on_site": "the organisation number is printed on the site", "name_and_address": "name and address on the site match the register"}.get(
            _s(site.get("identity_method")), _s(site.get("identity_method")) or "verified")
        x.say("online", f"Verified official website: {site['value']} ({how}).", [site])
    else:
        gap = (x.gaps("official_website") or [None])[0]
        if gap is not None:
            txt, st = _gap_text("Official website", gap)
            if st == "not_available":
                txt = "Official website: none verified (the register lists none and no candidate could be tied to this organisation number)"
            x.unknown("online", txt, [gap], st, expected=expected)
    socials = [c for c in x.avail("social_profile") if isinstance(c.get("value"), dict)]
    if socials:
        plats = sorted({_s(c["value"].get("platform")) for c in socials if c["value"].get("platform")})
        x.say("online", f"Company-owned profiles linked from its website: {', '.join(plats) or 'yes'}.", socials)
    elif site:
        gap = (x.gaps("social_profile") or [None])[0]
        if gap is not None:
            x.unknown("online", "Company-owned social profiles: none linked from the verified website", [gap], gap.get("availability"), expected=expected)


def _hiring(x: _Ctx) -> None:
    form = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    expected = form in RESIDENTIAL_FORMS or form in NO_OWNER_FORMS
    jobs = [c for c in x.avail("job_posting") if isinstance(c.get("value"), dict)]
    if jobs:
        def jdate(c: dict) -> str:
            v = c["value"]
            return _s(v.get("published") or v.get("date") or v.get("updated") or c.get("effective_date"))
        jobs_sorted = sorted(jobs, key=jdate, reverse=True)
        titles = "; ".join(f"\"{_clip(c['value'].get('title') or c['value'].get('job_title') or 'untitled', 70)}\"" + (f" ({jdate(c)[:10]})" if jdate(c) else "") for c in jobs_sorted[:3])
        x.say("hiring", f"Hiring: {len(jobs)} active job ad{'s' if len(jobs) != 1 else ''} with this organisation number, most recent {titles}.", jobs_sorted[:5])
    else:
        gap = (x.gaps("job_posting") or [None])[0]
        note = _s((gap or {}).get("note"))
        if gap is not None and gap.get("availability") == "not_available" and gap.get("evidence_ids"):
            x.say("hiring", "Hiring: the public job-ad feed was checked and lists no active ads for this organisation number.", [gap], kind="checked_absence")
        elif gap is not None:
            if "not_run" in note or "not run" in note:
                x.unknown("hiring", "Hiring: the job-ad feed was not checked in this run, so hiring is unknown", [gap], "failed", expected=expected)
            elif gap.get("availability") == "not_available" and "feed: complete" in note:
                x.unknown("hiring", "Hiring: the public NAV job-ad feed was read in full and lists no active ads with this organisation number "
                          "or its subunits (ads posted elsewhere are not covered)", [gap], "not_available", expected=expected)
            else:
                txt, st = _gap_text("Hiring (active job ads)", gap)
                x.unknown("hiring", txt, [gap], st, expected=expected)
    acts = [c for c in x.avail("dated_activity") if isinstance(c.get("value"), dict)]
    if acts:
        def adate(c: dict) -> str:
            return _s(c["value"].get("date") or c.get("effective_date"))
        acts = sorted(acts, key=lambda c: (adate(c), _s(c.get("id"))), reverse=True)
        substantive = [c for c in acts if _activity_kind(c) not in ("routine", "tax_relief")]
        top = (substantive or acts)[0]
        run = x.env.get("run") if isinstance(x.env.get("run"), dict) else {}
        asof = _s(run.get("started_at") or run.get("completed_at"))[:10]
        recent = [c for c in acts if asof and _days_between(adate(c)[:10], asof) is not None and 0 <= (_days_between(adate(c)[:10], asof) or 0) <= 365]
        kinds: dict[str, int] = {}
        for c in recent:
            k = _activity_kind(c)
            kinds[k] = kinds.get(k, 0) + 1
        words = {"award": "public contract award", "news": "news or post on its website", "register": "register change", "filing": "accounts filing",
                 "routine": "routine register update", "inspection": "official food-safety inspection", "support": "registered public support award",
                 "licence": "Finanstilsynet licence registration", "research": "funded research project",
                 "tax_relief": "tax-relief report to Støtteregisteret"}
        mix = ", ".join(f"{n} {words.get(k, k)}{'s' if n != 1 else ''}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        newest = acts[0]
        title = _clip(_s(top['value'].get('title') or top['value'].get('kind') or 'item').replace("Register update: ", ""), 90)
        if top is newest:
            txt = f"Latest dated public activity: {adate(top)[:10]} \"{title}\""
        else:  # newer items exist but are routine re-reports; say so instead of hiding them
            txt = (f"Latest non-routine dated activity: {adate(top)[:10]} \"{title}\" (newer routine {'register updates' if _activity_kind(newest) == 'routine' else 'reports'} exist, the latest on "
                   f"{adate(newest)[:10]})")
        if recent and asof:
            txt += f"; in the 12 months before this run: {mix}"
        elif len(acts) > 1:
            txt += f" ({len(acts)} dated items in total, oldest {adate(acts[-1])[:10]})"
        x.say("activity_dated", txt + ".", [top] + ([newest] if newest is not top else []) + [c for c in recent if c is not top and c is not newest][:4])
        x.facts["activity"] = {"top": top, "newest": newest, "recent": recent, "kinds": kinds}
    else:
        gap = (x.gaps("dated_activity") or [None])[0]
        if gap is not None:
            txt, st = _gap_text("Dated public activity (news, announcements)", gap)
            x.unknown("activity_dated", txt, [gap], st, expected=expected)
    ratings = sorted((c for c in x.avail("official_rating") if isinstance(c.get("value"), dict)), key=lambda c: _s(c["value"].get("date")), reverse=True)
    if ratings:  # lane 13: Mattilsynet smilefjes, published only for the company's own or subunit organisation number
        v = ratings[0]["value"]
        more = f" ({len(ratings)} rated establishments)" if len(ratings) > 1 else ""
        x.say("rating", f"Official food-safety rating (Mattilsynet smilefjes) for {_s(v.get('establishment'))}: {_s(v.get('meaning'))}, "
                        f"latest inspection {_s(v.get('date'))[:10]}{more}.", ratings[:3])


_ROUTINE_EVENTS = {"employee_count_reported", "employee_count_change", "vat_registration_change", "contact_change", "postal_address_change", "website_change"}


def _activity_kind(c: dict) -> str:
    v = c.get("value") if isinstance(c.get("value"), dict) else {}
    kind, et = _s(v.get("kind")), _s(v.get("event_type"))
    if kind == "public_procurement_award" or et == "procurement_award":
        return "award"
    if kind == "company_news_or_post":
        return "news"
    lane13 = {"official_inspection": "inspection", "public_support_award": "support", "official_licence": "licence", "public_research_funding": "research"}
    if kind == "public_support_award" and _s(v.get("giver_orgnr")) == "974761076" and "skatteEllerAvgift" in _s(v.get("instrument")):
        return "tax_relief"  # Skatteetaten's recurring tax/duty relief reports (e.g. regional payroll tax) are routine
    if kind in lane13:
        return lane13[kind]
    if et == "annual_accounts_filed":
        return "filing"
    if et.endswith("_removed") or et in _ROUTINE_EVENTS:
        return "routine"
    return "register"


def _days_between(a: str, b: str) -> int | None:
    from datetime import date
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except ValueError:
        return None


CHANGE_WORDS = {
    "changed_value": "changed", "new_role": "new role", "removed_role": "role removed", "new_location": "new workplace",
    "closed_location": "workplace closed", "new_job": "new job ad", "closed_job": "job ad closed", "new_activity": "new dated activity",
    "new_profile": "new company profile", "removed_profile": "profile no longer linked", "new_website": "website verified",
    "website_unavailable": "website no longer verified", "new_filing": "new filed accounts", "no_longer_observed": "no longer observed",
    "new_value": "newly observed", "removed_value": "no longer observed",
}
FIELD_WORDS = {"legal_name": "legal name", "business_address": "business address", "registered_employees": "registered employees",
               "annual_result": "net result", "under_liquidation": "liquidation flag", "official_website": "website", "registered_workplace": "workplace",
               "job_posting": "job ad", "dated_activity": "dated activity", "social_profile": "profile", "legal_form": "legal form"}


def _short(v: Any, field: str) -> str:
    if v is None:
        return "nothing"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}".replace(",", " ")
    if isinstance(v, dict):
        if v.get("role") or v.get("role_code"):
            return f"{ROLE_EN.get(_s(v.get('role_code')).upper(), _s(v.get('role')))} {_people_name(v)}"
        if v.get("title"):
            return f"\"{_clip(v['title'], 60)}\""
        if v.get("url"):
            return _s(v["url"])
        if v.get("adresse") is not None or v.get("postnummer"):
            return ", ".join([*map(_s, v.get("adresse") or []), f"{_s(v.get('postnummer'))} {_s(v.get('poststed'))}".strip()])
        if v.get("name"):
            return _s(v["name"])
        if v.get("beskrivelse"):
            return _s(v["beskrivelse"])
    return _clip(v, 80)


def _changes(x: _Ctx) -> list[dict]:
    raw = x.env.get("changes")
    refresh = x.env.get("refresh") if isinstance(x.env.get("refresh"), dict) else {}
    prev_run = _s(refresh.get("previous_run_id"))
    out: list[dict] = []
    if not isinstance(raw, list):
        raw = []
    by_key = {c["key"]: c for c in x.claims if c.get("availability") == "available" and c.get("value") is not None and isinstance(c.get("key"), str)}
    material = [c for c in raw if isinstance(c, dict) and c.get("material", True) is not False
                and isinstance(c.get("change_type"), str) and isinstance(c.get("field"), str)]
    for ch in material[:12]:
        field = _s(ch.get("field"))
        ctype = _s(ch.get("change_type"))
        fw = FIELD_WORDS.get(field, FIN_LABEL.get(field, field.replace("_", " ")))
        if ctype == "changed_value":
            txt = f"{fw[:1].upper() + fw[1:]} changed from {_short(ch.get('old_value'), field)} to {_short(ch.get('new_value'), field)}"
        elif ctype == "new_filing":
            cur = by_key.get(ch.get("key")) if isinstance(ch.get("key"), str) else None
            txt = f"New filed accounts: {fw} {money(ch.get('new_value'), (cur or {}).get('unit'))}" + (f" for {_period_words(cur)}" if cur else "")
        else:
            val = ch.get("new_value") if ch.get("new_value") is not None else ch.get("old_value")
            txt = f"{CHANGE_WORDS.get(ctype, ctype.replace('_', ' ')).capitalize()}: {_short(val, field)}"
        cur = by_key.get(ch.get("key")) if isinstance(ch.get("key"), str) else None
        item = {"text": txt + ".", "change_type": ctype, "field": field, "claim_ids": [cur["id"]] if cur else [],
                "evidence_ids": [i for i in (ch.get("new_evidence_ids") or []) if i in x.evidence]}
        old_ev = [e.get("source_url") for e in ch.get("old_evidence") or [] if isinstance(e, dict) and e.get("source_url")]
        if old_ev:
            item["previous_source_urls"] = list(dict.fromkeys(old_ev))[:3]
        out.append(item)
    if out:
        cs = [x.by_id[i] for ch in out for i in ch["claim_ids"] if i in x.by_id]
        when = _s(refresh.get("previous_completed_at"))[:10]
        lead = f"Since the previous run{(' (' + prev_run + (', ' + when if when else '') + ')') if prev_run else ''}: "
        lead += "; ".join(c["text"].rstrip(".") for c in out[:4]) + (f"; and {len(out) - 4} more." if len(out) > 4 else ".")
        minor = [c for c in raw if isinstance(c, dict) and c.get("material") is False]
        if minor:
            kinds = sorted({FIELD_WORDS.get(_s(c.get("field")), _s(c.get("field")).replace("_", " ")) for c in minor})
            lead += f" Also {len(minor)} minor update{'s' if len(minor) != 1 else ''} ({', '.join(kinds[:4])})."
        if cs:
            x.say("changes", lead, cs, flag="change")
        else:
            x.unknown("changes", lead + " (removed items are cited by their previous evidence)", [], "available")
    elif prev_run:
        anchor = x.first("legal_name") or x.first("organisation_number")
        when = _s(refresh.get("previous_completed_at"))[:16].replace("T", " ")
        x.say("changes", f"No material changes since the previous run ({prev_run}{', ' + when + ' UTC' if when else ''}); every published fact "
              "was compared (name, form, address, status, roles, workplaces, industry, employees, key accounts, website, job ads).",
              [anchor] if anchor else [], kind="refresh")
    else:
        x.unknown("changes", "Changes over time: this is the first snapshot of this company, so there is no earlier run to compare against", [], "not_applicable")
    stale = [c for c in x.claims if c.get("stale") and c.get("availability") == "available" and c.get("value") is not None]
    if stale:
        fields = sorted({FIELD_WORDS.get(_s(c.get("field")), _s(c.get("field")).replace("_", " ")) for c in stale})
        x.say("changes", f"Carried forward unchanged from the previous run because this refresh could not re-check them: {', '.join(fields)}.", stale, flag="stale")
    return out


def _fact_bits(x: _Ctx) -> dict[str, Any]:
    """Short phrases (with the claims behind them) reused by the headline and the decision block."""
    out: dict[str, Any] = {}
    fin = x.facts.get("fin") or {}
    latest, prev, unit, period = fin.get("latest") or {}, fin.get("prev") or {}, fin.get("unit"), fin.get("period")
    rev, res, eq, assets = (latest.get(f) for f in ("revenue", "annual_result", "equity", "assets"))
    if rev:
        out["revenue"] = (f"revenue {money(rev['value'], rev.get('unit') or unit)} in {period}", [rev])
        p = prev.get("revenue")
        a, b = (_num(p["value"]) if p else None), _num(rev["value"])
        if p and a and a > 0 and b is not None:
            pct = 100 * (b - a) / a
            out["growth"] = (f"revenue {'up' if pct > 0.5 else 'down' if pct < -0.5 else 'flat'} {abs(pct):.0f}% on {_period_words(p)}", [p, rev], pct)
    if res and _num(res["value"]) is not None:
        v = _num(res["value"]) or 0
        out["result"] = (f"net {'loss' if v < 0 else 'profit'} of {money(abs(v), res.get('unit') or unit)} in {period}" if v else f"net result {money(0, res.get('unit') or unit)} in {period}", [res], v)
    if eq and _num(eq["value"]) is not None:
        e = _num(eq["value"]) or 0
        if e < 0:
            out["equity"] = (f"negative equity ({money(e, eq.get('unit') or unit)})", [eq], None)
        elif assets and (_num(assets["value"]) or 0) > 0:
            r = 100 * e / (_num(assets["value"]) or 1)
            out["equity"] = (f"equity ratio {r:.0f}%", [eq, assets], r)
    emp = x.first("registered_employees")
    if emp and _num(emp.get("value")) is not None:
        n = int(_num(emp["value"]) or 0)
        out["employees"] = (f"{n} registered employee{'s' if n != 1 else ''}", [emp], n)
    return out


def _peer_phrase(x: _Ctx, metric: str) -> str:
    p = (x.facts.get("peers") or {}).get(metric)
    return f" ({peers.rank_short(p['percentile'])} among peers)" if p else ""


def _decision(x: _Ctx) -> list[dict]:
    """'What to know before you apply / sell / partner / invest': each point is built from cited claims only."""
    f = _fact_bits(x)
    form = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    roles = [c for c in x.avail("role") if isinstance(c.get("value"), dict)]
    by_role: dict[str, list[dict]] = {}
    for c in roles:
        by_role.setdefault(_s(c["value"].get("role_code")).upper(), []).append(c)
    br, lq = x.first("bankrupt"), x.first("under_liquidation")
    flagged = [c for c in (br, lq) if c and c.get("value") is True]
    jobs = [c for c in x.avail("job_posting") if isinstance(c.get("value"), dict)]
    job_gap = (x.gaps("job_posting") or [None])[0]
    site = x.first("official_website")
    socials = [c for c in x.avail("social_profile") if isinstance(c.get("value"), dict)]
    founded = x.first("founded_date")
    run = x.env.get("run") if isinstance(x.env.get("run"), dict) else {}
    asof = _s(run.get("started_at") or run.get("completed_at"))[:10]
    peer_ids = sorted({"ref-peers"} if x.facts.get("peers") else set())
    blocks: list[dict] = []

    def block(audience: str, title: str, points: list[tuple | None]) -> None:
        pts = []
        for p in points:
            if not p:
                continue
            text, cs = p[0], [c for c in p[1] if c and c.get("id")]
            if not cs:
                continue
            ids = list(dict.fromkeys(c["id"] for c in cs))
            x.cited.update(ids)
            item = {"text": text, "claim_ids": ids, "evidence_ids": x._ev(cs)}
            if len(p) > 2 and p[2]:
                item["reference_ids"] = p[2]
            pts.append(item)
        if pts:
            blocks.append({"audience": audience, "title": title, "points": pts})

    def peer_refs(*metrics: str) -> list[str]:
        return peer_ids if any(m in (x.facts.get("peers") or {}) for m in metrics) else []

    warn = (f"the register flags it as {' and '.join('bankrupt' if c is br else 'under liquidation' for c in flagged)}", flagged) if flagged else None
    status_ok = ("no bankruptcy or liquidation proceedings are registered", [br, lq]) if br and lq and not flagged and br.get("value") is False and lq.get("value") is False else None

    # --- apply (job seekers)
    hiring = None
    if jobs:
        hiring = (f"{len(jobs)} active job ad{'s' if len(jobs) != 1 else ''} carry its organisation number", jobs[:5])
    elif job_gap is not None and job_gap.get("availability") == "not_available" and job_gap.get("evidence_ids"):
        hiring = ("no active job ads with its organisation number in the public NAV job feed at the time of this run", [job_gap])
    size = (f"{f['employees'][0]} on the Central Coordinating Register", f["employees"][1]) if "employees" in f else None
    trend = None
    bits = [b for b in (f.get("growth"), f.get("result")) if b]
    if bits:
        trend = ("latest accounts: " + ", ".join(b[0] for b in bits), [c for b in bits for c in b[1]])
    locs = [c for c in x.avail("registered_workplace") if isinstance(c.get("value"), dict)]
    munis: list[str] = []
    for c in locs:
        a = c["value"].get("address") if isinstance(c["value"].get("address"), dict) else {}
        m = _title(a.get("kommune") or a.get("poststed"))
        if m and m not in munis:
            munis.append(m)
    where = (f"workplaces registered in {', '.join(munis[:4])}" + (f" and {len(munis) - 4} more" if len(munis) > 4 else ""), locs) if munis else None
    residential = form in RESIDENTIAL_FORMS  # housing cooperatives/condominiums: no jobs to apply for, no shares to buy
    if not residential and (size or hiring):
        block("apply", "Before you apply for a job", [warn, size, hiring, (f["growth"][0], f["growth"][1]) if "growth" in f else None, where])

    # --- sell (supplier / credit view)
    solid = None
    if "equity" in f:
        txt, cs, r = f["equity"]
        solid = (txt + (_peer_phrase(x, "equity_ratio") if r is not None else ""), cs,
                 peer_refs("equity_ratio") if r is not None else [])
    result = (f["result"][0] + _peer_phrase(x, "annual_result"), f["result"][1], peer_refs("annual_result")) if "result" in f else None
    deciders: list[tuple[str, dict]] = []
    for code in ("DAGL", "INNH", "LEDE"):
        for c in by_role.get(code, [])[:1]:
            who = _people_name(c["value"])
            same = next((i for i, (t, _) in enumerate(deciders) if t.endswith(" " + who)), None)
            if same is None:
                deciders.append((f"{ROLE_EN.get(code, code)} {who}", c))
            else:
                deciders[same] = (deciders[same][0][: -len(who) - 1] + f" and {ROLE_EN.get(code, code)} {who}", deciders[same][1])
                deciders.append(("", c))
    deciders_txt = "; ".join(t for t, _ in deciders if t)
    who = ("decision makers in the register: " + deciders_txt, [c for _, c in deciders]) if deciders else None
    audit = None
    if by_role.get("REVI"):
        audit = (f"accounts audited by {_people_name(by_role['REVI'][0]['value'])}", by_role["REVI"][:1])
    block("sell", "Before you sell to it or give it credit", [warn or status_ok, solid, result, audit])  # leaders are in the overview

    # --- partner
    age = None
    days = _days_between(_s(founded.get("value"))[:10], asof) if founded and asof else None
    if founded and days is not None:
        yrs = int(days // 365.25)
        age = (f"founded {_s(founded['value'])[:10]}" + (f" (about {yrs} year{'s' if yrs != 1 else ''} before this run)" if yrs >= 1 else ""), [founded])
    online = None
    if site:
        plats = sorted({_s(c["value"].get("platform")) for c in socials if c["value"].get("platform")})
        online = (f"verified website {site['value']}" + (f" linking to its {', '.join(plats)} profile{'s' if len(plats) != 1 else ''}" if plats else ""), [site] + socials)
    grp = next((s for s in x.sentences if s["topic"] == "group"), None)
    group = (grp["text"].rstrip(".").replace("Group relationship: ", "").split("; the group's top")[0].split(" [")[0], [x.by_id[i] for i in grp["claim_ids"] if i in x.by_id]) if grp else None
    act = x.facts.get("activity") or {}
    recent = None
    if act.get("top"):
        t = act["top"]
        recent = (f"latest {'non-routine ' if act.get('newest') is not t else ''}dated activity {_s(t['value'].get('date') or t.get('effective_date'))[:10]}: \"{_clip(t['value'].get('title') or '', 80)}\"", [t])
    block("partner", "Before you partner with it", [warn, age, online, group, recent if not (online or group) else None])

    # --- invest
    scale = None
    if "revenue" in f:
        g = f.get("growth")
        scale = (f["revenue"][0] + _peer_phrase(x, "revenue") + (f"; {g[0]}" + _peer_phrase(x, "revenue_growth") if g else ""),
                 f["revenue"][1] + (g[1] if g else []), peer_refs("revenue", "revenue_growth"))
    marg = next((s for s in x.sentences if s.get("derived") == "operating_margin"), None)
    margin = ("operating margin" + marg["text"][len("Operating margin"):].rstrip(".").replace(" (operating result / revenue)", "") + _peer_phrase(x, "operating_margin"),
              [x.by_id[i] for i in marg["claim_ids"] if i in x.by_id], peer_refs("operating_margin")) if marg else None
    hist = next((s for s in x.sentences if s.get("derived") == "trajectory"), None)
    history = ("revenue by filed year" + hist["text"][len("Revenue by filed year"):].rstrip("."), [x.by_id[i] for i in hist["claim_ids"] if i in x.by_id]) if hist else None
    if not residential and form not in NO_OWNER_FORMS:
        block("invest", "Before you invest", [warn, scale, margin, history])
    return blocks


def _headline(x: _Ctx) -> str:
    name = _s(x.value("legal_name") or x.ident.get("name")) or _s(x.env.get("organisation_number"))
    code = _s(x.value("legal_form") or x.ident.get("legal_form")).upper()
    form = LEGAL_FORMS.get(code, code)
    ind = x.value("industry")
    what = x.facts.get("industry_en")
    if not what:
        what = nace_sector(ind.get("kode")) if isinstance(ind, dict) and _s(ind.get("kode")) not in UNSPECIFIED_NACE else None
    addr = x.value("business_address")
    place = _title(addr.get("kommune")) if isinstance(addr, dict) else ""
    bits = [f"{name}: {form}" + (f", {what}" if what else "") + (f", {place}" if place else "")]
    f = _fact_bits(x)
    if "employees" in f:
        bits.append(f["employees"][0])
    if "revenue" in f:
        g = f.get("growth")
        bits.append(f["revenue"][0] + (f" ({g[2]:+.0f}% on the year before)" if g else ""))
    if "result" in f:
        bits.append(f["result"][0].split(" in ")[0])
    warn = [s for s in x.sentences if s.get("flag") == "warning"]
    if warn:
        bits.append("warning: " + ("bankrupt/liquidation" if warn[0]["topic"] == "status" else "negative equity"))
    return "; ".join(bits)


TOPICS: list[tuple[str, Callable[[_Ctx], Any]]] = [
    ("identity", _identity), ("activity", _activity), ("status", _status), ("accounts", _accounts), ("market", _market),
    ("leadership", _leadership), ("group", _group), ("places", _places), ("online", _online), ("hiring", _hiring),
]

AUDIENCE_LABEL = {"apply": "If you are applying for a job", "sell": "If you are selling to it or giving it credit",
                  "partner": "If you are considering a partnership", "invest": "If you are considering investing"}


def render_text(syn: dict) -> str:
    """Compact plain-text brief of a synthesis (any version): overview, numbers, peers, online/hiring/activity,
    one line per decision audience, changes, and what could not be verified. Used for `brief` and for grading."""
    sents = syn.get("sentences") or []

    def pick(pred: Callable[[dict], bool], limit: int = 9) -> list[str]:
        return [s["text"] for s in sents if pred(s)][:limit]

    out = [_s(syn.get("headline"))]
    overview = [_clip(t, 170) for t in pick(lambda s: s.get("topic") in ("identity", "activity") or (s.get("topic") == "status" and s.get("flag") == "warning"))]
    overview += pick(lambda s: s.get("topic") == "leadership" and s["text"].startswith("Leadership"), 1)
    overview += pick(lambda s: s.get("topic") == "group", 1) + pick(lambda s: s.get("topic") == "places" and s.get("kind") != "checked_absence")
    # the brief keeps the latest figures and warnings here; growth, equity ratio and margin appear in the decision lines
    numbers = pick(lambda s: s.get("topic") == "accounts" and (s["text"].startswith("Latest filed") or s.get("flag") == "warning"))
    for s in sents:
        if s.get("topic") == "peers" and isinstance(s.get("peer_positions"), dict) and s.get("peer_label"):
            ranks = ", ".join(f"{peers.METRIC_WORDS.get(k, k)} {peers.rank_short(v['percentile'])}" for k, v in s["peer_positions"].items())
            numbers.append(f"Against peers ({s['peer_label'].replace('companies in the same sector (', '').replace(')', ' sector', 1) if s['peer_label'].startswith('companies in the same sector') else s['peer_label']}): {ranks}.")
        elif s.get("topic") == "peers":
            numbers.append(s["text"])
    activity = pick(lambda s: s.get("topic") in ("online", "hiring", "activity_dated", "rating"))
    unknowns = syn.get("unknowns") or []
    activity += [u["text"] + "." for u in unknowns if u.get("topic") == "hiring"]
    for title, body in (("Overview", overview), ("Numbers", numbers), ("Online, hiring and dated activity", activity)):
        if body:
            out.append(f"\n{title}:\n" + "\n".join(f"- {t}" for t in body))
    dec = syn.get("decision") or []
    if dec:
        out.append("\nWhat to know before you decide:")
        for b in dec:
            pts = "; ".join(p["text"] for p in b.get("points") or [])
            out.append(f"- {AUDIENCE_LABEL.get(b.get('audience'), b.get('title', ''))}: {pts}.")
    changes = [t.split("; every published fact")[0] + ("; every published fact was re-checked." if "; every published fact" in t else "")
               for t in pick(lambda s: s.get("topic") == "changes")] + [u["text"] + "." for u in unknowns if u.get("topic") == "changes"]
    if changes:
        out.append("\nChanges since the previous run:\n" + "\n".join(f"- {t}" for t in changes))
    gaps = [u for u in unknowns if u.get("topic") not in ("changes", "hiring")]
    if gaps:
        out.append("\nNot verified, and why:\n" + "\n".join(
            f"- {u['text']}" + (f" (why: {u['reason']})" if u.get("topic") == "online" and u.get("reason") else "")
            + "." for u in gaps))
    return "\n".join(out).strip()


def build_synthesis(env: dict) -> dict:
    """Pure function of the envelope: same envelope in, same synthesis out."""
    if not isinstance(env, dict):
        return {"status": "failed", "method": METHOD, "error": f"envelope is {type(env).__name__}, not an object", "sentences": [], "unknowns": [], "changes": [], "text": "",
                "supported_claim_ids": []}
    x = _Ctx(env)
    errors: list[str] = []
    state = _s(env.get("state") or env.get("terminal_status"))
    if not x.claims or not any(c.get("availability") == "available" for c in x.claims):
        x.unknown("identity", f"No verified facts for organisation number {_s(env.get('organisation_number')) or '?'}"
                  + (f" (run state: {state})" if state else ""), [], "not_available" if state == "not_found" else "failed")
        syn = {"status": "not_available", "method": METHOD, "headline": f"{_s(env.get('organisation_number'))}: no verified facts", "sentences": [],
               "unknowns": x.unknowns, "changes": [], "decision": [], "references": [], "text": "", "supported_claim_ids": []}
        syn["brief"] = render_text(syn)
        return syn
    for name, fn in TOPICS:
        try:
            fn(x)
        except Exception as exc:  # one malformed claim must not drop the rest of the synthesis
            errors.append(f"{name}: {type(exc).__name__}: {_clip(exc, 160)}")
    try:
        changes = _changes(x)
    except Exception as exc:
        changes = []
        errors.append(f"changes: {type(exc).__name__}: {_clip(exc, 160)}")
    if state == "partial":
        x.unknown("run", "This envelope is partial: some sources timed out or failed in this run", [], "failed")
    try:
        decision = _decision(x)
    except Exception as exc:
        decision = []
        errors.append(f"decision: {type(exc).__name__}: {_clip(exc, 160)}")
    try:
        headline = _headline(x)
    except Exception as exc:
        headline = _s(env.get("organisation_number"))
        errors.append(f"headline: {type(exc).__name__}")
    syn = {
        "status": "available" if x.sentences else "not_available",
        "method": METHOD,
        "headline": headline,
        "sentences": x.sentences,
        "decision": decision,
        "unknowns": x.unknowns,
        "changes": changes,
        "references": [x.references[k] for k in sorted(x.references)],
        "text": " ".join(s["text"] for s in x.sentences),
        "supported_claim_ids": sorted(x.cited, key=lambda i: (len(i), i)),
    }
    try:
        syn["brief"] = render_text(syn)
    except Exception as exc:
        syn["brief"] = syn["text"]
        errors.append(f"brief: {type(exc).__name__}")
    if errors:
        syn["errors"] = errors
    return syn


def add_synthesis(env: Any) -> Any:
    """Return a copy of the envelope with a `synthesis` section. Never raises; never touches other sections."""
    if not isinstance(env, dict):
        return env
    out = dict(env)
    try:
        out["synthesis"] = build_synthesis(copy.deepcopy(env))
    except Exception as exc:  # pragma: no cover - build_synthesis already guards each topic
        out["synthesis"] = {"status": "failed", "method": METHOD, "error": f"{type(exc).__name__}: {_clip(exc, 200)}", "sentences": [], "unknowns": [],
                            "changes": [], "text": "", "supported_claim_ids": []}
    return out


def summary_from_synthesis(env: Any) -> Any:
    """Opt-in (SIGNALPOST_SUMMARY_FROM_SYNTHESIS=1): replace `summary.text` with the cited synthesis brief, keeping the old
    template text as `summary.template_text`. Every statement in the brief is cited in `synthesis`; ids are copied over."""
    if not isinstance(env, dict) or not isinstance(env.get("synthesis"), dict) or env["synthesis"].get("status") != "available":
        return env
    syn = env["synthesis"]
    old = env.get("summary") if isinstance(env.get("summary"), dict) else {}
    out = dict(env)
    out["summary"] = {**old, "text": syn.get("brief") or syn.get("text"), "template_text": old.get("text"),
                      "supported_claim_ids": syn.get("supported_claim_ids") or [], "unknowns": [u.get("text") for u in syn.get("unknowns") or []],
                      "method": METHOD + "; see synthesis.sentences/decision for per-statement claim_ids"}
    return out


def validate_synthesis(env: dict, syn: dict | None = None) -> list[str]:
    """Problems with the synthesis: uncited sentences, citations of missing claims, unsupported states, fabricated zeros."""
    syn = syn if syn is not None else env.get("synthesis") or {}
    raw_claims = env.get("claims") if isinstance(env.get("claims"), list) else []
    raw_ev = env.get("evidence") if isinstance(env.get("evidence"), list) else []
    claims = {c["id"]: c for c in raw_claims if isinstance(c, dict) and isinstance(c.get("id"), str)}
    evidence = {e["id"] for e in raw_ev if isinstance(e, dict) and isinstance(e.get("id"), str)}
    problems: list[str] = []
    for s in syn.get("sentences") or []:
        if not s.get("claim_ids"):
            problems.append(f"{s.get('id')}: no supporting claim")
        for cid in s.get("claim_ids") or []:
            c = claims.get(cid)
            if c is None:
                problems.append(f"{s.get('id')}: cites unknown claim {cid}")
            elif c.get("availability") != "available" and not (s.get("kind") == "checked_absence" and isinstance(c.get("evidence_ids"), list) and c.get("evidence_ids")):
                problems.append(f"{s.get('id')}: cites {cid} with availability {c.get('availability')}")
        for eid in s.get("evidence_ids") or []:
            if eid not in evidence:
                problems.append(f"{s.get('id')}: cites unknown evidence {eid}")
    # A value that is missing in every claim must never appear as a zero in the text.
    for s in syn.get("sentences") or []:
        for cid in s.get("claim_ids") or []:
            c = claims.get(cid) or {}
            if c.get("availability") == "available" and c.get("value") is None:
                problems.append(f"{s.get('id')}: cites {cid} whose value is null")
    ref_ids = {r.get("id") for r in syn.get("references") or [] if isinstance(r, dict)}
    items = [(s.get("id"), s) for s in syn.get("sentences") or []]
    items += [(f"{b.get('audience')}#{i + 1}", p) for b in syn.get("decision") or [] for i, p in enumerate(b.get("points") or [])]
    for sid, s in items:
        for rid in s.get("reference_ids") or []:
            if rid not in ref_ids:
                problems.append(f"{sid}: cites unknown reference {rid}")
    for b in syn.get("decision") or []:
        for i, p in enumerate(b.get("points") or []):
            pid = f"{b.get('audience')}#{i + 1}"
            if not p.get("claim_ids"):
                problems.append(f"{pid}: no supporting claim")
            for cid in p.get("claim_ids") or []:
                c = claims.get(cid)
                if c is None:
                    problems.append(f"{pid}: cites unknown claim {cid}")
                elif c.get("availability") != "available" and not (isinstance(c.get("evidence_ids"), list) and c.get("evidence_ids")):
                    problems.append(f"{pid}: cites {cid} with availability {c.get('availability')} and no evidence")
                elif c.get("availability") == "available" and c.get("value") is None:
                    problems.append(f"{pid}: cites {cid} whose value is null")
            for eid in p.get("evidence_ids") or []:
                if eid not in evidence:
                    problems.append(f"{pid}: cites unknown evidence {eid}")
    for u in syn.get("unknowns") or []:
        if u.get("availability") not in STATES:
            problems.append(f"unknown {u.get('topic')}: invalid availability {u.get('availability')}")
    return problems
