#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_production_pricing import production_fair_value
from repair_nfl_production_prices import REPAIR_VERSION, pricing_role_group, repair_catalog


class NFLProductionPricingTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "nfl-player",
            "name": "NFL Player",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "role": "Wide Receiver",
            "careerStatus": "Active",
            "careerStage": "Established",
            "pricingDataStatus": "Evidence enriched — recent statistics, career statistics",
            "pricingConfidence": 0.86,
            "professionalGames": 60,
            "marketPrice": 120.0,
            "previousMarketPrice": 118.0,
            "trend": [112.0, 118.0, 120.0],
            "priceEvents": [],
            "priceHistory": [],
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

    def test_position_value_is_secondary_but_cross_position_prices_are_calibrated(self):
        wr = self.record(role="Wide Receiver")
        te = self.record(role="Tight End")
        wr_score, wr_price, _ = production_fair_value(wr)
        te_score, te_price, _ = production_fair_value(te)
        self.assertEqual(wr_score, te_score)
        self.assertGreater(wr_price, te_price)
        self.assertLess(wr_price / te_price, 1.25)

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

    def test_rookie_ipo_anchor_is_preserved(self):
        rookie = self.record(
            careerStage="Active Rookie",
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, fair, explanation = production_fair_value(rookie)
        self.assertAlmostEqual(fair, 51.31, places=2)
        self.assertEqual(explanation["rookieInfluence"], 1.0)

    def test_established_star_with_missing_game_count_is_still_rebased(self):
        established = self.record(
            id="established-zero-games",
            professionalGames=0,
            marketPrice=90.0,
            lastGameMovePct=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            path.write_text(json.dumps([established]), encoding="utf-8")
            repriced, synchronized = repair_catalog(path, repaired_at="2026-09-15T00:00:00Z")
            updated = json.loads(path.read_text(encoding="utf-8"))[0]
        self.assertEqual(synchronized, 1)
        self.assertEqual(repriced, 1)
        self.assertEqual(updated["nflProductionRebaseVersion"], REPAIR_VERSION)
        self.assertNotEqual(updated["marketPrice"], 90.0)

    def test_pure_rookie_ipo_is_not_force_rebased(self):
        rookie = self.record(
            id="rookie-ipo",
            careerStage="Active Rookie",
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            marketPrice=45.0,
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            path.write_text(json.dumps([rookie]), encoding="utf-8")
            repriced, synchronized = repair_catalog(path, repaired_at="2026-09-15T00:00:00Z")
            updated = json.loads(path.read_text(encoding="utf-8"))[0]
        self.assertEqual(synchronized, 1)
        self.assertEqual(repriced, 0)
        self.assertEqual(updated["marketPrice"], 45.0)
        self.assertNotIn("nflProductionRebaseVersion", updated)


if __name__ == "__main__":
    unittest.main()
