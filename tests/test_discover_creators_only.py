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

    def test_merge_prefers_specific_platform_role(self):
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

    def test_creator_metrics_are_bounded_and_complete(self):
        metrics = creator_metrics({"sitelinks": 150, "birthYear": 1998, "workStartYear": 2016})
        self.assertEqual(
            set(metrics),
            {"audience", "performance", "potential", "consistency", "achievements", "availability"},
        )
        for value in metrics.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)


if __name__ == "__main__":
    unittest.main()
