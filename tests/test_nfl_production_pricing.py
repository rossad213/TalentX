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
from repair_nfl_production_prices import REPAIR_VERSION, repair_catalog


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

    def test_recent_and_career_production_drive_value(self):
        productive = self.record(
            id="productive",
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.95,
                "efficiency": 0.90,
                "careerProduction": 0.75,
            }},
        )
        lower = self.record(
            id="lower",
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.50,
                "efficiency": 0.55,
                "careerProduction": 0.65,
            }},
        )
        self.assertGreater(production_fair_value(productive)[0], production_fair_value(lower)[0])
        self.assertGreater(production_fair_value(productive)[1], production_fair_value(lower)[1])

    def test_role_and_position_do_not_change_price(self):
        prices = {
            production_fair_value(self.record(role=role))[1]
            for role in (
                "Quarterback", "Running Back", "Wide Receiver", "Tight End",
                "Linebacker", "Defensive End", "Cornerback", "Safety",
            )
        }
        self.assertEqual(len(prices), 1)

    def test_starter_or_reserve_label_does_not_change_price(self):
        starter = self.record(starter=True, roleStatus="starter")
        reserve = self.record(starter=False, roleStatus="reserve")
        self.assertEqual(production_fair_value(starter)[1], production_fair_value(reserve)[1])

    def test_nonproduction_fields_do_not_drive_nfl_fair_value(self):
        low = self.record(
            pricingConfidence=0.40,
            careerStatus="Injured",
            activeMetrics={
                "performance": 80, "achievements": 5, "consistency": 76,
                "potential": 5, "availability": 5, "audience": 5,
            },
        )
        high = self.record(
            pricingConfidence=0.99,
            careerStatus="Active",
            activeMetrics={
                "performance": 80, "achievements": 100, "consistency": 76,
                "potential": 100, "availability": 100, "audience": 100,
            },
        )
        self.assertEqual(production_fair_value(low)[1], production_fair_value(high)[1])

    def test_price_curve_preserves_clear_production_separation(self):
        elite = self.record(pricingEvidenceSummary={"percentiles": {
            "recentProduction": 0.97, "efficiency": 0.93,
            "careerProduction": 0.90,
        }})
        average = self.record(pricingEvidenceSummary={"percentiles": {
            "recentProduction": 0.50, "efficiency": 0.50,
            "careerProduction": 0.50,
        }})
        elite_price = production_fair_value(elite)[1]
        average_price = production_fair_value(average)[1]
        self.assertGreater(elite_price, average_price * 1.8)

    def test_rookie_ipo_anchor_is_only_for_preproduction_player(self):
        rookie = self.record(
            careerStage="Active Rookie",
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            pricingEvidenceSummary={},
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, fair, explanation = production_fair_value(rookie)
        self.assertAlmostEqual(fair, 51.31, places=2)
        self.assertEqual(explanation["rookieInfluence"], 1.0)

        produced = self.record(
            careerStage="Active Rookie",
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, produced_fair, produced_explanation = production_fair_value(produced)
        self.assertNotAlmostEqual(produced_fair, 51.31, places=2)
        self.assertEqual(produced_explanation["rookieInfluence"], 0.0)

    def test_repair_uses_one_universal_nfl_production_cohort(self):
        low = self.record(
            id="low-wr", name="Low WR", role="Wide Receiver", marketPrice=100,
            pricingEvidenceSummary={"rawSignals": {
                "recentProduction": 20, "careerProduction": 50, "efficiency": 20,
                "usage": 5, "careerUsage": 10, "awardPoints": 0,
            }},
        )
        high = self.record(
            id="high-lb", name="High LB", role="Linebacker", marketPrice=100,
            pricingEvidenceSummary={"rawSignals": {
                "recentProduction": 100, "careerProduction": 200, "efficiency": 80,
                "usage": 15, "careerUsage": 40, "awardPoints": 0,
            }},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            path.write_text(json.dumps([low, high]), encoding="utf-8")
            repriced, synchronized = repair_catalog(path, repaired_at="2026-09-15T00:00:00Z")
            updated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(synchronized, 2)
        self.assertEqual(repriced, 2)
        by_id = {record["id"]: record for record in updated}
        self.assertEqual(by_id["low-wr"]["pricingEvidenceSummary"]["cohort"], "NFL · universal production")
        self.assertEqual(by_id["high-lb"]["pricingEvidenceSummary"]["cohort"], "NFL · universal production")
        self.assertGreater(by_id["high-lb"]["marketPrice"], by_id["low-wr"]["marketPrice"])

    def test_pure_rookie_ipo_without_production_is_not_force_rebased(self):
        rookie = self.record(
            id="rookie-ipo",
            careerStage="Active Rookie",
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            marketPrice=45.0,
            pricingEvidenceSummary={},
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
