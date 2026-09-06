from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hourly_price_refresh_reliable as reliable  # noqa: E402


class UncappedSportsRefreshTests(unittest.TestCase):
    def test_exceptional_performance_can_exceed_legacy_two_point_five_cap(self) -> None:
        evidence = {
            "comparable": True,
            "performanceDeltaPct": 500.0,
            "productionDeltaPct": 500.0,
            "efficiencyDeltaPct": None,
        }
        record = {
            "professionalGames": 80,
            "careerStage": "Established",
            "activeMetrics": {"consistency": 70},
        }
        with patch.object(reliable, "_original_game_event_move", return_value=(2.5, evidence)):
            move, details = reliable.results_based_game_event_move(
                record,
                {},
                {"teamWon": True},
                2.5,
            )
        self.assertGreater(move, 2.5)
        self.assertIsNone(details["hardMoveCapPct"])
        self.assertIn("uncapped", details["pricingBasis"])

    def test_routine_result_does_not_get_forced_toward_the_old_cap(self) -> None:
        evidence = {
            "comparable": True,
            "performanceDeltaPct": 25.0,
            "productionDeltaPct": 25.0,
            "efficiencyDeltaPct": None,
        }
        record = {
            "professionalGames": 140,
            "careerStage": "Established",
            "activeMetrics": {"consistency": 80},
        }
        with patch.object(reliable, "_original_game_event_move", return_value=(2.5, evidence)):
            move, _ = reliable.results_based_game_event_move(record, {}, {"teamWon": True}, 2.5)
        self.assertGreater(move, 0)
        self.assertLess(move, 1.0)

    def test_distinct_event_key_is_only_applied_once_per_refresh(self) -> None:
        old = {"id": "x", "marketPrice": 100.0, "trend": [100.0] * 18, "priceEvents": []}
        refreshed = {**old, "marketPrice": 100.0, "fundamentalValue": 100.0}
        event = {
            "eventKey": "espn:1",
            "eventId": "1",
            "provider": "ESPN",
            "league": "nba",
            "name": "Example game",
            "startedAt": "2026-09-06T00:00:00Z",
            "stats": {},
        }
        priced = (4.0, {"comparable": True, "performanceDeltaPct": 300.0, "outcomeMovePct": 0.0})
        with patch.object(reliable, "results_based_game_event_move", return_value=priced):
            result, change, events = reliable.apply_game_market_moves_with_history(
                old,
                refreshed,
                {},
                [event, dict(event)],
                2.5,
                "2026-09-06T01:00:00Z",
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(result["marketPrice"], 104.0)
        self.assertEqual(change, 4.0)

    def test_verified_result_can_move_beyond_old_thirty_percent_fair_value_band(self) -> None:
        old = {"id": "x", "marketPrice": 100.0, "trend": [100.0] * 18, "priceEvents": []}
        refreshed = {**old, "marketPrice": 100.0, "fundamentalValue": 100.0}
        event = {
            "eventKey": "espn:2",
            "eventId": "2",
            "provider": "ESPN",
            "league": "nba",
            "name": "Exceptional verified result",
            "startedAt": "2026-09-06T00:00:00Z",
            "stats": {},
        }
        priced = (40.0, {"comparable": True, "performanceDeltaPct": 5000.0, "outcomeMovePct": 0.0})
        with patch.object(reliable, "results_based_game_event_move", return_value=priced):
            result, change, _ = reliable.apply_game_market_moves_with_history(
                old,
                refreshed,
                {},
                [event],
                2.5,
                "2026-09-06T01:00:00Z",
            )
        self.assertEqual(result["marketPrice"], 140.0)
        self.assertEqual(change, 40.0)
        self.assertNotIn("eventPriceBand", result)


if __name__ == "__main__":
    unittest.main()
