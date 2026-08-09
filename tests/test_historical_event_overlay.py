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
