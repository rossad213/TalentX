from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hourly_price_refresh import (  # noqa: E402
    HOURLY_MODEL_VERSION,
    apply_game_market_moves,
    discover_recent_events,
    event_in_window,
    event_key,
    extract_espn_game_stats,
    game_event_move,
    prior_processed_events,
)


class HourlyGamePricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aja = {
            "id": "cur-a-ja-wilson",
            "name": "A’ja Wilson",
            "sourceNamespace": "espn",
            "sourceRecordId": "3149391",
            "leagueOrMedium": "WNBA",
            "discipline": "Basketball",
            "marketPrice": 154.99,
            "trend": [154.99] * 18,
        }
        self.evidence = {
            "signals": {"recentProduction": 120.27, "efficiency": 74.925},
            "recent": {
                "avgPoints": 24.0,
                "avgRebounds": 9.4,
                "avgAssists": 3.4,
                "avgSteals": 1.7,
                "avgBlocks": 2.1,
                "avgTurnovers": 2.0,
            },
        }
        self.event = {
            "eventKey": "espn:401857111",
            "eventId": "401857111",
            "provider": "ESPN",
            "league": "wnba",
            "name": "Las Vegas Aces at Atlanta Dream",
            "startedAt": "2026-08-03T23:00:00Z",
            "teamWon": True,
            "stats": {
                "points": 23,
                "rebounds": 6,
                "assists": 4,
                "blocks": 3,
                "turnovers": 1,
                "fieldGoalPct": 72.727,
                "threePointFieldGoalPct": 100,
                "freeThrowPct": 100,
            },
        }

    def test_espn_basketball_box_score_is_normalized(self) -> None:
        payload = {
            "boxscore": {
                "players": [
                    {
                        "team": {"id": "17"},
                        "statistics": [
                            {
                                "name": "game",
                                "labels": ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PTS"],
                                "athletes": [
                                    {
                                        "athlete": {"id": "3149391", "displayName": "A'ja Wilson"},
                                        "stats": ["31", "9-15", "1-2", "4-4", "6", "4", "1", "2", "2", "23"],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        }
        players = extract_espn_game_stats(payload, {"17"})
        self.assertEqual(players["3149391"]["stats"]["points"], 23)
        self.assertEqual(players["3149391"]["stats"]["rebounds"], 6)
        self.assertEqual(players["3149391"]["stats"]["fieldGoalPct"], 60)
        self.assertTrue(players["3149391"]["teamWon"])

    def test_aja_game_moves_relative_to_her_own_baseline(self) -> None:
        move, details = game_event_move(self.aja, self.evidence, self.event, 2.5)
        self.assertTrue(details["comparable"])
        self.assertLess(details["productionDeltaPct"], 0)
        self.assertGreater(details["efficiencyDeltaPct"], 0)
        self.assertGreater(move, 0)
        self.assertLessEqual(abs(move), 2.5)

    def test_completed_game_creates_one_recorded_price_point(self) -> None:
        repriced = {**self.aja, "marketPrice": 155.50, "fundamentalValue": 150.25}
        result, change, event_results = apply_game_market_moves(
            self.aja,
            repriced,
            self.evidence,
            [self.event],
            2.5,
            "2026-08-04T20:23:00Z",
        )
        self.assertNotEqual(change, 0)
        self.assertEqual(result["lastPriceEventId"], "espn:401857111")
        self.assertEqual(len(event_results), 1)
        self.assertEqual(result["trend"][-1], result["marketPrice"])

    def test_no_game_keeps_market_price_and_chart_flat(self) -> None:
        repriced = {**self.aja, "marketPrice": 160.00, "fundamentalValue": 152.00}
        result, change, events = apply_game_market_moves(
            self.aja,
            repriced,
            self.evidence,
            [],
            2.5,
            "2026-08-04T20:23:00Z",
        )
        self.assertEqual(change, 0)
        self.assertEqual(result["marketPrice"], 154.99)
        self.assertEqual(result["trend"], [154.99] * 18)
        self.assertEqual(events, [])

    def test_only_completed_games_enter_the_catchup_window(self) -> None:
        now = datetime(2026, 8, 4, 20, tzinfo=timezone.utc)
        cutoff = now - timedelta(hours=48)
        start = now - timedelta(hours=20)
        self.assertTrue(event_in_window(start, "post", now, cutoff))
        self.assertFalse(event_in_window(start, "in", now, cutoff))

    def test_processed_event_ids_are_retained_and_deduplicated(self) -> None:
        now = datetime(2026, 8, 4, 20, tzinfo=timezone.utc)
        recent_key = event_key("ESPN", "401857111")
        manifest = {
            "version": HOURLY_MODEL_VERSION,
            "processedEvents": [
                {"key": recent_key, "startedAt": "2026-08-03T23:00:00Z"},
                {"key": "espn:old", "startedAt": "2026-06-01T00:00:00Z"},
            ]
        }
        retained = prior_processed_events(manifest, now)
        self.assertIn(recent_key, retained)
        self.assertNotIn("espn:old", retained)
        self.assertEqual(prior_processed_events({**manifest, "version": "1.3-game-level-event-pricing"}, now), {})

    def test_discovery_skips_a_game_after_its_event_id_is_processed(self) -> None:
        scoreboard = {
            "events": [
                {
                    "id": "401857111",
                    "date": "2026-08-03T23:00:00Z",
                    "name": "Las Vegas Aces at Atlanta Dream",
                    "status": {"type": {"state": "post"}},
                    "competitions": [
                        {
                            "competitors": [
                                {"winner": True, "team": {"id": "17"}},
                                {"winner": False, "team": {"id": "20"}},
                            ]
                        }
                    ],
                }
            ]
        }
        summary = {
            "boxscore": {
                "players": [
                    {
                        "team": {"id": "17"},
                        "statistics": [
                            {
                                "labels": ["REB", "AST", "PTS"],
                                "athletes": [{"athlete": {"id": "3149391"}, "stats": ["6", "4", "23"]}],
                            }
                        ],
                    }
                ]
            }
        }

        def fake_fetch(url: str, _timeout: float):
            if "scoreboard" in url:
                return scoreboard if "20260803" in url else {"events": []}
            if "summary" in url:
                return summary
            if "api-web.nhle.com" in url:
                return {"games": []}
            raise AssertionError(f"Unexpected URL: {url}")

        records = [{**self.aja, "sourceLeagueSlug": "wnba"}]
        now = datetime(2026, 8, 4, 20, tzinfo=timezone.utc)
        with patch("hourly_price_refresh.fetch_json", side_effect=fake_fetch):
            participants, player_events, events, warnings = discover_recent_events(
                records,
                now=now,
                lookback_hours=48,
                timeout=1,
                workers=2,
                processed_keys=set(),
            )
        self.assertEqual(participants, {("espn", "3149391")})
        self.assertEqual(len(player_events[("espn", "3149391")]), 1)
        self.assertTrue(events[0]["ready"])
        self.assertEqual(warnings, [])

        with patch("hourly_price_refresh.fetch_json", side_effect=fake_fetch):
            participants, player_events, events, warnings = discover_recent_events(
                records,
                now=now,
                lookback_hours=48,
                timeout=1,
                workers=2,
                processed_keys=set(),
                processed_player_keys={"espn:401857111|espn:3149391"},
            )
        self.assertEqual(participants, set())
        self.assertEqual(player_events, {})
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["ready"])

        with patch("hourly_price_refresh.fetch_json", side_effect=fake_fetch):
            participants, player_events, events, warnings = discover_recent_events(
                records,
                now=now,
                lookback_hours=48,
                timeout=1,
                workers=2,
                processed_keys={"espn:401857111"},
            )
        self.assertEqual(participants, set())
        self.assertEqual(player_events, {})
        self.assertEqual(events, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
