from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "market_jobs"))

from soccer_json_history_runner import extract_soccer_lineup_stats, soccer_completed_event


class SoccerLineupJsonTests(unittest.TestCase):
    def test_soccer_full_time_statuses_are_completed(self):
        for value in (
            "post", "final", "STATUS_FINAL", "STATUS_FULL_TIME", "FULL_TIME",
            "Full Time", "after extra time", "STATUS_AFTER_PENALTIES",
        ):
            with self.subTest(value=value):
                self.assertTrue(soccer_completed_event(value))

    def test_nonfinal_soccer_statuses_are_not_completed(self):
        for value in ("pre", "scheduled", "in", "halftime", "postponed", "abandoned", "cancelled"):
            with self.subTest(value=value):
                self.assertFalse(soccer_completed_event(value))

    def test_starter_with_roster_stats_is_counted(self):
        payload = {
            "rosters": [{
                "team": {"id": "10"},
                "roster": [{
                    "starter": True,
                    "athlete": {"id": "123", "displayName": "Player One"},
                    "statistics": [
                        {"name": "minutes", "displayValue": "90'"},
                        {"name": "goals", "value": 1},
                        {"abbreviation": "SOG", "value": 2},
                    ],
                }],
            }]
        }
        stats = extract_soccer_lineup_stats(payload, {"10"})
        self.assertIn("123", stats)
        self.assertEqual(stats["123"]["stats"]["appearances"], 1.0)
        self.assertEqual(stats["123"]["stats"]["goals"], 1.0)
        self.assertEqual(stats["123"]["stats"]["shotsOnTarget"], 2.0)
        self.assertTrue(stats["123"]["teamWon"])

    def test_used_substitute_with_zero_scoring_stats_is_counted(self):
        payload = {
            "lineups": [{
                "team": {"id": "20"},
                "players": [{
                    "starter": False,
                    "subbedIn": True,
                    "athlete": {"id": "456", "displayName": "Player Two"},
                    "statistics": [{"name": "minutes", "displayValue": "14"}],
                }],
            }]
        }
        stats = extract_soccer_lineup_stats(payload, set())
        self.assertIn("456", stats)
        self.assertEqual(stats["456"]["stats"]["minutes"], 14.0)

    def test_unused_bench_player_is_not_counted(self):
        payload = {
            "rosters": [{
                "team": {"id": "30"},
                "roster": [{
                    "starter": False,
                    "subbedIn": False,
                    "athlete": {"id": "789", "displayName": "Bench Player"},
                    "statistics": [],
                }],
            }]
        }
        stats = extract_soccer_lineup_stats(payload, set())
        self.assertNotIn("789", stats)


if __name__ == "__main__":
    unittest.main()
