import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from discover_creators_only import (  # noqa: E402
    creator_candidate_is_eligible,
    creator_confidence,
    creator_metrics,
    merge_creator_candidates,
)


class DiscoverCreatorsOnlyTests(unittest.TestCase):
    def test_creator_proxy_accepts_internet_native_profile_without_work_start(self):
        current = datetime.now(timezone.utc).year
        candidate = {
            "qid": "Q123",
            "name": "Example Creator",
            "sitelinks": 8,
            "birthYear": current - 25,
            "workStartYear": None,
            "workEndYear": None,
        }
        self.assertTrue(
            creator_candidate_is_eligible(
                candidate,
                minimum_sitelinks=3,
                recent_cutoff=current - 3,
            )
        )

    def test_creator_proxy_rejects_stale_work_end(self):
        current = datetime.now(timezone.utc).year
        candidate = {
            "qid": "Q123",
            "name": "Former Creator",
            "sitelinks": 30,
            "birthYear": current - 35,
            "workStartYear": current - 10,
            "workEndYear": current - 8,
        }
        self.assertFalse(
            creator_candidate_is_eligible(
                candidate,
                minimum_sitelinks=3,
                recent_cutoff=current - 3,
            )
        )

    def test_merge_uses_single_unambiguous_specific_platform(self):
        rows = [
            {
                "qid": "Q1",
                "name": "Creator A",
                "sitelinks": 100,
                "discipline": "Social Media",
                "role": "Social media influencer",
                "platform": "Social platforms",
                "country": "Not listed",
                "birthYear": None,
                "workStartYear": None,
                "workEndYear": None,
            },
            {
                "qid": "Q1",
                "name": "Creator A",
                "sitelinks": 100,
                "discipline": "YouTube",
                "role": "YouTuber",
                "platform": "YouTube",
                "country": "United States",
                "birthYear": 1995,
                "workStartYear": 2015,
                "workEndYear": None,
            },
        ]
        merged = merge_creator_candidates(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["discipline"], "YouTube")
        self.assertEqual(merged[0]["platform"], "YouTube")
        self.assertEqual(merged[0]["country"], "United States")
        self.assertEqual(len(merged[0]["creatorOccupationEvidence"]), 2)

    def test_merge_does_not_invent_primary_platform_when_specific_sources_conflict(self):
        rows = [
            {
                "qid": "Q2",
                "name": "Creator B",
                "sitelinks": 80,
                "discipline": "YouTube",
                "role": "YouTuber",
                "platform": "YouTube",
                "country": "United States",
                "birthYear": 1998,
                "workStartYear": 2018,
                "workEndYear": None,
            },
            {
                "qid": "Q2",
                "name": "Creator B",
                "sitelinks": 80,
                "discipline": "Twitch",
                "role": "Twitch streamer",
                "platform": "Twitch",
                "country": "United States",
                "birthYear": 1998,
                "workStartYear": 2018,
                "workEndYear": None,
            },
        ]
        merged = merge_creator_candidates(rows)
        self.assertEqual(merged[0]["discipline"], "Digital Content")
        self.assertEqual(merged[0]["role"], "Multi-platform creator")
        self.assertEqual(merged[0]["platform"], "Digital platforms")

    def test_creator_metrics_do_not_use_sitelinks_as_production(self):
        current = datetime.now(timezone.utc).year
        low = creator_metrics({"sitelinks": 3, "birthYear": current - 27, "workStartYear": current - 8})
        high = creator_metrics({"sitelinks": 1000, "birthYear": current - 27, "workStartYear": current - 8})
        self.assertEqual(low, high)
        self.assertEqual(low["audience"], 50.0)
        self.assertEqual(low["performance"], 50.0)
        self.assertEqual(low["achievements"], 50.0)
        self.assertEqual(low["potential"], 50.0)

    def test_creator_metrics_are_bounded_and_include_runway(self):
        metrics = creator_metrics({"sitelinks": 150, "birthYear": 1998, "workStartYear": 2016})
        self.assertEqual(
            set(metrics),
            {"audience", "performance", "potential", "consistency", "achievements", "careerRunway", "availability"},
        )
        for value in metrics.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_provisional_confidence_stays_low_without_platform_production(self):
        candidate = {
            "sitelinks": 500,
            "birthYear": 1998,
            "workStartYear": 2016,
            "country": "United States",
            "creatorOccupationEvidence": [
                {"discipline": "YouTube", "role": "YouTuber", "platform": "YouTube"},
                {"discipline": "Social Media", "role": "Social media influencer", "platform": "Social platforms"},
            ],
        }
        confidence = creator_confidence(candidate)
        self.assertGreaterEqual(confidence, 0.32)
        self.assertLessEqual(confidence, 0.50)


if __name__ == "__main__":
    unittest.main()