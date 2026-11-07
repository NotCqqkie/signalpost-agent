"""Lane 13: synthesis v2 names official-signal activity correctly and treats Skatteetaten relief reports as routine."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.synthesis import _activity_kind  # noqa: E402


def claim(**v):
    return {"value": v}


class Lane13SynthesisTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(_activity_kind(claim(kind="official_inspection")), "inspection")
        self.assertEqual(_activity_kind(claim(kind="official_licence")), "licence")
        self.assertEqual(_activity_kind(claim(kind="public_research_funding")), "research")
        self.assertEqual(_activity_kind(claim(kind="public_support_award", giver_orgnr="971527412", instrument="tilskudd")), "support")
        self.assertEqual(_activity_kind(claim(kind="public_support_award", giver_orgnr="974761076", instrument="skatteEllerAvgiftsFritak")), "tax_relief")
        self.assertEqual(_activity_kind(claim(kind="public_procurement_award")), "award")


if __name__ == "__main__":
    unittest.main()
