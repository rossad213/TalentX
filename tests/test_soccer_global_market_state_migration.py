from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_soccer_global_market_state import (
    MIGRATION_EVENT_ID,
    MIGRATION_VERSION,
    migrate_record,
)
from pricing_engine_v2 import apply_v2


class SoccerGlobalMarketMigrationTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "soccer-player",
            "name": "Soccer Player",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "leagueOrMedium": "English League One",
            "careerStage": "Established",
            "careerStatus": "Active",
            "age": 30,
            "yearsActive": 10,
            "professionalGames": 250,
            "careerScore": 82,
            "pricingConfidence": 0.90,
            "activeMetrics": {
                "performance": 90,
                "achievements": 88,
                "consistency": 90,
                "potential": 70,
                "availability": 75,
                "audience": 70,
            },
            "momentumPct": 0,
            "demandPremiumPct": 0,
            "marketPrice": 190.0,
            "previousMarketPrice": 188.0,
            "lastGameMovePct": 2.4,
            "dailyChange": 2.4,
            "hourlyChangePct": 2.4,
            "trend": [180.0, 188.0, 190.0],
            "priceEvents": [{
                "eventKey": "espn:game-1",
                "eventId": "game-1",
                "eventType": "game",
                "priceBefore": 188.0,
                "priceAfter": 190.0,
                "movePct": 2.4,
            }],
            "priceHistory": [{
                "time": "2026-09-20T20:00:00Z",
                "eventId": "game-1",
                "eventType": "game",
                "phase": "close",
                "price": 190.0,
            }],
        }
        base.update(updates)
        return base

    def test_soccer_resets_once_to_clean_global_fair_value(self):
        original = self.record()
        expected = apply_v2({
            **original,
            "lastGameMovePct": 0.0,
            "dailyChange": 0.0,
            "hourlyChangePct": 0.0,
        })
        migrated, changed = migrate_record(original, "2026-09-25T21:00:00Z")

        self.assertTrue(changed)
        self.assertEqual(migrated["marketPrice"], expected["fairValue"])
        self.assertEqual(migrated["fundamentalValue"], expected["fairValue"])
        self.assertEqual(migrated["soccerMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertEqual(migrated["soccerMarketMigrationPriorMarketPrice"], 190.0)
        self.assertEqual(migrated["lastGameMovePct"], 0.0)
        self.assertEqual(migrated["trend"], [expected["fairValue"]] * 18)

    def test_event_ledger_is_preserved_and_migration_point_added(self):
        original = self.record()
        migrated, changed = migrate_record(original, "2026-09-25T21:00:00Z")

        self.assertTrue(changed)
        self.assertEqual(migrated["priceEvents"], original["priceEvents"])
        self.assertEqual(migrated["priceHistory"][0], original["priceHistory"][0])
        self.assertEqual(migrated["priceHistory"][-1]["eventId"], MIGRATION_EVENT_ID)
        self.assertEqual(migrated["priceHistory"][-1]["price"], migrated["marketPrice"])

    def test_migration_is_idempotent(self):
        first, changed = migrate_record(self.record(), "2026-09-25T21:00:00Z")
        second, changed_again = migrate_record(first, "2026-09-26T21:00:00Z")

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second, first)
        points = [item for item in second["priceHistory"] if item.get("eventId") == MIGRATION_EVENT_ID]
        self.assertEqual(len(points), 1)

    def test_non_soccer_is_untouched(self):
        nfl = self.record(discipline="American Football", leagueOrMedium="NFL")
        migrated, changed = migrate_record(nfl, "2026-09-25T21:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(migrated, nfl)


if __name__ == "__main__":
    unittest.main()
