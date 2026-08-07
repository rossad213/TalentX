#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from expand_individual_sport_sources import (
    RECENT_ACTIVITY_YEARS,
    SPORT_CONFIG,
    candidate_is_eligible,
    merge_candidates,
    top_up_records,
)


def candidate(name: str, qid: str, discipline: str, sitelinks: int = 12, birth_year: int | None = None):
    current = datetime.now(timezone.utc).year
    config = SPORT_CONFIG[discipline]
    if birth_year is None:
        birth_year = current - min(28, int(config["maxAge"]) - 1)
    return {
        "name": name,
        "qid": qid,
        "discipline": discipline,
        "role": next(iter(config["occupations"].values())),
        "sitelinks": sitelinks,
        "birthYear": birth_year,
        "workStartYear": current - 6,
        "workEndYear": None,
        "country": "Testland",
    }


class IndividualSportDiscoveryTests(unittest.TestCase):
    def test_eligibility_enforces_age_source_and_recent_end(self):
        current = datetime.now(timezone.utc).year
        recent = current - RECENT_ACTIVITY_YEARS
        good = candidate("Current Player", "Q100", "Tennis")
        self.assertTrue(candidate_is_eligible(good, 2, recent))
        too_old = {**good, "qid": "Q101", "birthYear": current - 60}
        self.assertFalse(candidate_is_eligible(too_old, 2, recent))
        retired = {**good, "qid": "Q102", "workEndYear": recent - 1}
        self.assertFalse(candidate_is_eligible(retired, 2, recent))
        weak = {**good, "qid": "Q103", "workStartYear": None, "sitelinks": 2}
        self.assertFalse(candidate_is_eligible(weak, 2, recent))

    def test_top_up_reaches_target_without_duplicates(self):
        candidates = {
            discipline: [candidate(f"{discipline} Player {i}", f"Q{index * 1000 + i}", discipline) for i in range(1, 8)]
            for index, discipline in enumerate(SPORT_CONFIG, start=1)
        }
        records, summary = top_up_records([], candidates, 5, "2026-08-06T00:00:00Z")
        self.assertEqual(len(records), 25)
        self.assertEqual(len({record["id"] for record in records}), 25)
        self.assertEqual(len({record["ticker"] for record in records}), 25)
        for discipline in SPORT_CONFIG:
            self.assertEqual(summary["countsAfter"][discipline], 5)

    def test_existing_stronger_record_is_preserved_and_enriched(self):
        existing = [{
            "id": "verified-player",
            "name": "Verified Player",
            "ticker": "VP",
            "primaryCategory": "Athlete",
            "discipline": "Golf",
            "marketPrice": 180.0,
            "pricingConfidence": 0.96,
            "activeMetrics": {"performance": 99},
        }]
        candidates = {discipline: [] for discipline in SPORT_CONFIG}
        candidates["Golf"] = [candidate("Verified Player", "Q500", "Golf")]
        updated, summary = top_up_records(existing, candidates, 1, "2026-08-06T00:00:00Z")
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["marketPrice"], 180.0)
        self.assertEqual(updated[0]["pricingConfidence"], 0.96)
        self.assertEqual(updated[0]["activeMetrics"]["performance"], 99)
        self.assertEqual(updated[0]["wikidataId"], "Q500")
        self.assertEqual(summary["changes"]["Golf"]["enriched"], 1)

    def test_merge_candidates_prefers_stronger_role_and_source_data(self):
        rows = [
            {**candidate("One Golfer", "Q900", "Golf", 7), "role": "Golfer", "country": "Not listed", "workStartYear": None},
            {**candidate("One Golfer", "Q900", "Golf", 12), "role": "Professional Golfer", "country": "Canada"},
        ]
        merged = merge_candidates(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["role"], "Professional Golfer")
        self.assertEqual(merged[0]["country"], "Canada")
        self.assertEqual(merged[0]["sitelinks"], 12)


if __name__ == "__main__":
    unittest.main()
