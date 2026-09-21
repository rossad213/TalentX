#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_production_pricing import (
    career_runway_score,
    price_from_score,
    production_fair_value,
)
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
            "age": 28,
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

    def test_recent_and_career_production_remain_dominant(self):
        productive = self.record(
            id="productive",
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.95,
                "efficiency": 0.90,
                "careerProduction": 0.85,
                "awardPoints": 0.40,
            }},
        )
        lower = self.record(
            id="lower",
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.50,
                "efficiency": 0.55,
                "careerProduction": 0.65,
                "awardPoints": 0.80,
            }},
        )
        self.assertGreater(production_fair_value(productive)[0], production_fair_value(lower)[0])
        self.assertGreater(production_fair_value(productive)[1], production_fair_value(lower)[1])

    def test_role_and_position_do_not_create_price_premium(self):
        prices = {
            production_fair_value(self.record(role=role))[1]
            for role in (
                "Quarterback", "Running Back", "Wide Receiver", "Tight End",
                "Linebacker", "Defensive End", "Cornerback", "Safety",
            )
        }
        self.assertEqual(len(prices), 1)

    def test_verified_role_changes_potential_without_changing_production(self):
        starter = self.record(starter=True, roleStatus="starter")
        reserve = self.record(starter=False, roleStatus="reserve")
        starter_result = production_fair_value(starter)
        reserve_result = production_fair_value(reserve)
        self.assertEqual(starter_result[2]["productionScore"], reserve_result[2]["productionScore"])
        self.assertGreater(starter_result[2]["valueInputs"]["potential"], reserve_result[2]["valueInputs"]["potential"])
        self.assertGreater(starter_result[1], reserve_result[1])

    def test_audience_or_fame_does_not_change_nfl_fair_value(self):
        low = self.record(activeMetrics={
            "performance": 80, "achievements": 70, "consistency": 76,
            "potential": 70, "availability": 75, "audience": 5,
        })
        high = self.record(activeMetrics={
            "performance": 80, "achievements": 70, "consistency": 76,
            "potential": 70, "availability": 75, "audience": 100,
        })
        self.assertEqual(production_fair_value(low)[1], production_fair_value(high)[1])

    def test_development_potential_changes_value(self):
        low = self.record(activeMetrics={"consistency": 76, "potential": 35, "availability": 75})
        high = self.record(activeMetrics={"consistency": 76, "potential": 95, "availability": 75})
        self.assertGreater(production_fair_value(high)[1], production_fair_value(low)[1])

    def test_age_and_career_stage_apply_modest_not_crushing_discount(self):
        young = self.record(age=25, careerStage="Early Career")
        veteran = self.record(age=34, careerStage="Veteran")
        young_price = production_fair_value(young)[1]
        veteran_price = production_fair_value(veteran)[1]
        self.assertGreater(young_price, veteran_price)
        self.assertGreater(veteran_price, young_price * 0.70)
        self.assertGreater(career_runway_score(young), career_runway_score(veteran))

    def test_elite_veteran_production_still_beats_young_mediocre_production(self):
        elite_veteran = self.record(
            age=34,
            careerStage="Veteran",
            activeMetrics={"availability": 75},
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.93,
                "careerProduction": 0.98,
                "efficiency": 0.90,
                "awardPoints": 0.90,
            }},
        )
        young_mediocre = self.record(
            age=23,
            careerStage="Early Career",
            activeMetrics={"availability": 90},
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.55,
                "careerProduction": 0.45,
                "efficiency": 0.55,
                "awardPoints": 0.10,
            }},
        )
        self.assertGreater(production_fair_value(elite_veteran)[1], production_fair_value(young_mediocre)[1])

    def test_price_curve_matches_shared_talentx_athlete_market_scale(self):
        self.assertGreater(price_from_score(70), 155)
        self.assertLess(price_from_score(70), 170)
        self.assertGreater(price_from_score(93), 280)
        self.assertGreater(price_from_score(95), 295)

    def test_rookie_ipo_anchor_is_preserved_before_production(self):
        rookie = self.record(
            careerStage="Active Rookie",
            age=22,
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            pricingEvidenceSummary={},
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, fair, explanation = production_fair_value(rookie)
        self.assertAlmostEqual(fair, 51.31, places=2)
        self.assertEqual(explanation["rookieInfluence"], 1.0)

    def test_third_year_player_has_exited_the_ipo_regime(self):
        third_year = self.record(
            careerStage="Early Career",
            age=24,
            draftYear=2024,
            experienceYears=3,
            professionalGames=30,
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 80.0},
        )
        _, _, explanation = production_fair_value(third_year)
        self.assertEqual(explanation["rookieInfluence"], 0.0)
        self.assertEqual(explanation["fairValue"], explanation["careerFairValue"])

    def test_rookie_ipo_fades_instead_of_disappearing_after_first_stats(self):
        produced = self.record(
            careerStage="Active Rookie",
            age=22,
            professionalGames=4,
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, fair, explanation = production_fair_value(produced)
        self.assertEqual(explanation["rookieInfluence"], 0.75)
        self.assertNotAlmostEqual(fair, explanation["careerFairValue"], places=2)
        self.assertNotAlmostEqual(fair, 51.31, places=2)

        later = self.record(
            careerStage="Early Career",
            age=23,
            professionalGames=24,
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 51.31},
        )
        _, _, later_explanation = production_fair_value(later)
        self.assertEqual(later_explanation["rookieInfluence"], 0.10)

    def test_rookie_score_can_reconstruct_missing_ipo_anchor(self):
        rookie = self.record(
            careerStage="Rookie IPO",
            age=22,
            professionalGames=0,
            pricingEvidenceSummary={},
            rookiePricing={"draftInfluencePct": 100, "rookieScore": 90},
        )
        _, fair, explanation = production_fair_value(rookie)
        self.assertGreater(fair, 110)
        self.assertLess(fair, 116)
        self.assertEqual(fair, explanation["rookieIpoAnchor"])

    def test_josh_allen_shape_prices_well_above_late_career_veteran_shape(self):
        elite_prime = self.record(
            name="Elite Prime QB",
            role="Quarterback",
            age=30,
            careerStage="Established",
            activeMetrics={"availability": 90},
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.98,
                "careerProduction": 0.96,
                "efficiency": 0.95,
                "awardPoints": 0.95,
            }},
        )
        late_veteran = self.record(
            name="Accomplished Late Veteran",
            role="Defensive End",
            age=34,
            careerStage="Veteran",
            activeMetrics={"availability": 75},
            pricingEvidenceSummary={"percentiles": {
                "recentProduction": 0.70,
                "careerProduction": 0.90,
                "efficiency": 0.75,
                "awardPoints": 0.75,
            }},
        )
        elite_price = production_fair_value(elite_prime)[1]
        veteran_price = production_fair_value(late_veteran)[1]
        self.assertGreater(elite_price, 225)
        self.assertLess(veteran_price, 100)
        self.assertGreater(elite_price, veteran_price * 2)

    def test_repair_normalizes_raw_production_inside_position_groups(self):
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
                "usage": 15, "careerUsage": 40, "awardPoints": 8,
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
        self.assertEqual(by_id["low-wr"]["pricingEvidenceSummary"]["cohort"], "NFL · REC normalized production")
        self.assertEqual(by_id["high-lb"]["pricingEvidenceSummary"]["cohort"], "NFL · DEF normalized production")
        self.assertGreater(by_id["high-lb"]["marketPrice"], by_id["low-wr"]["marketPrice"])
        self.assertEqual(by_id["high-lb"]["nflProductionRebaseVersion"], REPAIR_VERSION)

    def test_pure_rookie_ipo_is_rebased_to_ipo_value_instead_of_left_at_seven_dollars(self):
        rookie = self.record(
            id="rookie-ipo",
            careerStage="Active Rookie",
            age=22,
            pricingDataStatus="Rookie IPO — verified draft position; awaiting professional statistics",
            professionalGames=0,
            marketPrice=7.0,
            previousMarketPrice=7.0,
            pricingEvidenceSummary={},
            rookiePricing={"draftInfluencePct": 100, "calibratedIpoPrice": 42.0},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            path.write_text(json.dumps([rookie]), encoding="utf-8")
            repriced, synchronized = repair_catalog(path, repaired_at="2026-09-15T00:00:00Z")
            updated = json.loads(path.read_text(encoding="utf-8"))[0]
        self.assertEqual(synchronized, 1)
        self.assertEqual(repriced, 1)
        self.assertAlmostEqual(updated["marketPrice"], 42.0, places=2)
        self.assertEqual(updated["nflProductionRebaseVersion"], REPAIR_VERSION)


if __name__ == "__main__":
    unittest.main()
