"""Build data/cc_no_domains.txt.gz (strategy `cc_domains`) from a Common Crawl domain-level web graph.

Usage:  uv run python scripts/build_cc_domain_list.py [--release cc-main-2026-aug-sep-oct] [--vertices local.txt.gz]
        uv run python scripts/build_cc_domain_list.py --heads-from signalpost-company-universe-2025.jsonl.gz

The second form writes data/common_name_heads.txt.gz: first name-words shared by >= 20 universe companies, which
`domain_list` never uses alone as a domain candidate.

Streams the release's `*-domain-vertices.txt.gz` (~1 GB, lines `id<TAB>reversed.domain<TAB>hosts`) from
data.commoncrawl.org once, keeps second-level .no domains (`no.example` -> `example`) and writes them sorted, one
per line. Common Crawl terms of use: https://commoncrawl.org/terms-of-use. The agent never calls Common Crawl at run time.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import urllib.request
from pathlib import Path

BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph/{r}/domain/{r}-domain-vertices.txt.gz"
OUT = Path(__file__).resolve().parents[1] / "data" / "cc_no_domains.txt.gz"


def build_heads(universe: str, out: Path, threshold: int = 20) -> None:
    import collections
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from norway_company_agent.domain_list import _tokens
    heads: collections.Counter = collections.Counter()
    with gzip.open(universe, "rt", encoding="utf-8") as fh:
        for line in fh:
            name = json.loads(line).get("name") or ""
            for style in ("short", "long"):
                toks = [t for t in _tokens(name, style) if t != "og"]
                if toks:
                    heads[toks[0]] += 1
    keep = sorted(h for h, c in heads.items() if c >= 2 * threshold)  # each name is counted under both spellings
    with gzip.open(out, "wt", encoding="ascii", compresslevel=9) as fh:
        for h in keep:
            fh.write(h + "\n")
    print(f"{len(keep)} common heads -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heads-from", help="universe JSONL.gz: write data/common_name_heads.txt.gz instead of the domain list")
    ap.add_argument("--release", default="cc-main-2026-aug-sep-oct")
    ap.add_argument("--vertices", help="local copy of the vertices file instead of downloading")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    if args.heads_from:
        build_heads(args.heads_from, OUT.parent / "common_name_heads.txt.gz")
        return
    if args.vertices:
        raw = open(args.vertices, "rb")
    else:
        req = urllib.request.Request(BASE.format(r=args.release), headers={"User-Agent": "signalpost-agent domain-list build (one-off bulk download)"})
        raw = urllib.request.urlopen(req, timeout=60)
    digest = hashlib.sha256()

    class Tee(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def readinto(self, b) -> int:
            chunk = raw.read(len(b))
            digest.update(chunk)
            b[:len(chunk)] = chunk
            return len(chunk)

    labels: set[str] = set()
    seen_no = False
    with gzip.open(io.BufferedReader(Tee(), 1 << 20), "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            rev = parts[1]
            if rev.startswith("no."):
                seen_no = True
                bits = rev.split(".")
                if len(bits) == 2 and bits[1]:
                    labels.add(bits[1].strip())
            elif seen_no:
                break  # vertices are sorted by reversed domain: the .no block is contiguous
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="ascii", compresslevel=9) as fh:
        for label in sorted(labels):
            fh.write(label + "\n")
    print(f"{len(labels)} .no domains -> {args.out} (sha256 of vertices bytes read: {digest.hexdigest()}, partial if stopped early)")


if __name__ == "__main__":
    main()
