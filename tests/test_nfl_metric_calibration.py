#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
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

    def test_established_51_vs_81_games_has_small_confidence_maturity_gap(self):
        fifty_one = sample_maturity(self.record(professionalGames=51))
        eighty_one = sample_maturity(self.record(professionalGames=81))
        self.assertGreater(fifty_one, 95)
        self.assertLess(eighty_one - fifty_one, 4)

    def test_semantic_performance_does_not_apply_a_second_sample_shrink(self):
        metrics = self.record()["activeMetrics"]
        younger, detail = calibrated_metrics(self.record(professionalGames=40), metrics)
        veteran, _ = calibrated_metrics(self.record(professionalGames=140), metrics)
        self.assertEqual(younger["performance"], veteran["performance"])
        self.assertEqual(detail["performance"]["shrinkFactor"], 1.0)
        self.assertTrue(detail["performance"]["evidenceAlreadyStabilized"])


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

    def test_recent_major_award_outweighs_same_old_award(self):
        year = datetime.now(timezone.utc).year
        recent = self.record(
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.90,
                    "careerProduction": 0.90,
                    "efficiency": 0.90,
                    "awardPoints": 0.95,
                },
                "rawSignals": {"awardPoints": 16.0},
                "awardNames": ["NFL Defensive Player of the Year"],
                "awardDetails": [{"name": "NFL Defensive Player of the Year", "year": year - 1}],
            },
        )
        old = self.record(
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.90,
                    "careerProduction": 0.90,
                    "efficiency": 0.90,
                    "awardPoints": 0.95,
                },
                "rawSignals": {"awardPoints": 16.0},
                "awardNames": ["NFL Defensive Player of the Year"],
                "awardDetails": [{"name": "NFL Defensive Player of the Year", "year": year - 5}],
            },
        )
        recent_metrics, recent_detail = calibrated_metrics(recent, recent["activeMetrics"])
        old_metrics, _ = calibrated_metrics(old, old["activeMetrics"])
        self.assertGreater(recent_metrics["achievements"], old_metrics["achievements"])
        self.assertGreater(
            recent_detail["achievements"]["honorRecency"]["recencyScore"],
            0,
        )

    def test_active_pup_status_reduces_availability_without_reducing_performance(self):
        healthy = self.record()
        injured = self.record(
            nflInjuryActive=True,
            nflInjuryStatus="Physically Unable to Perform",
            nflInjuryType="Knee",
            nflInjuryVerifiedAt="2026-09-26T06:00:00Z",
        )
        healthy_metrics, _ = calibrated_metrics(healthy, healthy["activeMetrics"])
        injured_metrics, detail = calibrated_metrics(injured, injured["activeMetrics"])
        self.assertEqual(injured_metrics["performance"], healthy_metrics["performance"])
        self.assertLess(injured_metrics["availability"], healthy_metrics["availability"])
        self.assertEqual(injured_metrics["availability"], 25.0)
        self.assertTrue(detail["availability"]["activeInjury"])

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
