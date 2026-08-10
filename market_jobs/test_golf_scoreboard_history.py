from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MARKET_JOBS = ROOT / "market_jobs"
for path in (SCRIPTS, MARKET_JOBS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from golf_scoreboard_history import apply_history, verified_historical_events


class GolfScoreboardHistoryTests(unittest.TestCase):
    def test_history_backfill_keeps_live_price_unchanged(self):
        records = [{
            "id": "golf-rory",
            "name": "Rory McIlroy",
            "primaryCategory": "Athlete",
            "discipline": "Golf",
            "marketPrice": 210.0,
            "previousMarketPrice": 208.0,
            "dailyChange": 0.4,
            "hourlyChangePct": 0.0,
            "lastPriceEventId": "live-existing",
            "priceExplanation": "Live state remains authoritative",
            "rosterPriority": 2,
            "priceEvents": [],
        }]
        tournaments = [{
            "tournamentKey": "pga:401999111",
            "competitionId": "401999111",
            "tour": "PGA",
            "tournament": "PGA Championship",
            "major": True,
            "completedAt": "2026-05-17T23:00:00Z",
            "startedAt": "2026-05-17T23:00:00Z",
            "sourceUrl": "https://example.test",
            "fieldSize": 120,
            "expectedRounds": 4,
            "competitors": [{
                "name": "Rory McIlroy",
                "normalizedName": "rorymcilroy",
                "athleteId": "3470",
                "finish": 3,
                "score": -9.0,
                "scoreDisplay": "-9",
                "roundsPlayed": 4,
                "status": "FINISHED",
            }],
        }]
        updated, touched, added = apply_history(records, tournaments, days=365, max_move_pct=2.5)
        self.assertEqual(touched, 1)
        self.assertEqual(added, 1)
        result = updated[0]
        self.assertEqual(result["marketPrice"], 210.0)
        self.assertEqual(result["previousMarketPrice"], 208.0)
        self.assertEqual(result["dailyChange"], 0.4)
        self.assertEqual(result["lastPriceEventId"], "live-existing")
        self.assertEqual(result["priceExplanation"], "Live state remains authoritative")
        events = verified_historical_events(result)
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]["movePct"], 0)
        self.assertGreater(len(result.get("priceHistory", [])), 0)


if __name__ == "__main__":
    unittest.main()
