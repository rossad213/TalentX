from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_nfl_injuries import apply_injuries, parse_injury_report


class NFLInjurySyncTests(unittest.TestCase):
    def test_report_is_keyed_by_espn_id_and_name(self):
        payload = {
            "injuries": [
                {
                    "team": {"abbreviation": "GB"},
                    "injuries": [
                        {
                            "athlete": {"id": "4241457", "fullName": "Micah Parsons"},
                            "status": "Out",
                            "details": {"type": "Knee"},
                            "date": "2026-09-25",
                        }
                    ],
                }
            ]
        }
        by_id, by_name = parse_injury_report(payload)
        self.assertEqual(by_id["4241457"]["status"], "Out")
        self.assertEqual(by_id["4241457"]["type"], "Knee")
        self.assertEqual(by_name["micahparsons"]["team"], "GB")

    def test_only_nfl_espn_records_receive_injury_state(self):
        nfl = {
            "id": "nfl",
            "name": "Micah Parsons",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "4241457",
        }
        healthy = {
            "id": "healthy",
            "name": "Healthy Player",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "999",
            "nflInjuryActive": True,
            "nflInjuryStatus": "Out",
        }
        nba = {
            "id": "nba",
            "name": "NBA Player",
            "leagueOrMedium": "NBA",
            "sourceNamespace": "espn",
            "sourceRecordId": "4241457",
        }
        listed = {
            "4241457": {
                "athleteId": "4241457",
                "name": "Micah Parsons",
                "status": "Physically Unable to Perform",
                "type": "Knee",
                "team": "GB",
                "date": "2026-09-25",
            }
        }
        updated, active = apply_injuries([nfl, healthy, nba], listed, {}, verified_at="2026-09-26T06:00:00Z")
        self.assertEqual(active, 1)
        self.assertTrue(updated[0]["nflInjuryActive"])
        self.assertEqual(updated[0]["nflInjuryStatus"], "Physically Unable to Perform")
        self.assertEqual(updated[0]["nflInjuryType"], "Knee")
        self.assertFalse(updated[1]["nflInjuryActive"])
        self.assertEqual(updated[1]["nflInjuryStatus"], "Not listed")
        self.assertEqual(updated[2], nba)


if __name__ == "__main__":
    unittest.main()
