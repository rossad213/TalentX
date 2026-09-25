from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_nhl_market_state import MIGRATION_EVENT_ID, MIGRATION_VERSION, migrate_record
from pricing_engine_v2 import apply_v2


class NHLMarketMigrationTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "nhl-player",
            "name": "NHL Player",
            "primaryCategory": "Athlete",
            "discipline": "Hockey",
            "leagueOrMedium": "NHL",
            "role": "C",
            "careerStatus": "Active",
            "careerStage": "Established",
            "professionalGames": 400,
            "pricingConfidence": 0.90,
            "activeMetrics": {
                "performance": 82, "achievements": 76, "consistency": 80,
                "potential": 72, "availability": 75, "audience": 65,
            },
            "marketPrice": 400.0,
            "fundamentalValue": 75.0,
            "lastGameMovePct": 11.0,
            "dailyChange": 11.0,
            "hourlyChangePct": 11.0,
            "trend": [80.0, 120.0, 200.0, 400.0],
            "priceEvents": [{"eventKey": "nhl:game-1", "eventType": "game", "priceAfter": 80.0}],
            "priceHistory": [{"time": "2026-09-20T00:00:00Z", "eventId": "nhl:game-1", "phase": "close", "price": 80.0}],
        }
        base.update(updates)
        return base

    def test_reset_uses_clean_fair_value_once(self):
        original = self.record()
        expected = apply_v2({**original, "lastGameMovePct": 0.0, "dailyChange": 0.0, "hourlyChangePct": 0.0})
        migrated, changed = migrate_record(original, "2026-09-25T23:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["marketPrice"], expected["fairValue"])
        self.assertEqual(migrated["nhlMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertEqual(migrated["lastGameMovePct"], 0.0)
        self.assertEqual(migrated["trend"], [expected["fairValue"]] * 18)

    def test_event_ledger_is_preserved(self):
        original = self.record()
        migrated, _ = migrate_record(original, "2026-09-25T23:00:00Z")
        self.assertEqual(migrated["priceEvents"], original["priceEvents"])
        self.assertEqual(migrated["priceHistory"][0], original["priceHistory"][0])
        self.assertEqual(migrated["priceHistory"][-1]["eventId"], MIGRATION_EVENT_ID)

    def test_migration_is_idempotent(self):
        first, changed = migrate_record(self.record(), "2026-09-25T23:00:00Z")
        second, changed_again = migrate_record(first, "2026-09-26T23:00:00Z")
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second, first)

    def test_non_nhl_is_untouched(self):
        nba = self.record(discipline="Basketball", leagueOrMedium="NBA")
        migrated, changed = migrate_record(nba, "2026-09-25T23:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(migrated, nba)


if __name__ == "__main__":
    unittest.main()
