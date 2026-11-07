"""Build data/shared_domains.txt.gz: e-mail / homepage domains that the BRREG Enhetsregisteret lists for at least
MIN entities (service providers: housing-co-op managers, pension administrators, accountants, chains). A name-only
website match on such a domain is not accepted unless the domain is the company's own brand (main2 fresh3 audit:
gabler.no was published for two pension funds it administers).

    uv run python scripts/build_shared_domains.py <brreg-enheter.csv(.gz)> [--min 8]
Source: https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv (NLOD 2.0)."""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import re
from pathlib import Path

FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "online.no", "outlook.com", "outlook.no", "yahoo.com", "yahoo.no", "live.no",
            "icloud.com", "me.com", "live.com", "msn.com", "frisurf.no", "getmail.no", "broadpark.no", "c2i.net", "start.no",
            "lyse.net", "altibox.no", "post.no", "epost.no", "mail.com", "protonmail.com", "gmx.com", "tele2.no"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--min", type=int, default=8)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "shared_domains.txt.gz"))
    a = ap.parse_args()
    raw = open(a.csv, "rb").read(2)
    opener = gzip.open if raw == b"\x1f\x8b" else open
    counts: collections.Counter[str] = collections.Counter()
    with opener(a.csv, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        h = next(r)
        ie, ih = h.index("epostadresse"), h.index("hjemmeside")
        for row in r:
            doms = set()
            e = row[ie].strip().lower()
            if "@" in e:
                doms.add(e.rsplit("@", 1)[1])
            w = re.sub(r"^(https?://)?(www\.)?", "", row[ih].strip().lower()).split("/")[0]
            if "." in w:
                doms.add(w)
            counts.update(d for d in doms if d not in FREEMAIL)
    keep = sorted(d for d, n in counts.items() if n >= a.min)
    with gzip.GzipFile(a.out, "wb", mtime=0) as g:
        g.write(("\n".join(keep) + "\n").encode())
    print(len(keep), "shared domains ->", a.out)


if __name__ == "__main__":
    main()
