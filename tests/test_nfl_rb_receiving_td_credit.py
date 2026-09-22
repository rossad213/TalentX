from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hourly_price_refresh_nfl as nfl  # noqa: E402
import hourly_price_refresh_nfl_rb as rb  # noqa: E402


class NflRunningBackReceivingTouchdownTests(unittest.TestCase):
    def bijan_record(self) -> dict:
        return {
            "id": "live-espn-americanfootball-4430807",
            "name": "Bijan Robinson",
            "leagueOrMedium": "NFL",
            "discipline": "American Football",
            "role": "Running Back",
            "careerStage": "Established",
            "professionalGames": 52,
            "marketPrice": 133.84,
            "activeMetrics": {"consistency": 70},
            "sourceNamespace": "espn",
            "sourceRecordId": "4430807",
            "sourceLeagueSlug": "nfl",
        }

    def season_history(self) -> dict[int, dict[str, float]]:
        return {
            2025: {
                "gamesplayed": 17,
                "rushingyards": 1478,
                "rushingtouchdowns": 7,
                "yardsperrushattempt": 5.2,
                "receptions": 79,
                "receivingyards": 820,
                "receivingtouchdowns": 4,
                "yardsperreception": 10.4,
            },
            2026: {
                "gamesplayed": 1,
                "rushingyards": 83,
                "rushingtouchdowns": 0,
                "yardsperrushattempt": 4.0,
                "receptions": 8,
                "receivingyards": 90,
                "receivingtouchdowns": 1,
                "yardsperreception": 11.3,
            },
        }

    def bijan_item(self) -> dict:
        record = self.bijan_record()
        current = self.season_history()[2026]
        return {
            "ok": True,
            "record": record,
            "recent": current,
            "career": {},
            "signals": rb.signal_bundle_with_rb_receiving_td_credit(record, current, {}, 0),
            "nflSeasonStats": self.season_history(),
            "professionalGames": 52,
        }

    def bijan_event(self) -> dict:
        return {
            "eventKey": "espn:401872658",
            "eventId": "401872658",
            "provider": "ESPN",
            "league": "nfl",
            "name": "Atlanta Falcons at Pittsburgh Steelers",
            "startedAt": "2026-09-13T17:00:00Z",
            "teamWon": False,
            "eventType": "game",
            "stats": {
                "rushingYards": 83,
                "yardsPerRushAttempt": 4.0,
                "rushingTouchdowns": 0,
                "receptions": 8,
                "receivingYards": 90,
                "yardsPerReception": 11.3,
                "receivingTouchdowns": 1,
            },
        }

    def test_rb_receiving_touchdown_gets_same_recent_credit_as_rushing_td(self) -> None:
        record = self.bijan_record()
        no_td = {
            "rushingYards": 83,
            "rushingTouchdowns": 0,
            "receivingYards": 90,
            "receptions": 8,
            "receivingTouchdowns": 0,
        }
        with_td = {**no_td, "receivingTouchdowns": 1}
        first = rb.signal_bundle_with_rb_receiving_td_credit(record, no_td, {}, 0)
        second = rb.signal_bundle_with_rb_receiving_td_credit(record, with_td, {}, 0)
        self.assertAlmostEqual(
            second["recentProduction"] - first["recentProduction"],
            rb.RB_RECEIVING_TD_RECENT_WEIGHT,
            places=6,
        )

    def test_non_running_back_signal_is_unchanged(self) -> None:
        record = {**self.bijan_record(), "role": "Wide Receiver"}
        stats = {"receivingYards": 90, "receivingTouchdowns": 1, "receptions": 8}
        base = rb._original_signal_bundle(record, stats, {}, 0)
        corrected = rb.signal_bundle_with_rb_receiving_td_credit(record, stats, {}, 0)
        self.assertEqual(corrected, base)

    def test_bijan_steelers_game_becomes_meaningfully_positive(self) -> None:
        record = self.bijan_record()
        item = self.bijan_item()
        with patch.object(nfl.refresh, "signal_bundle", rb.signal_bundle_with_rb_receiving_td_credit):
            move, evidence = nfl.nfl_results_based_game_event_move(
                record, item, self.bijan_event(), None
            )
        self.assertTrue(evidence["comparable"])
        self.assertAlmostEqual(evidence["actualPerformanceScore"], 25.067, places=3)
        self.assertAlmostEqual(evidence["expectedPerformanceScore"], 18.149, places=3)
        self.assertGreater(evidence["productionDeltaPct"], 35)
        self.assertLess(evidence["efficiencyDeltaPct"], 0)
        self.assertGreater(evidence["performanceDeltaPct"], 25)
        self.assertAlmostEqual(move, 1.100, places=2)
        self.assertGreater(move, 1.0)
        self.assertLess(move, 1.3)


    def test_game_event_moves_from_pre_game_price_without_fair_value_reanchor(self) -> None:
        old_record = {
            **self.bijan_record(),
            "marketPrice": 100.0,
            "trend": [100.0] * 18,
            "priceEvents": [],
        }
        new_record = dict(old_record)
        event = {
            **self.bijan_event(),
            "eventKey": "espn:no-reanchor",
            "eventId": "no-reanchor",
        }
        reliability = SimpleNamespace(
            RESULTS_MODEL_VERSION="test-results",
            results_based_game_event_move=lambda *_args, **_kwargs: (
                2.0,
                {"comparable": True, "performanceDeltaPct": 25.0},
            ),
        )
        with (
            patch.object(rb, "_reliability", reliability),
            patch.object(
                rb.production_pricing,
                "production_fair_value",
                return_value=(80.0, 200.0, {"fairValue": 200.0}),
            ),
        ):
            result, change_pct, events = rb.production_first_apply_game_market_moves(
                old_record,
                new_record,
                {},
                [event],
                None,
                "2026-09-21T18:00:00Z",
            )

        self.assertEqual(result["marketPrice"], 102.0)
        self.assertAlmostEqual(change_pct, 2.0, places=2)
        self.assertAlmostEqual(events[0]["movePct"], 2.0, places=3)
        self.assertEqual(events[0]["productionAnchorMovePct"], 0.0)
        self.assertEqual(events[0]["productionAnchorPrice"], 100.0)
        self.assertEqual(events[0]["productionTargetPrice"], 200.0)
        self.assertEqual(events[0]["productionTargetGapPct"], 100.0)


if __name__ == "__main__":
    unittest.main()
