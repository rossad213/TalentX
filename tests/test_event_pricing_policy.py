#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from event_pricing_policy import apply_policy, explainable_event_move  # noqa: E402


class EventPricingPolicyTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "cur-example",
            "lastPriceEventId": "espn:1",
            "lastPriceEvent": "Example game",
            "lastPriceEventAt": "2026-08-04T20:00:00Z",
            "previousMarketPrice": 100.0,
            "marketPrice": 101.0,
            "lastGameMovePct": 1.0,
            "lastGamePerformanceDeltaPct": 40.0,
            "demandPremiumPct": 4.0,
            "professionalGames": 250,
            "careerStage": "Established",
            "pricingConfidence": 0.9,
            "activeMetrics": {
                "achievements": 85,
                "potential": 75,
                "availability": 90,
                "consistency": 88,
            },
            "trend": [100.0, 101.0],
        }
        base.update(updates)
        return base

    def test_positive_game_creates_explanation(self):
        updated, changed = apply_policy(self.record())
        self.assertTrue(changed)
        self.assertGreater(updated["lastGameMovePct"], 0)
        self.assertEqual(updated["priceExplanation"]["headline"], "Strong game performance")
        self.assertEqual(updated["volatilityTier"], "Low")

    def test_negative_game_decreases_price(self):
        updated, _ = apply_policy(self.record(lastGameMovePct=-1.0, lastGamePerformanceDeltaPct=-45.0))
        self.assertLess(updated["lastGameMovePct"], 0)
        self.assertLess(updated["marketPrice"], updated["previousMarketPrice"])
        self.assertEqual(updated["priceExplanation"]["headline"], "Below expectations")

    def test_same_inputs_are_idempotent(self):
        first, _ = apply_policy(self.record())
        second, changed = apply_policy(first)
        self.assertFalse(changed)
        self.assertEqual(first["marketPrice"], second["marketPrice"])
        self.assertEqual(first["priceExplanation"], second["priceExplanation"])

    def test_no_event_means_no_change(self):
        original = self.record(lastPriceEventId="")
        updated, changed = apply_policy(original)
        self.assertFalse(changed)
        self.assertEqual(updated, original)

    def test_rookie_is_more_sensitive_than_established_player(self):
        veteran_move, _ = explainable_event_move(self.record())
        rookie_move, _ = explainable_event_move(
            self.record(professionalGames=8, careerStage="Rookie", activeMetrics={"consistency": 55})
        )
        self.assertGreater(abs(rookie_move), abs(veteran_move))


if __name__ == "__main__":
    unittest.main()
