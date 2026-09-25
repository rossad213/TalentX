#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_metric_calibration import calibrate_record, calibrated_metrics, sample_maturity


class NflMetricCalibrationTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "nfl-player",
            "name": "NFL Player",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "careerStatus": "Active",
            "careerStage": "Established",
            "professionalGames": 95,
            "experienceYears": 7,
            "activeMetrics": {
                "performance": 90,
                "achievements": 90,
                "consistency": 90,
                "potential": 80,
                "availability": 75,
                "audience": 97,
            },
            "pricingEvidenceSummary": {
                "percentiles": {
                    "recentProduction": 0.90,
                    "careerProduction": 0.96,
                    "efficiency": 0.92,
                    "awardPoints": 0.90,
                },
                "rawSignals": {"awardPoints": 6.0},
                "awardNames": [],
            },
        }
        base.update(updates)
        return base

    def test_non_nfl_records_are_unchanged(self):
        nba = self.record(leagueOrMedium="NBA")
        self.assertEqual(calibrate_record(nba), nba)

    def test_sample_maturity_saturates_between_95_and_141_games(self):
        ninety_five = sample_maturity(self.record(professionalGames=95))
        one_forty_one = sample_maturity(self.record(professionalGames=141))
        self.assertGreater(ninety_five, 95)
        self.assertLess(one_forty_one - ninety_five, 4)

    def test_missing_game_total_uses_experience_instead_of_zero(self):
        established = self.record(professionalGames=0, experienceYears=6)
        self.assertGreater(sample_maturity(established), 90)

    def test_performance_is_not_materially_boosted_by_veteran_game_count(self):
        metrics = self.record()["activeMetrics"]
        p95, _ = calibrated_metrics(self.record(professionalGames=95), metrics)
        p141, _ = calibrated_metrics(self.record(professionalGames=141), metrics)
        self.assertLess(abs(p141["performance"] - p95["performance"]), 1.0)

    def test_achievement_score_is_honors_led_not_career_volume_led(self):
        decorated = self.record(
            professionalGames=126,
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.90,
                    "careerProduction": 0.90,
                    "efficiency": 0.90,
                    "awardPoints": 0.999,
                },
                "rawSignals": {"awardPoints": 18.0},
                "awardNames": [],
            },
        )
        accumulator = self.record(
            professionalGames=141,
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.90,
                    "careerProduction": 0.995,
                    "efficiency": 0.90,
                    "awardPoints": 0.995,
                },
                "rawSignals": {"awardPoints": 6.0},
                "awardNames": [],
            },
        )
        decorated_metrics, _ = calibrated_metrics(decorated, decorated["activeMetrics"])
        accumulator_metrics, _ = calibrated_metrics(accumulator, accumulator["activeMetrics"])
        self.assertGreater(decorated_metrics["achievements"], accumulator_metrics["achievements"])

    def test_audience_does_not_reuse_prior_audience_or_performance(self):
        high_old = self.record(activeMetrics={
            "performance": 99, "achievements": 99, "consistency": 90,
            "potential": 80, "availability": 75, "audience": 97,
        })
        low_old = self.record(activeMetrics={
            "performance": 30, "achievements": 20, "consistency": 90,
            "potential": 80, "availability": 75, "audience": 20,
        })
        high_metrics, _ = calibrated_metrics(high_old, high_old["activeMetrics"])
        low_metrics, _ = calibrated_metrics(low_old, low_old["activeMetrics"])
        self.assertEqual(high_metrics["audience"], low_metrics["audience"])
        self.assertLess(high_metrics["audience"], 80)

    def test_news_attention_can_differentiate_audience_without_award_double_counting(self):
        quiet, _ = calibrated_metrics(self.record(), self.record()["activeMetrics"], news_count=0)
        active, _ = calibrated_metrics(self.record(), self.record()["activeMetrics"], news_count=12)
        self.assertGreater(active["attention"], quiet["attention"])
        self.assertGreater(active["audience"], quiet["audience"])


if __name__ == "__main__":
    unittest.main()
