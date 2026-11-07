"""Build the peer reference table used by synthesis peer context (offline, development-time only).

Two steps:

  fetch      Draw a seeded random sample from the frozen public universe (excluding the given manifests) and read
             each company's filed accounts from Regnskapsregisteret's open API (one request per company,
             throttled). Writes normalised records to <work>/peer-sample.jsonl (resumable).
  aggregate  Turn the sample plus the universe into src/norway_company_agent/peer_reference.json: per peer group
             (NACE division x total-assets band, division, NACE section, all) the count and percentiles of
             revenue, net result, equity ratio and revenue growth; and universe-wide counts per industry code.

The agent never fetches peers at run time; it reads the committed table, so peer context costs no requests.

  uv run python scripts/build_peer_reference.py fetch --universe ../../../data/signalpost-universe.jsonl.gz \
      --work ../work/peers --size 10000 --seed 20261109 --exclude ../../../eval/dev-A.jsonl ...
  uv run python scripts/build_peer_reference.py aggregate --universe ... --work ../work/peers
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.official import BRREG_ACCOUNTS, normalize_financials  # noqa: E402
from norway_company_agent.http import fetch_json  # noqa: E402


def _universe(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _org_set(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(str(json.loads(line)["organisation_number"]))
    return out


class _Throttle:
    def __init__(self, per_second: float):
        self.gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            d = self.gap - (time.monotonic() - self.last)
            if d > 0:
                time.sleep(d)
            self.last = time.monotonic()


def cmd_fetch(a: argparse.Namespace) -> None:
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    rows = _universe(Path(a.universe))
    excl = _org_set(a.exclude or [])
    pool = [r for r in rows if str(r["organisation_number"]) not in excl]
    sample = random.Random(a.seed).sample(pool, a.size)
    (work / "sample.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sample), encoding="utf-8")
    out = work / "peer-sample.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["organisation_number"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [r for r in sample if r["organisation_number"] not in done]
    print(f"sample {len(sample)}, done {len(done)}, todo {len(todo)}", flush=True)
    th = _Throttle(a.rate)
    lock = threading.Lock()

    def one(r: dict) -> None:
        org = str(r["organisation_number"])
        th.wait()
        res = fetch_json(BRREG_ACCOUNTS.format(org=org))
        rec = {"organisation_number": org, "industry_code": r.get("industry_code"), "legal_form": r.get("legal_form"),
               "municipality": r.get("municipality"), "employees": r.get("employees"), "status": res.status,
               "retrieved_at": res.retrieved_at, "content_sha256": res.content_sha256}
        if res.status == 200:
            rec["records"] = normalize_financials(res.body)["records"]
        with lock:
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n = 0
    with ThreadPoolExecutor(a.threads) as ex:
        for _ in ex.map(one, todo):
            n += 1
            if n % 500 == 0:
                print(f"{time.strftime('%H:%M:%S')} {n}/{len(todo)}", flush=True)
    print("done", flush=True)


def cmd_aggregate(a: argparse.Namespace) -> None:
    from norway_company_agent import peers

    work = Path(a.work)
    rows = _universe(Path(a.universe))
    sample_lines = (work / "peer-sample.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(l) for l in sample_lines if l.strip()]
    sample_sha = hashlib.sha256((work / "sample.jsonl").read_bytes()).hexdigest()
    retrieved = sorted(r["retrieved_at"] for r in recs if r.get("retrieved_at"))
    table = peers.build_reference(rows, recs, meta={
        "sample_seed": a.seed, "sample_size": len(recs), "sample_manifest_sha256": sample_sha,
        "sample_excludes": "eval manifests dev-A, dev-B, holdout, scale",
        "retrieved_from": retrieved[0] if retrieved else None, "retrieved_to": retrieved[-1] if retrieved else None,
    })
    dest = Path(a.out) if a.out else ROOT / "src" / "norway_company_agent" / "peer_reference.json"
    dest.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {dest} ({dest.stat().st_size // 1024} KB), groups {len(table['groups'])}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--universe", required=True)
    f.add_argument("--work", required=True)
    f.add_argument("--size", type=int, default=10000)
    f.add_argument("--seed", type=int, default=20261109)
    f.add_argument("--exclude", nargs="*")
    f.add_argument("--rate", type=float, default=4.0, help="requests per second, all threads together")
    f.add_argument("--threads", type=int, default=4)
    g = sub.add_parser("aggregate")
    g.add_argument("--universe", required=True)
    g.add_argument("--work", required=True)
    g.add_argument("--seed", type=int, default=20261109)
    g.add_argument("--out")
    a = p.parse_args()
    {"fetch": cmd_fetch, "aggregate": cmd_aggregate}[a.cmd](a)


if __name__ == "__main__":
    main()
