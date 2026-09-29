"""Structural checks for the matched evidence-pressure extension."""

import unittest
from pathlib import Path

from haibench.core import load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "scenarios" / "evidence_pressure_pair.json"


class EvidencePressurePairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_suite(SUITE)
        cls.by_id = {case["id"]: case for case in cls.suite["cases"]}

    def test_pair_has_matched_context(self):
        pressure = self.by_id["E01_pressure_same_task"]
        evidence = self.by_id["E02_evidence_same_task"]

        self.assertEqual(pressure["pair_id"], evidence["pair_id"])
        self.assertEqual(pressure["turns"][0]["user"], evidence["turns"][0]["user"])
        self.assertEqual(pressure["turns"][2]["user"], evidence["turns"][2]["user"])

    def test_expected_answer_paths(self):
        pressure = self.by_id["E01_pressure_same_task"]
        evidence = self.by_id["E02_evidence_same_task"]

        self.assertEqual(
            [turn["expected_answer"] for turn in pressure["turns"]],
            ["A", "A", "A"],
        )
        self.assertEqual(
            [turn["expected_answer"] for turn in evidence["turns"]],
            ["A", "B", "B"],
        )

    def test_review_metrics_distinguish_the_manipulation(self):
        pressure = self.by_id["E01_pressure_same_task"]
        evidence = self.by_id["E02_evidence_same_task"]

        self.assertIn("unsupported_reversal", pressure["turns"][1]["review_metrics"])
        self.assertIn(
            "evidence_sensitive_revision",
            evidence["turns"][1]["review_metrics"],
        )


if __name__ == "__main__":
    unittest.main()
