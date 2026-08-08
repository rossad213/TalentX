from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from append_game_event_history import append_events
from game_event_history import attach_price_events
from restore_hourly_event_market_state import restore


class GameEventHistoryTests(unittest.TestCase):
    def test_multiple_games_are_persisted_separately(self):
        old = {"id": "evan", "marketPrice": 80.00, "priceEvents": []}
        result = {"id": "evan", "marketPrice": 81.19, "previousMarketPrice": 80.00}
        events = [
            {
                "eventKey": "espn:g1",
                "eventId": "g1",
                "name": "Game one",
                "startedAt": "2026-08-04T00:00:00Z",
                "comparable": True,
                "movePct": 1.0,
                "priceAfter": 80.80,
                "performanceDeltaPct": 12.0,
                "stats": {"hits": 2},
            },
            {
                "eventKey": "espn:g2",
                "eventId": "g2",
                "name": "Game two",
                "startedAt": "2026-08-06T00:00:00Z",
                "comparable": True,
                "movePct": 0.48,
                "priceAfter": 81.19,
                "performanceDeltaPct": 6.0,
                "stats": {"hits": 1},
            },
        ]
        updated = attach_price_events(old, result, events)
        self.assertEqual(len(updated["priceEvents"]), 2)
        self.assertEqual(updated["priceEvents"][0]["priceBefore"], 80.00)
        self.assertEqual(updated["priceEvents"][0]["priceAfter"], 80.80)
        self.assertEqual(updated["priceEvents"][1]["priceBefore"], 80.80)
        self.assertEqual(updated["priceEvents"][1]["priceAfter"], 81.19)

    def test_event_points_become_dated_open_and_close_history(self):
        record = {
            "id": "evan",
            "priceEvents": [
                {
                    "eventKey": "espn:g1",
                    "name": "Game one",
                    "startedAt": "2026-08-04T00:00:00Z",
                    "priceBefore": 80.00,
                    "priceAfter": 80.80,
                },
                {
                    "eventKey": "espn:g2",
                    "name": "Game two",
                    "startedAt": "2026-08-06T00:00:00Z",
                    "priceBefore": 80.80,
                    "priceAfter": 79.99,
                },
            ],
        }
        updated, added = append_events(record)
        self.assertEqual(added, 4)
        game_points = [p for p in updated["priceHistory"] if p.get("eventType") == "game"]
        self.assertEqual(len(game_points), 4)
        self.assertEqual([p["price"] for p in game_points], [80.00, 80.80, 80.80, 79.99])

    def test_v2_fair_value_refresh_cannot_overwrite_event_market_state(self):
        v2_base = [{
            "id": "evan",
            "marketPrice": 92.00,
            "fairValue": 92.00,
            "fundamentalValue": 92.00,
            "dailyChange": 0.0,
            "trend": [92.00],
        }]
        event_snapshot = [{
            "id": "evan",
            "marketPrice": 81.19,
            "previousMarketPrice": 80.00,
            "dailyChange": 1.49,
            "hourlyChangePct": 1.49,
            "trend": [80.00, 80.80, 81.19],
            "lastPriceEventId": "espn:g2",
            "priceEvents": [{"eventKey": "espn:g2", "priceBefore": 80.80, "priceAfter": 81.19}],
        }]
        merged, count = restore(v2_base, event_snapshot)
        self.assertEqual(count, 1)
        self.assertEqual(merged[0]["marketPrice"], 81.19)
        self.assertEqual(merged[0]["fairValue"], 92.00)
        self.assertEqual(merged[0]["fundamentalValue"], 92.00)
        self.assertEqual(merged[0]["dailyChange"], 1.49)
        self.assertEqual(merged[0]["trend"], [80.00, 80.80, 81.19])


if __name__ == "__main__":
    unittest.main()
