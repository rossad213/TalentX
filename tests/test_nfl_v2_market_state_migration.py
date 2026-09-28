from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_nfl_v2_market_state import (  # noqa: E402
    MIGRATION_EVENT_ID,
    MIGRATION_VERSION,
    migrate_record,
)
from pricing_engine_v2 import apply_v2  # noqa: E402
from nfl_metric_calibration import calibrate_record  # noqa: E402
from hourly_price_refresh_nfl import NFL_FUNDAMENTAL_EVIDENCE_VERSION  # noqa: E402


class NflV2MarketStateMigrationTests(unittest.TestCase):
    def nfl_record(self, **updates):
        base = {
            "id": "nfl-player",
            "name": "NFL Player",
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "careerStage": "Established",
            "professionalGames": 80,
            "careerScore": 64.7,
            "pricingConfidence": 0.84,
            "activeMetrics": {
                "performance": 66,
                "achievements": 48,
                "consistency": 62,
                "potential": 67,
                "availability": 76,
                "audience": 60,
            },
            "momentumPct": 0,
            "demandPremiumPct": 0,
            "marketPrice": 204.88,
            "previousMarketPrice": 207.41,
            "fundamentalValue": 74.98,
            "fairValue": 74.98,
            "lastGameMovePct": -1.22,
            "dailyChange": -1.22,
            "hourlyChangePct": -1.22,
            "trend": [213.27, 209.81, 207.41, 204.88],
            "lastPriceEventId": "espn:game-3",
            "priceEvents": [
                {
                    "eventKey": "espn:game-3",
                    "eventId": "game-3",
                    "eventType": "game",
                    "league": "nfl",
                    "priceBefore": 207.41,
                    "priceAfter": 204.88,
                    "movePct": -1.22,
                }
            ],
            "priceHistory": [
                {
                    "time": "2026-09-20T20:00:00Z",
                    "eventId": "espn:game-3",
                    "eventType": "game",
                    "phase": "close",
                    "price": 204.88,
                }
            ],
        }
        base.update(updates)
        return base

    def test_nfl_resets_once_to_clean_v2_fair_value(self):
        original = self.nfl_record(lastGameMovePct=-54.68, marketPrice=195.91)
        expected = apply_v2({**calibrate_record(original), "lastGameMovePct": 0.0, "dailyChange": 0.0, "hourlyChangePct": 0.0})
        migrated, changed = migrate_record(original, "2026-09-24T16:15:00Z")

        self.assertTrue(changed)
        self.assertEqual(migrated["marketPrice"], expected["fairValue"])
        self.assertEqual(migrated["fairValue"], expected["fairValue"])
        self.assertEqual(migrated["fundamentalValue"], expected["fairValue"])
        self.assertEqual(migrated["previousMarketPrice"], expected["fairValue"])
        self.assertEqual(migrated["dailyChange"], 0.0)
        self.assertEqual(migrated["hourlyChangePct"], 0.0)
        self.assertEqual(migrated["lastGameMovePct"], 0.0)
        self.assertEqual(migrated["nflMarketMigrationPriorMarketPrice"], 195.91)
        self.assertEqual(migrated["nflMarketMigrationPriorLastGameMovePct"], -54.68)
        self.assertEqual(migrated["nflMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertEqual(migrated["trend"], [expected["fairValue"]] * 18)

    def test_existing_game_ledger_is_preserved_for_audit(self):
        original = self.nfl_record()
        prior_events = original["priceEvents"]
        migrated, changed = migrate_record(original, "2026-09-24T16:15:00Z")

        self.assertTrue(changed)
        self.assertEqual(migrated["priceEvents"], prior_events)
        self.assertEqual(migrated["priceHistory"][0], original["priceHistory"][0])
        self.assertEqual(migrated["priceHistory"][-1]["eventId"], MIGRATION_EVENT_ID)
        self.assertEqual(migrated["priceHistory"][-1]["price"], migrated["marketPrice"])

    def test_prior_migration_version_is_reset_again_for_v1_2_semantic_cleanup(self):
        original = self.nfl_record(
            marketPrice=463.46,
            nflMarketMigrationVersion="1.0-nfl-v2-market-state-reset",
            nflMarketMigratedAt="2026-09-24T16:15:00Z",
            lastGameMovePct=-2.43,
        )
        expected = apply_v2({
            **calibrate_record(original),
            "lastGameMovePct": 0.0,
            "dailyChange": 0.0,
            "hourlyChangePct": 0.0,
        })
        migrated, changed = migrate_record(original, "2026-09-24T19:00:00Z")

        self.assertTrue(changed)
        self.assertEqual(migrated["marketPrice"], expected["fairValue"])
        self.assertEqual(migrated["nflMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertEqual(migrated["nflMarketMigratedAt"], "2026-09-24T19:00:00Z")
        self.assertEqual(migrated["nflMarketMigrationPriorMarketPrice"], 463.46)

    def test_live_espn_player_waits_for_unified_evidence_before_reset(self):
        pending = self.nfl_record(
            sourceNamespace="espn",
            careerStatus="Active",
            nflFundamentalEvidenceVersion=None,
        )
        unchanged, changed = migrate_record(pending, "2026-09-25T01:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(unchanged, pending)

        ready = {
            **pending,
            "nflFundamentalEvidenceVersion": NFL_FUNDAMENTAL_EVIDENCE_VERSION,
        }
        migrated, changed_ready = migrate_record(ready, "2026-09-25T01:05:00Z")
        self.assertTrue(changed_ready)
        self.assertEqual(migrated["nflMarketMigrationVersion"], MIGRATION_VERSION)

    def test_zero_game_drafted_player_can_reset_without_established_window(self):
        rookie = self.nfl_record(
            sourceNamespace="espn",
            careerStatus="Active",
            careerStage="Active Rookie",
            professionalGames=0,
            nflFundamentalEvidenceVersion=None,
            draftYear=2026,
            draftRound=4,
            draftPick=118,
            rookiePricing={
                "draftSport": "NFL",
                "rookieScore": 66,
                "draftInfluencePct": 100,
            },
            marketPrice=88.0,
            fundamentalValue=42.0,
        )
        migrated, changed = migrate_record(rookie, "2026-09-26T06:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["nflMarketMigrationVersion"], MIGRATION_VERSION)
        self.assertNotEqual(migrated["marketPrice"], 88.0)
        self.assertEqual(migrated["marketPrice"], migrated["fundamentalValue"])

    def test_latest_post_epoch_game_is_preserved_exactly_once_on_new_fundamental(self):
        original=self.nfl_record(
            marketPrice=126.68,
            previousMarketPrice=117.40,
            lastGameMovePct=7.905,
            lastPriceEventId="espn:401872950",
            lastPriceEventAt="2026-09-27T17:00:00Z",
            nflMarketMigrationVersion="1.4-nfl-recency-availability-position-value-reset",
            nflMarketMigratedAt="2026-09-26T07:46:16Z",
            priceEvents=[{
                "eventKey":"espn:401872950","eventId":"401872950","eventType":"game",
                "league":"nfl","verified":True,"startedAt":"2026-09-27T17:00:00Z",
                "priceBefore":100.83,"priceAfter":108.80,"movePct":7.905,
            }],
        )
        expected_fair=apply_v2({
            **calibrate_record(original),
            "lastGameMovePct":0.0,
            "dailyChange":0.0,
            "hourlyChangePct":0.0,
        })["fairValue"]
        migrated,changed=migrate_record(original,"2026-09-27T23:30:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["fundamentalValue"],expected_fair)
        self.assertEqual(migrated["previousMarketPrice"],expected_fair)
        self.assertEqual(migrated["marketPrice"],round(expected_fair*1.07905,2))
        self.assertEqual(migrated["lastGameMovePct"],7.905)
        self.assertEqual(migrated["nflMarketMigrationPreservedEventId"],"espn:401872950")
        self.assertLess(migrated["marketPrice"],126.68)
        preserved=migrated["priceEvents"][0]
        self.assertEqual(preserved["priceBefore"],expected_fair)
        self.assertEqual(preserved["priceAfter"],migrated["marketPrice"])
        self.assertEqual(preserved["marketEpochRebased"],MIGRATION_VERSION)

    def test_preserved_legacy_game_is_recalibrated_to_absolute_performance_authority(self):
        original=self.nfl_record(
            role="Tight End",
            marketPrice=102.47,
            previousMarketPrice=94.70,
            lastGameMovePct=8.205,
            lastPriceEventId="espn:401872954",
            lastPriceEventAt="2026-09-27T17:00:00Z",
            nflMarketMigrationVersion="1.5-nfl-career-tier-and-exactly-once-reset",
            nflMarketMigratedAt="2026-09-27T23:30:00Z",
            nflMarketMigrationPreservedEventId="espn:401872954",
            nflMarketMigrationPreservedEventMovePct=8.205,
            priceEvents=[{
                "eventKey":"espn:401872954","eventId":"401872954","eventType":"game",
                "league":"nfl","verified":True,"startedAt":"2026-09-27T17:00:00Z",
                "priceBefore":94.70,"priceAfter":102.47,"movePct":8.205,
                "performanceMovePct":8.256,"outcomeMovePct":-0.05,
                "actualPerformanceScore":17.25,"expectedPerformanceScore":2.76,
                "nflExpectationModelVersion":"1.8-nfl-position-normalized-established-window",
            }],
        )
        migrated,changed=migrate_record(original,"2026-09-28T05:30:00Z")
        self.assertTrue(changed)
        self.assertLess(migrated["lastGameMovePct"],8.205)
        self.assertGreater(migrated["lastGameMovePct"],4.0)
        event=migrated["priceEvents"][0]
        self.assertEqual(event["nflExpectationModelVersion"],"1.9-nfl-absolute-performance-authority")
        self.assertLess(event["performanceMovePct"],8.256)
        self.assertEqual(event["preservedGameMoveRecalibrated"],MIGRATION_VERSION)
        self.assertEqual(event["priceBefore"],migrated["fundamentalValue"])
        self.assertEqual(event["priceAfter"],migrated["marketPrice"])

    def test_full_breakout_preserved_game_keeps_near_full_authority(self):
        original=self.nfl_record(
            role="Tight End",
            marketPrice=92.99,
            previousMarketPrice=85.28,
            lastGameMovePct=9.036,
            lastPriceEventId="espn:401872954",
            lastPriceEventAt="2026-09-27T17:00:00Z",
            nflMarketMigrationVersion="1.5-nfl-career-tier-and-exactly-once-reset",
            nflMarketMigratedAt="2026-09-27T23:30:00Z",
            nflMarketMigrationPreservedEventId="espn:401872954",
            nflMarketMigrationPreservedEventMovePct=9.036,
            priceEvents=[{
                "eventKey":"espn:401872954","eventId":"401872954","eventType":"game",
                "league":"nfl","verified":True,"startedAt":"2026-09-27T17:00:00Z",
                "priceBefore":85.28,"priceAfter":92.99,"movePct":9.036,
                "performanceMovePct":9.086,"outcomeMovePct":-0.05,
                "actualPerformanceScore":24.75,"expectedPerformanceScore":4.083,
                "nflExpectationModelVersion":"1.8-nfl-position-normalized-established-window",
            }],
        )
        migrated,changed=migrate_record(original,"2026-09-28T05:30:00Z")
        self.assertTrue(changed)
        self.assertAlmostEqual(migrated["lastGameMovePct"],9.036,places=2)

    def test_false_post_game_market_observations_are_removed_during_rebase(self):
        original=self.nfl_record(
            marketPrice=121.47,
            previousMarketPrice=112.33,
            lastGameMovePct=8.137,
            lastPriceEventId="espn:401872950",
            lastPriceEventAt="2026-09-27T17:00:00Z",
            nflMarketMigrationVersion="1.4-nfl-recency-availability-position-value-reset",
            nflMarketMigratedAt="2026-09-26T07:46:16Z",
            priceEvents=[{
                "eventKey":"espn:401872950","eventId":"401872950","eventType":"game",
                "league":"nfl","verified":True,"startedAt":"2026-09-27T17:00:00Z",
                "priceBefore":96.06,"priceAfter":103.88,"movePct":8.141,
            }],
            priceHistory=[
                {"time":"2026-09-27T16:59:59Z","eventId":"espn:401872950","eventType":"game","phase":"open","price":96.06},
                {"time":"2026-09-27T17:00:00Z","eventId":"espn:401872950","eventType":"game","phase":"close","price":103.88},
                {"time":"2026-09-27T21:59:30Z","eventId":"current-market-price","eventType":"market-observation","phase":"close","price":103.88},
                {"time":"2026-09-27T22:22:57Z","eventId":"current-market-price","eventType":"market-observation","phase":"close","price":112.33},
                {"time":"2026-09-27T22:52:15Z","eventId":"current-market-price","eventType":"market-observation","phase":"close","price":121.47},
            ],
        )
        migrated,changed=migrate_record(original,"2026-09-27T23:30:00Z")
        self.assertTrue(changed)
        observations=[p for p in migrated["priceHistory"] if p.get("eventId")=="current-market-price"]
        self.assertEqual(observations,[])
        game_points=[p for p in migrated["priceHistory"] if p.get("eventId")=="espn:401872950"]
        self.assertEqual(len(game_points),2)
        self.assertEqual(game_points[0]["price"],migrated["fundamentalValue"])
        self.assertEqual(game_points[1]["price"],migrated["marketPrice"])

    def test_old_pre_epoch_game_still_cannot_be_revived_by_v1_6(self):
        original=self.nfl_record(
            lastGameMovePct=6.0,
            lastPriceEventId="espn:old-game",
            lastPriceEventAt="2026-09-20T17:00:00Z",
            nflMarketMigrationVersion="1.5-nfl-career-tier-and-exactly-once-reset",
            nflMarketMigratedAt="2026-09-27T23:30:00Z",
            nflMarketMigrationPreservedEventId="espn:different-game",
            priceEvents=[{
                "eventKey":"espn:old-game","eventId":"old-game","eventType":"game",
                "league":"nfl","verified":True,"startedAt":"2026-09-20T17:00:00Z",
                "movePct":6.0,"performanceMovePct":6.05,"outcomeMovePct":-0.05,
                "actualPerformanceScore":18.0,"expectedPerformanceScore":3.0,
            }],
        )
        migrated,changed=migrate_record(original,"2026-09-28T05:30:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["lastGameMovePct"],0.0)
        self.assertNotIn("nflMarketMigrationPreservedEventId",migrated)

    def test_migration_is_idempotent(self):
        first, changed = migrate_record(self.nfl_record(), "2026-09-24T16:15:00Z")
        second, changed_again = migrate_record(first, "2026-09-25T16:15:00Z")

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second, first)
        points = [
            point for point in second["priceHistory"]
            if point.get("eventId") == MIGRATION_EVENT_ID
        ]
        self.assertEqual(len(points), 1)

    def test_nba_and_nhl_are_byte_for_byte_unchanged(self):
        for league in ("NBA", "NHL"):
            original = self.nfl_record(
                id=f"{league.lower()}-player",
                discipline="Basketball" if league == "NBA" else "Ice Hockey",
                leagueOrMedium=league,
            )
            migrated, changed = migrate_record(original, "2026-09-24T16:15:00Z")
            self.assertFalse(changed)
            self.assertEqual(migrated, original)
            self.assertNotIn("nflMarketMigrationVersion", migrated)


if __name__ == "__main__":
    unittest.main()
