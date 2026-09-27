from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_motorsport_market_state import MIGRATION_VERSION, migrate_record, normalized_event_path


class MotorsportMarketMigrationTests(unittest.TestCase):
    def test_backfill_path_ends_at_target(self):
        events = [
            {"eventKey": "r1", "eventId": "r1", "eventType": "game", "provider": "Jolpica F1", "startedAt": "2026-03-01T12:00:00Z", "movePct": 1.5},
            {"eventKey": "r2", "eventId": "r2", "eventType": "game", "provider": "Jolpica F1", "startedAt": "2026-03-08T12:00:00Z", "movePct": -0.5},
        ]
        rebuilt, history = normalized_event_path(events, 123.45)
        self.assertEqual(rebuilt[-1]["priceAfter"], 123.45)
        self.assertEqual(history[-1]["price"], 123.45)
        self.assertEqual(history[-1]["historyType"], "verified-event-replay")

    def test_migration_is_motorsport_only_and_idempotent(self):
        nfl = {"id": "nfl", "primaryCategory": "Athlete", "discipline": "American Football", "marketPrice": 50}
        unchanged, changed = migrate_record(nfl, "2026-09-26T23:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(unchanged, nfl)

        already = {"id": "f1", "primaryCategory": "Athlete", "discipline": "Motorsport", "motorsportMarketMigrationVersion": MIGRATION_VERSION, "marketPrice": 100}
        unchanged2, changed2 = migrate_record(already, "2026-09-26T23:00:00Z")
        self.assertFalse(changed2)
        self.assertEqual(unchanged2, already)


if __name__ == "__main__":
    unittest.main()
