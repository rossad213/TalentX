import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from expand_non_athlete_sources import (  # noqa: E402
    candidate_is_eligible,
    evidence_metrics,
    merge_candidates,
    parse_year,
)


class ExpandNonAthleteSourcesTests(unittest.TestCase):
    def test_parse_year(self):
        self.assertEqual(parse_year("+1994-01-01T00:00:00Z"), 1994)
        self.assertIsNone(parse_year("unknown"))

    def test_eligibility_rejects_weak_missing_activity(self):
        current = datetime.now(timezone.utc).year
        candidate = {
            "qid": "Q123",
            "name": "Example Artist",
            "sitelinks": 15,
            "birthYear": current - 30,
            "workStartYear": None,
            "workEndYear": None,
        }
        self.assertFalse(candidate_is_eligible(candidate, minimum_sitelinks=10, recent_cutoff=current - 3))
        candidate["sitelinks"] = 60
        self.assertTrue(candidate_is_eligible(candidate, minimum_sitelinks=10, recent_cutoff=current - 3))

    def test_merge_candidates_prefers_specific_discipline(self):
        rows = [
            {"qid": "Q1", "name": "A", "sitelinks": 100, "discipline": "Acting", "role": "Actor", "country": "Not listed", "birthYear": None, "workStartYear": None, "workEndYear": None},
            {"qid": "Q1", "name": "A", "sitelinks": 100, "discipline": "Film", "role": "Film actor", "country": "United States", "birthYear": 1990, "workStartYear": 2010, "workEndYear": None},
        ]
        merged = merge_candidates(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["discipline"], "Film")
        self.assertEqual(merged[0]["country"], "United States")
        self.assertEqual(merged[0]["birthYear"], 1990)

    def test_metrics_are_bounded(self):
        metrics = evidence_metrics({"sitelinks": 250, "birthYear": 1995, "workStartYear": 2012}, "Music")
        self.assertEqual(set(metrics), {"performance", "achievements", "consistency", "potential", "availability", "audience"})
        for value in metrics.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)


if __name__ == "__main__":
    unittest.main()
