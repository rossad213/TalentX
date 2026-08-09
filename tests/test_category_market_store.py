import tempfile
import unittest
from pathlib import Path

from scripts.category_market_store import extract_category, merge_category, primary_category


class CategoryMarketStoreTests(unittest.TestCase):
    def setUp(self):
        self.base = [
            {
                "id": "a1",
                "name": "Athlete One",
                "primaryCategory": "Athlete",
                "marketPrice": 100.0,
                "fundamentalValue": 95.0,
                "description": "fresh athlete metadata",
            },
            {
                "id": "m1",
                "name": "Music One",
                "primaryCategory": "Music",
                "marketPrice": 200.0,
                "fundamentalValue": 190.0,
                "description": "fresh music metadata",
            },
            {
                "id": "x1",
                "name": "Actor One",
                "primaryCategory": "Actor",
                "marketPrice": 150.0,
                "fundamentalValue": 145.0,
            },
        ]

    def test_aliases_map_to_primary_categories(self):
        self.assertEqual(primary_category("sports"), "Athlete")
        self.assertEqual(primary_category("music"), "Music")
        self.assertEqual(primary_category("actors"), "Actor")
        self.assertEqual(primary_category("creators"), "Creator")

    def test_extract_returns_only_owned_category(self):
        selected = extract_category(self.base, "music")
        self.assertEqual([item["id"] for item in selected], ["m1"])

    def test_replace_mode_replaces_only_target_category_record(self):
        overlay = [{
            "id": "m1",
            "name": "Music One",
            "primaryCategory": "Music",
            "marketPrice": 222.0,
            "fundamentalValue": 210.0,
            "description": "music-workflow metadata",
        }]
        merged, touched = merge_category(self.base, overlay, "music", "replace")
        self.assertEqual(touched, 1)
        by_id = {item["id"]: item for item in merged}
        self.assertEqual(by_id["m1"]["marketPrice"], 222.0)
        self.assertEqual(by_id["m1"]["description"], "music-workflow metadata")
        self.assertEqual(by_id["a1"]["marketPrice"], 100.0)

    def test_market_mode_preserves_fresh_baseline_metadata_and_fundamentals(self):
        overlay = [{
            "id": "m1",
            "name": "Music One old",
            "primaryCategory": "Music",
            "marketPrice": 221.0,
            "fundamentalValue": 170.0,
            "description": "stale metadata",
            "priceEvents": [{"eventKey": "release:1", "priceAfter": 221.0}],
            "priceHistory": [{"eventId": "release:1", "price": 221.0}],
            "lastPriceEventId": "release:1",
            "priceHistoryStatus": "verified",
            "dailyChange": 1.2,
        }]
        merged, touched = merge_category(self.base, overlay, "music", "market")
        self.assertEqual(touched, 1)
        record = next(item for item in merged if item["id"] == "m1")
        self.assertEqual(record["description"], "fresh music metadata")
        self.assertEqual(record["fundamentalValue"], 190.0)
        self.assertEqual(record["marketPrice"], 221.0)
        self.assertEqual(record["lastPriceEventId"], "release:1")
        self.assertEqual(record["priceHistoryStatus"], "verified")
        self.assertEqual(record["dailyChange"], 0.0)

    def test_wrong_category_overlay_is_rejected(self):
        overlay = [{"id": "a1", "primaryCategory": "Athlete", "marketPrice": 101.0}]
        with self.assertRaises(ValueError):
            merge_category(self.base, overlay, "music", "replace")


if __name__ == "__main__":
    unittest.main()
