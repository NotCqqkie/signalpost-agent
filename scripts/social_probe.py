"""main2 E17 probe: re-fetch verified sites (home + identity pages) and list social links the current rule drops.
  uv run python scripts/social_probe.py --envelopes RUN/envelopes.jsonl --output OUT.jsonl"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_company_agent.footprint import fetch_page, identity_links, registered_domain, social_from_pages, walk_json, type_set  # noqa: E402
from norway_company_agent.net import Fetcher  # noqa: E402
from norway_company_agent.website import normalize_social_url  # noqa: E402

def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--envelopes", required=True); ap.add_argument("--output", required=True)
    args = ap.parse_args()
    sites = []
    for line in open(args.envelopes, encoding="utf-8"):
        env = json.loads(line)
        web = [c for c in env["claims"] if c["field"] == "official_website" and c["availability"] == "available"]
        if web:
            sites.append((env["organisation_number"], env.get("identity", {}).get("name"), web[0]["value"]))
    root = Fetcher(per_host_interval=1.0)
    def run(site):
        org, name, url = site
        rec = {"organisation_number": org, "name": name, "site": url}
        f = root.child(120)
        home, resp = fetch_page(f, url, "homepage")
        if not home:
            rec["error"] = resp.error or f"HTTP {resp.status}"; return rec
        dom = registered_domain(home.url); pages = [home]
        for link in identity_links(home, dom):
            p, _ = fetch_page(f, link, "identity")
            if p: pages.append(p)
        prof = {"organisation_number": org, "name": name}
        kept = {r["url"].lower() for r in social_from_pages(pages, prof, dom)}
        links = {}
        for p in pages:
            org_same = set()
            for block in p.jsonld:
                for node in walk_json(block):
                    same = node.get("sameAs"); ts = type_set(node)
                    for u in (same if isinstance(same, list) else [same]):
                        if isinstance(u, str) and normalize_social_url(u):
                            org_same.add(normalize_social_url(u)["url"].lower())
                            links.setdefault(normalize_social_url(u)["url"].lower(), {"pages": set(), "jsonld_types": set(), "anchor": False})["jsonld_types"].update(ts)
            for u, text, _ in p.links:
                n = normalize_social_url(u)
                if n:
                    d = links.setdefault(n["url"].lower(), {"pages": set(), "jsonld_types": set(), "anchor": False}); d["anchor"] = True; d["pages"].add(p.url)
        import re as _re
        rec["fb_numeric"] = sorted({(m, sum(1 for p in pages if m in p.resp.text())) for p in pages for m in _re.findall(r"facebook\.com/profile\.php\?id=\d+", p.resp.text())})
        rec["kept"] = sorted(kept)
        rec["dropped"] = [{"url": u, "n_pages": len(d["pages"]), "of_pages": len(pages), "jsonld_types": sorted(d["jsonld_types"]), "anchor": d["anchor"]} for u, d in links.items() if u not in kept]
        return rec
    with open(args.output, "w", encoding="utf-8") as out, ThreadPoolExecutor(6) as pool:
        for rec in pool.map(run, sites):
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
if __name__ == "__main__":
    main()
