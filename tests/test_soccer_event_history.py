from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from soccer_event_history import player_log_events, schedule_event_info, team_id_for


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

    def test_player_match_log_becomes_verified_participation_events(self):
        html = """
        <table>
          <thead><tr><th>Date</th><th>OPP</th><th>Result</th><th>G</th><th>A</th><th>SH</th><th>ST</th></tr></thead>
          <tbody>
            <tr><td>Sat 4/11</td><td>vsRBNY</td><td>D 2-2</td><td>0</td><td>0</td><td>3</td><td>1</td></tr>
            <tr><td>Sat 4/4</td><td>vsATX</td><td>W 3-1</td><td>1</td><td>1</td><td>5</td><td>2</td></tr>
          </tbody>
        </table>
        """
        record = {
            "sourceRecordId": "45843",
            "sourceLeagueSlug": "usa.1",
            "teamOrPlatform": "Inter Miami CF",
        }
        events = player_log_events(
            record,
            html,
            season_value=2026,
            start=datetime(2025, 8, 9, tzinfo=timezone.utc),
            end=datetime(2026, 8, 9, tzinfo=timezone.utc),
            source_url="https://www.espn.com/soccer/player/matches/_/id/45843",
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["stats"]["appearances"], 1.0)
        self.assertEqual(events[0]["stats"]["shotsOnTarget"], 2.0 if events[0]["teamWon"] is True else 1.0)
        self.assertTrue(all(event["verifiedParticipation"] for event in events))

    def test_fall_spring_season_places_april_in_following_calendar_year(self):
        html = """
        <table><tr><th>Date</th><th>OPP</th><th>Result</th><th>G</th><th>A</th></tr>
        <tr><td>Sat 4/11</td><td>vsARS</td><td>W 2-0</td><td>1</td><td>0</td></tr></table>
        """
        record = {
            "sourceRecordId": "123",
            "sourceLeagueSlug": "eng.1",
            "teamOrPlatform": "Club",
        }
        events = player_log_events(
            record,
            html,
            season_value=2025,
            start=datetime(2025, 8, 9, tzinfo=timezone.utc),
            end=datetime(2026, 8, 9, tzinfo=timezone.utc),
            source_url="https://www.espn.com/soccer/player/matches/_/id/123",
        )
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["startedAt"].startswith("2026-04-11"))


if __name__ == "__main__":
    unittest.main()
