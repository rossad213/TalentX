import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from reprice_creator_fundamentals import (  # noqa: E402
    CAREER_BASELINE_MIN_REFRESH_DAYS,
    CAREER_FUNDAMENTAL_WEIGHT,
    RECENT_PRODUCTION_WEIGHT,
    EVENT_HALF_LIFE_DAYS,
    UNVERIFIED_CURATED_SHRINK_WEIGHT,
    CREATOR_WEIGHTS,
    MODEL_VERSION,
    active_score,
    curated_creator_prior,
    event_multiplier,
    reprice_records,
    youtube_evidence_weight,
)


# Creator v2.3 career-baseline regression coverage
# Weekly career-baseline throttling is intentionally regression-tested.
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

    def test_creator_fundamental_is_authoritative_fair_value(self):
        records = [dict(self.records[0], fairValue=999.0, pricingV2={"genericFairValue": 12.34})]
        repriced, _ = reprice_records(records, self.manifest)
        creator = repriced[0]
        self.assertEqual(creator["fairValue"], creator["fundamentalValue"])
        self.assertEqual(
            creator["pricingV2"]["creatorAuthoritativeFundamental"],
            creator["fundamentalValue"],
        )
        self.assertEqual(
            creator["pricingV2"]["creatorAuthoritativeModelVersion"],
            MODEL_VERSION,
        )

    def test_source_discovered_creator_loses_curated_benchmark_metadata(self):
        records = [dict(
            self.records[2],
            benchmarkRank=999,
            benchmarkPoolSize=1000,
        )]
        repriced, _ = reprice_records(records, {})
        creator = repriced[0]
        self.assertNotIn("benchmarkRank", creator)
        self.assertNotIn("benchmarkPoolSize", creator)
        self.assertIn("Source-discovered", creator["rankingStatus"])

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

    def test_platform_aware_weighting_distinguishes_youtube_centrality(self):
        youtube = {
            "benchmarkRank": 1,
            "benchmarkPoolSize": 100,
            "teamOrPlatform": "YouTube",
        }
        mixed = {
            "benchmarkRank": 3,
            "benchmarkPoolSize": 100,
            "teamOrPlatform": "Twitch / YouTube",
        }
        secondary = {
            "benchmarkRank": 24,
            "benchmarkPoolSize": 100,
            "teamOrPlatform": "TikTok / Podcasting",
        }
        discovered = {
            "sourceNamespace": "wikidata-creator",
            "teamOrPlatform": "Digital platforms",
        }
        self.assertEqual(youtube_evidence_weight(youtube, True), 1.0)
        self.assertEqual(youtube_evidence_weight(mixed, True), 0.65)
        self.assertEqual(youtube_evidence_weight(secondary, True), 0.35)
        self.assertEqual(youtube_evidence_weight(discovered, False), 0.50)

    def test_secondary_youtube_does_not_overwrite_curated_creator_prior(self):
        records = [dict(
            self.records[0],
            name="Secondary Platform Creator",
            benchmarkRank=10,
            benchmarkPoolSize=100,
            teamOrPlatform="TikTok / Podcasting",
        )]
        weak_manifest = {
            "videoSnapshots": {
                "v1": {"recordId": "cur-large", "channelId": "UC-X", "views": 5_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "v2": {"recordId": "cur-large", "channelId": "UC-X", "views": 4_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "v3": {"recordId": "cur-large", "channelId": "UC-X", "views": 3_000, "publishedAt": "2026-08-27T12:00:00Z"},
            },
            "performanceState": {
                "cur-large:v1": {"effectiveRatio": 0.4, "checkedAt": "2026-09-15T12:00:00Z"},
            },
        }
        repriced, _ = reprice_records(records, weak_manifest)
        creator = repriced[0]
        policy = creator["creatorPlatformEvidencePolicy"]
        self.assertEqual(policy["youtubeCentralityWithinRecentSleeve"], 0.35)
        self.assertEqual(policy["recentProductionSleeveWeight"], RECENT_PRODUCTION_WEIGHT)
        self.assertEqual(policy["effectiveRecentYouTubeWeight"], 0.105)
        self.assertEqual(policy["effectiveCareerBaselineWeight"], 0.895)
        self.assertTrue(creator["pricingDataStatus"].startswith("Evidence enriched — persistent Creator career baseline"))
        self.assertGreater(creator["careerScore"], 55.0)

    def test_youtube_first_creator_remains_direct_evidence_driven(self):
        records = [dict(
            self.records[0],
            name="YouTube First Creator",
            benchmarkRank=1,
            benchmarkPoolSize=100,
            teamOrPlatform="YouTube",
        )]
        repriced, _ = reprice_records(records, self.manifest)
        creator = repriced[0]
        policy = creator["creatorPlatformEvidencePolicy"]
        self.assertEqual(policy["youtubeCentralityWithinRecentSleeve"], 1.0)
        self.assertEqual(policy["recentProductionSleeveWeight"], RECENT_PRODUCTION_WEIGHT)
        self.assertEqual(policy["effectiveRecentYouTubeWeight"], 0.30)
        self.assertEqual(policy["effectiveCareerBaselineWeight"], 0.70)
        self.assertTrue(creator["pricingDataStatus"].startswith("Evidence enriched — persistent Creator career baseline"))

    def test_creator_v23_uses_seventy_thirty_career_recent_split(self):
        self.assertEqual(CAREER_FUNDAMENTAL_WEIGHT, 0.70)
        self.assertEqual(RECENT_PRODUCTION_WEIGHT, 0.30)
        records = [dict(
            self.records[0],
            name="Stable YouTube Creator",
            benchmarkRank=5,
            benchmarkPoolSize=100,
            teamOrPlatform="YouTube",
        )]
        repriced, _ = reprice_records(records, self.manifest)
        creator = repriced[0]
        policy = creator["creatorPlatformEvidencePolicy"]
        self.assertEqual(policy["effectiveCareerBaselineWeight"], 0.70)
        self.assertEqual(policy["effectiveRecentYouTubeWeight"], 0.30)
        self.assertIn("creatorCareerBaselineMetrics", creator)

    def test_persisted_career_baseline_limits_one_refresh_slump(self):
        strong_baseline = {
            "audience": 90.0,
            "performance": 88.0,
            "achievements": 90.0,
            "potential": 80.0,
            "consistency": 88.0,
            "careerRunway": 80.0,
            "availability": 80.0,
        }
        records = [dict(
            self.records[0],
            name="Established Creator",
            teamOrPlatform="YouTube",
            creatorCareerBaselineMetrics=strong_baseline,
            creatorCareerBaselineVersion="creator-career-baseline-v1",
        )]
        weak_manifest = {
            "videoSnapshots": {
                "v1": {"recordId": "cur-large", "channelId": "UC-X", "views": 5_000, "publishedAt": "2026-09-10T12:00:00Z"},
                "v2": {"recordId": "cur-large", "channelId": "UC-X", "views": 4_000, "publishedAt": "2026-09-03T12:00:00Z"},
                "v3": {"recordId": "cur-large", "channelId": "UC-X", "views": 3_000, "publishedAt": "2026-08-27T12:00:00Z"},
            },
            "performanceState": {
                "cur-large:v1": {"effectiveRatio": 0.35, "checkedAt": "2026-09-15T12:00:00Z"},
            },
        }
        repriced, _ = reprice_records(records, weak_manifest)
        creator = repriced[0]
        self.assertGreater(creator["activeMetrics"]["audience"], 70.0)
        self.assertGreater(creator["careerScore"], 65.0)
        self.assertEqual(creator["creatorCareerBaselineSource"], "persisted verified career baseline")

    def test_discovered_non_youtube_creator_no_longer_gets_full_youtube_weight(self):
        records = [dict(
            self.records[0],
            name="Discovered Podcaster",
            teamOrPlatform="Podcast platforms",
        )]
        repriced, _ = reprice_records(records, self.manifest)
        creator = repriced[0]
        policy = creator["creatorPlatformEvidencePolicy"]
        self.assertFalse(policy["curatedPriorAvailable"])
        self.assertEqual(policy["youtubeCentralityWithinRecentSleeve"], 0.35)
        self.assertEqual(policy["effectiveRecentYouTubeWeight"], 0.105)
        self.assertEqual(policy["effectiveCareerBaselineWeight"], 0.895)

    def test_same_evidence_does_not_reapply_career_baseline_refresh(self):
        records = [dict(
            self.records[0],
            name="Persistent Creator",
            benchmarkRank=5,
            benchmarkPoolSize=100,
            teamOrPlatform="YouTube",
        )]
        first, _ = reprice_records(records, self.manifest)
        first_creator = first[0]
        second, _ = reprice_records(first, self.manifest)
        second_creator = second[0]
        self.assertEqual(
            first_creator["creatorCareerBaselineMetrics"],
            second_creator["creatorCareerBaselineMetrics"],
        )
        self.assertEqual(
            first_creator["creatorCareerBaselineUpdatedAt"],
            second_creator["creatorCareerBaselineUpdatedAt"],
        )
        self.assertEqual(CAREER_BASELINE_MIN_REFRESH_DAYS, 7)

    def test_creator_events_decay_toward_fundamental(self):
        record = {
            "priceEvents": [
                {"movePct": 10.0, "startedAt": "2026-09-30T00:00:00Z"},
                {"movePct": 10.0, "startedAt": "2026-09-20T00:00:00Z"},
            ]
        }
        as_of = __import__("datetime").datetime(2026, 9, 30, tzinfo=__import__("datetime").timezone.utc)
        multiplier = event_multiplier(record, as_of=as_of)
        # Today contributes 10%; a 10-day-old event contributes half its move.
        self.assertAlmostEqual(multiplier, 1.10 * 1.05, places=6)
        self.assertEqual(EVENT_HALF_LIFE_DAYS, 10.0)

    def test_historical_backfill_does_not_move_live_creator_price(self):
        record = {
            "priceEvents": [
                {"movePct": 50.0, "startedAt": "2026-09-30T00:00:00Z", "historicalBackfill": True}
            ]
        }
        as_of = __import__("datetime").datetime(2026, 9, 30, tzinfo=__import__("datetime").timezone.utc)
        self.assertEqual(event_multiplier(record, as_of=as_of), 1.0)

    def test_verified_2026_dhar_mann_anchor_strengthens_curated_prior(self):
        anchored = {
            "name": "Dhar Mann",
            "benchmarkRank": 17,
            "benchmarkPoolSize": 100,
            "age": 42,
        }
        unanchored = {
            "name": "Unanchored Creator",
            "benchmarkRank": 17,
            "benchmarkPoolSize": 100,
            "age": 42,
        }
        anchored_prior = curated_creator_prior(anchored)
        unanchored_prior = curated_creator_prior(unanchored)
        self.assertIsNotNone(anchored_prior)
        self.assertIsNotNone(unanchored_prior)
        self.assertGreater(active_score(anchored_prior), active_score(unanchored_prior))

    def test_missing_direct_evidence_is_shrunk_toward_neutral_elite_score(self):
        record = {
            "id": "cur-curated",
            "name": "Curated Missing Evidence",
            "primaryCategory": "Creator",
            "benchmarkRank": 1,
            "benchmarkPoolSize": 100,
            "teamOrPlatform": "Podcasting",
            "pricingConfidence": 0.70,
            "dataConfidence": 0.70,
            "marketPrice": 100.0,
            "priceEvents": [],
            "trend": [100.0] * 18,
        }
        repriced, _ = reprice_records([record], {})
        creator = repriced[0]
        self.assertIn("creatorEvidenceUncertainty", creator)
        self.assertEqual(
            creator["creatorEvidenceUncertainty"]["shrinkWeight"],
            UNVERIFIED_CURATED_SHRINK_WEIGHT,
        )
        self.assertLess(creator["careerScore"], 92.0)

    def test_verified_anchor_immediately_strengthens_persisted_dhar_baseline(self):
        record = {
            "id": "cur-dhar-mann",
            "name": "Dhar Mann",
            "primaryCategory": "Creator",
            "benchmarkRank": 17,
            "benchmarkPoolSize": 100,
            "teamOrPlatform": "YouTube",
            "pricingConfidence": 0.88,
            "dataConfidence": 0.88,
            "age": 42,
            "marketPrice": 120.0,
            "trend": [120.0] * 18,
            "priceEvents": [],
            "creatorCareerBaselineMetrics": {
                "audience": 76.0,
                "performance": 75.0,
                "achievements": 77.0,
                "potential": 70.0,
                "consistency": 76.0,
                "careerRunway": 60.0,
                "availability": 80.0,
            },
            "creatorCareerBaselineUpdatedAt": "2026-09-30T00:00:00Z",
        }
        repriced, _ = reprice_records([record], {})
        creator = repriced[0]
        self.assertEqual(
            creator["creatorCareerBaselineSource"],
            "persisted career baseline + verified 2026 career anchor",
        )
        self.assertGreater(creator["creatorCareerBaselineMetrics"]["audience"], 76.0)

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