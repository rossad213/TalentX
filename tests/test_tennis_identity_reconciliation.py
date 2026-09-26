from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_tennis_identities import reconcile_records


class TennisIdentityReconciliationTests(unittest.TestCase):
    def player(self, **updates):
        base = {
            "id": "athlete-tennis-carlos-alcaraz",
            "name": "Carlos Alcaraz",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "leagueOrMedium": "ATP",
            "sourceNamespace": "curated-individual-sport-roster",
            "sourceRank": 2,
            "pricingConfidence": 0.64,
            "talentScore": 84.19,
            "marketPrice": 179.68,
            "activeMetrics": {
                "performance": 86.2,
                "achievements": 82.1,
                "consistency": 83.6,
                "potential": 80.9,
                "availability": 88.0,
                "audience": 83.2,
            },
            "priceEvents": [{
                "eventKey": "espn-tennis:atp:1",
                "eventId": "1",
                "eventType": "game",
                "sport": "tennis",
                "tour": "ATP",
                "startedAt": "2026-09-01T12:00:00Z",
                "verified": True,
            }],
            "priceHistory": [{
                "time": "2026-09-01T12:00:00Z",
                "eventId": "espn-tennis:atp:1",
                "phase": "close",
                "price": 179.68,
            }],
        }
        base.update(updates)
        return base

    def test_same_player_same_tour_same_rank_collapses_and_merges_history(self):
        roster = self.player()
        benchmark = self.player(
            id="cur-carlos-alcaraz",
            sourceNamespace=None,
            sourceRank=None,
            rosterSourceRank=2,
            pricingConfidence=0.70,
            talentScore=89.82,
            marketPrice=204.86,
            activeMetrics={
                "performance": 93.4,
                "achievements": 94.0,
                "consistency": 69.8,
                "potential": 97.7,
                "availability": 92.6,
                "audience": 95.6,
            },
            priceEvents=[{
                "eventKey": "espn-tennis:atp:2",
                "eventId": "2",
                "eventType": "game",
                "sport": "tennis",
                "tour": "ATP",
                "startedAt": "2026-09-08T12:00:00Z",
                "verified": True,
            }],
            priceHistory=[{
                "time": "2026-09-08T12:00:00Z",
                "eventId": "espn-tennis:atp:2",
                "phase": "close",
                "price": 204.86,
            }],
        )
        reconciled, repairs = reconcile_records([roster, benchmark])
        self.assertEqual(len(reconciled), 1)
        record = reconciled[0]
        self.assertEqual(record["id"], "cur-carlos-alcaraz")
        self.assertEqual(record["sourceRank"], 2)
        self.assertEqual(record["rosterSourceRank"], 2)
        self.assertEqual({event["eventId"] for event in record["priceEvents"]}, {"1", "2"})
        self.assertEqual(len(record["priceHistory"]), 2)
        self.assertEqual(record["tennisCanonicalAliasIds"], ["athlete-tennis-carlos-alcaraz"])
        self.assertEqual(len(repairs), 1)

    def test_same_name_different_tours_are_not_collapsed(self):
        atp = self.player(id="atp-one", leagueOrMedium="ATP", sourceRank=10)
        wta = self.player(id="wta-one", leagueOrMedium="WTA", sourceRank=10)
        reconciled, repairs = reconcile_records([atp, wta])
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(repairs, [])

    def test_conflicting_ranks_are_not_collapsed(self):
        one = self.player(id="one", sourceRank=2)
        two = self.player(id="two", sourceRank=None, rosterSourceRank=9)
        reconciled, repairs = reconcile_records([one, two])
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(repairs, [])

    def test_non_tennis_is_untouched(self):
        nba = {
            "id": "nba", "name": "Carlos Alcaraz", "primaryCategory": "Athlete",
            "discipline": "Basketball", "leagueOrMedium": "NBA",
        }
        reconciled, repairs = reconcile_records([nba])
        self.assertEqual(reconciled, [nba])
        self.assertEqual(repairs, [])


if __name__ == "__main__":
    unittest.main()
