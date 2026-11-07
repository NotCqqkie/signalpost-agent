"""Website candidates from a list of known .no domains (lane 5, strategy `cc_domains`).

Source: Common Crawl web graph, domain-level vertices (https://commoncrawl.org/web-graphs; terms of use
https://commoncrawl.org/terms-of-use). `scripts/build_cc_domain_list.py` keeps the second-level .no domains and
writes `data/cc_no_domains.txt.gz` (one label per line, e.g. `opplandbyggvask` for opplandbyggvask.no).

The list only answers "does a site with this name exist?". It lets the agent try name variants beyond the three
fixed guesses (`compact.no`, `hyphen-ated.no`, `compact.com`) without blind DNS probing: leading-token prefixes,
the name without weak/geographic words, merged initials (N.K.S. -> nks) and ø/å/æ spelled oe/aa/ae. A listed domain
is a candidate only; publication still needs the unchanged on-site identity gate in `footprint.verify_site`
(the organisation number on the site, or the full legal name with its form suffix plus the registered address
or telephone number). Candidates carry the source tag `cc_domain_list`, which is not a declared source.
"""
from __future__ import annotations

import gzip
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from .footprint import LEGAL_FORMS, WEAK_TOKENS, guess_domains

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "cc_no_domains.txt.gz"
# first name-words shared by >= 20 universe companies (e.g. andersen, pizza, nordic): alone they name someone else's site
HEADS_PATH = Path(__file__).resolve().parents[2] / "data" / "common_name_heads.txt.gz"
SOURCE = "cc_domain_list"
# words that name a place, a trade or a generic noun: alone they match unrelated sites
GENERIC = WEAK_TOKENS | {"norsk", "norske", "bygg", "byggservice", "transport", "regnskap", "data", "elektro", "maskin", "auto", "bil",
                         "eiendomsselskap", "prosjekt", "utleie", "handel", "partner", "partners", "solutions", "systems", "technology",
                         "tech", "media", "design", "energi", "marine", "maritime", "offshore", "consultants", "advokat", "advokatfirma",
                         "tannlege", "lege", "klinikk", "senter", "sameie", "sameiet", "borettslag", "borettslaget", "boligsameie",
                         "boligsameiet", "avd", "avdeling", "oslo", "bergen", "trondheim", "stavanger", "tromso", "vest", "ost", "nord",
                         "sor", "midt", "west", "east", "north", "south", "co", "company", "selskap", "selskapet", "og", "and", "of"}
PLACE_TOKENS = {"norge", "norway", "nordic", "scandinavia", "skandinavia", "oslo", "bergen", "trondheim", "stavanger", "tromso",
                "akershus", "viken", "vestland", "rogaland", "agder", "innlandet", "troms", "finnmark", "nordland", "trondelag"}


def _fold(text: str, style: str) -> str:
    table = {"ø": "o", "Ø": "o", "å": "a", "Å": "a", "æ": "ae", "Æ": "ae"} if style == "short" else \
            {"ø": "oe", "Ø": "oe", "å": "aa", "Å": "aa", "æ": "ae", "Æ": "ae"}
    text = str(text or "").translate(str.maketrans(table)).replace("&", " og ")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def _tokens(name: str, style: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", _fold(name, style))
    merged: list[str] = []
    run = ""
    for t in raw:  # N.K.S. / A B C -> nks
        if len(t) == 1 and t.isalpha() and t not in ("i",):
            run += t
            continue
        if run:
            merged.append(run)
            run = ""
        merged.append(t)
    if run:
        merged.append(run)
    return [t for t in merged if t not in LEGAL_FORMS or t in ("og",)]


def _ok(label: str) -> bool:
    core = label.replace("-", "")
    return 4 <= len(core) <= 63 and not core.isdigit() and not label.startswith("-") and not label.endswith("-")


@lru_cache(maxsize=1)
def common_heads() -> frozenset[str]:
    if not HEADS_PATH.exists():
        return frozenset()
    with gzip.open(HEADS_PATH, "rt", encoding="ascii", errors="ignore") as fh:
        return frozenset(line.strip() for line in fh if line.strip())


def name_variants(name: str) -> list[str]:
    """Candidate second-level labels for a company name, most specific first."""
    out: list[str] = []
    generic = GENERIC | common_heads()

    def add(parts: list[str]) -> None:
        parts = [p for p in parts if p]
        if not parts:
            return
        for label in ("".join(parts), "-".join(parts)):
            if _ok(label) and label not in out:
                out.append(label)

    for style in ("short", "long"):
        toks = [t for t in _tokens(name, style) if t != "og"]
        if not toks or len(toks) > 8:
            continue
        add(toks)
        strong = [t for t in toks if t not in WEAK_TOKENS and t not in PLACE_TOKENS]
        if strong and strong != toks and not all(t in generic or t in PLACE_TOKENS or len(t) < 4 for t in strong):
            add(strong)
        for k in range(len(toks) - 1, 0, -1):  # leading-token prefixes: "proact it norge" -> proact-it, proact
            head = toks[:k]
            if k == 1 and (head[0] in generic or len(head[0]) < 4):
                continue
            if all(t in generic or t in PLACE_TOKENS for t in head):
                continue
            add(head)
        if len(toks) >= 3:  # drop one middle word: "salmaker erik engh" -> salmakerengh
            for i in range(1, len(toks) - 1):
                add(toks[:i] + toks[i + 1:])
        legal = [t for t in re.findall(r"[a-z0-9]+", _fold(name, style)) if t in ("as", "asa")]
        if legal:
            add(toks + legal[-1:])
    return out


@lru_cache(maxsize=2)
def load_labels(path: str | None = None) -> frozenset[str]:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return frozenset()
    with gzip.open(p, "rt", encoding="ascii", errors="ignore") as fh:
        return frozenset(line.strip() for line in fh if line.strip())


def list_candidates(profile: dict[str, Any], limit: int = 4, labels: frozenset[str] | None = None) -> list[tuple[str, str]]:
    """Up to `limit` listed .no domains derived from the legal name, excluding the fixed name guesses."""
    labels = load_labels() if labels is None else labels
    if not labels:
        return []
    name = str(profile.get("name") or "")
    skip = set(guess_domains(name))
    out: list[tuple[str, str]] = []
    for label in name_variants(name):
        dom = f"{label}.no"
        if dom in skip or label not in labels:
            continue
        out.append((f"https://{dom}/", SOURCE))
        if len(out) >= limit:
            break
    return out
