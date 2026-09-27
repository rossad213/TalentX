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

    def test_formula1_waits_for_verified_race_evidence(self):
        provisional = {
            "id": "f1-provisional", "primaryCategory": "Athlete", "discipline": "Motorsport",
            "leagueOrMedium": "Formula 1", "marketPrice": 110,
            "professionEvidenceVerified": False, "priceEvents": [],
        }
        unchanged, changed = migrate_record(provisional, "2026-09-26T23:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(unchanged, provisional)

    def test_prior_motorsport_epoch_is_rebased_to_current_version(self):
        stale = {
            "id": "f1", "name": "Max Verstappen", "primaryCategory": "Athlete",
            "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "marketPrice": 197.11, "fundamentalValue": 196.91,
            "professionEvidenceVerified": True,
            "motorsportMarketMigrationVersion": "1.0-motorsport-verified-race-ledger",
            "pricingConfidence": 0.936,
            "activeMetrics": {"performance": 88, "achievements": 90, "consistency": 84, "potential": 78, "availability": 88, "audience": 92},
            "priceEvents": [{
                "eventKey": "jolpica-f1:2026:15:max_verstappen",
                "eventId": "jolpica-f1:2026:15:max_verstappen",
                "eventType": "game", "provider": "Jolpica F1",
                "startedAt": "2026-09-26T11:00:00Z", "movePct": 1.1,
                "performanceDeltaPct": 20.0,
            }],
        }
        updated, changed = migrate_record(stale, "2026-09-27T12:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["motorsportMarketMigrationVersion"], MIGRATION_VERSION)

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
