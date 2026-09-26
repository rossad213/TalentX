from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_nfl_verified_event_history import (
    BACKFILL_VERSION,
    apply_backfill,
    point_in_time_item,
    reconstruct_backfill_chain,
    replay_history_points,
    needs_backfill,
)


class NFLVerifiedHistoryBackfillTests(unittest.TestCase):
    def record(self, **updates):
        base = {
            "id": "live-espn-football-1",
            "name": "Example Player",
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "1",
            "role": "Defensive End",
            "marketPrice": 150.0,
            "previousMarketPrice": 148.0,
            "fairValue": 152.0,
            "fundamentalValue": 152.0,
            "dailyChange": 1.35,
            "hourlyChangePct": 1.35,
            "lastPriceEventId": "espn:live",
            "lastPriceEventAt": "2026-09-20T17:00:00Z",
            "lastPriceEvent": "Live game",
            "lastGameMovePct": 1.35,
            "priceEvents": [],
            "priceHistory": [],
        }
        base.update(updates)
        return base

    def test_point_in_time_item_excludes_future_same_season_and_later_seasons(self):
        record = self.record()
        base_item = {
            "ok": True,
            "nflSeasonStats": {
                2024: {"gamesplayed": 17, "sacks": 12},
                2025: {"gamesplayed": 17, "sacks": 16},
                2026: {"gamesplayed": 2, "sacks": 3},
            },
            "career": {"gamesplayed": 80, "sacks": 70},
            "recent": {"gamesplayed": 2, "sacks": 3},
            "signals": {"recentProduction": 999},
        }
        prior_event = {
            "startedAt": "2025-09-07T17:00:00Z",
            "stats": {"sacks": 2, "totalTackles": 5},
        }
        item, prior_games = point_in_time_item(record, base_item, [prior_event], 2025)
        self.assertEqual(set(item["nflSeasonStats"]), {2024, 2025})
        self.assertEqual(item["nflSeasonStats"][2025]["gamesplayed"], 1.0)
        self.assertEqual(item["nflSeasonStats"][2025]["sacks"], 2.0)
        self.assertNotEqual(item["signals"].get("recentProduction"), 999)
        self.assertEqual(prior_games, 18)

    def test_replay_percentages_exactly_match_rounded_chart_prices(self):
        record = self.record(marketPrice=111.0)
        generated = [
            {
                "eventKey": "espn:g1",
                "eventId": "g1",
                "startedAt": "2025-09-01T17:00:00Z",
                "name": "Game 1",
                "modelMovePct": 10.0,
                "historicalBackfill": True,
            },
            {
                "eventKey": "espn:g2",
                "eventId": "g2",
                "startedAt": "2025-09-08T17:00:00Z",
                "name": "Game 2",
                "modelMovePct": -5.0,
                "historicalBackfill": True,
            },
        ]
        live = [{
            "eventKey": "espn:live",
            "eventId": "live",
            "startedAt": "2025-09-15T17:00:00Z",
            "priceBefore": 100.0,
            "priceAfter": 101.0,
            "movePct": 1.0,
        }]
        replay, anchor = reconstruct_backfill_chain(record, generated, live)
        self.assertEqual(anchor, "earliest-recorded-live-event")
        self.assertEqual(replay[-1]["priceAfter"], 100.0)
        for event in replay:
            calculated = round(
                (event["priceAfter"] / event["priceBefore"] - 1.0) * 100.0,
                3,
            )
            self.assertEqual(event["movePct"], calculated)
            self.assertEqual(event["chartMovePct"], calculated)
            self.assertTrue(event["chartCorrelationVerified"])

        points = replay_history_points(replay)
        self.assertEqual(len(points), 4)
        for event in replay:
            pair = [point for point in points if point["eventId"] == event["eventKey"]]
            self.assertEqual([point["phase"] for point in pair], ["open", "close"])
            self.assertEqual(pair[0]["price"], event["priceBefore"])
            self.assertEqual(pair[1]["price"], event["priceAfter"])
            self.assertEqual(pair[1]["movePct"], event["movePct"])

    def test_backfill_preserves_today_market_and_live_event_state(self):
        live_event = {
            "eventKey": "espn:live",
            "eventId": "live",
            "startedAt": "2026-09-20T17:00:00Z",
            "priceBefore": 148.0,
            "priceAfter": 150.0,
            "movePct": 1.351,
            "eventType": "game",
        }
        old_backfill = {
            "eventKey": "espn:old-backfill",
            "eventId": "old-backfill",
            "startedAt": "2025-09-01T17:00:00Z",
            "priceBefore": 90.0,
            "priceAfter": 91.0,
            "movePct": 1.111,
            "eventType": "game",
            "historicalBackfill": True,
            "backfillModel": "historical-game-events-v2",
        }
        record = self.record(
            priceEvents=[old_backfill, live_event],
            priceHistory=[
                {
                    "time": "2025-09-01T17:00:00Z",
                    "price": 91.0,
                    "eventId": "espn:old-backfill",
                    "phase": "close",
                    "source": "verified-historical-game-backfill",
                },
                {
                    "time": "2026-09-20T17:00:00Z",
                    "price": 150.0,
                    "eventId": "espn:live",
                    "phase": "close",
                    "historyType": "verified",
                },
            ],
        )
        generated = [{
            "eventKey": "espn:g1",
            "eventId": "g1",
            "startedAt": "2025-10-01T17:00:00Z",
            "name": "Historical game",
            "modelMovePct": 2.0,
            "historicalBackfill": True,
            "priceBasis": "test",
        }]

        updated, added = apply_backfill(
            record,
            generated,
            completed_at="2026-09-26T08:00:00Z",
            days=400,
        )
        self.assertEqual(added, 1)
        for key in (
            "marketPrice",
            "previousMarketPrice",
            "fairValue",
            "fundamentalValue",
            "dailyChange",
            "hourlyChangePct",
            "lastPriceEventId",
            "lastPriceEventAt",
            "lastPriceEvent",
            "lastGameMovePct",
        ):
            self.assertEqual(updated.get(key), record.get(key))

        keys = {event["eventKey"] for event in updated["priceEvents"]}
        self.assertEqual(keys, {"espn:g1", "espn:live"})
        self.assertEqual(updated["nflHistoricalBackfillVersion"], BACKFILL_VERSION)
        self.assertFalse(any(
            point.get("source") == "verified-historical-game-backfill"
            for point in updated["priceHistory"]
        ))
        self.assertTrue(any(
            point.get("eventId") == "espn:live"
            for point in updated["priceHistory"]
        ))

    def test_non_nfl_and_completed_version_are_not_selected(self):
        self.assertFalse(needs_backfill({
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NBA",
            "sourceNamespace": "espn",
            "sourceRecordId": "1",
        }))
        self.assertFalse(needs_backfill(self.record(nflHistoricalBackfillVersion=BACKFILL_VERSION)))
        self.assertTrue(needs_backfill(self.record()))


if __name__ == "__main__":
    unittest.main()
