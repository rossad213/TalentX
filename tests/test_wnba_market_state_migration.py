from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_wnba_market_state import (
    MIGRATION_EVENT_ID,
    MIGRATION_VERSION,
    STATE_VERSION,
    migrate_record,
    seed_state_manifest,
)
from pricing_engine_v2 import apply_v2


class WNBAMarketMigrationTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "live-espn-basketball-5345444",
            "name": "Laura Juskaite",
            "primaryCategory": "Athlete",
            "discipline": "Basketball",
            "leagueOrMedium": "WNBA",
            "sourceNamespace": "espn",
            "sourceRecordId": "5345444",
            "role": "Forward",
            "careerStatus": "Active",
            "careerStage": "Active Rookie",
            "professionalGames": 43,
            "pricingConfidence": 0.84,
            "activeMetrics": {
                "performance": 72.7,
                "achievements": 63.5,
                "potential": 68.3,
                "audience": 89.2,
                "availability": 75.0,
                "consistency": 76.4,
            },
            "marketPrice": 244.55,
            "fundamentalValue": 106.66,
            "lastGameMovePct": 1.112,
            "dailyChange": 1.11,
            "hourlyChangePct": 1.11,
            "trend": [107.76, 127.73, 181.06, 244.55],
            "priceEvents": [
                {
                    "eventKey": "espn:401857215",
                    "eventId": "401857215",
                    "provider": "ESPN",
                    "league": "wnba",
                    "name": "Toronto Tempo at Connecticut Sun",
                    "startedAt": "2026-09-24T23:00:00Z",
                    "eventType": "game",
                    "priceBefore": 241.86,
                    "priceAfter": 244.55,
                    "movePct": 1.112,
                }
            ],
            "priceHistory": [
                {
                    "time": "2026-09-24T23:00:00Z",
                    "eventId": "espn:401857215",
                    "phase": "close",
                    "price": 244.55,
                }
            ],
        }
        base.update(updates)
        return base

    def test_reset_uses_clean_fair_value_once(self):
        original = self.record()
        expected = apply_v2({**original, "lastGameMovePct": 0.0, "dailyChange": 0.0, "hourlyChangePct": 0.0})
        migrated, changed = migrate_record(original, "2026-09-26T05:30:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["marketPrice"], expected["fairValue"])
        self.assertEqual(migrated["fundamentalValue"], expected["fairValue"])
        self.assertEqual(migrated["wnbaMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertEqual(migrated["lastGameMovePct"], 0.0)
        self.assertEqual(migrated["trend"], [expected["fairValue"]] * 18)

    def test_event_ledger_is_preserved_for_audit(self):
        original = self.record()
        migrated, _ = migrate_record(original, "2026-09-26T05:30:00Z")
        self.assertEqual(migrated["priceEvents"], original["priceEvents"])
        self.assertEqual(migrated["priceHistory"][0], original["priceHistory"][0])
        self.assertEqual(migrated["priceHistory"][-1]["eventId"], MIGRATION_EVENT_ID)

    def test_migration_is_idempotent(self):
        first, changed = migrate_record(self.record(), "2026-09-26T05:30:00Z")
        second, changed_again = migrate_record(first, "2026-09-27T05:30:00Z")
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second, first)

    def test_non_wnba_is_untouched(self):
        nba = self.record(leagueOrMedium="NBA", name="NBA Player")
        migrated, changed = migrate_record(nba, "2026-09-26T05:30:00Z")
        self.assertFalse(changed)
        self.assertEqual(migrated, nba)

    def test_manifest_seeds_existing_games_as_already_processed(self):
        record = self.record()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wnba_event_refresh_manifest.json"
            event_count, player_count = seed_state_manifest([record], path, "2026-09-26T05:30:00Z")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], STATE_VERSION)
        self.assertEqual(event_count, 1)
        self.assertEqual(player_count, 1)
        self.assertEqual(payload["processedEvents"][0]["key"], "espn:401857215")
        self.assertIn("espn:401857215|espn:5345444", payload["processedPlayerEvents"])

    def test_manifest_seeding_preserves_newer_markers(self):
        record = self.record()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wnba_event_refresh_manifest.json"
            path.write_text(json.dumps({
                "version": STATE_VERSION,
                "processedEvents": [{
                    "key": "espn:new-game",
                    "eventId": "new-game",
                    "startedAt": "2026-09-25T23:00:00Z",
                }],
                "processedPlayerEvents": ["espn:new-game|espn:999"],
            }), encoding="utf-8")
            seed_state_manifest([record], path, "2026-09-26T05:30:00Z")
            payload = json.loads(path.read_text(encoding="utf-8"))
        keys = {item["key"] for item in payload["processedEvents"]}
        self.assertEqual(keys, {"espn:401857215", "espn:new-game"})
        self.assertIn("espn:new-game|espn:999", payload["processedPlayerEvents"])


if __name__ == "__main__":
    unittest.main()
