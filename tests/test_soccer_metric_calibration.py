from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pricing_engine_v2 import apply_v2
from soccer_metric_calibration import (
    SOCCER_METRIC_WEIGHTS,
    calibrate_record,
    calibrated_metrics,
    competition_context,
)


class SoccerMetricCalibrationTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "soccer-player",
            "name": "Soccer Player",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "leagueOrMedium": "Premier League",
            "careerStage": "Prime",
            "careerStatus": "Active",
            "age": 25,
            "yearsActive": 6,
            "professionalGames": 180,
            "careerScore": 80,
            "pricingConfidence": 0.90,
            "activeMetrics": {
                "performance": 88,
                "achievements": 78,
                "consistency": 84,
                "potential": 90,
                "availability": 75,
                "audience": 75,
            },
            "momentumPct": 0,
            "demandPremiumPct": 0,
            "lastGameMovePct": 0,
            "marketPrice": 150,
            "fundamentalValue": 145,
            "trend": [145, 150],
        }
        base.update(updates)
        return base

    def test_top_flight_is_not_discounted(self):
        level, factor, _ = competition_context(self.record())
        self.assertEqual(level, 1)
        self.assertEqual(factor, 1.0)

    def test_lower_divisions_translate_local_percentiles_to_global_scale(self):
        top, _ = calibrated_metrics(self.record(leagueOrMedium="Premier League"))
        second, _ = calibrated_metrics(self.record(leagueOrMedium="English Championship"))
        third, _ = calibrated_metrics(self.record(leagueOrMedium="English League One"))
        fourth, _ = calibrated_metrics(self.record(leagueOrMedium="English League Two"))

        self.assertGreater(top["performance"], second["performance"])
        self.assertGreater(second["performance"], third["performance"])
        self.assertGreater(third["performance"], fourth["performance"])
        self.assertGreater(top["achievements"], second["achievements"])
        self.assertGreater(top["consistency"], second["consistency"])

    def test_same_local_player_is_cheaper_in_lower_division(self):
        top = apply_v2(self.record(leagueOrMedium="Premier League"))
        third = apply_v2(self.record(leagueOrMedium="English League One"))
        fourth = apply_v2(self.record(leagueOrMedium="English League Two"))

        self.assertGreater(top["fairValue"], third["fairValue"])
        self.assertGreater(third["fairValue"], fourth["fairValue"])
        self.assertEqual(top["soccerCompetitionFactor"], 1.0)
        self.assertEqual(third["soccerCompetitionLevel"], 3)

    def test_stat_percentiles_override_inflated_legacy_performance_proxy(self):
        record = self.record(
            activeMetrics={
                "performance": 99,
                "achievements": 99,
                "consistency": 99,
                "potential": 75,
                "availability": 75,
                "audience": 90,
            },
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.50,
                    "efficiency": 0.50,
                    "careerProduction": 0.50,
                    "awardPoints": 0.50,
                }
            },
        )
        calibrated, audit = calibrated_metrics(record)
        self.assertTrue(audit["rebuiltFromStatPercentiles"])
        self.assertLess(calibrated["performance"], 99)
        self.assertLess(calibrated["achievements"], 99)
        self.assertLess(calibrated["consistency"], 99)

    def test_late_career_proxy_is_separated_from_current_performance(self):
        old_star = calibrate_record(self.record(
            age=39,
            professionalGames=0,
            pricingDataStatus="Provisional — roster, experience and role evidence only",
            soccerEvidenceStatus="Wikidata identity and career evidence",
            activeMetrics={
                "performance": 96,
                "achievements": 99,
                "consistency": 98,
                "potential": 46,
                "availability": 78,
                "audience": 99,
            },
        ))
        self.assertLess(old_star["soccerGlobalMetrics"]["performance"], 90)
        self.assertLess(old_star["soccerGlobalMetrics"]["consistency"], 90)
        self.assertLessEqual(old_star["soccerGlobalMetrics"]["potential"], 26)
        self.assertEqual(old_star["soccerGlobalMetrics"]["achievements"], 99)

    def test_young_top_flight_potential_is_preserved(self):
        young = calibrate_record(self.record(age=19, activeMetrics={
            "performance": 85,
            "achievements": 79,
            "consistency": 93,
            "potential": 97,
            "availability": 75,
            "audience": 90,
        }))
        self.assertEqual(young["soccerGlobalMetrics"]["potential"], 97)
        self.assertEqual(young["soccerCompetitionFactor"], 1.0)

    def test_soccer_uses_forward_looking_weights(self):
        self.assertGreater(SOCCER_METRIC_WEIGHTS["potential"], 0.14)
        self.assertLess(SOCCER_METRIC_WEIGHTS["achievements"], 0.24)

    def test_non_soccer_record_is_unchanged_by_calibrator(self):
        nba = self.record(
            discipline="Basketball",
            leagueOrMedium="NBA",
        )
        self.assertEqual(calibrate_record(nba), nba)


if __name__ == "__main__":
    unittest.main()
