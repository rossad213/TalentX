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
import hourly_price_refresh_nfl_opportunity as opportunity  # noqa: E402


class NflOpportunityProtectionTests(unittest.TestCase):
    def test_credible_established_baseline_is_unchanged(self) -> None:
        record = {"leagueOrMedium": "NFL", "role": "Quarterback"}
        evidence = {
            "comparable": True,
            "actualPerformanceScore": 16.0,
            "expectedPerformanceScore": 11.2,
            "productionDeltaPct": 42.86,
            "performanceDeltaPct": 38.0,
            "efficiencyDeltaPct": 18.0,
            "expectationSource": "existing",
        }
        with patch.object(opportunity, "_original_game_evidence", return_value=evidence):
            protected = opportunity.protect_game_evidence(record, {}, {})
        self.assertFalse(protected["opportunityFloorApplied"])
        self.assertEqual(protected["expectedPerformanceScore"], 11.2)
        self.assertEqual(protected["performanceDeltaPct"], 38.0)

    def test_tiny_backup_qb_baseline_uses_role_floor(self) -> None:
        record = {"leagueOrMedium": "NFL", "role": "Quarterback"}
        evidence = {
            "comparable": True,
            "actualPerformanceScore": 7.949,
            "expectedPerformanceScore": 0.005,
            "productionDeltaPct": 166833.33,
            "performanceDeltaPct": 133475.53,
            "efficiencyDeltaPct": 44.33,
            "expectationSource": "existing",
        }
        with patch.object(opportunity, "_original_game_evidence", return_value=evidence):
            protected = opportunity.protect_game_evidence(record, {}, {})
        self.assertTrue(protected["opportunityFloorApplied"])
        self.assertAlmostEqual(protected["personalExpectedPerformanceScore"], 0.005, places=3)
        self.assertEqual(protected["expectedPerformanceScore"], 5.0)
        self.assertGreater(protected["performanceDeltaPct"], 50)
        self.assertLess(protected["performanceDeltaPct"], 70)

    def test_low_output_running_back_does_not_get_fake_breakout(self) -> None:
        record = {"leagueOrMedium": "NFL", "role": "Running Back"}
        evidence = {
            "comparable": True,
            "actualPerformanceScore": 1.84,
            "expectedPerformanceScore": 0.03,
            "productionDeltaPct": 6033.33,
            "performanceDeltaPct": 4800.0,
            "efficiencyDeltaPct": 40.0,
            "expectationSource": "existing",
        }
        with patch.object(opportunity, "_original_game_evidence", return_value=evidence):
            protected = opportunity.protect_game_evidence(record, {}, {})
        self.assertTrue(protected["opportunityFloorApplied"])
        self.assertAlmostEqual(protected["expectedPerformanceScore"], 1.84, places=2)
        self.assertAlmostEqual(protected["performanceDeltaPct"], 0.0, places=2)

    def test_defensive_replacement_floor_reduces_tiny_denominator(self) -> None:
        record = {"leagueOrMedium": "NFL", "role": "Linebacker"}
        evidence = {
            "comparable": True,
            "actualPerformanceScore": 17.0,
            "expectedPerformanceScore": 2.906,
            "productionDeltaPct": 485.02,
            "performanceDeltaPct": 708.02,
            "efficiencyDeltaPct": 1600.0,
            "expectationSource": "existing",
        }
        with patch.object(opportunity, "_original_game_evidence", return_value=evidence):
            protected = opportunity.protect_game_evidence(record, {}, {})
        self.assertTrue(protected["opportunityFloorApplied"])
        self.assertEqual(protected["expectedPerformanceScore"], 8.0)
        self.assertAlmostEqual(protected["performanceDeltaPct"], 112.5, places=1)

    def test_zero_game_gap_season_does_not_erase_last_played_year(self) -> None:
        item = {
            "nflSeasonStats": {
                2024: {"gamesplayed": 17, "passingyards": 3400},
                2025: {"gamesplayed": 0, "passingyards": 0},
                2026: {"gamesplayed": 1, "passingyards": 220},
            }
        }
        filtered = opportunity._played_prior_history(item)
        self.assertIn(2024, filtered["nflSeasonStats"])
        self.assertNotIn(2025, filtered["nflSeasonStats"])
        self.assertIn(2026, filtered["nflSeasonStats"])

    def test_compacted_high_delta_low_sample_event_is_targeted(self) -> None:
        record = {
            "id": "test-player",
            "leagueOrMedium": "NFL",
            "role": "Linebacker",
            "professionalGames": 18,
            "lastGamePerformanceDeltaPct": 704.19,
            "lastPriceEventId": "espn:test",
            "priceEvents": [{
                "eventKey": "espn:test",
                "eventId": "test",
                "eventType": "game",
                "league": "nfl",
                "startedAt": "2026-09-13T17:00:00Z",
                "stats": {"tackles": 3, "sacks": 1},
            }],
        }
        self.assertTrue(opportunity._needs_opportunity_repair(
            record, now=datetime(2026, 9, 14, 20, tzinfo=timezone.utc)
        ))

    def test_established_star_high_delta_without_tiny_baseline_is_not_targeted(self) -> None:
        record = {
            "id": "star",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "professionalGames": 140,
            "lastGamePerformanceDeltaPct": 700.0,
            "lastPriceEventId": "espn:star",
            "priceEvents": [{
                "eventKey": "espn:star",
                "eventId": "star",
                "eventType": "game",
                "league": "nfl",
                "startedAt": "2026-09-13T17:00:00Z",
                "expectedPerformanceScore": 12.0,
                "stats": {"passingYards": 450},
            }],
        }
        self.assertFalse(opportunity._needs_opportunity_repair(
            record, now=datetime(2026, 9, 14, 20, tzinfo=timezone.utc)
        ))

    def test_earlier_backup_spike_is_repaired_and_later_move_is_replayed(self) -> None:
        record = {
            "id": "reserve-qb",
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
            "professionalGames": 12,
            "marketPrice": 142.80,
            "previousMarketPrice": 140.00,
            "lastPriceEventId": "espn:game-2",
            "dailyChange": 2.0,
            "priceEvents": [
                {
                    "eventKey": "espn:game-1",
                    "eventId": "game-1",
                    "eventType": "game",
                    "league": "nfl",
                    "startedAt": "2026-09-08T17:00:00Z",
                    "expectedPerformanceScore": 0.05,
                    "performanceDeltaPct": 9000.0,
                    "movePct": 40.0,
                    "priceBefore": 100.0,
                    "priceAfter": 140.0,
                    "stats": {"passingYards": 180},
                },
                {
                    "eventKey": "espn:game-2",
                    "eventId": "game-2",
                    "eventType": "game",
                    "league": "nfl",
                    "startedAt": "2026-09-13T17:00:00Z",
                    "expectedPerformanceScore": 6.0,
                    "performanceDeltaPct": 10.0,
                    "movePct": 2.0,
                    "priceBefore": 140.0,
                    "priceAfter": 142.8,
                    "stats": {"passingYards": 210},
                },
            ],
            "priceHistory": [
                {"time": "2026-09-08T17:00:00Z", "eventId": "espn:game-1", "phase": "open", "price": 100.0},
                {"time": "2026-09-08T20:00:00Z", "eventId": "espn:game-1", "phase": "close", "price": 140.0},
                {"time": "2026-09-13T17:00:00Z", "eventId": "espn:game-2", "phase": "open", "price": 140.0},
                {"time": "2026-09-13T20:00:00Z", "eventId": "espn:game-2", "phase": "close", "price": 142.8},
            ],
        }
        evidence = {
            "comparable": True,
            "expectedPerformanceScore": 5.0,
            "actualPerformanceScore": 5.2,
            "performanceDeltaPct": 4.0,
            "opportunityFloorApplied": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(opportunity.refresh, "fetch_hourly_evidence", return_value={"ok": True}), patch.object(
                opportunity.nfl, "nfl_results_based_game_event_move", return_value=(1.0, evidence)
            ):
                changed = opportunity.migrate_latest_opportunity_events(
                    path, timeout=1, now=datetime(2026, 9, 14, 20, tzinfo=timezone.utc)
                )
            repaired = json.loads(path.read_text(encoding="utf-8"))[0]

        self.assertEqual(changed, 1)
        first, second = repaired["priceEvents"]
        self.assertEqual(first["priceBefore"], 100.0)
        self.assertEqual(first["priceAfter"], 101.0)
        self.assertAlmostEqual(first["movePct"], 1.0, places=3)
        self.assertEqual(second["priceBefore"], 101.0)
        self.assertEqual(second["priceAfter"], 103.02)
        self.assertAlmostEqual(second["movePct"], 2.0, places=3)
        self.assertEqual(repaired["marketPrice"], 103.02)
        self.assertAlmostEqual(repaired["dailyChange"], 2.0, places=3)
        self.assertEqual(repaired["priceHistory"][-1]["price"], 103.02)


if __name__ == "__main__":
    unittest.main()
