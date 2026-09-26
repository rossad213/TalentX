from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_tennis_market_state import (
    MIGRATION_EVENT_ID,
    MIGRATION_VERSION,
    migrate_record,
)


class TennisMarketMigrationTests(unittest.TestCase):
    def tennis_record(self, **updates):
        base = {
            "id": "athlete-tennis-carlos-alcaraz",
            "name": "Carlos Alcaraz",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "leagueOrMedium": "ATP",
            "sourceRank": 2,
            "careerStage": "Established",
            "careerStatus": "Active",
            "careerScore": 86,
            "pricingConfidence": .64,
            "dataConfidence": .64,
            "marketPrice": 140.0,
            "fundamentalValue": 118.0,
            "lastGameMovePct": 2.0,
            "dailyChange": 2.0,
            "hourlyChangePct": 2.0,
            "activeMetrics": {
                "performance": 88,
                "achievements": 84,
                "consistency": 85,
                "potential": 82,
                "availability": 88,
                "audience": 85,
            },
            "priceEvents": [
                {
                    "eventKey": f"espn-tennis:atp:{i}",
                    "eventType": "game",
                    "sport": "tennis",
                    "tour": "ATP",
                    "verified": True,
                }
                for i in range(180)
            ],
            "priceHistory": [
                {
                    "time": "2026-09-20T00:00:00Z",
                    "price": 140.0,
                    "eventId": "espn-tennis:atp:179",
                    "phase": "close",
                    "historyType": "verified",
                }
            ],
        }
        base.update(updates)
        return base

    def test_rebase_lifts_established_verified_tennis_price(self):
        original = self.tennis_record()
        migrated, changed = migrate_record(original, "2026-09-26T21:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["tennisMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertGreaterEqual(migrated["confidenceScore"], 82)
        self.assertGreater(migrated["marketPrice"], original["fundamentalValue"] * 1.30)
        self.assertEqual(migrated["marketPrice"], migrated["fundamentalValue"])
        self.assertEqual(migrated["lastGameMovePct"], 0.0)
        self.assertEqual(migrated["dailyChange"], 0.0)

    def test_verified_event_and_chart_history_are_preserved(self):
        original = self.tennis_record()
        migrated, _ = migrate_record(original, "2026-09-26T21:00:00Z")
        self.assertEqual(migrated["priceEvents"], original["priceEvents"])
        self.assertEqual(migrated["priceHistory"][0], original["priceHistory"][0])
        self.assertEqual(migrated["priceHistory"][-1]["eventId"], MIGRATION_EVENT_ID)

    def test_migration_is_idempotent(self):
        first, changed = migrate_record(self.tennis_record(), "2026-09-26T21:00:00Z")
        second, changed_again = migrate_record(first, "2026-09-27T21:00:00Z")
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second, first)

    def test_non_tennis_is_untouched(self):
        nba = self.tennis_record(discipline="Basketball", leagueOrMedium="NBA")
        migrated, changed = migrate_record(nba, "2026-09-26T21:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(migrated, nba)


if __name__ == "__main__":
    unittest.main()
