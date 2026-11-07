"""Judge-style headless test of a built Signalpost site: the judged tasks, timed, at laptop and phone size.

    python scripts/judge_viewer.py <site_dir> <out_dir> [orgA] [orgB]

Needs Playwright with Chromium (development tool only; the agent does not depend on it). At 1280x800 and 375x812 it runs:
  find by name / organisation number / town, open a company, open the source of its revenue, the Ask panel (prompt,
  free question, unanswerable question), compare two companies (from the profile and from the compare tray), open a
  source in the comparison, reload shared URLs (comparison, source, search), plus an overflow sweep over many
  company pages. Every step records wall time, horizontal overflow and a screenshot; the run fails on any JS error,
  any request outside the site folder, any horizontal overflow or any failed task. Also measures index load time and
  search time per keystroke. Writes <out_dir>/judge-report.json and screenshots.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path


def main() -> None:
    from playwright.sync_api import sync_playwright

    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    site, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    html = (site / "index.html").read_text(encoding="utf-8")
    index = json.loads(html.split("window.SP_INDEX=", 1)[1].split(";window.SP_META=", 1)[0])
    by_org = {r["o"]: r for r in index}
    ranked = sorted([r for r in index if r.get("rev")], key=lambda r: (-sum(r["cov"]), -(r["b"]["web"] + r["b"]["prof"] + r["b"]["act"]), r["o"]))
    org_a = sys.argv[3] if len(sys.argv) > 3 else ranked[0]["o"]
    org_b = sys.argv[4] if len(sys.argv) > 4 else next(r["o"] for r in ranked[1:] if r["n"].split()[0] != by_org[org_a]["n"].split()[0])
    name_a, name_b = by_org[org_a]["n"], by_org[org_b]["n"]
    town = by_org[org_a]["k"]
    url = site.joinpath("index.html").as_uri()
    sweep = [r["o"] for r in sorted(index, key=lambda r: r["o"])[:: max(1, len(index) // 30)]][:30]
    sweep += [r["o"] for r in sorted(index, key=lambda r: -len(r["n"]))[:3]]  # longest names
    report = {"site": str(site), "companies": len(index), "org_a": [org_a, name_a], "org_b": [org_b, name_b], "town": town, "viewports": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, (w, h) in (("laptop", (1280, 800)), ("phone", (375, 812))):
            phone = w < 500
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if phone else 1, is_mobile=phone, has_touch=phone)
            page = ctx.new_page()
            errors, external = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            ctx.route("**/*", lambda route: (route.continue_() if route.request.url.startswith("file:") else (external.append(route.request.url), route.abort())))
            steps, tasks = [], {}

            def shot(name: str, full: bool = False) -> int:
                page.wait_for_timeout(120)
                ov = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                fn = f"{label}-{len(steps) + 1:02d}-{name}.png"
                page.screenshot(path=str(out / fn), full_page=full)
                (out / fn.replace(".png", ".txt")).write_text(page.inner_text("body"), encoding="utf-8")
                steps.append({"step": name, "screenshot": fn, "overflow_px": ov})
                return ov

            def task(name: str, fn) -> None:
                t0 = time.perf_counter()
                try:
                    ok, detail = fn()
                except Exception as exc:  # a failed task is a finding, not a crash
                    ok, detail = False, f"{type(exc).__name__}: {str(exc)[:300]}"
                tasks[name] = {"ok": bool(ok), "seconds": round(time.perf_counter() - t0, 2), "detail": detail}

            def open_filters() -> None:  # phones show filters behind a "Filters and sorting" toggle
                if page.is_visible(".fsum") and not page.evaluate("document.querySelector('details.filters').open"):
                    page.click(".fsum")

            def goto(hash_: str = "") -> None:
                page.goto(url + hash_)
                page.wait_for_selector("#app > *")

            # 0. load
            t0 = time.perf_counter()
            goto()
            page.wait_for_selector("#count")
            load_ms = page.evaluate("performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd")
            report_load = {"wall_s": round(time.perf_counter() - t0, 2), "dom_content_loaded_ms": round(load_ms, 1)}
            shot("home", full=False)

            def find_name():
                page.fill("#q", "")
                page.type("#q", name_a[:12], delay=20)
                shot("find-by-name")
                first = page.eval_on_selector(".row h3 a", "a => a.getAttribute('href')")
                page.click(f'.row h3 a[href="#/c/{org_a}"]')
                page.wait_for_selector(".hero h1")
                return first == f"#/c/{org_a}" and page.inner_text(".hero h1").strip() == name_a, f"first hit {first}"
            task("find_by_name", find_name)
            shot("profile-top")
            page.screenshot(path=str(out / f"{label}-profile-full.png"), full_page=True)

            def open_revenue_source():
                page.click(".tile[data-src] >> nth=0")
                page.wait_for_selector("#sheet.open")
                txt = page.inner_text("#sheet")
                links = page.eval_on_selector_all("#sheet a.open-src", "els => els.map(e => e.href)")
                ok = "Revenue" in txt and "Retrieved" in txt and "Period" in txt and bool(links) and all(l.startswith("https://") for l in links)
                return ok, {"source_urls": links[:2], "deep_link": page.evaluate("location.hash")}
            task("open_source_of_revenue", open_revenue_source)
            shot("revenue-source")
            page.click("#sheet-x")

            def ask_panel():
                page.click('.prompt[data-ask="leadership"]')
                a1 = page.inner_text("#answer")
                page.fill("#askq", "Is it hiring right now?")
                page.press("#askq", "Enter")
                a2 = page.inner_text("#answer")
                shot("ask-hiring")
                page.fill("#askq", "What is the CEO's favourite colour of car?")
                page.press("#askq", "Enter")
                a3 = page.inner_text("#answer")
                n_cites = page.eval_on_selector_all("#answer .cite", "els => els.length")
                page.fill("#askq", "What was revenue last year?")
                page.press("#askq", "Enter")
                a4 = page.inner_text("#answer")
                page.click("#answer .cite >> nth=0")
                page.wait_for_selector("#sheet.open")
                page.click("#sheet-x")
                ok = "Who runs it" in a1 and "working here" in a2.lower() and ("Not enough evidence" in a3 or "Not in the sources" in a3) and "Revenue" in a4
                return ok, {"leadership": a1[:160], "hiring": a2[:160], "unanswerable": a3[:120], "revenue": a4[:160]}
            task("ask_panel", ask_panel)

            def compare_from_profile():
                page.fill("#cw", "")
                page.type("#cw", name_b[:10], delay=20)
                page.click(f'a.cwhit[href="#/compare/{org_a}/{org_b}"]')
                page.wait_for_selector(".cmp table")
                heads = page.inner_text(".cmp thead")
                return name_a in heads and name_b in heads, page.evaluate("location.hash")
            goto(f"#/c/{org_a}")
            task("compare_from_profile", compare_from_profile)
            shot("compare", full=False)
            page.screenshot(path=str(out / f"{label}-compare-full.png"), full_page=True)

            def compare_source():
                page.click(".cmp .srcbtn[data-side='b'] >> nth=2")
                page.wait_for_selector("#sheet.open")
                ok = "Retrieved" in page.inner_text("#sheet")
                return ok, page.inner_text("#sheet h2")
            task("compare_open_source", compare_source)
            shot("compare-source")
            page.click("#sheet-x")

            def compare_via_tray():
                goto("#/")
                page.fill("#q", name_b[:10])
                page.click(f'button[data-pick="{org_b}"]')
                page.fill("#q", org_a)
                page.click(f'button[data-pick="{org_a}"]')
                shot("tray")
                page.click("#tray-go")
                page.wait_for_selector(".cmp table")
                return True, page.evaluate("location.hash")
            task("compare_via_tray", compare_via_tray)

            def find_orgnr():
                goto("#/")
                page.fill("#q", f"{org_b[:3]} {org_b[3:6]} {org_b[6:]}")
                hit = page.eval_on_selector(".row h3 a", "a => a.getAttribute('href')")
                return hit == f"#/c/{org_b}", hit
            task("find_by_orgnr", find_orgnr)
            shot("find-by-orgnr")

            def find_town():
                goto("#/")
                page.fill("#q", town)
                n = page.eval_on_selector_all(".row", "els => els.length")
                first_meta = page.inner_text(".row .small")
                page.fill("#q", "")
                open_filters()
                page.select_option("#town", town)
                n2 = page.eval_on_selector_all(".row", "els => els.length")
                h2 = page.evaluate("location.hash")
                return n > 0 and town.lower() in first_meta.lower() and n2 > 0, {"typed_hits": n, "first": first_meta[:100], "filter_hits": n2, "url": h2}
            task("find_by_town", find_town)
            shot("find-by-town")

            def filters():
                goto("#/")
                open_filters()
                page.click('[data-f="all5"]')
                n = page.eval_on_selector_all(".row", "els => els.length")
                txt = page.inner_text("#count")
                h2 = page.evaluate("location.hash")
                return n > 0 and "f=all5" in h2, {"count": txt, "url": h2}
            task("filter_all_five_areas", filters)
            shot("filter-all5")

            def shared_urls():
                res = {}
                goto(f"#/compare/{org_a}/{org_b}")
                res["compare"] = page.is_visible(".cmp table")
                goto(f"#/c/{org_a}/source/{page_claim_for(site, org_a, 'revenue')}")
                page.wait_for_selector("#sheet.open")
                res["source"] = "Revenue" in page.inner_text("#sheet")
                goto(f"#/?q={town}&f=web")
                res["search"] = page.input_value("#q") == town and page.get_attribute('[data-f="web"]', "aria-pressed") == "true"
                goto(f"#/c/{org_a}/ask/financials")
                res["ask"] = "financials" in page.inner_text("#answer").lower()
                goto(f"#/c/{org_a}/leadership")
                res["section"] = page.evaluate("document.getElementById('sec-leadership').getBoundingClientRect().top < window.innerHeight")
                return all(res.values()), res
            task("shared_urls", shared_urls)

            # overflow sweep: many profiles and comparisons
            worst = []
            for o in sweep:
                goto(f"#/c/{o}")
                page.wait_for_selector(".hero h1")
                ov = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if ov > 0:
                    worst.append([o, ov])
            for a, b in zip(sweep[:6], sweep[6:12]):
                goto(f"#/compare/{a}/{b}")
                page.wait_for_selector(".cmp table")
                ov = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                if ov > 0:
                    worst.append([f"{a}/{b}", ov])

            # search speed per keystroke on this index
            goto("#/")
            q = name_b[:14]
            ks = page.evaluate("""q => { const el = document.querySelector('#q'); const out = [];
                for (let i = 1; i <= q.length; i++) { el.value = q.slice(0, i); const t = performance.now(); el.dispatchEvent(new Event('input', {bubbles: true})); out.push(performance.now() - t); }
                for (let i = q.length - 1; i >= 0; i--) { el.value = q.slice(0, i); const t = performance.now(); el.dispatchEvent(new Event('input', {bubbles: true})); out.push(performance.now() - t); }
                return out; }""", q)
            report["viewports"][label] = {
                "viewport": [w, h], "load": report_load, "tasks": tasks, "steps": steps, "overflow_sweep": {"pages": len(sweep) + 6, "overflowing": worst},
                "keystroke_ms": {"n": len(ks), "median": round(statistics.median(ks), 2), "max": round(max(ks), 2)},
                "js_errors": errors, "external_requests": external,
            }
            ctx.close()
        browser.close()
    vp = report["viewports"].values()
    report["passed"] = all(all(t["ok"] for t in v["tasks"].values()) and not v["js_errors"] and not v["external_requests"]
                           and all(s["overflow_px"] <= 0 for s in v["steps"]) and not v["overflow_sweep"]["overflowing"] and v["keystroke_ms"]["max"] < 100 for v in vp)
    (out / "judge-report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {k: {"tasks": {t: [x["ok"], x["seconds"]] for t, x in v["tasks"].items()}, "load": v["load"], "keystroke_ms": v["keystroke_ms"], "errors": len(v["js_errors"]),
                   "external": len(v["external_requests"]), "overflow_steps": [s["step"] for s in v["steps"] if s["overflow_px"] > 0], "overflow_sweep": v["overflow_sweep"]["overflowing"]}
               for k, v in report["viewports"].items()}
    print(json.dumps({"passed": report["passed"], "org_a": report["org_a"], "org_b": report["org_b"], **summary}, indent=1, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


def page_claim_for(site: Path, org: str, field: str) -> str:
    txt = (site / "data" / f"{org}.js").read_text(encoding="utf-8")
    env = json.loads(txt.split(",", 1)[1].rsplit(");", 1)[0])
    cs = [c for c in env["claims"] if c.get("field") == field and c.get("availability") == "available"]
    c = next((c for c in cs if c.get("latest")), cs[0] if cs else None)
    return c["id"] if c else ""


if __name__ == "__main__":
    main()
