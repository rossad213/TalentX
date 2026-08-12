import json
import tempfile
import unittest
from pathlib import Path

from scripts.category_market_store import (
    dedupe_tickers,
    extract_category,
    finalize_catalog,
    merge_category,
    primary_category,
    resolve_cross_category_identities,
)


class CategoryMarketStoreTests(unittest.TestCase):
    def setUp(self):
        self.base = [
            {
                "id": "a1",
                "name": "Athlete One",
                "ticker": "ONE",
                "primaryCategory": "Athlete",
                "discipline": "Basketball",
                "marketPrice": 100.0,
                "fundamentalValue": 95.0,
                "description": "fresh athlete metadata",
                "sourceNamespace": "espn",
                "marketSegment": "Current",
            },
            {
                "id": "m1",
                "name": "Music One",
                "ticker": "MONE",
                "primaryCategory": "Music",
                "marketPrice": 200.0,
                "fundamentalValue": 190.0,
                "description": "fresh music metadata",
            },
            {
                "id": "x1",
                "name": "Actor One",
                "ticker": "AONE",
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

    def test_duplicate_tickers_are_repaired_deterministically(self):
        records = [dict(item) for item in self.base]
        records[1]["ticker"] = "ONE"
        first_repairs = dedupe_tickers(records)
        self.assertEqual(records[0]["ticker"], "ONE")
        self.assertNotEqual(records[1]["ticker"], "ONE")
        self.assertEqual(len({item["ticker"] for item in records}), len(records))
        replacement = records[1]["ticker"]
        self.assertEqual(len(first_repairs), 1)

        rerun = [dict(item) for item in self.base]
        rerun[1]["ticker"] = "ONE"
        dedupe_tickers(rerun)
        self.assertEqual(rerun[1]["ticker"], replacement)

    def test_verified_athlete_wins_cross_category_identity(self):
        records = [
            {
                "id": "nba-lebron",
                "name": "LeBron James",
                "ticker": "LEBJ",
                "primaryCategory": "Athlete",
                "discipline": "Basketball",
                "leagueOrMedium": "NBA",
                "teamOrPlatform": "Los Angeles Lakers",
                "role": "Forward",
                "marketSegment": "Current",
                "sourceNamespace": "espn",
                "dataConfidence": 0.95,
                "pricingConfidence": 0.90,
                "careerScore": 93.0,
                "searchText": "lebron james athlete basketball nba los angeles lakers forward",
            },
            {
                "id": "actor-lebron",
                "name": "LeBron James",
                "ticker": "LEBA",
                "primaryCategory": "Actor",
                "discipline": "Television",
                "leagueOrMedium": "Film & Television",
                "teamOrPlatform": "Screen",
                "role": "Actor",
                "marketSegment": "Current",
                "sourceName": "Curated screen roster",
                "sourceUrl": "https://example.test/lebron-screen",
            },
        ]

        resolved, repairs = resolve_cross_category_identities(records)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(len(repairs), 1)
        winner = resolved[0]
        self.assertEqual(winner["id"], "nba-lebron")
        self.assertEqual(winner["primaryCategory"], "Athlete")
        self.assertEqual(winner["secondaryCategories"], ["Actor"])
        self.assertEqual(winner["secondaryCareerActivities"][0]["discipline"], "Television")
        self.assertIn("film & television", winner["searchText"])

    def test_same_name_without_verified_athlete_is_not_auto_merged(self):
        records = [
            {"id": "music-sam", "name": "Sam Lee", "primaryCategory": "Music"},
            {"id": "actor-sam", "name": "Sam Lee", "primaryCategory": "Actor"},
        ]
        resolved, repairs = resolve_cross_category_identities(records)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(repairs, [])

    def test_finalize_refreshes_csv_manifest_and_tickers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "current_catalog.json"
            csv_path = root / "current_catalog.csv"
            manifest = root / "catalog_manifest.json"
            records = [dict(item) for item in self.base]
            records[1]["ticker"] = "ONE"
            catalog.write_text(json.dumps(records), encoding="utf-8")
            manifest.write_text(json.dumps({"currentCatalogRecords": 999}), encoding="utf-8")

            count, repaired = finalize_catalog(catalog, csv_path, manifest)
            self.assertEqual(count, 3)
            self.assertEqual(repaired, 1)
            finalized = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertEqual(len({item["ticker"] for item in finalized}), 3)
            updated_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(updated_manifest["currentCatalogRecords"], 3)
            self.assertEqual(updated_manifest["totalRecords"], 3)
            self.assertEqual(updated_manifest["tickerCollisionRepairs"], 1)
            self.assertEqual(updated_manifest["crossCategoryIdentityRepairs"], 0)
            self.assertEqual(updated_manifest["categories"]["Music"], 1)
            self.assertEqual(updated_manifest["automatedRosterVerifiedRecords"], 1)
            self.assertTrue(csv_path.exists())


if __name__ == "__main__":
    unittest.main()
