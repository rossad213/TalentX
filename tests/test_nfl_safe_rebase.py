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

from repair_nfl_production_prices_safe import (  # noqa: E402
    SAFE_REPAIR_VERSION,
    UNSAFE_REPAIR_VERSION,
    _recent_sample_games,
    _regular_season_event_games,
    repair_catalog,
)


class NFLSafeRebaseTests(unittest.TestCase):
    def record(
        self,
        player_id: str,
        name: str,
        *,
        usage: float,
        recent: float,
        career: float,
        old_sample=None,
        role: str = "Running Back",
        event_stats: dict | None = None,
    ):
        year = datetime.now(timezone.utc).year
        event = {
            "eventType": "game",
            "eventKey": f"{player_id}-week1",
            "startedAt": f"{year}-09-13T17:00:00Z",
            "priceBefore": 70.0,
            "priceAfter": 70.0,
        }
        if event_stats is not None:
            event["stats"] = event_stats
        return {
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
            "professionalGames": 0,
            "marketPrice": 70.0,
            "previousMarketPrice": 70.0,
            "trend": [70.0],
            "priceHistory": [],
            "priceEvents": [event],
            "activeMetrics": {
                "performance": 70,
                "achievements": 40,
                "consistency": 70,
                "potential": 80,
                "availability": 75,
                "audience": 50,
            },
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

    def test_provider_usage_beats_empty_event_shell(self):
        record = self.record("usage-rb", "Usage RB", usage=36.0, recent=100.0, career=150.0, old_sample=9)
        self.assertEqual(_recent_sample_games(record), 9)

    def test_verified_qb_box_score_beats_generic_usage(self):
        record = self.record(
            "usage-qb",
            "Usage QB",
            usage=17.0,
            recent=170.0,
            career=250.0,
            old_sample=9,
            role="Quarterback",
            event_stats={"passingYards": 250.0, "passingTouchdowns": 2.0, "interceptions": 1.0},
        )
        self.assertEqual(_recent_sample_games(record), 1)

    def test_verified_receiver_box_score_beats_generic_usage(self):
        record = self.record(
            "usage-wr",
            "Usage WR",
            usage=17.0,
            recent=140.0,
            career=200.0,
            old_sample=9,
            role="Wide Receiver",
            event_stats={"receptions": 6.0, "receivingYards": 90.0, "receivingTouchdowns": 1.0},
        )
        self.assertEqual(_recent_sample_games(record), 1)

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
        affected = self.record(
            "affected",
            "Affected RB",
            usage=0.0,
            recent=20.0,
            career=200.0,
            old_sample=None,
            event_stats={"rushingYards": 25.0, "car": 8.0},
        )
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
        # Use a non-RB fixture so this test isolates broad-v4 preservation rather
        # than intentionally triggering the RB cohort calibration layer.
        affected = self.record(
            "unsafe-affected",
            "Unsafe Affected QB",
            usage=0.0,
            recent=20.0,
            career=200.0,
            old_sample=1,
            role="Quarterback",
            event_stats={"passingYards": 210.0, "passingTouchdowns": 1.0, "interceptions": 1.0},
        )
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
