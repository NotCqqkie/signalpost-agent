import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_company_agent.viewer_site import build_site, index_row  # noqa: E402
from test_synthesis import envelope  # noqa: E402


def tree_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(d).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


class ViewerSiteTests(unittest.TestCase):
    def test_builds_index_and_per_company_files_deterministically(self):
        envs = [envelope(), {**envelope("BRL"), "organisation_number": "850562482"}]
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = build_site(envs, Path(a)), build_site(envs, Path(b))
            self.assertEqual(ra["companies"], 2)
            self.assertTrue((Path(a) / "data" / "986389180.js").exists())
            self.assertEqual(tree_hash(Path(a)), tree_hash(Path(b)))
            html = (Path(a) / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("http://", html.split("<script>window.SP_INDEX")[0].replace("http-equiv", ""))
            self.assertNotIn("https://cdn", html)
            data = (Path(a) / "data" / "986389180.js").read_text(encoding="utf-8")
            self.assertIn('"synthesis"', data)

    def test_script_injection_is_escaped(self):
        env = envelope()
        env["claims"][0]["value"] = "</script><script>alert(1)</script>"
        with tempfile.TemporaryDirectory() as d:
            build_site([env], Path(d))
            self.assertNotIn("</script><script>alert", (Path(d) / "index.html").read_text(encoding="utf-8"))
            self.assertNotIn("</script><script>alert", (Path(d) / "data" / "986389180.js").read_text(encoding="utf-8"))

    def test_broken_and_duplicate_inputs_are_skipped_not_fatal(self):
        envs = [None, "x", {"organisation_number": ""}, envelope(), envelope(), {"organisation_number": "123456789", "state": "failed", "claims": []}]
        with tempfile.TemporaryDirectory() as d:
            r = build_site(envs, Path(d))
        self.assertEqual(r["companies"], 2)
        self.assertEqual(r["skipped"], 4)

    def test_index_row_badges(self):
        from norway_company_agent.synthesis import add_synthesis
        row = index_row(add_synthesis(envelope()))
        self.assertEqual(row["o"], "986389180")
        self.assertEqual(row["rev"], [461342972.0, "NOK", "2025"])
        self.assertEqual(row["b"]["web"], 1)
        self.assertEqual(row["b"]["jobs"], 0)
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
