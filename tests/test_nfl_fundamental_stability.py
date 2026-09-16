#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_production_pricing import production_fair_value  # noqa: E402
from repair_nfl_production_prices import (  # noqa: E402
    EARLY_SEASON_FULL_WEIGHT_GAMES,
    REPAIR_VERSION,
    _regular_season_event_games,
    _stabilize_early_season_percentiles,
    repair_catalog,
)


class NFLFundamentalStabilityTests(unittest.TestCase):
    def record(self, *, player_id: str, name: str, role: str, raw: dict, **updates):
        base = {
            "id": player_id,
            "name": name,
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": role,
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": 24,
            "experienceYears": 3,
            "starter": True,
            "pricingDataStatus": "Evidence enriched — recent statistics, career statistics",
            "pricingConfidence": 0.86,
            "professionalGames": 35,
            "marketPrice": 70.0,
            "previousMarketPrice": 70.0,
            "trend": [70.0],
            "priceEvents": [],
            "priceHistory": [],
            "activeMetrics": {
                "performance": 75,
                "achievements": 45,
                "consistency": 70,
                "potential": 80,
                "availability": 75,
                "audience": 50,
            },
            "pricingEvidenceSummary": {
                "rawSignals": {
                    "recentProduction": 0.0,
                    "careerProduction": 0.0,
                    "efficiency": 0.0,
                    "usage": 68.0,
                    "careerUsage": 120.0,
                    "awardPoints": 0.0,
                    **raw,
                }
            },
        }
        base.update(updates)
        return base

    def test_position_normalization_prevents_qb_raw_scale_from_distorting_running_backs(self):
        low_rb = self.record(
            player_id="rb-low",
            name="RB Low",
            role="Running Back",
            raw={"recentProduction": 50, "careerProduction": 100, "efficiency": 50},
        )
        high_rb = self.record(
            player_id="rb-high",
            name="RB High",
            role="Running Back",
            raw={"recentProduction": 100, "careerProduction": 200, "efficiency": 80},
        )
        giant_qb = self.record(
            player_id="qb-giant",
            name="QB Giant",
            role="Quarterback",
            raw={"recentProduction": 5000, "careerProduction": 12000, "efficiency": 200},
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            path.write_text(json.dumps([low_rb, high_rb, giant_qb]), encoding="utf-8")
            repair_catalog(path, repaired_at="2026-09-15T00:00:00Z")
            updated = {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

        low_pct = updated["rb-low"]["pricingEvidenceSummary"]["percentiles"]["recentProduction"]
        high_pct = updated["rb-high"]["pricingEvidenceSummary"]["percentiles"]["recentProduction"]
        self.assertAlmostEqual(low_pct, 0.25, places=2)
        self.assertAlmostEqual(high_pct, 0.75, places=2)
        self.assertGreater(high_pct, low_pct)
        self.assertEqual(updated["rb-high"]["pricingEvidenceSummary"]["cohort"], "NFL · RB normalized production")
        self.assertEqual(updated["rb-high"]["nflProductionRebaseVersion"], REPAIR_VERSION)

    def test_one_game_recent_sample_is_shrunk_toward_durable_career_evidence(self):
        record = self.record(
            player_id="one-game-rb",
            name="One Game RB",
            role="Running Back",
            raw={"usage": 4.0},
            starter=True,
        )
        raw_pcts = {
            "recentProduction": 0.95,
            "careerProduction": 0.75,
            "efficiency": 0.90,
            "usage": 0.50,
            "careerUsage": 0.75,
            "awardPoints": 0.50,
        }
        stable, games, weight = _stabilize_early_season_percentiles(record, raw_pcts)
        self.assertEqual(games, 1)
        self.assertAlmostEqual(weight, 1.0 / EARLY_SEASON_FULL_WEIGHT_GAMES, places=4)
        self.assertAlmostEqual(stable["recentProduction"], 0.75 + (0.95 - 0.75) / 6.0, places=4)
        self.assertLess(stable["recentProduction"], 0.80)
        self.assertLess(stable["efficiency"], 0.60)

    def test_regular_season_event_history_supplies_sample_when_usage_is_missing(self):
        season_year = datetime.now(timezone.utc).year if datetime.now(timezone.utc).month >= 7 else datetime.now(timezone.utc).year - 1
        record = self.record(
            player_id="event-rb",
            name="Event RB",
            role="Running Back",
            raw={"usage": 0.0},
            starter=False,
            priceEvents=[
                {
                    "eventType": "game",
                    "eventKey": "preseason-1",
                    "startedAt": f"{season_year}-08-20T00:00:00Z",
                },
                {
                    "eventType": "game",
                    "eventKey": "regular-1",
                    "startedAt": f"{season_year}-09-13T17:00:00Z",
                },
            ],
        )
        self.assertEqual(_regular_season_event_games(record), 1)
        raw_pcts = {
            "recentProduction": 0.60,
            "careerProduction": 0.92,
            "efficiency": 0.65,
            "usage": 0.45,
            "careerUsage": 0.50,
            "awardPoints": 0.50,
        }
        stable, games, weight = _stabilize_early_season_percentiles(record, raw_pcts)
        self.assertEqual(games, 1)
        self.assertAlmostEqual(weight, 1.0 / 6.0, places=4)
        self.assertGreater(stable["recentProduction"], 0.85)
        self.assertLess(stable["efficiency"], 0.55)

    def test_six_game_sample_receives_full_recent_weight(self):
        record = self.record(
            player_id="six-game-rb",
            name="Six Game RB",
            role="Running Back",
            raw={"usage": 24.0},
            starter=True,
        )
        raw_pcts = {
            "recentProduction": 0.90,
            "careerProduction": 0.60,
            "efficiency": 0.80,
            "usage": 0.50,
            "careerUsage": 0.60,
            "awardPoints": 0.50,
        }
        stable, games, weight = _stabilize_early_season_percentiles(record, raw_pcts)
        self.assertEqual(games, 6)
        self.assertEqual(weight, 1.0)
        self.assertEqual(stable, raw_pcts)

    def test_second_year_player_with_zero_nfl_games_keeps_decaying_draft_anchor(self):
        current_year = datetime.now(timezone.utc).year
        howard_shape = {
            "id": "will-howard-shape",
            "name": "Will Howard Shape",
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": 24,
            "experienceYears": 2,
            "professionalGames": 0,
            "draftYear": current_year - 1,
            "draftRound": 6,
            "draftPick": 185,
            "careerScore": 35,
            "activeMetrics": {
                "performance": 36,
                "achievements": 14,
                "consistency": 38,
                "potential": 60,
                "availability": 82,
                "audience": 45,
            },
            "pricingEvidenceSummary": {},
        }
        _, fair, explanation = production_fair_value(howard_shape)
        self.assertIsNotNone(explanation)
        self.assertGreater(explanation["rookieIpoAnchor"], 25.0)
        self.assertEqual(explanation["rookieInfluence"], 0.60)
        self.assertEqual(explanation["rookieAnchorReconstruction"]["source"], "reconstructed-from-draft-metadata")
        self.assertGreater(fair, 15.0)
        self.assertGreater(fair, 6.0)

    def test_catalog_recovers_factual_draft_metadata_before_pricing_no_debut_player(self):
        current_year = datetime.now(timezone.utc).year
        howard = self.record(
            player_id="howard-live-shape",
            name="Will Howard",
            role="Quarterback",
            raw={"recentProduction": 0.0, "careerProduction": 0.26, "efficiency": 0.0, "usage": 0.0},
            starter=False,
            experienceYears=2,
            professionalGames=0,
            marketPrice=5.0,
            previousMarketPrice=5.0,
            careerScore=45.0,
            priceEvents=[
                {
                    "eventType": "game",
                    "eventKey": "preseason-only",
                    "startedAt": f"{current_year}-08-27T23:00:00Z",
                    "priceBefore": 5.0,
                    "priceAfter": 5.0,
                }
            ],
        )
        peer = self.record(
            player_id="qb-peer",
            name="QB Peer",
            role="Quarterback",
            raw={"recentProduction": 80.0, "careerProduction": 150.0, "efficiency": 90.0},
        )
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "sports.json"
            metadata = directory / "draft.json"
            path.write_text(json.dumps([howard, peer]), encoding="utf-8")
            metadata.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "name": "Will Howard",
                                "league": "NFL",
                                "draftYear": current_year - 1,
                                "draftRound": 6,
                                "draftPick": 185,
                            }
                        ],
                        "source": "https://www.nfl.com/draft/tracker/2025/teams/pittsburgh-steelers",
                    }
                ),
                encoding="utf-8",
            )
            repair_catalog(path, repaired_at="2026-09-16T00:00:00Z", draft_metadata_path=metadata)
            updated = {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

        repaired = updated["howard-live-shape"]
        self.assertEqual(repaired["draftPick"], 185)
        self.assertEqual(repaired["draftRound"], 6)
        self.assertGreater(repaired["nflProductionPricing"]["rookieInfluence"], 0.0)
        self.assertGreater(repaired["marketPrice"], 6.0)
        self.assertEqual(repaired["nflProductionRebaseVersion"], REPAIR_VERSION)

    def test_old_zero_game_draft_anchor_eventually_expires(self):
        current_year = datetime.now(timezone.utc).year
        old = {
            "id": "old-no-debut",
            "name": "Old No Debut",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "careerStatus": "Active",
            "age": 27,
            "professionalGames": 0,
            "draftYear": current_year - 4,
            "draftPick": 185,
            "careerScore": 35,
            "activeMetrics": {"performance": 36, "achievements": 14, "consistency": 38, "availability": 75},
            "pricingEvidenceSummary": {},
        }
        _, fair, explanation = production_fair_value(old)
        self.assertEqual(explanation["rookieInfluence"], 0.0)
        self.assertAlmostEqual(fair, explanation["careerFairValue"], places=2)


if __name__ == "__main__":
    unittest.main()
