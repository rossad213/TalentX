from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from enrich_current_catalog import cohort_key
from recalibrate_nhl_metrics import VERSION, recalibrate_records


def record(name: str, role: str, recent: float, career: float, efficiency: float, **updates):
    base = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "primaryCategory": "Athlete",
        "discipline": "Hockey",
        "leagueOrMedium": "NHL",
        "role": role,
        "careerStatus": "Active",
        "age": 27,
        "activeMetrics": {
            "performance": 50, "achievements": 50, "consistency": 50,
            "potential": 70, "availability": 75, "audience": 50,
        },
        "pricingEvidenceSummary": {
            "newsCount": 0,
            "rawSignals": {
                "recentProduction": recent,
                "careerProduction": career,
                "efficiency": efficiency,
                "usage": 20,
                "careerUsage": 100,
                "awardPoints": 0,
            },
        },
    }
    base.update(updates)
    return base


class NHLMetricRecalibrationTests(unittest.TestCase):
    def test_g_position_code_is_goalie(self):
        self.assertEqual(cohort_key(record("Goalie", "G", 10, 20, 30)), ("NHL", "GOALIE"))
        self.assertEqual(cohort_key(record("Skater", "C", 10, 20, 30)), ("NHL", "SKATER"))

    def test_goalies_are_ranked_against_goalies_not_skaters(self):
        goalies = [record(f"Goalie {i}", "G", 10 + i, 20 + i, 30 + i) for i in range(10)]
        skaters = [record(f"Skater {i}", "C", 500 + i, 800 + i, 100 + i) for i in range(10)]
        updated, changed = recalibrate_records([*goalies, *skaters])
        self.assertEqual(changed, 20)
        middle = next(item for item in updated if item["name"] == "Goalie 5")
        self.assertEqual(middle["nhlRoleGroup"], "GOALIE")
        self.assertEqual(middle["pricingEvidenceSummary"]["cohort"], "NHL · GOALIE")
        self.assertGreater(middle["pricingEvidenceSummary"]["percentiles"]["recentProduction"], 0.4)
        self.assertEqual(middle["nhlMetricCalibrationVersion"], VERSION)

    def test_non_nhl_record_is_unchanged(self):
        nba = {
            "id": "nba", "primaryCategory": "Athlete", "discipline": "Basketball",
            "leagueOrMedium": "NBA", "role": "G", "activeMetrics": {"performance": 99},
        }
        updated, changed = recalibrate_records([nba])
        self.assertEqual(changed, 0)
        self.assertEqual(updated[0], nba)


if __name__ == "__main__":
    unittest.main()
