from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "market_jobs"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tennis_event_refresh import flatten_scoreboard
from tennis_scoreboard_history import apply_history, verified_historical_events


class TennisScoreboardHistoryTests(unittest.TestCase):
    def test_history_adds_verified_events_without_changing_current_price(self):
        payload = {
            "events": [{
                "id": "event-2026",
                "name": "Test Championship",
                "major": True,
                "groupings": [{
                    "grouping": {"slug": "womens-singles"},
                    "competitions": [{
                        "id": "match-1",
                        "date": "2026-07-10T12:00:00Z",
                        "status": {"type": {"name": "STATUS_FINAL", "state": "post", "completed": True}},
                        "type": {"slug": "womens-singles", "text": "Women's Singles"},
                        "round": {"displayName": "Semifinal"},
                        "competitors": [
                            {
                                "id": "11",
                                "winner": True,
                                "athlete": {"id": "11", "displayName": "Test Winner"},
                                "linescores": [{"value": 6, "winner": True}, {"value": 6, "winner": True}],
                            },
                            {
                                "id": "22",
                                "winner": False,
                                "athlete": {"id": "22", "displayName": "Test Loser"},
                                "linescores": [{"value": 2, "winner": False}, {"value": 3, "winner": False}],
                            },
                        ],
                    }],
                }],
            }]
        }
        matches = flatten_scoreboard(payload, "wta", "https://example.invalid")
        records = [
            {
                "id": "winner",
                "name": "Test Winner",
                "primaryCategory": "Athlete",
                "discipline": "Tennis",
                "sourceRank": 5,
                "marketPrice": 150.0,
                "pricingConfidence": 0.9,
                "priceEvents": [],
            },
            {
                "id": "loser",
                "name": "Test Loser",
                "primaryCategory": "Athlete",
                "discipline": "Tennis",
                "sourceRank": 8,
                "marketPrice": 120.0,
                "pricingConfidence": 0.85,
                "priceEvents": [],
            },
        ]
        updated, touched, added = apply_history(records, matches, days=365, max_move_pct=2.5)
        self.assertEqual(touched, 2)
        self.assertEqual(added, 2)
        self.assertEqual(updated[0]["marketPrice"], 150.0)
        self.assertEqual(updated[1]["marketPrice"], 120.0)
        self.assertEqual(len(verified_historical_events(updated[0])), 1)
        self.assertEqual(len(verified_historical_events(updated[1])), 1)
        self.assertGreater(verified_historical_events(updated[0])[0]["movePct"], 0)
        self.assertLess(verified_historical_events(updated[1])[0]["movePct"], 0)


if __name__ == "__main__":
    unittest.main()
