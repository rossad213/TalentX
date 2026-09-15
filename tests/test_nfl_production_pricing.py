#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_production_pricing import production_fair_value
from repair_nfl_production_prices import pricing_role_group


class NFLProductionPricingTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "nfl-player",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "role": "Wide Receiver",
            "careerStatus": "Active",
            "pricingConfidence": 0.86,
            "professionalGames": 60,
            "activeMetrics": {
                "performance": 80,
                "achievements": 70,
                "consistency": 76,
                "potential": 70,
                "availability": 75,
                "audience": 50,
            },
            "pricingEvidenceSummary": {
                "percentiles": {
                    "recentProduction": 0.80,
                    "efficiency": 0.75,
                    "careerProduction": 0.72,
                    "awardPoints": 0.45,
                },
                "rawSignals": {
                    "recentProduction": 100,
                    "careerProduction": 200,
                    "efficiency": 80,
                    "usage": 10,
                    "careerUsage": 40,
                    "awardPoints": 4,
                },
            },
        }
        base.update(updates)
        return base

    def test_current_production_beats_veteran_resume(self):
        productive = self.record(
            id="productive",
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.95,
                    "efficiency": 0.90,
                    "careerProduction": 0.72,
                    "awardPoints": 0.35,
                }
            },
            activeMetrics={
                "performance": 95,
                "achievements": 58,
                "consistency": 78,
                "potential": 78,
                "availability": 75,
                "audience": 35,
            },
        )
        veteran = self.record(
            id="veteran",
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.45,
                    "efficiency": 0.52,
                    "careerProduction": 0.94,
                    "awardPoints": 0.95,
                }
            },
            activeMetrics={
                "performance": 50,
                "achievements": 96,
                "consistency": 92,
                "potential": 45,
                "availability": 75,
                "audience": 98,
            },
        )
        productive_score, productive_price, _ = production_fair_value(productive)
        veteran_score, veteran_price, _ = production_fair_value(veteran)
        self.assertGreater(productive_score, veteran_score)
        self.assertGreater(productive_price, veteran_price)

    def test_audience_does_not_drive_nfl_fair_value(self):
        low_audience = self.record(activeMetrics={
            "performance": 80, "achievements": 70, "consistency": 76,
            "potential": 70, "availability": 75, "audience": 10,
        })
        high_audience = self.record(activeMetrics={
            "performance": 80, "achievements": 70, "consistency": 76,
            "potential": 70, "availability": 75, "audience": 100,
        })
        self.assertEqual(production_fair_value(low_audience)[1], production_fair_value(high_audience)[1])

    def test_role_groups_separate_receivers_and_defenders(self):
        self.assertEqual(pricing_role_group(self.record(role="Wide Receiver")), "WR")
        self.assertEqual(pricing_role_group(self.record(role="Tight End")), "TE")
        self.assertEqual(pricing_role_group(self.record(role="Linebacker")), "LB")
        self.assertEqual(pricing_role_group(self.record(role="Defensive End")), "EDGE")
        self.assertEqual(pricing_role_group(self.record(role="Cornerback")), "CB")

    def test_price_curve_preserves_clear_separation(self):
        elite = self.record(pricingEvidenceSummary={"percentiles": {
            "recentProduction": 0.97, "efficiency": 0.93,
            "careerProduction": 0.90, "awardPoints": 0.70,
        }})
        average = self.record(pricingEvidenceSummary={"percentiles": {
            "recentProduction": 0.50, "efficiency": 0.50,
            "careerProduction": 0.50, "awardPoints": 0.50,
        }})
        elite_price = production_fair_value(elite)[1]
        average_price = production_fair_value(average)[1]
        self.assertGreater(elite_price, average_price * 1.45)


if __name__ == "__main__":
    unittest.main()
