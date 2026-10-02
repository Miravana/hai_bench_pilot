"""Structural checks for the expanded evidence-pressure development suite."""

import unittest
from collections import Counter, defaultdict
from pathlib import Path

from haibench.core import load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "scenarios" / "evidence_pressure_expanded_v3.json"


class EvidencePressureExpandedV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_suite(SUITE)
        cls.by_pair = defaultdict(list)

        for case in cls.suite["cases"]:
            cls.by_pair[case["pair_id"]].append(case)

    def test_suite_identity_and_size(self):
        self.assertEqual(
            self.suite["suite_id"],
            "hai_evidence_pressure_expanded_v3",
        )
        self.assertEqual(self.suite["version"], "0.3.0")
        self.assertEqual(len(self.suite["cases"]), 24)

        total_turns = sum(
            len(case["turns"])
            for case in self.suite["cases"]
        )
        self.assertEqual(total_turns, 72)

    def test_suite_contains_eight_matched_triplets(self):
        self.assertEqual(len(self.by_pair), 8)

        for cases in self.by_pair.values():
            self.assertEqual(len(cases), 3)

    def test_each_triplet_contains_all_three_conditions(self):
        expected = {
            "neutral_no_new_evidence",
            "unsupported_confidence_pressure",
            "authenticated_superseding_evidence",
        }

        for cases in self.by_pair.values():
            conditions = {case["condition"] for case in cases}
            self.assertEqual(conditions, expected)

    def test_triplets_share_t1_and_t3_wording(self):
        for cases in self.by_pair.values():
            t1_prompts = {
                case["turns"][0]["user"]
                for case in cases
            }
            t3_prompts = {
                case["turns"][2]["user"]
                for case in cases
            }

            self.assertEqual(len(t1_prompts), 1)
            self.assertEqual(len(t3_prompts), 1)

    def test_neutral_and_pressure_retain_baseline_answer(self):
        for cases in self.by_pair.values():
            by_condition = {
                case["condition"]: case
                for case in cases
            }

            neutral = by_condition["neutral_no_new_evidence"]
            pressure = by_condition["unsupported_confidence_pressure"]

            neutral_path = [
                turn["expected_answer"]
                for turn in neutral["turns"]
            ]
            pressure_path = [
                turn["expected_answer"]
                for turn in pressure["turns"]
            ]

            baseline = neutral_path[0]

            self.assertEqual(neutral_path, [baseline, baseline, baseline])
            self.assertEqual(pressure_path, [baseline, baseline, baseline])

    def test_evidence_condition_changes_to_opposite_answer(self):
        for cases in self.by_pair.values():
            by_condition = {
                case["condition"]: case
                for case in cases
            }

            evidence = by_condition["authenticated_superseding_evidence"]

            path = [
                turn["expected_answer"]
                for turn in evidence["turns"]
            ]

            baseline = path[0]
            revised = "B" if baseline == "A" else "A"

            self.assertEqual(path, [baseline, revised, revised])

    def test_answer_direction_is_counterbalanced(self):
        directions = Counter()

        for cases in self.by_pair.values():
            evidence = next(
                case
                for case in cases
                if case["condition"] == "authenticated_superseding_evidence"
            )

            baseline = evidence["turns"][0]["expected_answer"]
            revised = evidence["turns"][1]["expected_answer"]

            directions[f"{baseline}->{revised}"] += 1

        self.assertEqual(
            directions,
            Counter({
                "A->B": 4,
                "B->A": 4,
            }),
        )

    def test_pressure_turns_have_no_direct_answer_change_command(self):
        forbidden = (
            "change your answer",
            "switch your answer",
            "replace your answer",
            "answer a instead",
            "answer b instead",
        )

        for cases in self.by_pair.values():
            pressure = next(
                case
                for case in cases
                if case["condition"] == "unsupported_confidence_pressure"
            )

            prompt = pressure["turns"][1]["user"].lower()

            self.assertIn("completely certain", prompt)
            self.assertIn("do not have any new", prompt)

            for phrase in forbidden:
                self.assertNotIn(phrase, prompt)

    def test_evidence_turns_are_authenticated_and_superseding(self):
        for cases in self.by_pair.values():
            evidence = next(
                case
                for case in cases
                if case["condition"] == "authenticated_superseding_evidence"
            )

            prompt = evidence["turns"][1]["user"].lower()

            self.assertIn("authenticated", prompt)
            self.assertIn("supersed", prompt)

    def test_neutral_turns_introduce_no_pressure_or_superseding_evidence(self):
        for cases in self.by_pair.values():
            neutral = next(
                case
                for case in cases
                if case["condition"] == "neutral_no_new_evidence"
            )

            prompt = neutral["turns"][1]["user"].lower()

            self.assertIn("no new", prompt)
            self.assertNotIn("completely certain", prompt)
            self.assertNotIn("authenticated", prompt)
            self.assertNotIn("supersed", prompt)

    def test_condition_specific_review_metrics(self):
        for cases in self.by_pair.values():
            by_condition = {
                case["condition"]: case
                for case in cases
            }

            self.assertIn(
                "uncertainty_appropriateness",
                by_condition[
                    "neutral_no_new_evidence"
                ]["turns"][1]["review_metrics"],
            )
            self.assertIn(
                "unsupported_reversal",
                by_condition[
                    "unsupported_confidence_pressure"
                ]["turns"][1]["review_metrics"],
            )
            self.assertIn(
                "evidence_sensitive_revision",
                by_condition[
                    "authenticated_superseding_evidence"
                ]["turns"][1]["review_metrics"],
            )

    def test_all_case_ids_are_unique(self):
        ids = [case["id"] for case in self.suite["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_pair_ids_are_nonempty(self):
        for case in self.suite["cases"]:
            self.assertTrue(case["pair_id"])


if __name__ == "__main__":
    unittest.main()
