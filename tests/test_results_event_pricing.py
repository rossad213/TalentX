from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from results_event_pricing import result_move_from_delta, result_sensitivity, valid_move  # noqa: E402


class ResultsEventPricingTests(unittest.TestCase):
    def test_routine_variance_stays_small(self) -> None:
        self.assertLess(result_move_from_delta(10), 0.25)
        self.assertLess(result_move_from_delta(25), 0.75)
        self.assertLess(result_move_from_delta(50), 1.25)

    def test_exceptional_results_are_not_flattened_at_two_point_five(self) -> None:
        self.assertGreater(result_move_from_delta(200), 2.5)
        self.assertGreater(result_move_from_delta(500), result_move_from_delta(200))
        self.assertGreater(result_move_from_delta(1000), result_move_from_delta(500))

    def test_curve_is_symmetric_for_result_surprise(self) -> None:
        self.assertAlmostEqual(result_move_from_delta(75), -result_move_from_delta(-75), places=9)

    def test_small_noise_has_a_dead_zone(self) -> None:
        self.assertEqual(result_move_from_delta(1.5), 0.0)
        self.assertEqual(result_move_from_delta(-2.0), 0.0)

    def test_rookies_are_more_sensitive_without_using_a_cap(self) -> None:
        rookie_tier, rookie = result_sensitivity({"professionalGames": 5, "careerStage": "Rookie"})
        veteran_tier, veteran = result_sensitivity({
            "professionalGames": 300,
            "careerStage": "Established",
            "activeMetrics": {"consistency": 90},
        })
        self.assertEqual(rookie_tier, "High")
        self.assertEqual(veteran_tier, "Low")
        self.assertGreater(rookie, veteran)

    def test_only_mathematically_invalid_downside_is_rejected(self) -> None:
        self.assertEqual(valid_move(250), 250.0)
        self.assertEqual(valid_move(-99.9), -99.9)
        self.assertIsNone(valid_move(-100))


if __name__ == "__main__":
    unittest.main()
