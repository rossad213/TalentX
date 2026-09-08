from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from actor_direct_outcome_refresh import (
    box_office_target,
    latest_netflix_weeks,
    netflix_target,
    normalize_title,
    parse_netflix_global,
    parse_the_numbers,
)


class ActorDirectOutcomeTests(unittest.TestCase):
    def test_title_normalization_is_conservative(self) -> None:
        self.assertEqual(normalize_title("Spider-Man: Brand New Day"), "spidermanbrandnewday")
        self.assertEqual(normalize_title("Rock & Roll"), "rockandroll")
        self.assertNotEqual(normalize_title("The Invite"), normalize_title("Invite Only"))

    def test_the_numbers_parser_reads_rank_gross_and_change(self) -> None:
        sample = """
        <html><h1>Weekend Domestic Box Office September 4, 2026</h1>
        <table>
          <tr><th>Rank</th><th>Prev</th><th>Title</th><th>Gross</th><th>Weekly Change</th><th>Theaters</th><th>Avg</th><th>Total Gross</th></tr>
          <tr><td>1</td><td>(1)</td><td><a>Spider-Man: Brand New Day</a></td><td>$18,175,000</td><td>-19%</td><td>3520</td><td>$5,163</td><td>$917,937,000</td></tr>
          <tr><td>2</td><td>(new)</td><td>By Any Means</td><td>$7,500,000</td><td></td><td>2903</td><td>$2,584</td><td>$7,500,000</td></tr>
        </table></html>
        """
        when, rows = parse_the_numbers(sample)
        self.assertEqual(when.date().isoformat(), "2026-09-04")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["gross"], 18175000)
        self.assertEqual(rows[0]["weeklyChangePct"], -19)
        self.assertEqual(rows[1]["new"], True)

    def test_extreme_box_office_result_can_exceed_old_two_point_five_cap(self) -> None:
        rows = [
            {"rank": 1, "gross": 360_000_000, "weeklyChangePct": None},
            {"rank": 2, "gross": 30_000_000, "weeklyChangePct": -40},
            {"rank": 3, "gross": 20_000_000, "weeklyChangePct": -45},
            {"rank": 4, "gross": 10_000_000, "weeklyChangePct": -45},
            {"rank": 5, "gross": 8_000_000, "weeklyChangePct": -45},
        ]
        self.assertGreater(box_office_target(rows[0], rows), 2.5)

    def test_netflix_tsv_uses_latest_and_previous_week(self) -> None:
        sample = """week\tcategory\tweekly_rank\tshow_title\tseason_title\tweekly_hours_viewed\truntime\tweekly_views\tcumulative_weeks_in_top_10
2026-08-23\tFilms (English)\t2\tTest Movie\tN/A\t10000000\t1.5\t6000000\t1
2026-08-30\tFilms (English)\t1\tTest Movie\tN/A\t24000000\t1.5\t16000000\t2
2026-08-30\tFilms (English)\t2\tOther Movie\tN/A\t9000000\t1.5\t6000000\t1
"""
        rows = parse_netflix_global(sample)
        latest, current, previous = latest_netflix_weeks(rows, datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(latest.date().isoformat(), "2026-08-30")
        self.assertEqual(len(current), 2)
        self.assertEqual(len(previous), 1)
        target = netflix_target(current[0], current, previous[0])
        self.assertGreater(target, 0)

    def test_netflix_large_view_breakout_is_not_hard_capped(self) -> None:
        current = [
            {"category": "Films (English)", "rank": 1, "views": 50_000_000, "weeksInTop10": 1},
            {"category": "Films (English)", "rank": 2, "views": 5_000_000, "weeksInTop10": 1},
            {"category": "Films (English)", "rank": 3, "views": 4_000_000, "weeksInTop10": 1},
            {"category": "Films (English)", "rank": 4, "views": 3_000_000, "weeksInTop10": 1},
        ]
        self.assertGreater(netflix_target(current[0], current, None), 2.0)


if __name__ == "__main__":
    unittest.main()
