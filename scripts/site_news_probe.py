"""Lane 8 probe: re-crawl verified sites listed in a run's envelopes, with and without the `site_news` flag.

Only the homepage of an already-verified site is fetched again (no re-verification); the crawl then runs as in the
pipeline. Output: one JSON line per site with baseline and new activity items.
  uv run python scripts/site_news_probe.py --envelopes RUN/envelopes.jsonl --output OUT.jsonl [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.evidence import utc_now  # noqa: E402
from norway_company_agent.footprint import crawl_verified_site, fetch_page, registered_domain  # noqa: E402
from norway_company_agent.net import Fetcher  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelopes", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only-new", action="store_true", help="skip the baseline crawl")
    args = ap.parse_args()
    sites = []
    for line in open(args.envelopes, encoding="utf-8"):
        env = json.loads(line)
        web = [c for c in env["claims"] if c["field"] == "official_website" and c["availability"] == "available"]
        if web:
            sites.append((env["organisation_number"], env.get("identity", {}).get("name"), web[0]["value"]))
    if args.limit:
        sites = sites[: args.limit]
    today = utc_now()[:10]
    root = Fetcher(per_host_interval=1.0)

    def run(site):
        org, name, url = site
        rec = {"organisation_number": org, "name": name, "site": url}
        f = root.child(150)
        home, resp = fetch_page(f, url, "homepage")
        if not home:
            rec["error"] = resp.error or f"HTTP {resp.status}"
            return rec
        dom = registered_domain(home.url)
        prof = {"organisation_number": org, "name": name}
        for flag, key in ((False, "baseline"), (True, "new")):
            if key == "baseline" and args.only_new:
                continue
            attempt = {"pages": [home], "domain": dom}
            try:
                crawl = crawl_verified_site(f.child(120), {**prof, "_site_news": flag}, attempt, today=today)
                rec[key] = crawl["activity"]
                rec[key + "_log"] = [l for l in crawl["crawl_log"] if l.get("kind") in ("news_index", "article", "site_news", "sitemap", "feed", "wp_posts")]
            except Exception as exc:  # report, keep going
                rec[key + "_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return rec

    with open(args.output, "w", encoding="utf-8") as out, ThreadPoolExecutor(args.workers) as pool:
        for rec in pool.map(run, sites):
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(rec["organisation_number"], rec["site"], len(rec.get("baseline") or []), len(rec.get("new") or []), rec.get("error") or rec.get("new_error") or "", flush=True)


if __name__ == "__main__":
    main()
