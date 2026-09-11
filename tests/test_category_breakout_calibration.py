from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from market_outcome_calibration_v2 import (  # noqa: E402
    MODEL_VERSION,
    actor_box_office_target,
    actor_direct_box_office_target,
    actor_netflix_target,
    creator_performance_target,
    music_chart_target,
    music_movement_target,
)
from reprice_category_outcomes_v2 import reprice_record  # noqa: E402


class CategoryBreakoutCalibrationTests(unittest.TestCase):
    def test_music_top_end_has_real_separation(self) -> None:
        self.assertGreater(music_chart_target(1)[1], 3.0)
        self.assertGreater(music_chart_target(5)[1], 2.0)
        self.assertLess(music_chart_target(40)[1], 1.0)
        self.assertLess(music_chart_target(100)[1], 0.30)
        self.assertGreater(music_movement_target(5, 37), 2.0)
        self.assertEqual(music_movement_target(5, 5), 0.0)

    def test_creator_curve_keeps_routine_wins_small_and_breakouts_large(self) -> None:
        warm = creator_performance_target(1.60)
        breakout = creator_performance_target(5.0)
        extreme = creator_performance_target(64.0)
        self.assertIsNotNone(warm)
        self.assertIsNotNone(breakout)
        self.assertIsNotNone(extreme)
        self.assertLess(warm[1], 1.0)
        self.assertGreater(breakout[1], 2.5)
        self.assertGreater(extreme[1], 6.0)

    def test_actor_fallback_breakout_scales_without_a_cap(self) -> None:
        _, three_x = actor_box_office_target(3.0, 10)
        _, ten_x = actor_box_office_target(10.0, 10)
        _, fifty_x = actor_box_office_target(50.0, 10)
        self.assertGreater(three_x, 1.5)
        self.assertGreater(ten_x, 3.0)
        self.assertGreater(fifty_x, ten_x)

    def test_direct_actor_blockbuster_and_streaming_breakout_scale(self) -> None:
        theatrical = [
            {"rank": 1, "gross": 360_000_000, "weeklyChangePct": None},
            {"rank": 2, "gross": 30_000_000, "weeklyChangePct": -40},
            {"rank": 3, "gross": 20_000_000, "weeklyChangePct": -45},
            {"rank": 4, "gross": 10_000_000, "weeklyChangePct": -45},
            {"rank": 5, "gross": 8_000_000, "weeklyChangePct": -45},
        ]
        self.assertGreater(actor_direct_box_office_target(theatrical[0], theatrical), 4.0)

        streaming = [
            {"category": "Films (English)", "rank": 1, "views": 50_000_000, "weeksInTop10": 2},
            {"category": "Films (English)", "rank": 2, "views": 5_000_000, "weeksInTop10": 1},
            {"category": "Films (English)", "rank": 3, "views": 4_000_000, "weeksInTop10": 1},
            {"category": "Films (English)", "rank": 4, "views": 3_000_000, "weeksInTop10": 1},
        ]
        previous = {"views": 20_000_000}
        self.assertGreater(actor_netflix_target(streaming[0], streaming, previous), 3.0)

    def test_existing_music_outcome_migrates_once_without_duplicate_event(self) -> None:
        record = {
            "id": "music-test",
            "name": "Test Singer",
            "primaryCategory": "Music",
            "marketPrice": 101.10,
            "previousMarketPrice": 100.0,
            "priceEvents": [{
                "eventKey": "chart:test:1",
                "eventId": "chart:test:1",
                "eventType": "music-chart-outcome",
                "verified": True,
                "chartRank": 1,
                "startedAt": "2026-09-01T00:00:00Z",
                "targetOutcomeMovePct": 1.25,
                "movePct": 1.10,
                "priceBefore": 100.0,
                "priceAfter": 101.10,
            }],
            "priceHistory": [
                {"eventId": "chart:test:1", "phase": "open", "price": 100.0},
                {"eventId": "chart:test:1", "phase": "close", "price": 101.10},
            ],
        }
        now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        migrated, count = reprice_record(record, "music", now)
        self.assertEqual(count, 1)
        self.assertGreater(migrated["marketPrice"], record["marketPrice"])
        self.assertEqual(len(migrated["priceEvents"]), 1)
        self.assertEqual(migrated["priceEvents"][0]["pricingCalibrationVersion"], MODEL_VERSION)
        self.assertGreater(migrated["priceEvents"][0]["movePct"], 3.0)
        self.assertGreater(migrated["priceHistory"][1]["price"], 101.10)

        rerun, second = reprice_record(migrated, "music", now)
        self.assertEqual(second, 0)
        self.assertEqual(rerun["marketPrice"], migrated["marketPrice"])
        self.assertEqual(len(rerun["priceEvents"]), 1)


if __name__ == "__main__":
    unittest.main()
