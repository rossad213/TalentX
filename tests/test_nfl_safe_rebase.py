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

from nfl_production_pricing import MODEL_VERSION  # noqa: E402
from repair_nfl_production_prices_safe import (  # noqa: E402
    SAFE_REPAIR_VERSION,
    UNSAFE_REPAIR_VERSION,
    _recent_sample_games,
    _regular_season_event_games,
    repair_catalog,
)


class NFLSafeRebaseTests(unittest.TestCase):
    def record(self, player_id: str, name: str, *, usage: float, recent: float, career: float, old_sample=None):
        year = datetime.now(timezone.utc).year
        return {
            "id": player_id,
            "name": name,
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": "Running Back",
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": 24,
            "experienceYears": 3,
            "starter": True,
            "professionalGames": 0,
            "marketPrice": 70.0,
            "previousMarketPrice": 70.0,
            "trend": [70.0],
            "priceHistory": [],
            "priceEvents": [
                {
                    "eventType": "game",
                    "eventKey": f"{player_id}-week1",
                    "startedAt": f"{year}-09-13T17:00:00Z",
                    "priceBefore": 70.0,
                    "priceAfter": 70.0,
                }
            ],
            "activeMetrics": {
                "performance": 70,
                "achievements": 40,
                "consistency": 70,
                "potential": 80,
                "availability": 75,
                "audience": 50,
            },
            "nflProductionPriceModelVersion": MODEL_VERSION,
            "pricingEvidenceSummary": {
                "recentSampleGamesEstimate": old_sample,
                "rawSignals": {
                    "recentProduction": recent,
                    "careerProduction": career,
                    "efficiency": 80.0,
                    "usage": usage,
                    "careerUsage": 100.0,
                    "awardPoints": 0.0,
                },
            },
        }

    def test_verified_game_event_beats_generic_usage_estimate(self):
        record = self.record("usage-rb", "Usage RB", usage=36.0, recent=100.0, career=150.0, old_sample=9)
        self.assertEqual(_recent_sample_games(record), 1)

    def test_two_verified_games_fix_non_rb_one_game_usage_estimate(self):
        year = datetime.now(timezone.utc).year
        record = self.record("two-game-wr", "Two Game WR", usage=2.0, recent=100.0, career=150.0, old_sample=1)
        record["role"] = "Wide Receiver"
        record["starter"] = False
        record["priceEvents"].append({
            "eventType": "game",
            "eventKey": "two-game-wr-week2",
            "startedAt": f"{year}-09-20T17:00:00Z",
            "priceBefore": 70.0,
            "priceAfter": 70.0,
        })
        self.assertEqual(_recent_sample_games(record), 2)

    def test_preseason_only_does_not_invent_zero_game_sample(self):
        year = datetime.now(timezone.utc).year
        record = self.record("pre-rb", "Preseason RB", usage=0.0, recent=20.0, career=100.0, old_sample=None)
        record["priceEvents"] = [
            {
                "eventType": "game",
                "eventKey": "preseason",
                "startedAt": f"{year}-08-20T00:00:00Z",
            }
        ]
        self.assertIsNone(_regular_season_event_games(record))
        self.assertIsNone(_recent_sample_games(record))

    def test_only_changed_evidence_rebases_market_price(self):
        affected = self.record("affected", "Affected RB", usage=0.0, recent=20.0, career=200.0, old_sample=None)
        stable = self.record("stable", "Stable RB", usage=4.0, recent=120.0, career=180.0, old_sample=1)
        stable["marketPrice"] = 88.88
        stable["previousMarketPrice"] = 88.88
        stable["trend"] = [88.88]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            empty_draft = Path(directory) / "draft.json"
            path.write_text(json.dumps([affected, stable]), encoding="utf-8")
            empty_draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repriced, synchronized, sample_repairs, draft_repairs = repair_catalog(
                path,
                repaired_at="2026-09-16T00:00:00Z",
                draft_metadata_path=empty_draft,
            )
            updated = {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}

        self.assertEqual(repriced, 1)
        self.assertEqual(sample_repairs, 1)
        self.assertEqual(draft_repairs, 0)
        self.assertEqual(synchronized, 2)
        self.assertNotEqual(updated["affected"]["marketPrice"], 70.0)
        self.assertEqual(updated["affected"]["nflProductionRebaseVersion"], SAFE_REPAIR_VERSION)
        self.assertEqual(updated["stable"]["marketPrice"], 88.88)
        self.assertNotEqual(updated["stable"].get("nflProductionRebaseVersion"), SAFE_REPAIR_VERSION)

    def test_unaffected_broad_v4_rebase_is_restored(self):
        stable = self.record("unsafe-stable", "Unsafe Stable RB", usage=4.0, recent=120.0, career=180.0, old_sample=1)
        stable["marketPrice"] = 50.0
        stable["previousMarketPrice"] = 45.0
        stable["trend"] = [50.0]
        stable["nflProductionRebaseVersion"] = UNSAFE_REPAIR_VERSION
        stable["nflProductionRebase"] = {
            "oldPrice": 88.88,
            "historyScaleRatio": 50.0 / 88.88,
            "rebasedPrice": 50.0,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            draft = Path(directory) / "draft.json"
            path.write_text(json.dumps([stable]), encoding="utf-8")
            draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repair_catalog(path, repaired_at="2026-09-16T00:00:00Z", draft_metadata_path=draft)
            updated = json.loads(path.read_text(encoding="utf-8"))[0]

        self.assertEqual(updated["marketPrice"], 88.88)
        self.assertEqual(updated["nflProductionRebaseVersion"], SAFE_REPAIR_VERSION)
        self.assertEqual(updated["nflProductionRebase"]["reason"], "unaffected-broad-v4-rebase-restored")

    def test_evidence_backed_broad_v4_rebase_is_preserved(self):
        affected = self.record("unsafe-affected", "Unsafe Affected RB", usage=0.0, recent=20.0, career=200.0, old_sample=1)
        affected["marketPrice"] = 111.11
        affected["nflProductionRebaseVersion"] = UNSAFE_REPAIR_VERSION
        affected["nflProductionRebase"] = {
            "oldPrice": 70.0,
            "historyScaleRatio": 111.11 / 70.0,
            "rebasedPrice": 111.11,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sports.json"
            draft = Path(directory) / "draft.json"
            path.write_text(json.dumps([affected]), encoding="utf-8")
            draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repair_catalog(path, repaired_at="2026-09-16T00:00:00Z", draft_metadata_path=draft)
            updated = json.loads(path.read_text(encoding="utf-8"))[0]

        self.assertEqual(updated["marketPrice"], 111.11)
        self.assertEqual(updated["nflProductionRebaseVersion"], SAFE_REPAIR_VERSION)
        self.assertEqual(updated["nflProductionRebase"]["reason"], "missing-sample-recovered")


if __name__ == "__main__":
    unittest.main()
