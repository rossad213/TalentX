from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from append_price_history import append_record_history


class AppendPriceHistoryTests(unittest.TestCase):
    def test_full_nfl_replay_status_survives_append(self):
        record = {
            "id": "nfl-1",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "marketPrice": 204.39,
            "previousMarketPrice": 199.21,
            "lastPriceRefreshAt": "2026-09-26T16:30:00Z",
            "priceHistoryStatus": "source-backed-full-point-in-time-nfl-replay",
            "priceHistory": [
                {
                    "time": "2026-09-21T00:19:59Z",
                    "price": 199.21,
                    "eventId": "espn:week2",
                    "phase": "open",
                    "historyType": "verified-event-replay",
                    "source": "verified-nfl-event-replay",
                },
                {
                    "time": "2026-09-21T00:20:00Z",
                    "price": 204.39,
                    "eventId": "espn:week2",
                    "phase": "close",
                    "historyType": "verified-event-replay",
                    "source": "verified-nfl-event-replay",
                },
            ],
        }
        updated, _ = append_record_history(
            record,
            datetime(2026, 9, 26, 16, 31, tzinfo=timezone.utc),
        )
        self.assertEqual(
            updated["priceHistoryStatus"],
            "source-backed-full-point-in-time-nfl-replay",
        )
        replay = [
            point for point in updated["priceHistory"]
            if point.get("source") == "verified-nfl-event-replay"
        ]
        self.assertEqual(len(replay), 2)

    def test_music_current_observation_only_is_not_labeled_source_backed(self):
        record={"id":"music-1","primaryCategory":"Music","marketPrice":120.0,
                "lastPriceRefreshAt":"2026-09-26T16:30:00Z","priceHistory":[],"priceEvents":[]}
        updated,_=append_record_history(record,datetime(2026,9,26,16,31,tzinfo=timezone.utc))
        self.assertEqual(updated["priceHistoryStatus"],"market-observation-only")

    def test_music_verified_event_history_is_labeled_source_backed_partial(self):
        record={"id":"music-2","primaryCategory":"Music","marketPrice":125.0,"previousMarketPrice":120.0,
                "lastPriceEventAt":"2026-09-20T00:00:00Z","lastPriceEventId":"billboard:1",
                "priceEvents":[{"eventKey":"billboard:1","eventType":"music-chart-outcome","verified":True,
                                "startedAt":"2026-09-20T00:00:00Z","priceBefore":120.0,"priceAfter":125.0}],
                "priceHistory":[]}
        updated,_=append_record_history(record,datetime(2026,9,26,16,31,tzinfo=timezone.utc))
        self.assertEqual(updated["priceHistoryStatus"],"source-backed-partial-history")

    def test_actor_does_not_recreate_generic_recorded_event_from_stale_last_event_metadata(self):
        record={
            "id":"actor-stale","primaryCategory":"Actor","marketPrice":105.15,
            "previousMarketPrice":170.19,
            "lastPriceEventAt":"2026-08-24T18:58:33Z",
            "lastPriceEventId":"wikidata:award:Q43387663:Q2089918",
            "lastPriceEvent":"Award: Volpi Cup for Best Actress",
            "priceEvents":[],
            "priceHistory":[],
        }
        updated,_=append_record_history(
            record,
            datetime(2026,9,28,23,45,tzinfo=timezone.utc),
        )
        self.assertFalse(any(
            point.get("eventType")=="recorded-event"
            for point in updated["priceHistory"]
        ))

    def test_actor_purges_existing_legacy_recorded_event_history(self):
        record={
            "id":"actor-old","primaryCategory":"Actor","marketPrice":105.15,
            "priceEvents":[],
            "priceHistory":[{
                "time":"2026-08-24T18:58:33Z",
                "price":170.19,
                "eventId":"wikidata:award:Q43387663:Q2089918",
                "label":"Award: Volpi Cup for Best Actress",
                "phase":"close",
                "historyType":"verified",
                "eventType":"recorded-event",
            }],
        }
        updated,_=append_record_history(
            record,
            datetime(2026,9,28,23,45,tzinfo=timezone.utc),
        )
        self.assertFalse(any(
            point.get("eventType")=="recorded-event"
            for point in updated["priceHistory"]
        ))

    def test_actor_clears_stale_last_event_pointer_when_event_is_not_in_durable_ledger(self):
        record={
            "id":"actor-pointer","primaryCategory":"Actor","marketPrice":105.15,
            "lastPriceEventAt":"2026-08-24T18:58:33Z",
            "lastPriceEventId":"wikidata:award:Q43387663:Q2089918",
            "lastPriceEvent":"Award: stale",
            "lastEventMovePct":1.0,
            "lastEventType":"award",
            "lastEventSource":"Wikidata",
            "priceEvents":[],
            "priceHistory":[],
        }
        updated,_=append_record_history(
            record,
            datetime(2026,9,28,23,50,tzinfo=timezone.utc),
        )
        for field in (
            "lastPriceEventAt","lastPriceEventId","lastPriceEvent",
            "lastEventMovePct","lastEventType","lastEventSource",
        ):
            self.assertNotIn(field,updated)

    def test_actor_current_observation_only_is_not_labeled_source_backed(self):
        record={
            "id":"actor-1","primaryCategory":"Actor","marketPrice":120.0,
            "lastPriceRefreshAt":"2026-09-26T16:30:00Z","priceHistory":[],"priceEvents":[],
        }
        updated,_=append_record_history(record,datetime(2026,9,26,16,31,tzinfo=timezone.utc))
        self.assertEqual(updated["priceHistoryStatus"],"market-observation-only")

    def test_actor_model_migration_point_alone_is_not_source_backed_event_history(self):
        record={
            "id":"actor-migration-only","primaryCategory":"Actor","marketPrice":85.0,
            "lastPriceRefreshAt":"2026-09-28T22:00:00Z",
            "priceEvents":[],
            "priceHistory":[{
                "time":"2026-09-28T22:00:00Z",
                "price":85.0,
                "eventId":"model:actor-source-first-market-epoch-v1",
                "phase":"close",
                "historyType":"verified",
                "eventType":"model_migration",
            }],
        }
        updated,_=append_record_history(
            record,datetime(2026,9,28,22,1,tzinfo=timezone.utc)
        )
        self.assertEqual(updated["priceHistoryStatus"],"market-observation-only")

    def test_actor_verified_event_history_is_labeled_source_backed_partial(self):
        record={
            "id":"actor-2","primaryCategory":"Actor","marketPrice":125.0,"previousMarketPrice":120.0,
            "lastPriceEventAt":"2026-09-20T00:00:00Z","lastPriceEventId":"box:1",
            "priceEvents":[{
                "eventKey":"box:1","eventType":"actor-box-office-outcome","verified":True,
                "startedAt":"2026-09-20T00:00:00Z","priceBefore":120.0,"priceAfter":125.0,
            }],
            "priceHistory":[],
        }
        updated,_=append_record_history(record,datetime(2026,9,26,16,31,tzinfo=timezone.utc))
        self.assertEqual(updated["priceHistoryStatus"],"source-backed-partial-history")

    def test_non_nfl_history_keeps_normal_verified_status(self):
        record = {
            "id": "nba-1",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NBA",
            "marketPrice": 100.0,
            "lastPriceRefreshAt": "2026-09-26T16:30:00Z",
            "priceHistory": [],
        }
        updated, _ = append_record_history(
            record,
            datetime(2026, 9, 26, 16, 31, tzinfo=timezone.utc),
        )
        self.assertEqual(updated["priceHistoryStatus"], "verified")


if __name__ == "__main__":
    unittest.main()
