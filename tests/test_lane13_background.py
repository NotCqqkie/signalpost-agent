"""Lane 13: background register lookups never hold an envelope past the global deadline."""
import sys
import time
import unittest
from concurrent.futures import Future
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.agent import Agent  # noqa: E402


class Lane13BackgroundTests(unittest.TestCase):
    def test_unfinished_lookup_becomes_failed_at_deadline(self):
        a = Agent(run_id="t", budget_s=1.0, strategies={"state_aid", "fin_licences"}, log=lambda *_: None)
        a.deadline = time.monotonic() - 1  # already past
        done, pending = Future(), Future()
        done.set_result({"state_aid": {"state": "checked", "awards": [], "requests": []}})
        states = {"1": {"org": "1"}, "2": {"org": "2"}}
        t0 = time.monotonic()
        a._collect_lane13({"1": done, "2": pending}, states)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(states["1"]["state_aid"]["state"], "checked")
        self.assertEqual(states["2"]["state_aid"]["state"], "failed")
        self.assertEqual(states["2"]["fin_licences"]["state"], "failed")
        self.assertIn("deadline", states["2"]["state_aid"]["error"])


if __name__ == "__main__":
    unittest.main()
