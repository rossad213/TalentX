from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from athlete_career_event_refresh import (
    apply_new_events,
    automatic_team_change,
    curated_event,
)


class AthleteCareerEventRefreshTests(unittest.TestCase):
    def test_curated_event_reprices_exactly_once(self):
        record = {
            "id": "lebron",
            "name": "LeBron James",
            "primaryCategory": "Athlete",
            "discipline": "Basketball",
            "leagueOrMedium": "NBA",
            "teamOrPlatform": "Philadelphia 76ers",
            "marketPrice": 100.0,
            "previousMarketPrice": 100.0,
            "trend": [100.0],
            "priceEvents": [
                {
                    "eventKey": "espn:game-1",
                    "eventId": "game-1",
                    "eventType": "game",
                    "name": "Earlier game",
                    "startedAt": "2026-04-01T00:00:00Z",
                    "movePct": 1.0,
                    "priceBefore": 99.0,
                    "priceAfter": 99.99,
                    "verified": True,
                }
            ],
        }
        spec = {
            "eventKey": "nba:lebron-sixers",
            "match": {"name": "LeBron James"},
            "eventType": "athlete-signing",
            "name": "Signed with Philadelphia 76ers",
            "startedAt": "2026-07-26T00:00:00Z",
            "provider": "NBA.com",
            "sourceUrl": "https://www.nba.com/example",
            "destinationTeam": "Philadelphia 76ers",
            "targetMovePct": 1.25,
        }
        event = curated_event(record, spec)
        self.assertIsNotNone(event)

        updated, added = apply_new_events(record, [event], "2026-08-09T19:00:00Z")
        self.assertEqual(added, 1)
        self.assertEqual(updated["marketPrice"], 101.25)
        self.assertEqual(len(updated["priceEvents"]), 2)
        self.assertEqual(updated["priceEvents"][-1]["eventKey"], "nba:lebron-sixers")

        repeated, repeated_added = apply_new_events(updated, [event], "2026-08-09T20:00:00Z")
        self.assertEqual(repeated_added, 0)
        self.assertEqual(repeated["marketPrice"], 101.25)

    def test_curated_destination_prevents_duplicate_roster_delta(self):
        prior = {
            "id": "lebron",
            "name": "LeBron James",
            "primaryCategory": "Athlete",
            "teamOrPlatform": "Los Angeles Lakers",
        }
        current = {
            "id": "lebron",
            "name": "LeBron James",
            "primaryCategory": "Athlete",
            "teamOrPlatform": "Philadelphia 76ers",
            "marketPrice": 100.0,
            "fundamentalValue": 105.0,
            "lastVerifiedAt": "2026-08-09T18:00:00Z",
            "sourceName": "ESPN current team roster endpoint",
            "sourceUrl": "https://site.api.espn.com/example",
        }
        event = automatic_team_change(
            current,
            prior,
            covered_destination="Philadelphia 76ers",
        )
        self.assertIsNone(event)

    def test_uncovered_roster_team_change_creates_small_verified_event(self):
        prior = {
            "id": "player",
            "primaryCategory": "Athlete",
            "teamOrPlatform": "Old Club",
        }
        current = {
            "id": "player",
            "name": "Player Example",
            "primaryCategory": "Athlete",
            "teamOrPlatform": "New Club",
            "marketPrice": 80.0,
            "fundamentalValue": 84.0,
            "lastVerifiedAt": "2026-08-09T18:00:00Z",
            "sourceName": "Verified roster feed",
            "sourceUrl": "https://example.com/roster",
        }
        event = automatic_team_change(current, prior)
        self.assertIsNotNone(event)
        self.assertTrue(event["verified"])
        self.assertEqual(event["eventType"], "athlete-team-change")
        self.assertGreater(abs(float(event["movePct"])), 0.0)

    def test_large_expectation_gap_can_exceed_old_team_change_limit(self):
        prior = {"id": "player", "primaryCategory": "Athlete", "teamOrPlatform": "Old Club"}
        current = {
            "id": "player", "name": "Player Example", "primaryCategory": "Athlete",
            "teamOrPlatform": "New Club", "marketPrice": 20.0, "fundamentalValue": 200.0,
            "lastVerifiedAt": "2026-08-09T18:00:00Z", "sourceName": "Verified roster feed",
            "sourceUrl": "https://example.com/roster",
        }
        event = automatic_team_change(current, prior)
        self.assertIsNotNone(event)
        self.assertGreater(abs(float(event["movePct"])), 0.6)


if __name__ == "__main__":
    unittest.main()
