from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from billboard_chart_refresh import (
    artist_credit_matches,
    movement_target,
    parse_billboard_chart,
    refresh_catalog,
)


SAMPLE_HTML = """
<html><body>
<h1>Billboard Global 200</h1>
<div>Week of September 05, 2026</div>
<div class="row"><span>1</span><h2>Dai Dai</h2><h3>Shakira X Burna Boy</h3><span>1</span><span>1</span><span>14</span></div>
<div class="row"><span>5</span><h2>BbY WOW</h2><h3>Karol G With Judeline &amp; rusowsky</h3><span>37</span><span>5</span><span>3</span></div>
<div class="row"><span>16</span><h2>Jolene</h2><h3>Dolly Parton</h3><span>New</span><span>16</span><span>1</span></div>
</body></html>
"""


class BillboardChartRefreshTests(unittest.TestCase):
    def test_parser_reads_chart_week_and_rank_movement(self) -> None:
        chart_date, rows = parse_billboard_chart(
            SAMPLE_HTML,
            datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(chart_date.date().isoformat(), "2026-09-05")
        karol = next(row for row in rows if row["title"] == "BbY WOW")
        self.assertEqual(karol["rank"], 5)
        self.assertEqual(karol["lastWeekRank"], 37)
        self.assertEqual(karol["artistCredit"], "Karol G With Judeline & rusowsky")

    def test_artist_credit_matching_handles_collaborations(self) -> None:
        self.assertTrue(artist_credit_matches("Karol G", "Karol G With Judeline & rusowsky"))
        self.assertTrue(artist_credit_matches("Burna Boy", "Shakira X Burna Boy"))
        self.assertFalse(artist_credit_matches("Karol G", "Shakira X Burna Boy"))

    def test_rank_jump_is_positive_and_flat_rank_is_zero(self) -> None:
        self.assertGreater(movement_target(5, 37), 0)
        self.assertLess(movement_target(37, 5), 0)
        self.assertEqual(movement_target(5, 5), 0)
        self.assertGreater(movement_target(16, None, "new"), 0)
        self.assertEqual(movement_target(16, None, "ranked"), 0)

    def test_refresh_applies_one_strongest_signal_per_artist_week(self) -> None:
        records = [{
            "id": "cur-karol-g",
            "name": "Karol G",
            "primaryCategory": "Music",
            "marketPrice": 100.0,
            "pricingConfidence": 0.80,
            "activeMetrics": {"audience": 90},
            "trend": [100.0],
            "priceEvents": [],
        }]
        chart_date, rows = parse_billboard_chart(SAMPLE_HTML)
        chart_a = {
            "slug": "billboard-global-200",
            "name": "Billboard Global 200",
            "url": "https://ca.billboard.com/charts/billboard-global-200",
        }
        chart_b = {
            "slug": "billboard-global-excl-us",
            "name": "Billboard Global Excl. U.S.",
            "url": "https://ca.billboard.com/charts/billboard-global-excl-us",
        }
        updated, changed, applied = refresh_catalog(
            records,
            [(chart_a, chart_date, rows), (chart_b, chart_date, rows)],
        )
        self.assertEqual(changed, 1)
        self.assertEqual(applied, 1)
        self.assertGreater(updated[0]["marketPrice"], 100.0)
        self.assertEqual(len(updated[0]["priceEvents"]), 1)
        event = updated[0]["priceEvents"][0]
        self.assertEqual(event["eventType"], "music-chart-outcome")
        self.assertEqual(event["chartRank"], 5)
        self.assertEqual(event["previousChartRank"], 37)
        self.assertEqual(event["releaseTitle"], "BbY WOW")

    def test_existing_nearby_chart_event_suppresses_duplicate(self) -> None:
        records = [{
            "id": "cur-karol-g",
            "name": "Karol G",
            "primaryCategory": "Music",
            "marketPrice": 100.0,
            "pricingConfidence": 0.80,
            "activeMetrics": {"audience": 90},
            "trend": [100.0],
            "priceEvents": [{
                "eventKey": "wikidata:music-chart:q1:q2:top-5",
                "eventType": "music-chart-outcome",
                "chartRank": 5,
                "startedAt": "2026-09-03T00:00:00Z",
                "priceBefore": 99.0,
                "priceAfter": 100.0,
            }],
        }]
        chart_date, rows = parse_billboard_chart(SAMPLE_HTML)
        chart = {
            "slug": "billboard-global-200",
            "name": "Billboard Global 200",
            "url": "https://ca.billboard.com/charts/billboard-global-200",
        }
        updated, changed, applied = refresh_catalog(records, [(chart, chart_date, rows)])
        self.assertEqual((changed, applied), (0, 0))
        self.assertEqual(updated[0]["marketPrice"], 100.0)
        self.assertEqual(len(updated[0]["priceEvents"]), 1)


if __name__ == "__main__":
    unittest.main()
