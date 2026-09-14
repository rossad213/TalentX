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
            "careerStage": "Early Career",
            "professionalGames": 35,
            "marketPrice": 102.29,
            "activeMetrics": {"consistency": 70},
            "sourceNamespace": "espn",
            "sourceRecordId": "4431611",
            "sourceLeagueSlug": "nfl",
        }

    def season_history(self) -> dict[int, dict[str, float]]:
        return {
            2024: {
                "gamesplayed": 17,
                "passingyards": 3541,
                "passingtouchdowns": 20,
                "interceptions": 6,
                "rushingyards": 489,
                "rushingtouchdowns": 0,
                "qbrating": 87.8,
                "completionpct": 62.5,
                "yardsperpassattempt": 6.3,
            },
            2025: {
                "gamesplayed": 17,
                "passingyards": 3942,
                "passingtouchdowns": 27,
                "interceptions": 7,
                "rushingyards": 388,
                "rushingtouchdowns": 3,
                "qbrating": 90.1,
                "adjqbr": 58.2,
                "completionpct": 58.1,
                "yardsperpassattempt": 6.9,
            },
            2026: {
                "gamesplayed": 1,
                "passingyards": 269,
                "passingtouchdowns": 2,
                "interceptions": 0,
                "rushingyards": 65,
                "rushingtouchdowns": 2,
                "qbrating": 124.1,
                "adjqbr": 94.9,
                "completionpct": 72.4,
                "yardsperpassattempt": 9.3,
            },
        }

    def caleb_item(self) -> dict:
        record = self.quarterback()
        current = self.season_history()[2026]
        return {
            "ok": True,
            "record": record,
            "recent": current,
            "career": {},
            "signals": nfl.refresh.signal_bundle(record, current, {}, 0),
            "nflSeasonStats": self.season_history(),
            "professionalGames": 35,
        }

    def caleb_event(self) -> dict:
        return {
            "eventKey": "espn:401872661",
            "eventId": "401872661",
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
                "battingAverage": 9.3,
            },
        }

    def test_parser_merges_passing_and_rushing_by_season(self) -> None:
        payload = {
            "categories": [
                {
                    "name": "passing",
                    "names": ["gamesPlayed", "passingYards", "passingTouchdowns", "interceptions", "QBRating"],
                    "statistics": [
                        {"season": {"year": 2025}, "stats": ["17", "3,942", "27", "7", "90.1"]},
                        {"season": {"year": 2026}, "stats": ["1", "269", "2", "0", "124.1"]},
                    ],
                },
                {
                    "name": "rushing",
                    "names": ["gamesPlayed", "rushingYards", "rushingTouchdowns"],
                    "statistics": [
                        {"season": {"year": 2025}, "stats": ["17", "388", "3"]},
                        {"season": {"year": 2026}, "stats": ["1", "65", "2"]},
                    ],
                },
            ]
        }
        history = nfl.parse_nfl_season_history(payload)
        self.assertEqual(history[2025]["gamesplayed"], 17)
        self.assertEqual(history[2025]["passingyards"], 3942)
        self.assertEqual(history[2025]["rushingyards"], 388)
        self.assertEqual(history[2026]["gamesplayed"], 1)

    def test_week_one_uses_prior_completed_season_not_current_game_or_projection(self) -> None:
        record = self.quarterback()
        item = self.caleb_item()
        baseline = nfl.nfl_expected_baseline_stats(record, item)
        self.assertAlmostEqual(baseline["passingyards"], 3942 / 17, places=3)
        self.assertAlmostEqual(baseline["passingtouchdowns"], 27 / 17, places=3)
        self.assertAlmostEqual(baseline["rushingyards"], 388 / 17, places=3)
        expected = nfl.nfl_aware_expected_game_signal(record, item)
        self.assertAlmostEqual(expected, 11.22857, places=4)

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
        move, evidence = nfl.nfl_results_based_game_event_move(
            self.quarterback(), self.caleb_item(), self.caleb_event(), None
        )
        self.assertTrue(evidence["comparable"])
        self.assertAlmostEqual(evidence["expectedPerformanceScore"], 11.229, places=3)
        self.assertGreater(evidence["productionDeltaPct"], 110)
        self.assertGreater(evidence["efficiencyDeltaPct"], 30)
        self.assertGreater(evidence["performanceDeltaPct"], 90)
        self.assertGreater(move, 3.0)
        self.assertLess(move, 5.0)
        self.assertAlmostEqual(move, 3.594, places=2)
        self.assertEqual(evidence["nflExpectationModelVersion"], nfl.NFL_EXPECTATION_MODEL_VERSION)
        self.assertIsNone(evidence["hardMoveCapPct"])

    def test_stored_legacy_avg_and_qbr_fields_are_normalized_for_qb_evidence(self) -> None:
        _, evidence = nfl.nfl_results_based_game_event_move(
            self.quarterback(), self.caleb_item(), self.caleb_event(), None
        )
        # Passer rating 124.1 is compared with the 2025 passer rating of 90.1,
        # rather than treating ESPN adjusted QBR 94.9 as passer rating.
        self.assertAlmostEqual(evidence["efficiencyDeltaPct"], 37.74, places=2)

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

    def test_existing_v1_event_is_repriced_once_in_place(self) -> None:
        key = "espn:401872661"
        record = {
            **self.quarterback(),
            "marketPrice": 102.29,
            "previousMarketPrice": 105.99,
            "dailyChange": -3.491,
            "hourlyChangePct": -3.491,
            "lastPriceEventId": key,
            "lastPriceEvent": "Chicago Bears at Carolina Panthers",
            "lastPriceEventAt": "2026-09-13T17:00:00Z",
            "lastGameMovePct": -3.491,
            "nflExpectationModelVersion": "1.0-nfl-true-per-game-expectation",
            "trend": [105.99] * 17 + [102.29],
            "priceEvents": [{
                **self.caleb_event(),
                "priceBefore": 105.99,
                "priceAfter": 102.29,
                "movePct": -3.491,
                "performanceDeltaPct": -86.72,
                "expectedPerformanceScore": 405.383,
                "nflExpectationModelVersion": "1.0-nfl-true-per-game-expectation",
                "verified": True,
            }],
            "priceHistory": [
                {"eventId": key, "phase": "open", "price": 105.99},
                {"eventId": key, "phase": "close", "price": 102.29},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sports.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(nfl.refresh, "fetch_hourly_evidence", return_value=self.caleb_item()):
                changed = nfl.migrate_latest_nfl_expectations(
                    path, timeout=1, now=datetime(2026, 9, 14, 19, tzinfo=timezone.utc)
                )
            self.assertEqual(changed, 1)
            migrated = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(len(migrated["priceEvents"]), 1)
            self.assertGreater(migrated["lastGameMovePct"], 3.0)
            self.assertLess(migrated["lastGameMovePct"], 5.0)
            self.assertAlmostEqual(migrated["marketPrice"], 109.80, places=2)
            self.assertEqual(migrated["priceHistory"][0]["price"], 105.99)
            self.assertEqual(migrated["priceHistory"][1]["price"], migrated["marketPrice"])
            self.assertEqual(migrated["nflExpectationModelVersion"], nfl.NFL_EXPECTATION_MODEL_VERSION)
            self.assertAlmostEqual(migrated["priceEvents"][0]["expectedPerformanceScore"], 11.229, places=3)

            with patch.object(nfl.refresh, "fetch_hourly_evidence", return_value=self.caleb_item()):
                changed_again = nfl.migrate_latest_nfl_expectations(
                    path, timeout=1, now=datetime(2026, 9, 14, 19, tzinfo=timezone.utc)
                )
            self.assertEqual(changed_again, 0)
            unchanged = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(len(unchanged["priceEvents"]), 1)
            self.assertEqual(unchanged["marketPrice"], migrated["marketPrice"])


if __name__ == "__main__":
    unittest.main()
