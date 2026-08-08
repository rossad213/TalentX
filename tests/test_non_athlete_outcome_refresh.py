from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from non_athlete_outcome_refresh import (
    actor_box_office_target,
    apply_outcome_events,
    attention_ratio,
    attention_target,
    best_box_office_ratio,
    chart_target,
    music_chart_positions,
    outcome_event,
)


class NonAthleteOutcomePricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.music = {
            "id": "music-test",
            "name": "Test Singer",
            "primaryCategory": "Music",
            "marketPrice": 100.0,
            "pricingConfidence": 0.82,
            "activeMetrics": {"audience": 90},
            "trend": [100.0],
            "priceEvents": [],
        }
        self.actor = {
            "id": "actor-test",
            "name": "Test Actor",
            "primaryCategory": "Actor",
            "marketPrice": 80.0,
            "pricingConfidence": 0.78,
            "activeMetrics": {"audience": 70},
            "trend": [80.0],
            "priceEvents": [],
        }

    def test_music_chart_qualifier_is_read(self) -> None:
        entity = {
            "claims": {
                "P2291": [{
                    "mainsnak": {"datavalue": {"value": {"id": "Q64569517"}}},
                    "qualifiers": {"P1352": [{"datavalue": {"value": {"amount": "+1", "unit": "1"}}}]},
                }]
            }
        }
        self.assertEqual(music_chart_positions(entity), [(1, "Q64569517")])
        self.assertGreater(chart_target(1)[1], chart_target(40)[1])

    def test_box_office_ratio_requires_matching_currency(self) -> None:
        entity = {
            "claims": {
                "P2142": [{"mainsnak": {"datavalue": {"value": {"amount": "+300", "unit": "usd"}}}}],
                "P2130": [{"mainsnak": {"datavalue": {"value": {"amount": "+100", "unit": "usd"}}}}],
            }
        }
        ratio = best_box_office_ratio(entity)
        self.assertIsNotNone(ratio)
        self.assertEqual(ratio[0], 3.0)
        tier, move = actor_box_office_target(ratio[0], 10)
        self.assertEqual(tier, "breakout")
        self.assertGreater(move, 0)

    def test_actor_underperformance_can_move_price_down(self) -> None:
        tier, move = actor_box_office_target(0.40, 22)
        self.assertEqual(tier, "severe-underperform")
        self.assertLess(move, 0)

    def test_attention_signal_can_be_positive_or_negative(self) -> None:
        event = datetime(2026, 8, 1, tzinfo=timezone.utc)
        points = []
        for offset in range(-21, 7):
            views = 100 if offset < 0 else 250
            points.append((event + timedelta(days=offset), views))
        measured = attention_ratio(points, event)
        self.assertIsNotNone(measured)
        self.assertEqual(round(measured[0], 2), 2.50)
        self.assertGreater(attention_target(measured[0])[1], 0)
        self.assertLess(attention_target(0.50)[1], 0)

    def test_verified_outcome_creates_durable_price_event(self) -> None:
        event = outcome_event(
            self.music,
            "wikidata:music-chart:Q1:Q2:top-10",
            "music-chart-outcome",
            "Chart result: #7",
            "Wikidata",
            "https://www.wikidata.org/wiki/Q2",
            0.70,
            datetime(2026, 8, 8, tzinfo=timezone.utc),
            {"chartRank": 7},
        )
        updated, count = apply_outcome_events(self.music, [event])
        self.assertEqual(count, 1)
        self.assertGreater(updated["marketPrice"], 100.0)
        self.assertEqual(updated["priceEvents"][0]["eventType"], "music-chart-outcome")
        self.assertEqual(updated["lastPriceEventId"], event["eventKey"])
        self.assertEqual(updated["priceExplanation"]["headline"], "Chart performance: #7")

    def test_duplicate_outcome_cannot_price_twice(self) -> None:
        event = outcome_event(
            self.actor,
            "wikidata:box-office:Q1:Q2:strong",
            "actor-box-office-outcome",
            "Box-office outcome: 2.10× production cost",
            "Wikidata",
            "https://www.wikidata.org/wiki/Q2",
            0.90,
            datetime(2026, 8, 8, tzinfo=timezone.utc),
            {"boxOfficeToCostRatio": 2.1},
        )
        once, count = apply_outcome_events(self.actor, [event])
        self.assertEqual(count, 1)
        twice, second = apply_outcome_events(once, [event])
        self.assertEqual(second, 0)
        self.assertEqual(twice["marketPrice"], once["marketPrice"])
        self.assertEqual(len(twice["priceEvents"]), 1)

    def test_no_outcome_means_no_move(self) -> None:
        updated, count = apply_outcome_events(self.music, [])
        self.assertEqual(count, 0)
        self.assertEqual(updated["marketPrice"], 100.0)


if __name__ == "__main__":
    unittest.main()
