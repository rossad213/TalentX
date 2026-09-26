import unittest

from scripts.merge_historical_event_overlay import merge_catalog


class HistoricalEventOverlayTests(unittest.TestCase):
    def test_imports_history_without_changing_live_market_fields(self):
        base = [{
            "id": "m1",
            "name": "Artist One",
            "primaryCategory": "Music",
            "marketPrice": 105.0,
            "previousMarketPrice": 100.0,
            "dailyChange": 5.0,
            "lastPriceEventId": "live:1",
            "priceExplanation": {"headline": "Live move"},
            "priceEvents": [{
                "eventKey": "live:1",
                "eventType": "music-release",
                "startedAt": "2026-08-01T12:00:00Z",
                "movePct": 5.0,
                "priceBefore": 100.0,
                "priceAfter": 105.0,
                "verified": True,
            }],
        }]
        overlay = [{
            "id": "m1",
            "primaryCategory": "Music",
            "marketPrice": 999.0,
            "priceHistoryBackfillDays": 365,
            "priceHistoryBackfilledAt": "2026-08-09T18:00:00Z",
            "priceEvents": [{
                "eventKey": "history:1",
                "eventType": "music-release",
                "startedAt": "2026-02-01T12:00:00Z",
                "movePct": 2.0,
                "verified": True,
                "historicalBackfill": True,
            }],
        }]
        merged, touched, imported = merge_catalog(base, overlay, "Music")
        self.assertEqual(touched, 1)
        self.assertEqual(imported, 1)
        record = merged[0]
        self.assertEqual(record["marketPrice"], 105.0)
        self.assertEqual(record["previousMarketPrice"], 100.0)
        self.assertEqual(record["dailyChange"], 5.0)
        self.assertEqual(record["lastPriceEventId"], "live:1")
        self.assertEqual(record["priceExplanation"], {"headline": "Live move"})
        events = {event["eventKey"]: event for event in record["priceEvents"]}
        self.assertEqual(events["live:1"]["priceAfter"], 105.0)
        self.assertAlmostEqual(events["history:1"]["priceAfter"], events["live:1"]["priceBefore"], places=2)
        self.assertEqual(record["priceHistoryBackfillDays"], 365)

    def test_live_event_wins_duplicate_key(self):
        base = [{
            "id": "a1",
            "primaryCategory": "Athlete",
            "marketPrice": 101.0,
            "priceEvents": [{
                "eventKey": "game:1",
                "startedAt": "2026-07-01T12:00:00Z",
                "movePct": 1.0,
                "name": "Live authoritative game",
                "verified": True,
            }],
        }]
        overlay = [{
            "id": "a1",
            "primaryCategory": "Athlete",
            "priceEvents": [{
                "eventKey": "game:1",
                "startedAt": "2026-07-01T12:00:00Z",
                "movePct": 9.0,
                "name": "Historical duplicate",
                "historicalBackfill": True,
                "verified": True,
            }],
        }]
        merged, touched, imported = merge_catalog(base, overlay, "sports")
        self.assertEqual(touched, 0)
        self.assertEqual(imported, 0)
        self.assertEqual(merged[0]["priceEvents"][0]["name"], "Live authoritative game")


    def test_nfl_point_in_time_overlay_preserves_live_prices_and_chart_move(self):
        base = [{
            "id": "n1",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "marketPrice": 151.5,
            "previousMarketPrice": 150.0,
            "dailyChange": 1.0,
            "lastPriceEventId": "espn:live",
            "priceHistory": [{
                "time": "2026-09-20T17:00:00Z",
                "price": 151.5,
                "eventId": "espn:live",
                "phase": "close",
                "historyType": "verified",
            }],
            "priceEvents": [{
                "eventKey": "espn:live",
                "eventId": "live",
                "eventType": "game",
                "startedAt": "2026-09-20T17:00:00Z",
                "movePct": 1.0,
                "priceBefore": 150.0,
                "priceAfter": 151.5,
                "verified": True,
            }],
        }]
        overlay = [{
            "id": "n1",
            "primaryCategory": "Athlete",
            "leagueOrMedium": "NFL",
            "nflHistoricalBackfillVersion": "1.0-nfl-point-in-time-event-replay",
            "nflHistoricalBackfillDays": 1095,
            "priceEvents": [{
                "eventKey": "espn:hist",
                "eventId": "hist",
                "eventType": "game",
                "startedAt": "2025-09-01T17:00:00Z",
                "modelMovePct": 2.0,
                "movePct": 2.0,
                "priceBefore": 147.06,
                "priceAfter": 150.0,
                "verified": True,
                "historicalBackfill": True,
                "historicalExpectationMode": "point-in-time-pre-game",
            }],
        }]
        merged, touched, imported = merge_catalog(base, overlay, "sports")
        self.assertEqual((touched, imported), (1, 1))
        record = merged[0]
        self.assertEqual(record["marketPrice"], 151.5)
        self.assertEqual(record["previousMarketPrice"], 150.0)
        self.assertEqual(record["lastPriceEventId"], "espn:live")
        events = {event["eventKey"]: event for event in record["priceEvents"]}
        self.assertEqual(events["espn:live"]["priceBefore"], 150.0)
        self.assertEqual(events["espn:live"]["priceAfter"], 151.5)
        self.assertEqual(events["espn:hist"]["priceAfter"], 150.0)
        calculated = round(
            (events["espn:hist"]["priceAfter"] / events["espn:hist"]["priceBefore"] - 1.0) * 100.0,
            3,
        )
        self.assertEqual(events["espn:hist"]["movePct"], calculated)
        self.assertEqual(events["espn:hist"]["chartMovePct"], calculated)
        self.assertTrue(events["espn:hist"]["chartCorrelationVerified"])
        self.assertEqual(record["nflHistoricalBackfillDays"], 1095)
        self.assertTrue(any(point.get("eventId") == "espn:live" for point in record["priceHistory"]))

    def test_other_categories_are_untouched(self):
        base = [{"id": "x1", "primaryCategory": "Actor", "marketPrice": 88.0}]
        overlay = [{
            "id": "x1",
            "primaryCategory": "Actor",
            "priceEvents": [{
                "eventKey": "history:actor",
                "startedAt": "2026-01-01T12:00:00Z",
                "movePct": 0.3,
                "historicalBackfill": True,
                "verified": True,
            }],
        }]
        merged, touched, imported = merge_catalog(base, overlay, "Music")
        self.assertEqual((touched, imported), (0, 0))
        self.assertEqual(merged, base)


if __name__ == "__main__":
    unittest.main()
