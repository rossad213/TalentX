from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hourly_price_refresh_nfl as nfl  # noqa: E402


class NflPerGameExpectationTests(unittest.TestCase):
    def quarterback(self) -> dict:
        return {
            "id": "live-espn-americanfootball-4431611",
            "name": "Caleb Williams",
            "leagueOrMedium": "NFL",
            "discipline": "American Football",
            "role": "Quarterback",
            "careerStage": "Established",
            "professionalGames": 32,
            "marketPrice": 105.82,
            "activeMetrics": {"consistency": 70},
        }

    def career_baseline(self) -> dict:
        # 32-game career totals corresponding to a one-game expectation of
        # 245 pass yds, 1.7 pass TD, 0.7 INT, 35 rush yds and 0.35 rush TD.
        return {
            "gamesPlayed": 32,
            "passingYards": 7840,
            "passingTouchdowns": 54.4,
            "interceptions": 22.4,
            "rushingYards": 1120,
            "rushingTouchdowns": 11.2,
            "passerRating": 92.0,
        }

    def caleb_recent(self) -> dict:
        return {
            "gamesPlayed": 1,
            "passingYards": 269,
            "passingTouchdowns": 2,
            "interceptions": 0,
            "rushingYards": 65,
            "rushingTouchdowns": 2,
            # This unrelated AVG-style rate field used to cause the entire
            # season signal to be treated as though it were already per-game.
            "avgYardsPerAttempt": 9.28,
            "passerRating": 124.1,
        }

    def caleb_item(self) -> dict:
        record = self.quarterback()
        recent = self.caleb_recent()
        career = self.career_baseline()
        return {
            "ok": True,
            "record": record,
            "recent": recent,
            "career": career,
            "signals": nfl.refresh.signal_bundle(record, recent, career, 0),
        }

    def caleb_event(self) -> dict:
        return {
            "eventKey": "espn:4431611-week1",
            "eventId": "4431611-week1",
            "provider": "ESPN",
            "league": "nfl",
            "name": "Chicago Bears at Carolina Panthers",
            "startedAt": "2026-09-13T17:00:00Z",
            "teamWon": True,
            "eventType": "game",
            "stats": {
                "passingYards": 269,
                "passingTouchdowns": 2,
                "interceptions": 0,
                "rushingYards": 65,
                "rushingTouchdowns": 2,
                "passerRating": 124.1,
                "QBRating": 94.9,
            },
        }

    def test_nfl_totals_are_converted_to_one_game_even_with_avg_field(self) -> None:
        record = self.quarterback()
        recent = {
            "gamesPlayed": 10,
            "passingYards": 2500,
            "passingTouchdowns": 20,
            "interceptions": 10,
            "rushingYards": 300,
            "rushingTouchdowns": 4,
            "avgYardsPerAttempt": 7.5,
        }
        item = {"recent": recent, "career": {}, "signals": nfl.refresh.signal_bundle(record, recent, {}, 0)}
        expected = nfl.nfl_aware_expected_game_signal(record, item)
        self.assertGreater(expected, 12.0)
        self.assertLess(expected, 13.5)

    def test_caleb_like_four_touchdown_game_is_well_above_expectation(self) -> None:
        record = self.quarterback()
        move, evidence = nfl.nfl_results_based_game_event_move(
            record, self.caleb_item(), self.caleb_event(), None
        )
        self.assertTrue(evidence["comparable"])
        self.assertGreater(evidence["performanceDeltaPct"], 70)
        self.assertGreater(move, 2.8)
        self.assertLess(move, 5.0)
        self.assertEqual(evidence["nflExpectationModelVersion"], nfl.NFL_EXPECTATION_MODEL_VERSION)
        self.assertIsNone(evidence["hardMoveCapPct"])

    def test_genuinely_poor_qb_game_still_moves_negative(self) -> None:
        poor = {
            **self.caleb_event(),
            "eventKey": "espn:poor-qb",
            "eventId": "poor-qb",
            "teamWon": False,
            "stats": {
                "passingYards": 140,
                "passingTouchdowns": 0,
                "interceptions": 2,
                "rushingYards": 10,
                "rushingTouchdowns": 0,
                "passerRating": 48.0,
            },
        }
        move, evidence = nfl.nfl_results_based_game_event_move(
            self.quarterback(), self.caleb_item(), poor, None
        )
        self.assertTrue(evidence["comparable"])
        self.assertLess(evidence["performanceDeltaPct"], 0)
        self.assertLess(move, 0)

    def test_existing_latest_nfl_event_is_repriced_once_in_place(self) -> None:
        key = "espn:4431611-week1"
        record = {
            **self.quarterback(),
            "marketPrice": 105.82,
            "previousMarketPrice": 105.99,
            "dailyChange": -0.16,
            "hourlyChangePct": -0.16,
            "lastPriceEventId": key,
            "lastPriceEvent": "Chicago Bears at Carolina Panthers",
            "lastPriceEventAt": "2026-09-13T17:00:00Z",
            "lastGameMovePct": -0.16,
            "trend": [105.99] * 17 + [105.82],
            "priceEvents": [{
                **self.caleb_event(),
                "priceBefore": 105.99,
                "priceAfter": 105.82,
                "movePct": -0.16,
                "performanceDeltaPct": -12.0,
                "verified": True,
            }],
            "priceHistory": [
                {"eventId": key, "phase": "open", "price": 105.99},
                {"eventId": key, "phase": "close", "price": 105.82},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sports.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(nfl.refresh, "fetch_hourly_evidence", return_value=self.caleb_item()):
                changed = nfl.migrate_latest_nfl_expectations(
                    path,
                    timeout=1,
                    now=datetime(2026, 9, 14, 16, tzinfo=timezone.utc),
                )
            self.assertEqual(changed, 1)
            migrated = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(len(migrated["priceEvents"]), 1)
            self.assertGreater(migrated["lastGameMovePct"], 2.8)
            self.assertGreater(migrated["marketPrice"], 105.99)
            self.assertEqual(migrated["priceHistory"][0]["price"], 105.99)
            self.assertEqual(migrated["priceHistory"][1]["price"], migrated["marketPrice"])
            self.assertEqual(migrated["nflExpectationModelVersion"], nfl.NFL_EXPECTATION_MODEL_VERSION)

            with patch.object(nfl.refresh, "fetch_hourly_evidence", return_value=self.caleb_item()):
                changed_again = nfl.migrate_latest_nfl_expectations(
                    path,
                    timeout=1,
                    now=datetime(2026, 9, 14, 16, tzinfo=timezone.utc),
                )
            self.assertEqual(changed_again, 0)
            unchanged = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(len(unchanged["priceEvents"]), 1)
            self.assertEqual(unchanged["marketPrice"], migrated["marketPrice"])


if __name__ == "__main__":
    unittest.main()
