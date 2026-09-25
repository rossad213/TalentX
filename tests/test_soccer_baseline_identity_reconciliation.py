from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_soccer_baseline_identities import reconcile_records


class SoccerBaselineIdentityTests(unittest.TestCase):
    def live(self, **updates):
        base = {
            "id": "live-espn-soccer-253989",
            "name": "Erling Haaland",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "leagueOrMedium": "Premier League",
            "teamOrPlatform": "Manchester City",
            "role": "Forward",
            "careerStatus": "Active",
            "marketSegment": "Current",
            "sourceNamespace": "espn",
            "sourceName": "ESPN current team roster endpoint",
            "sourceRecordId": "253989",
            "activeMetrics": {"performance": 92},
            "marketPrice": 220.0,
        }
        base.update(updates)
        return base

    def test_restores_live_id_and_keeps_rich_event_ledger(self):
        current = [{
            **self.live(
                id="cur-erling-haaland",
                activeMetrics={"performance": 61},
                marketPrice=133.0,
            ),
            "priceEvents": [{"eventId": "g1"}, {"eventId": "g2"}],
            "priceHistory": [{"eventId": "g1"}, {"eventId": "g2"}],
        }]
        reconciled, repairs = reconcile_records(current, [self.live()])
        self.assertEqual(len(reconciled), 1)
        record = reconciled[0]
        self.assertEqual(record["id"], "live-espn-soccer-253989")
        self.assertEqual(record["activeMetrics"]["performance"], 92)
        self.assertEqual(len(record["priceEvents"]), 2)
        self.assertEqual(record["soccerCanonicalAliasIds"], ["cur-erling-haaland"])
        self.assertEqual(len(repairs), 1)

    def test_current_first_seed_without_source_id_is_replaced_by_live_identity(self):
        current = [{
            "id": "cur-alexia-putellas",
            "name": "Alexia Putellas",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "leagueOrMedium": "Liga F",
            "teamOrPlatform": "Barcelona",
            "marketSegment": "Current",
            "sourceName": "TalentX current-first seed",
        }]
        baseline = [self.live(
            id="live-espn-soccer-219995",
            name="Alexia Putellas",
            sourceRecordId="219995",
            leagueOrMedium="Women's Super League",
            teamOrPlatform="London City Lionesses",
        )]
        reconciled, _ = reconcile_records(current, baseline)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["id"], "live-espn-soccer-219995")
        self.assertEqual(reconciled[0]["teamOrPlatform"], "London City Lionesses")

    def test_exact_live_current_record_is_preserved(self):
        current_live = self.live(activeMetrics={"performance": 95}, marketPrice=225.0)
        seed = {
            "id": "cur-erling-haaland",
            "name": "Erling Haaland",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "marketSegment": "Current",
            "sourceName": "TalentX current-first seed",
        }
        reconciled, _ = reconcile_records([current_live, seed], [self.live()])
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["id"], current_live["id"])
        self.assertEqual(reconciled[0]["activeMetrics"]["performance"], 95)
        self.assertEqual(reconciled[0]["marketPrice"], 225.0)

    def test_unverified_homonym_is_not_removed(self):
        homonym = {
            "id": "other-player",
            "name": "Jordan Lee",
            "primaryCategory": "Athlete",
            "discipline": "Soccer",
            "marketSegment": "Current",
            "sourceNamespace": "other",
            "sourceRecordId": "xyz",
        }
        baseline = [self.live(
            id="live-espn-soccer-999",
            name="Jordan Lee",
            sourceRecordId="999",
        )]
        reconciled, _ = reconcile_records([homonym], baseline)
        self.assertEqual({item["id"] for item in reconciled}, {"other-player", "live-espn-soccer-999"})

    def test_non_soccer_is_untouched(self):
        nba = {
            "id": "nba-1",
            "name": "Player",
            "primaryCategory": "Athlete",
            "discipline": "Basketball",
            "leagueOrMedium": "NBA",
        }
        reconciled, repairs = reconcile_records([nba], [self.live()])
        self.assertIn(nba, reconciled)
        self.assertEqual(len(repairs), 1)  # missing Soccer baseline identity is restored


if __name__ == "__main__":
    unittest.main()
