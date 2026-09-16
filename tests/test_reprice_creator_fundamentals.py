import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from reprice_creator_fundamentals import (  # noqa: E402
    CREATOR_WEIGHTS,
    MODEL_VERSION,
    active_score,
    reprice_records,
)


class RepriceCreatorFundamentalsTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "id": "cur-large",
                "name": "Large Creator",
                "primaryCategory": "Creator",
                "sourceNamespace": "wikidata-creator",
                "pricingDataStatus": "Provisional — creator identity/activity evidence only; platform production unverified",
                "pricingConfidence": 0.40,
                "age": 26,
                "yearsActive": 8,
                "marketPrice": 50.0,
                "fundamentalValue": 50.0,
                "trend": [50.0] * 18,
                "priceEvents": [],
            },
            {
                "id": "cur-small",
                "name": "Small Creator",
                "primaryCategory": "Creator",
                "sourceNamespace": "wikidata-creator",
                "pricingDataStatus": "Provisional — creator identity/activity evidence only; platform production unverified",
                "pricingConfidence": 0.40,
                "age": 26,
                "yearsActive": 8,
                "marketPrice": 50.0,
                "fundamentalValue": 50.0,
                "trend": [50.0] * 18,
                "priceEvents": [],
            },
            {
                "id": "cur-provisional",
                "name": "Provisional Creator",
                "primaryCategory": "Creator",
                "sourceNamespace": "wikidata-creator",
                "pricingDataStatus": "Provisional — creator identity/activity evidence only; platform production unverified",
                "pricingConfidence": 0.44,
                "age": 26,
                "yearsActive": 8,
                "marketPrice": 50.0,
                "fundamentalValue": 50.0,
                "trend": [50.0] * 18,
                "priceEvents": [],
            },
        ]
        self.manifest = {
            "videoSnapshots": {
                "large-1": {"recordId": "cur-large", "channelId": "UC-LARGE", "views": 2_000_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "large-2": {"recordId": "cur-large", "channelId": "UC-LARGE", "views": 1_800_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "large-3": {"recordId": "cur-large", "channelId": "UC-LARGE", "views": 1_600_000, "publishedAt": "2026-08-27T12:00:00Z"},
                "large-4": {"recordId": "cur-large", "channelId": "UC-LARGE", "views": 1_500_000, "publishedAt": "2026-08-20T12:00:00Z"},
                "small-1": {"recordId": "cur-small", "channelId": "UC-SMALL", "views": 80_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "small-2": {"recordId": "cur-small", "channelId": "UC-SMALL", "views": 70_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "small-3": {"recordId": "cur-small", "channelId": "UC-SMALL", "views": 60_000, "publishedAt": "2026-08-27T12:00:00Z"},
                "small-4": {"recordId": "cur-small", "channelId": "UC-SMALL", "views": 55_000, "publishedAt": "2026-08-20T12:00:00Z"},
            },
            "performanceState": {
                "cur-large:large-1": {"effectiveRatio": 1.5, "checkedAt": "2026-09-15T12:00:00Z"},
                "cur-small:small-1": {"effectiveRatio": 0.8, "checkedAt": "2026-09-15T12:00:00Z"},
            },
        }

    def test_weights_match_creator_v2_policy(self):
        self.assertAlmostEqual(sum(CREATOR_WEIGHTS.values()), 1.0)
        self.assertEqual(CREATOR_WEIGHTS["audience"], 0.30)
        self.assertEqual(CREATOR_WEIGHTS["performance"], 0.25)
        self.assertEqual(CREATOR_WEIGHTS["achievements"], 0.20)
        self.assertEqual(CREATOR_WEIGHTS["potential"], 0.10)
        self.assertEqual(CREATOR_WEIGHTS["consistency"], 0.10)
        self.assertEqual(CREATOR_WEIGHTS["careerRunway"], 0.05)

    def test_verified_production_separates_creator_fundamentals(self):
        repriced, summary = reprice_records(self.records, self.manifest)
        by_id = {record["id"]: record for record in repriced}
        self.assertEqual(summary["verifiedProductionCount"], 2)
        self.assertGreater(by_id["cur-large"]["marketPrice"], by_id["cur-small"]["marketPrice"])
        self.assertGreater(by_id["cur-large"]["activeMetrics"]["audience"], by_id["cur-small"]["activeMetrics"]["audience"])
        self.assertGreater(by_id["cur-large"]["activeMetrics"]["potential"], by_id["cur-small"]["activeMetrics"]["potential"])
        self.assertEqual(by_id["cur-large"]["pricingModelVersion"], MODEL_VERSION)
        self.assertTrue(by_id["cur-large"]["pricingDataStatus"].startswith("Evidence enriched"))

    def test_unverified_creator_stays_low_confidence_provisional(self):
        repriced, _ = reprice_records(self.records, self.manifest)
        by_id = {record["id"]: record for record in repriced}
        provisional = by_id["cur-provisional"]
        self.assertTrue(provisional["pricingDataStatus"].startswith("Provisional"))
        self.assertLessEqual(provisional["pricingConfidence"], 0.50)
        self.assertEqual(provisional["activeMetrics"]["audience"], 50.0)
        self.assertEqual(provisional["activeMetrics"]["performance"], 50.0)

    def test_identical_evidence_can_legitimately_tie(self):
        records = [dict(self.records[0], id="a", name="A"), dict(self.records[0], id="b", name="B")]
        manifest = {
            "videoSnapshots": {
                "a1": {"recordId": "a", "channelId": "A", "views": 100_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "a2": {"recordId": "a", "channelId": "A", "views": 90_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "a3": {"recordId": "a", "channelId": "A", "views": 80_000, "publishedAt": "2026-08-27T12:00:00Z"},
                "b1": {"recordId": "b", "channelId": "B", "views": 100_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "b2": {"recordId": "b", "channelId": "B", "views": 90_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "b3": {"recordId": "b", "channelId": "B", "views": 80_000, "publishedAt": "2026-08-27T12:00:00Z"},
            },
            "performanceState": {
                "a:a1": {"effectiveRatio": 1.0, "checkedAt": "2026-09-15T12:00:00Z"},
                "b:b1": {"effectiveRatio": 1.0, "checkedAt": "2026-09-15T12:00:00Z"},
            },
        }
        repriced, _ = reprice_records(records, manifest)
        self.assertEqual(repriced[0]["marketPrice"], repriced[1]["marketPrice"])

    def test_active_score_uses_all_six_creator_components(self):
        metrics = {
            "audience": 100,
            "performance": 100,
            "achievements": 100,
            "potential": 100,
            "consistency": 100,
            "careerRunway": 100,
        }
        self.assertEqual(active_score(metrics), 100.0)


if __name__ == "__main__":
    unittest.main()