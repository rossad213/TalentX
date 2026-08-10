from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET_JOBS = ROOT / "market_jobs"
SCRIPTS = ROOT / "scripts"
for path in (MARKET_JOBS, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hourly_price_refresh import extract_espn_game_stats
from soccer_json_history import (
    direct_soccer_move,
    normalized_soccer_stats,
    schedule_event_info,
    source_team_id,
)


class SoccerJsonHistoryTests(unittest.TestCase):
    def test_team_id_can_be_recovered_from_roster_evidence(self):
        record = {
            "sourceUrl": "https://example.invalid/profile",
            "pricingEvidence": [
                "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/teams/20232/roster"
            ],
        }
        self.assertEqual(source_team_id(record), "20232")

    def test_completed_schedule_match_is_accepted(self):
        event = {
            "id": "401999999",
            "date": "2026-08-01T15:00:00Z",
            "name": "Club A vs Club B",
            "status": {"type": {"state": "post"}},
            "competitions": [{
                "competitors": [
                    {"team": {"id": "1"}, "winner": True},
                    {"team": {"id": "2"}, "winner": False},
                ]
            }],
        }
        info = schedule_event_info(
            event,
            league="usa.1",
            start=datetime(2025, 8, 9, tzinfo=timezone.utc),
            end=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(info)
        self.assertEqual(info["eventKey"], "espn:401999999")
        self.assertEqual(info["winningTeamIds"], ["1"])

    def test_real_espn_soccer_boxscore_shape_yields_player_stats(self):
        payload = {
            "boxscore": {
                "players": [{
                    "team": {"id": "1"},
                    "statistics": [{
                        "name": "starters",
                        "labels": ["MIN", "G", "A", "SH", "ST", "YC", "RC"],
                        "athletes": [{
                            "athlete": {"id": "45843", "displayName": "Lionel Messi"},
                            "stats": ["90", "1", "1", "5", "3", "0", "0"],
                        }],
                    }],
                }]
            }
        }
        stats = extract_espn_game_stats(payload, {"1"})
        self.assertIn("45843", stats)
        normalized = normalized_soccer_stats(stats["45843"]["stats"])
        self.assertEqual(normalized["minutes"], 90.0)
        self.assertEqual(normalized["goals"], 1.0)
        self.assertEqual(normalized["assists"], 1.0)
        self.assertEqual(normalized["shotsOnTarget"], 3.0)
        self.assertEqual(normalized["appearances"], 1.0)
        self.assertTrue(stats["45843"]["teamWon"])

    def test_verified_boxscore_fallback_creates_nonzero_move(self):
        event = {
            "stats": {
                "minutes": 90,
                "appearances": 1,
                "goals": 1,
                "assists": 0,
                "shotsOnTarget": 2,
            },
            "teamWon": True,
        }
        move, evidence = direct_soccer_move(event, 2.5)
        self.assertTrue(evidence["comparable"])
        self.assertGreater(move, 0)
        self.assertLessEqual(move, 1.5)

    def test_verified_loss_still_creates_small_negative_context_move(self):
        event = {
            "stats": {"minutes": 90, "appearances": 1},
            "teamWon": False,
        }
        move, evidence = direct_soccer_move(event, 2.5)
        self.assertTrue(evidence["comparable"])
        self.assertLess(move, 0)


if __name__ == "__main__":
    unittest.main()
