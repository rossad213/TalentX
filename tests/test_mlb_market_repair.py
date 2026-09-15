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
    def test_corrupted_mlb_price_returns_to_preserved_v1_baseline(self) -> None:
        record = {
            "id": "mlb-1",
            "leagueOrMedium": "MLB",
            "marketPrice": 7231.13,
            "pricingV1": {"marketPrice": 58.81, "pricingModelVersion": "4.1-event-driven-pricing"},
            "lastGameMovePct": 6.317,
            "lastGamePerformanceDeltaPct": 1010.52,
            "priceEvents": [
                {"eventType": "game", "eventKey": "espn:1", "priceAfter": 7231.13},
                {"eventType": "career", "eventKey": "signing:1", "priceAfter": 60.0},
            ],
            "priceHistory": [
                {"time": "2026-08-01T00:00:00Z", "price": 62.0},
                {"time": "2026-09-01T00:00:00Z", "price": 5000.0},
            ],
            "trend": [5000.0, 7231.13],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-15T02:30:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["marketPrice"], 58.81)
        self.assertEqual(updated["lastGameMovePct"], 0.0)
        self.assertEqual(len(updated["priceEvents"]), 1)
        self.assertEqual(updated["priceEvents"][0]["eventKey"], "signing:1")
        self.assertNotIn("lastGamePerformanceDeltaPct", updated)
        self.assertEqual(updated["priceHistory"][-1]["price"], 58.81)
        self.assertFalse(any(point.get("price") == 5000.0 for point in updated["priceHistory"]))

    def test_non_mlb_record_is_byte_for_byte_untouched_by_record_repair(self) -> None:
        record = {
            "id": "nfl-1",
            "leagueOrMedium": "NFL",
            "marketPrice": 110.0,
            "pricingV1": {"marketPrice": 90.0},
            "priceEvents": [{"eventType": "game", "eventKey": "espn:2"}],
        }
        updated, changed = repair.repair_record(record, repaired_at="2026-09-15T02:30:00Z")
        self.assertFalse(changed)
        self.assertIs(updated, record)

    def test_catalog_repair_only_changes_mlb(self) -> None:
        rows = [
            {"id": "mlb-1", "leagueOrMedium": "MLB", "marketPrice": 1000.0, "pricingV1": {"marketPrice": 75.0}},
            {"id": "nba-1", "leagueOrMedium": "NBA", "marketPrice": 140.0, "pricingV1": {"marketPrice": 95.0}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            count = repair.repair_catalog(path, repaired_at="2026-09-15T02:30:00Z")
            result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(count, 1)
        self.assertEqual(result[0]["marketPrice"], 75.0)
        self.assertEqual(result[1], rows[1])


if __name__ == "__main__":
    unittest.main()
