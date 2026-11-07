"""Peer context from a committed reference table (no network at run time).

`peer_reference.json` is built offline by `scripts/build_peer_reference.py` from (a) the frozen public universe
(counts of 2025 filers per industry code, division and municipality) and (b) a seeded random sample of companies
from that universe whose filed accounts were read from Regnskapsregisteret's open API. For each peer group it holds
the number of peers and the 5th..95th percentiles of a few metrics. A company's position is computed against the
most specific group that has at least `MIN_PEERS` values: same industry division and total-assets band, then same
division, then same NACE section, then all sampled companies. Nothing is extrapolated: if no group has enough
values, no peer statement is made.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

REFERENCE_PATH = Path(__file__).with_name("peer_reference.json")
MIN_PEERS = 25
PCTS = list(range(5, 100, 5))  # stored percentiles
METRICS = ("revenue", "annual_result", "operating_margin", "equity_ratio", "revenue_growth")
METRIC_WORDS = {"revenue": "revenue", "annual_result": "net result", "operating_margin": "operating margin",
                "equity_ratio": "equity ratio", "revenue_growth": "revenue growth"}
# Total assets bands (NOK). Upper bound exclusive.
BANDS = [(None, 1e6, "total assets under NOK 1 million"), (1e6, 1e7, "total assets NOK 1-10 million"),
         (1e7, 1e8, "total assets NOK 10-100 million"), (1e8, None, "total assets NOK 100 million or more")]
# NACE Rev. 2.1 / SN2025 sections by two-digit division (the universe uses SN2025 codes).
SECTIONS = [(1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"), (45, 47, "G"),
            (49, 53, "H"), (55, 56, "I"), (58, 60, "J"), (61, 63, "K"), (64, 66, "L"), (68, 68, "M"), (69, 75, "N"),
            (77, 82, "O"), (84, 84, "P"), (85, 85, "Q"), (86, 88, "R"), (90, 93, "S"), (94, 96, "T"), (97, 98, "U"),
            (99, 99, "V")]


SECTION_NAMES = {"A": "agriculture, forestry and fishing", "B": "mining and quarrying", "C": "manufacturing", "D": "energy supply",
                 "E": "water supply and waste", "F": "construction", "G": "wholesale and retail trade", "H": "transport and storage",
                 "I": "accommodation and food service", "J": "publishing, broadcasting and content", "K": "telecommunications and IT services",
                 "L": "finance and insurance", "M": "real estate", "N": "professional, scientific and technical services",
                 "O": "administrative and support services", "P": "public administration", "Q": "education", "R": "health and social work",
                 "S": "arts, sports and recreation", "T": "other services and membership organisations", "U": "households and housing cooperatives",
                 "V": "extraterritorial organisations"}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def division(code: Any) -> str | None:
    c = str(code or "").strip()
    if len(c) < 2 or not c[:2].isdigit() or c[:2] == "00":
        return None
    return c[:2]


def section(div: str | None) -> str | None:
    if not div:
        return None
    d = int(div)
    return next((s for lo, hi, s in SECTIONS if lo <= d <= hi), None)


def band(assets: Any) -> int | None:
    a = _num(assets)
    if a is None or a < 0:
        return None
    for i, (lo, hi, _) in enumerate(BANDS):
        if (lo is None or a >= lo) and (hi is None or a < hi):
            return i
    return None


def metrics_from(latest: dict, prev: dict | None) -> dict[str, float]:
    """Metrics for one company-year. Inputs use the agent's field names (revenue, annual_result, ...)."""
    out: dict[str, float] = {}
    rev, res, op = _num(latest.get("revenue")), _num(latest.get("annual_result")), _num(latest.get("operating_result"))
    eq, assets = _num(latest.get("equity")), _num(latest.get("assets"))
    if rev is not None:
        out["revenue"] = rev
    if res is not None:
        out["annual_result"] = res
    if op is not None and rev is not None and rev > 0:
        out["operating_margin"] = 100 * op / rev
    if eq is not None and assets is not None and assets > 0:
        out["equity_ratio"] = 100 * eq / assets
    if prev is not None:
        p = _num(prev.get("revenue"))
        if p is not None and p > 0 and rev is not None:
            out["revenue_growth"] = 100 * (rev - p) / p
    return out


def _pick_records(records: list[dict]) -> tuple[dict | None, dict | None]:
    """Latest company (SELSKAP) record and the one before it; group accounts only if no company accounts."""
    rs = [r for r in records if isinstance(r, dict)]
    sel = [r for r in rs if str(r.get("account_type") or "").upper() == "SELSKAP"] or rs
    sel = sorted(sel, key=lambda r: str((r.get("period") or {}).get("tilDato") or ""), reverse=True)
    if not sel:
        return None, None
    return sel[0], (sel[1] if len(sel) > 1 else None)


def _quantiles(values: list[float]) -> list[float]:
    v = sorted(values)
    n = len(v)
    out = []
    for p in PCTS:
        k = (n - 1) * p / 100
        lo = int(k)
        hi = min(lo + 1, n - 1)
        x = v[lo] + (v[hi] - v[lo]) * (k - lo)
        out.append(float(f"{x:.4g}"))
    return out


def build_reference(universe: Iterable[dict], sample: Iterable[dict], meta: dict | None = None) -> dict:
    """Aggregate the universe counts and the sampled accounts into the reference table (see module doc)."""
    code_counts: dict[str, int] = {}
    div_counts: dict[str, int] = {}
    div_muni: dict[str, int] = {}
    n_universe = 0
    for r in universe:
        n_universe += 1
        code = str(r.get("industry_code") or "")
        code_counts[code] = code_counts.get(code, 0) + 1
        d = division(code)
        if d:
            div_counts[d] = div_counts.get(d, 0) + 1
            m = str(r.get("municipality_number") or "")
            if m:
                k = f"{d}|{m}"
                div_muni[k] = div_muni.get(k, 0) + 1
    values: dict[str, dict[str, list[float]]] = {}
    years: dict[str, int] = {}
    n_with_accounts = 0
    for s in sample:
        latest, prev = _pick_records(s.get("records") or [])
        if latest is None:
            continue
        n_with_accounts += 1
        y = str((latest.get("period") or {}).get("tilDato") or "")[:4]
        years[y] = years.get(y, 0) + 1
        m = metrics_from(latest, prev)
        d = division(s.get("industry_code"))
        b = band(latest.get("assets"))
        keys = ["all"]
        if d:
            keys += [f"s{section(d)}", f"d{d}"]
            if b is not None:
                keys.append(f"d{d}|b{b}")
        for k in keys:
            g = values.setdefault(k, {})
            for name, v in m.items():
                g.setdefault(name, []).append(v)
    groups: dict[str, dict] = {}
    for k, g in values.items():
        out = {name: {"n": len(v), "q": _quantiles(v)} for name, v in g.items() if len(v) >= MIN_PEERS}
        if out:
            groups[k] = out
    return {
        "version": "peer_reference/1",
        "meta": {**(meta or {}), "universe_rows": n_universe, "sample_with_accounts": n_with_accounts, "latest_year_counts": years,
                 "accounts_source": "https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}",
                 "universe_source": "https://builderr.ai/signalpost-company-universe-2025.jsonl.gz",
                 "universe_sha256": "b82d6a3e7231d1759a958c282bc4366b80ec2fab8095053d8ed7fa9cd01bc838",
                 "percentiles": PCTS, "min_peers": MIN_PEERS, "bands": [b[2] for b in BANDS]},
        "code_counts": code_counts, "division_counts": div_counts, "division_municipality_counts": div_muni,
        "groups": groups,
    }


@lru_cache(maxsize=2)
def load_reference(path: str | None = None) -> dict | None:
    p = Path(path) if path else REFERENCE_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def percentile_of(value: float, q: list[float]) -> float:
    """Approximate percentile rank of value among the peers from the stored 5..95 percentiles (ties -> middle)."""
    if value < q[0]:
        return 2.5
    if value > q[-1]:
        return 97.5
    tied = [i for i, x in enumerate(q) if x == value]
    if tied:
        return (PCTS[tied[0]] + PCTS[tied[-1]]) / 2
    i = next(i for i, x in enumerate(q) if x > value)  # q[i-1] < value < q[i]
    a, b = q[i - 1], q[i]
    return PCTS[i - 1] + 5 * (value - a) / (b - a)


def group_label(key: str, div: str | None) -> str:
    if key == "all":
        return "all sampled companies"
    if key.startswith("s"):
        return f"companies in the same sector ({SECTION_NAMES.get(key[1:], key[1:])})"
    if "|b" in key:
        b = int(key.split("|b")[1])
        return f"companies in industry division {div} (codes {div}.xxx) with {BANDS[b][2]}"
    return f"companies in industry division {div} (codes {div}.xxx)"


def position(metric: str, value: float, industry_code: Any, assets: Any, ref: dict | None = None) -> dict | None:
    """Where `value` sits among the most specific peer group with enough data; None if no group qualifies."""
    ref = ref if ref is not None else load_reference()
    if not ref or value is None:
        return None
    d = division(industry_code)
    b = band(assets)
    keys = ([f"d{d}|b{b}"] if d and b is not None else []) + ([f"d{d}", f"s{section(d)}"] if d else []) + ["all"]
    for k in keys:
        st = (ref.get("groups") or {}).get(k, {}).get(metric)
        if st and st.get("n", 0) >= MIN_PEERS:
            q = st["q"]
            return {"metric": metric, "value": value, "percentile": round(percentile_of(value, q)), "median": q[PCTS.index(50)],
                    "p25": q[PCTS.index(25)], "p75": q[PCTS.index(75)], "n": st["n"], "group": k, "group_label": group_label(k, d)}
    return None


def rank_words(p: float) -> str:
    if p >= 90:
        return "in the top 10%"
    if p >= 75:
        return "in the top quarter"
    if p > 55:
        return "above the median"
    if p >= 45:
        return "close to the median"
    if p > 25:
        return "below the median"
    if p > 10:
        return "in the bottom quarter"
    return "in the bottom 10%"


def rank_short(p: float) -> str:
    return {"in the top 10%": "top 10%", "in the top quarter": "top quarter", "above the median": "above median",
            "close to the median": "near median", "below the median": "below median", "in the bottom quarter": "bottom quarter",
            "in the bottom 10%": "bottom 10%"}[rank_words(p)]
