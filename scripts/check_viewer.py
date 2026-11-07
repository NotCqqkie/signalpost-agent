"""Headless-browser check of a built viewer at phone (390 px) and desktop (1280 px) widths, with screenshots.

    python scripts/check_viewer.py <viewer_dir> <screenshot_dir> [query] [orgA] [orgB]

Needs Playwright with Chromium (optional dependency; not used by the agent). Flows: search page, search by name,
company page, open a source, compare two companies, search by organisation number. Fails if the page logs a JS
error, requests anything outside the viewer folder, or scrolls sideways.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    from playwright.sync_api import sync_playwright

    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    site, shots = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    shots.mkdir(parents=True, exist_ok=True)
    index = json.loads((site / "index.html").read_text(encoding="utf-8").split("window.SP_INDEX=", 1)[1].split(";window.SP_META=", 1)[0])
    rich = sorted(index, key=lambda r: (-(r["b"]["web"] + r["b"]["prof"] + r["b"]["act"]), r["o"]))
    org_a = sys.argv[4] if len(sys.argv) > 4 else rich[0]["o"]
    org_b = sys.argv[5] if len(sys.argv) > 5 else rich[1]["o"]
    query = sys.argv[3] if len(sys.argv) > 3 else next(r["n"] for r in index if r["o"] == org_a).split()[0]
    url = site.joinpath("index.html").as_uri()
    report = {"url": url, "org_a": org_a, "org_b": org_b, "query": query, "widths": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((390, 844), (1280, 860)):
            ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2 if width < 500 else 1,
                                      is_mobile=width < 500, has_touch=width < 500)
            page = ctx.new_page()
            errors, external = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            ctx.route("**/*", lambda route: (route.continue_() if route.request.url.startswith("file:") else (external.append(route.request.url), route.abort())))
            res = {"steps": []}

            def step(name: str, full: bool = False) -> None:
                page.wait_for_timeout(250)
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                path = shots / f"{width}-{len(res['steps']) + 1:02d}-{name}.png"
                page.screenshot(path=str(path), full_page=full)
                res["steps"].append({"step": name, "screenshot": path.name, "horizontal_overflow_px": overflow})

            page.goto(url)
            step("search")
            page.fill("#q", query)
            step("search-results")
            page.click(f'a[href="#/c/{org_a}"] >> nth=0')
            page.wait_for_selector(".hero h1")
            step("company", full=True)
            page.click(".summary .cite >> nth=0")
            page.wait_for_selector("#sheet.open")
            res["source_sheet_links"] = page.eval_on_selector_all("#sheet a.open-src", "els => els.map(e => e.href)")
            step("source")
            page.click("#sheet-x")
            page.click(f'button[data-pick="{org_a}"]')
            page.goto(url + f"#/c/{org_b}")
            page.wait_for_selector(".hero h1")
            page.click(f'button[data-pick="{org_b}"]')
            page.click("#tray a.btn")
            page.wait_for_selector(".cmp table")
            step("compare", full=True)
            page.click(".cmp .srcbtn >> nth=0")
            page.wait_for_selector("#sheet.open")
            step("compare-source")
            page.goto(url + f"#/c/{org_a}")
            page.wait_for_selector("#cw")
            page.fill("#cw", next(r["n"] for r in index if r["o"] == org_b).split()[0])
            step("compare-with-search")
            page.click(f'a.cwhit[href="#/compare/{org_a}/{org_b}"]')
            page.wait_for_selector(".cmp table")
            res["compare_with_ok"] = True
            page.goto(url + "#/")
            page.fill("#q", org_b[:3] + " " + org_b[3:6] + " " + org_b[6:])
            res["orgnr_search_first_hit"] = page.eval_on_selector(".row h3 a", "a => a.getAttribute('href')")
            step("orgnr-search")
            res["errors"], res["external_requests"] = errors, external
            report["widths"][width] = res
            ctx.close()
        browser.close()
    report["passed"] = all(not r["errors"] and not r["external_requests"] and all(s["horizontal_overflow_px"] <= 0 for s in r["steps"])
                           and r["orgnr_search_first_hit"] == f"#/c/{org_b}" and r["source_sheet_links"] and r.get("compare_with_ok") for r in report["widths"].values())
    (shots / "check-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
