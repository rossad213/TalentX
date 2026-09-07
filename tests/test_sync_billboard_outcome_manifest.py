from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_billboard_outcome_manifest import release_work_qid, sync_manifest


class BillboardOutcomeManifestSyncTests(unittest.TestCase):
    def test_release_title_matches_verified_work_qid(self) -> None:
        record = {
            "priceEvents": [{
                "eventType": "music-release",
                "name": "BbY WOW — Single",
                "workQid": "Q12345",
            }]
        }
        self.assertEqual(release_work_qid(record, "BbY WOW"), "Q12345")

    def test_billboard_rank_updates_existing_chart_state(self) -> None:
        record = {
            "id": "cur-karol-g",
            "name": "Karol G",
            "primaryCategory": "Music",
            "priceEvents": [
                {
                    "eventType": "music-release",
                    "name": "BbY WOW — Single",
                    "workQid": "Q12345",
                },
                {
                    "eventKey": "billboard:global:2026-09-05:karol:bby-wow:5",
                    "eventType": "music-chart-outcome",
                    "chartProvider": "Billboard",
                    "chartSlug": "billboard-global-200",
                    "chartDate": "2026-09-05",
                    "chartRank": 5,
                    "releaseTitle": "BbY WOW",
                },
            ],
        }
        manifest, count = sync_manifest(
            [record],
            {"musicChartState": {}},
            datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(count, 1)
        state = manifest["musicChartState"]["0:Q12345"]
        self.assertEqual(state["bestRank"], 5)
        self.assertEqual(state["tier"], "top-5")
        self.assertEqual(state["provider"], "Billboard")
        self.assertGreater(state["targetMovePct"], 0)

    def test_same_state_is_not_rewritten(self) -> None:
        record = {
            "primaryCategory": "Music",
            "priceEvents": [
                {"eventType": "music-release", "name": "Song — Single", "workQid": "Q777"},
                {
                    "eventType": "music-chart-outcome",
                    "chartProvider": "Billboard",
                    "chartRank": 10,
                    "chartSlug": "billboard-global-200",
                    "chartDate": "2026-09-05",
                    "releaseTitle": "Song",
                },
            ],
        }
        manifest, first = sync_manifest([record], {"musicChartState": {}})
        manifest, second = sync_manifest([record], manifest)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)


if __name__ == "__main__":
    unittest.main()
