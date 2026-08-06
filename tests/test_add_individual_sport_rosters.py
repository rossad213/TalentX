#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from add_individual_sport_rosters import TARGET_DISCIPLINES, expand_rosters, normalize, upsert_rosters

ROSTER_PATH = ROOT / "data" / "individual_sport_rosters.json"


class IndividualSportRosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
        cls.rosters = expand_rosters(cls.payload)

    def test_each_target_has_at_least_twenty_unique_source_backed_names(self):
        for discipline in TARGET_DISCIPLINES:
            items = self.rosters[discipline]
            self.assertGreaterEqual(len(items), 20, discipline)
            names = [normalize(item["name"]) for item in items]
            self.assertEqual(len(names), len(set(names)), discipline)
            for item in items:
                self.assertTrue(item.get("sourceName"), (discipline, item))
                self.assertTrue(str(item.get("sourceUrl", "")).startswith("https://"), (discipline, item))
                self.assertTrue(item.get("sourceAsOf"), (discipline, item))

    def test_upsert_is_idempotent_and_meets_minimums(self):
        first, summary = upsert_rosters([], self.payload)
        second, second_summary = upsert_rosters(first, self.payload)
        self.assertEqual(len(first), len(second))
        self.assertEqual(second_summary["netAdded"], 0)
        self.assertEqual(
            [(r["name"], r["discipline"], r.get("id"), r.get("ticker")) for r in first],
            [(r["name"], r["discipline"], r.get("id"), r.get("ticker")) for r in second],
        )
        for discipline in TARGET_DISCIPLINES:
            self.assertGreaterEqual(summary["countsAfter"][discipline], 20)

    def test_existing_evidence_enriched_record_is_preserved(self):
        existing = [{
            "id": "verified-jannik",
            "name": "Jannik Sinner",
            "ticker": "JSIN",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "sourceName": "Verified tennis feed",
            "pricingDataStatus": "Evidence enriched",
            "pricingConfidence": 0.96,
            "dataConfidence": 0.96,
            "activeMetrics": {
                "performance": 99, "achievements": 97, "consistency": 96,
                "potential": 90, "availability": 94, "audience": 95,
            },
            "pricingEvidence": [{"source": "test"}],
        }]
        updated, summary = upsert_rosters(existing, self.payload)
        record = next(item for item in updated if item["id"] == "verified-jannik")
        self.assertEqual(record["sourceName"], "Verified tennis feed")
        self.assertEqual(record["pricingDataStatus"], "Evidence enriched")
        self.assertEqual(record["pricingConfidence"], 0.96)
        self.assertEqual(record["activeMetrics"]["performance"], 99)
        self.assertEqual(record["rosterSourceName"], "ATP Singles Rankings")
        self.assertEqual(summary["changes"]["Tennis"]["added"], 19)

    def test_generated_ids_tickers_and_required_build_fields_are_unique(self):
        records, _ = upsert_rosters([], self.payload)
        ids = [record["id"] for record in records]
        tickers = [record["ticker"] for record in records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(tickers), len(set(tickers)))
        for record in records:
            for key in ("name", "primaryCategory", "discipline", "leagueOrMedium", "careerStatus", "marketSegment"):
                self.assertTrue(record.get(key), (record.get("name"), key))


if __name__ == "__main__":
    unittest.main()
