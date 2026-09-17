#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import nfl_production_pricing as core
import nfl_rookie_transition as rookie
from sync_nfl_draft_metadata import sync_records


class NFLRookieIpoSystemTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "young-nfl-player",
            "name": "Young NFL Player",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": 23,
            "experienceYears": 2,
            "draftYear": 2025,
            "draftRound": 1,
            "draftPick": 1,
            "professionalGames": 12,
            "marketPrice": 40.0,
            "previousMarketPrice": 39.0,
            "priceEvents": [],
            "activeMetrics": {
                "performance": 70,
                "achievements": 45,
                "consistency": 68,
                "potential": 86,
                "availability": 78,
                "audience": 50,
            },
            "pricingEvidenceSummary": {
                "percentiles": {
                    "recentProduction": 0.62,
                    "careerProduction": 0.60,
                    "efficiency": 0.58,
                    "awardPoints": 0.45,
                },
                "rawSignals": {
                    "recentProduction": 30,
                    "careerProduction": 120,
                    "efficiency": 70,
                    "usage": 8,
                    "careerUsage": 25,
                    "awardPoints": 1,
                },
                "recentSampleGamesEstimate": 2,
            },
        }
        base.update(updates)
        return base

    def test_meaningful_production_reduces_but_does_not_erase_ipo(self):
        record = self.record()
        self.assertTrue(core._has_meaningful_professional_evidence(record))
        fair, explanation = rookie.fair_value(record, current_year=2026)
        self.assertIsNotNone(fair)
        self.assertGreater(explanation["rookieInfluence"], 0.0)
        self.assertLess(explanation["rookieInfluence"], 1.0)
        self.assertTrue(explanation["rookieIpoFactors"]["meaningfulProduction"])

    def test_influence_fades_with_games_experience_and_time(self):
        early = self.record(professionalGames=4, experienceYears=1, draftYear=2026)
        later = self.record(professionalGames=28, experienceYears=4, draftYear=2023)
        early_influence, _ = rookie.influence(early, current_year=2026)
        later_influence, _ = rookie.influence(later, current_year=2026)
        self.assertGreater(early_influence, later_influence)

    def test_older_player_retains_less_than_younger_same_draft_profile(self):
        younger = self.record(age=23, draftRound=2, draftPick=40)
        older = self.record(age=27, draftRound=2, draftPick=40)
        young_influence, _ = rookie.influence(younger, current_year=2026)
        old_influence, _ = rookie.influence(older, current_year=2026)
        self.assertGreater(young_influence, old_influence)

    def test_draft_capital_affects_anchor_and_retention(self):
        premium = self.record(draftPick=1)
        later = self.record(draftRound=3, draftPick=80)
        premium_fair, premium_explanation = rookie.fair_value(premium, current_year=2026)
        later_fair, later_explanation = rookie.fair_value(later, current_year=2026)
        self.assertGreater(premium_explanation["rookieIpoAnchor"], later_explanation["rookieIpoAnchor"])
        self.assertGreater(premium_explanation["rookieInfluence"], later_explanation["rookieInfluence"])
        self.assertGreater(premium_fair, later_fair)

    def test_running_back_hands_off_faster_than_quarterback_at_equal_games(self):
        quarterback = self.record(role="Quarterback", professionalGames=12)
        running_back = self.record(role="Running Back", professionalGames=12)
        qb_influence, qb_detail = rookie.influence(quarterback, current_year=2026)
        rb_influence, rb_detail = rookie.influence(running_back, current_year=2026)
        self.assertLess(qb_detail["roleGamePace"], rb_detail["roleGamePace"])
        self.assertGreater(qb_influence, rb_influence)

    def test_old_draft_class_is_no_longer_rookie_ipo_eligible(self):
        record = self.record(draftYear=2021, experienceYears=6, professionalGames=20)
        fair, explanation = rookie.fair_value(record, current_year=2026)
        self.assertIsNone(fair)
        self.assertIsNone(explanation)

    def test_missing_draft_metadata_never_invents_an_anchor(self):
        record = self.record(draftYear=None, draftRound=None, draftPick=None)
        fair, explanation = rookie.fair_value(record, current_year=2026)
        self.assertIsNone(fair)
        self.assertIsNone(explanation)

    def test_missing_recent_sample_does_not_treat_provider_zero_as_real_zero(self):
        record = self.record(
            professionalGames=0,
            experienceYears=3,
            draftYear=2024,
            age=24,
            pricingEvidenceSummary={
                "percentiles": {
                    "recentProduction": 0.05,
                    "careerProduction": 0.65,
                    "efficiency": 0.05,
                    "awardPoints": 0.45,
                },
                "rawSignals": {
                    "recentProduction": 0,
                    "careerProduction": 140,
                    "efficiency": 0,
                    "usage": 0,
                    "careerUsage": 30,
                    "awardPoints": 1,
                },
                "recentSampleGamesEstimate": None,
            },
        )
        unguarded = core.production_components(record)["productionScore"]
        _, explanation = rookie.fair_value(record, current_year=2026)
        self.assertTrue(explanation["rookieIpoMissingRecentSampleGuard"])
        self.assertGreater(explanation["productionScore"], unguarded)
        self.assertAlmostEqual(explanation["inputs"]["recentProduction"], 65.0, places=1)

    def test_metadata_sync_prefers_espn_id_without_changing_price(self):
        records = [{
            "name": "Catalog Name Variant", "leagueOrMedium": "NFL",
            "sourceNamespace": "espn", "sourceRecordId": "12345", "marketPrice": 44.5,
        }]
        source = [{
            "display_name": "Different Provider Name", "espn_id": "12345",
            "draft_year": "2025", "draft_round": "2", "draft_pick": "40",
        }]
        counts = sync_records(records, source, {}, synced_at="2026-09-16T00:00:00Z")
        self.assertEqual(counts["matchedByEspnId"], 1)
        self.assertEqual(records[0]["draftYear"], 2025)
        self.assertEqual(records[0]["draftRound"], 2)
        self.assertEqual(records[0]["draftPick"], 40)
        self.assertEqual(records[0]["marketPrice"], 44.5)

    def test_metadata_sync_uses_unique_name_then_local_override_as_fallbacks(self):
        records = [
            {"name": "Unique Prospect", "leagueOrMedium": "NFL", "marketPrice": 30.0},
            {"name": "Override Prospect", "leagueOrMedium": "NFL", "marketPrice": 31.0},
        ]
        source = [{
            "display_name": "Unique Prospect", "espn_id": "888",
            "draft_year": "2025", "draft_round": "3", "draft_pick": "70",
        }]
        overrides = {"overrideprospect": {
            "name": "Override Prospect", "draftYear": 2025, "draftRound": 5,
            "draftPick": 150, "sourceUrl": "https://example.invalid/factual-source",
        }}
        counts = sync_records(records, source, overrides, synced_at="2026-09-16T00:00:00Z")
        self.assertEqual(counts["matchedByName"], 1)
        self.assertEqual(counts["matchedByOverride"], 1)
        self.assertEqual(records[0]["draftPick"], 70)
        self.assertEqual(records[1]["draftPick"], 150)
        self.assertEqual([r["marketPrice"] for r in records], [30.0, 31.0])


if __name__ == "__main__":
    unittest.main()
