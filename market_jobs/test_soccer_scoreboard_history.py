from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET_JOBS = ROOT / "market_jobs"
if str(MARKET_JOBS) not in sys.path:
    sys.path.insert(0, str(MARKET_JOBS))

from soccer_scoreboard_history import ESPN_SCOREBOARD_DAY, event_info, scoreboard_days, soccer_completed


class SoccerScoreboardHistoryTests(unittest.TestCase):
    def test_scoreboard_days_split_one_year_into_single_calendar_days(self):
        start = datetime(2025, 9, 25, tzinfo=timezone.utc)
        end = datetime(2026, 9, 25, tzinfo=timezone.utc)
        days = scoreboard_days(start, end)

        self.assertEqual(len(days), 366)
        self.assertEqual(days[0], start)
        self.assertEqual(days[-1], end)
        for index, scoreboard_day in enumerate(days):
            if index:
                self.assertEqual(scoreboard_day, days[index - 1] + timedelta(days=1))

    def test_scoreboard_url_uses_single_date_not_range(self):
        url = ESPN_SCOREBOARD_DAY.format(league="eng.1", date="20260920")
        self.assertIn("dates=20260920", url)
        self.assertNotIn("20260920-", url)

    def test_status_full_time_is_completed(self):
        event = {
            "status": {
                "type": {
                    "state": "post",
                    "name": "STATUS_FULL_TIME",
                    "description": "Full Time",
                    "completed": True,
                }
            }
        }
        self.assertTrue(soccer_completed(event))

    def test_scoreboard_full_time_event_is_accepted(self):
        event = {
            "id": "401999999",
            "date": "2026-08-01T15:00:00Z",
            "name": "Club A vs Club B",
            "status": {"type": {"name": "STATUS_FULL_TIME", "completed": True}},
            "competitions": [{
                "competitors": [
                    {"team": {"id": "1"}, "winner": True},
                    {"team": {"id": "2"}, "winner": False},
                ]
            }],
        }
        info = event_info(
            event,
            "eng.1",
            datetime(2025, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(info)
        self.assertEqual(info["eventKey"], "espn:401999999")
        self.assertEqual(info["winningTeamIds"], ["1"])

    def test_in_progress_event_is_rejected(self):
        event = {
            "id": "401999998",
            "date": "2026-08-01T15:00:00Z",
            "status": {"type": {"state": "in", "name": "STATUS_IN_PROGRESS", "completed": False}},
            "competitions": [],
        }
        info = event_info(
            event,
            "eng.1",
            datetime(2025, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
