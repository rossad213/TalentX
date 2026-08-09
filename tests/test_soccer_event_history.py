from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from soccer_event_history import schedule_event_info, team_id_for


class SoccerEventHistoryTests(unittest.TestCase):
    def test_team_id_is_recovered_from_verified_roster_url(self):
        record = {
            "sourceUrl": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams/359/roster"
        }
        self.assertEqual(team_id_for(record), "359")

    def test_completed_match_becomes_dated_event(self):
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
            datetime(2025, 8, 9, tzinfo=timezone.utc),
            datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(info)
        self.assertEqual(info["eventKey"], "espn:401999999")
        self.assertEqual(info["teamIds"], ["1", "2"])
        self.assertEqual(info["winningTeamIds"], ["1"])

    def test_future_or_unfinished_match_is_not_backfilled(self):
        event = {
            "id": "future",
            "date": "2026-08-08T15:00:00Z",
            "status": {"type": {"state": "pre"}},
        }
        info = schedule_event_info(
            event,
            datetime(2025, 8, 9, tzinfo=timezone.utc),
            datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
