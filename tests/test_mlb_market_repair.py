from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import repair_mlb_market_prices as repair  # noqa: E402


class MlbMarketRepairTests(unittest.TestCase):
    def test_legacy_mlb_price_rebases_to_current_fundamental_not_v1(self) -> None:
        record = {
            "id": "mlb-1",
            "leagueOrMedium": "MLB",
            "marketPrice": 139.83,
            "fairValue": 207.66,
            "fundamentalValue": 207.66,
            "pricingV1": {"marketPrice": 58.81, "pricingModelVersion": "4.1-event-driven-pricing"},
            "lastGameMovePct": 6.317,
            "lastGamePerformanceDeltaPct": 1010.52,
            "priceEvents": [
                {"eventType": "game", "eventKey": "espn:1", "priceAfter": 145.0},
                {"eventType": "career", "eventKey": "signing:1", "priceAfter": 140.0},
            ],
            "priceHistory": [
                {"time": "2026-08-01T00:00:00Z", "price": 138.0, "eventId": "market:1"},
                {"time": "2026-09-01T00:00:00Z", "price": 145.0, "eventId": "espn:1", "eventType": "game"},
            ],
            "trend": [138.0, 145.0],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-26T18:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["marketPrice"], 207.66)
        self.assertEqual(updated["fundamentalValue"], 207.66)
        self.assertEqual(updated["previousMarketPrice"], 207.66)
        self.assertEqual(updated["dailyChange"], 0.0)
        self.assertEqual(updated["lastGameMovePct"], 0.0)
        self.assertEqual(len(updated["priceEvents"]), 1)
        self.assertEqual(updated["priceEvents"][0]["eventKey"], "signing:1")
        self.assertFalse(any(point.get("eventId") == "espn:1" for point in updated["priceHistory"]))
        self.assertEqual(updated["priceHistory"][-1]["price"], 207.66)
        self.assertEqual(updated["mlbMarketRepairVersion"], repair.REPAIR_VERSION)
        self.assertEqual(updated["mlbMarketMigrationPriorMarketPrice"], 139.83)
        self.assertEqual(updated["mlbMarketMigrationTargetPrice"], 207.66)
        self.assertEqual(updated["mlbMarketRepair"]["source"], "fairValue")

    def test_clean_current_epoch_is_idempotent(self) -> None:
        first, changed = repair.repair_record(
            {
                "id": "mlb-1",
                "leagueOrMedium": "MLB",
                "marketPrice": 139.0,
                "fairValue": 205.0,
                "fundamentalValue": 205.0,
                "pricingV1": {"marketPrice": 88.0},
                "priceEvents": [],
                "priceHistory": [],
                "lastGameMovePct": 0.0,
            },
            repaired_at="2026-09-26T18:00:00Z",
        )
        self.assertTrue(changed)
        second, changed_again = repair.repair_record(
            first,
            repaired_at="2026-09-27T18:00:00Z",
        )
        self.assertFalse(changed_again)
        self.assertIs(second, first)
        self.assertEqual(second["marketPrice"], 205.0)

    def test_invalid_game_state_after_migration_reanchors_to_current_fair_value(self) -> None:
        record = {
            "id": "mlb-1",
            "leagueOrMedium": "MLB",
            "marketPrice": 230.0,
            "fairValue": 210.0,
            "fundamentalValue": 210.0,
            "mlbMarketRepairVersion": repair.REPAIR_VERSION,
            "lastGameMovePct": 9.5,
            "priceEvents": [
                {"eventType": "game", "eventKey": "espn:new", "priceBefore": 210.0, "priceAfter": 230.0},
            ],
            "priceHistory": [
                {"time": "2026-09-26T17:00:00Z", "price": 230.0, "eventId": "espn:new", "eventType": "game"},
            ],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-26T18:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["marketPrice"], 210.0)
        self.assertEqual(updated["priceEvents"], [])
        self.assertEqual(updated["lastGameMovePct"], 0.0)
        self.assertIn("Removed invalid MLB game repricing", updated["mlbMarketMigrationReason"])

    def test_v1_is_only_a_fallback_when_current_fundamental_is_missing(self) -> None:
        record = {
            "id": "mlb-1",
            "leagueOrMedium": "MLB",
            "marketPrice": 1000.0,
            "pricingV1": {"marketPrice": 75.0},
            "priceEvents": [],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-26T18:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["marketPrice"], 75.0)
        self.assertEqual(updated["mlbMarketRepair"]["source"], "pricingV1.marketPrice fallback")

    def test_non_mlb_record_is_byte_for_byte_untouched_by_record_repair(self) -> None:
        record = {
            "id": "nfl-1",
            "leagueOrMedium": "NFL",
            "marketPrice": 110.0,
            "fairValue": 120.0,
            "pricingV1": {"marketPrice": 90.0},
            "priceEvents": [{"eventType": "game", "eventKey": "espn:2"}],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-26T18:00:00Z")
        self.assertFalse(changed)
        self.assertIs(updated, record)

    def test_catalog_repair_only_changes_mlb(self) -> None:
        rows = [
            {
                "id": "mlb-1", "leagueOrMedium": "MLB", "marketPrice": 75.0,
                "fairValue": 205.0, "fundamentalValue": 205.0,
                "pricingV1": {"marketPrice": 75.0},
            },
            {
                "id": "nba-1", "leagueOrMedium": "NBA", "marketPrice": 140.0,
                "fairValue": 200.0, "pricingV1": {"marketPrice": 95.0},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            count = repair.repair_catalog(path, repaired_at="2026-09-26T18:00:00Z")
            result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(count, 1)
        self.assertEqual(result[0]["marketPrice"], 205.0)
        self.assertEqual(result[1], rows[1])


if __name__ == "__main__":
    unittest.main()
