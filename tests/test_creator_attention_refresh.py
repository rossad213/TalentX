from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from creator_attention_refresh import (
    attention_target,
    historical_events,
    reconstruct_chain,
    title_matches_name,
    window_ratio,
)

# Deep-history coverage policy: never invent creator events or dates. This test
# file is also an intentional workflow trigger for a fresh verified backfill.


class CreatorAttentionRefreshTests(unittest.TestCase):
    def test_parenthetical_wikipedia_title_can_match_creator_name(self):
        self.assertTrue(title_matches_name("Example Creator (YouTuber)", "Example Creator"))
        self.assertFalse(title_matches_name("Different Person", "Example Creator"))

    def test_attention_thresholds_support_up_and_down_moves(self):
        self.assertEqual(attention_target(3.2), ("breakout", 0.75))
        self.assertEqual(attention_target(2.1), ("hot", 0.50))
        self.assertEqual(attention_target(0.50), ("cool", -0.25))
        self.assertEqual(attention_target(0.30), ("cold", -0.50))
        self.assertIsNone(attention_target(1.05))

    def test_window_ratio_compares_recent_week_to_prior_baseline(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        points = []
        for offset in range(28):
            views = 100 if offset < 21 else 200
            points.append((start + timedelta(days=offset), views))
        ratio = window_ratio(points, points[-1][0])
        self.assertIsNotNone(ratio)
        self.assertAlmostEqual(ratio[0], 2.0, places=3)

    def test_history_events_do_not_require_live_repricing(self):
        record = {
            "id": "creator-1",
            "name": "Creator One",
            "primaryCategory": "Creator",
            "marketPrice": 100.0,
            "pricingConfidence": 0.8,
            "activeMetrics": {"audience": 80},
        }
        start = datetime(2025, 8, 1, tzinfo=timezone.utc)
        points = []
        for offset in range(70):
            views = 100
            if 28 <= offset <= 40:
                views = 250
            points.append((start + timedelta(days=offset), views))
        events = historical_events(record, "Creator One", "https://example.com", points)
        self.assertTrue(events)
        rebuilt = reconstruct_chain(record["marketPrice"], events)
        self.assertEqual(rebuilt[-1]["priceAfter"], 100.0)
        self.assertTrue(all(event.get("historicalBackfill") is True for event in events))


if __name__ == "__main__":
    unittest.main()
