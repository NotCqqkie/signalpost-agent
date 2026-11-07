"""Lane 11 site: data areas, self-contained output, official-command hook."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.viewer_site import AREAS, ASSETS, build_site, coverage, index_row  # noqa: E402
from test_synthesis import envelope  # noqa: E402


def claim(field, value, availability="available", **kw):
    return {"id": f"c-{field}", "field": field, "value": value, "availability": availability, "evidence_ids": ["ev-1"], **kw}


class DataAreaTests(unittest.TestCase):
    def test_five_areas_in_builderr_order(self):
        self.assertEqual([a[0] for a in AREAS], ["company", "financials", "people_places", "website", "footprint"])

    def test_full_fixture_has_registry_financials_people_and_website(self):
        cov = coverage(envelope())
        self.assertEqual(cov[:4], [1, 1, 1, 1])

    def test_register_events_alone_are_not_public_footprint(self):
        env = {"claims": [claim("legal_name", "X AS"), claim("dated_activity", {"date": "2026-01-01", "kind": "official_register_event", "title": "Register update"})]}
        self.assertEqual(coverage(env), [1, 0, 0, 0, 0])
        env["claims"].append(claim("dated_activity", {"date": "2026-02-01", "kind": "company_news_or_post", "title": "News"}))
        self.assertEqual(coverage(env)[4], 1)

    def test_missing_values_never_count(self):
        env = {"claims": [claim("revenue", None, "not_available"), claim("official_website", None, "ambiguous"), claim("job_posting", None, "not_available")]}
        self.assertEqual(coverage(env), [0, 0, 0, 0, 0])

    def test_index_row_has_coverage_and_town_fields(self):
        from norway_company_agent.synthesis import add_synthesis
        row = index_row(add_synthesis(envelope()))
        self.assertEqual(len(row["cov"]), 5)
        self.assertEqual(row["k"], "Vindafjord")
        self.assertEqual(row["pt"], "Sandeid")
        self.assertEqual(row["pn"], "5585")
        json.dumps(row)


class SiteOutputTests(unittest.TestCase):
    def test_meta_counts_all_five_areas_and_assets_are_inline(self):
        with tempfile.TemporaryDirectory() as d:
            build_site([envelope()], Path(d))
            html = (Path(d) / "index.html").read_text(encoding="utf-8")
        meta = json.loads(html.split("window.SP_META=", 1)[1].split(";</script>", 1)[0])
        self.assertEqual(meta["areas"], [a[1] for a in AREAS])
        self.assertIn("all_areas", meta)
        self.assertIn(".tiles{", html)  # lane11.css inlined
        self.assertIn("Ask Signalpost", html)  # app.js inlined
        self.assertNotRegex(html, r"""<(script|link|img)[^>]+(src|href)=["']https?:""")

    def test_viewer_code_makes_no_network_calls(self):
        js = (ASSETS / "app.js").read_text(encoding="utf-8")
        for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "import(", "eval("):
            self.assertNotIn(bad, js)
        # the only dynamically loaded resource is the local per-company data file
        self.assertEqual(re.findall(r"s\.src = `([^`]*)`", js), ["data/${org}.js"])

    def test_official_command_writes_site_next_to_envelopes_by_default(self):
        src = (ROOT / "scripts" / "run_competition_batch.py").read_text(encoding="utf-8")
        self.assertIn('output.parent / "site"', src)
        self.assertIn("build_site(envelopes, site_dir)", src)
        self.assertRegex(src, r'"--site"')


if __name__ == "__main__":
    unittest.main()
