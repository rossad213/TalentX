from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_basketball_identities import reconcile_records


class BasketballIdentityReconciliationTests(unittest.TestCase):
    def player(self, **updates):
        base = {
            "id": "live-espn-basketball-3149391",
            "name": "A'ja Wilson",
            "primaryCategory": "Athlete",
            "discipline": "Basketball",
            "leagueOrMedium": "WNBA",
            "sourceNamespace": "espn",
            "sourceRecordId": "3149391",
            "lastVerifiedAt": "2026-09-21T03:42:21Z",
            "marketPrice": 204.0,
            "fairValue": 204.0,
            "fundamentalValue": 204.0,
            "priceEvents": [
                {"eventKey": "espn:new", "startedAt": "2026-09-24T00:00:00Z", "priceAfter": 204.0}
            ],
            "priceHistory": [
                {"time": "2026-09-24T00:00:00Z", "eventId": "espn:new", "phase": "close", "price": 204.0}
            ],
        }
        base.update(updates)
        return base

    def test_live_identity_wins_and_alias_history_is_merged(self):
        live = self.player()
        alias = self.player(
            id="cur-a-ja-wilson",
            name="A’ja Wilson",
            lastVerifiedAt="2026-08-30T18:43:44Z",
            marketPrice=171.0,
            fairValue=204.0,
            fundamentalValue=204.0,
            priceEvents=[
                {"eventKey": "espn:old", "startedAt": "2026-08-10T00:00:00Z", "priceAfter": 171.0}
            ],
            priceHistory=[
                {"time": "2026-08-10T00:00:00Z", "eventId": "espn:old", "phase": "close", "price": 171.0}
            ],
        )
        reconciled, repairs = reconcile_records([alias, live])
        self.assertEqual(len(reconciled), 1)
        record = reconciled[0]
        self.assertEqual(record["id"], live["id"])
        self.assertEqual(record["marketPrice"], 204.0)
        self.assertEqual({event["eventKey"] for event in record["priceEvents"]}, {"espn:old", "espn:new"})
        self.assertEqual(len(record["priceHistory"]), 2)
        self.assertEqual(record["basketballCanonicalAliasIds"], ["cur-a-ja-wilson"])
        self.assertEqual(len(repairs), 1)

    def test_nba_duplicate_uses_same_provider_identity_rule(self):
        live = self.player(
            id="live-espn-basketball-3112335",
            name="Nikola Jokic",
            leagueOrMedium="NBA",
            sourceRecordId="3112335",
        )
        alias = self.player(
            id="cur-nikola-joki",
            name="Nikola Jokić",
            leagueOrMedium="NBA",
            sourceRecordId="3112335",
            lastVerifiedAt="2026-08-30T18:43:44Z",
        )
        reconciled, repairs = reconcile_records([alias, live])
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["id"], "live-espn-basketball-3112335")
        self.assertEqual(len(repairs), 1)

    def test_same_name_different_provider_ids_are_not_collapsed(self):
        one = self.player(id="b-one", name="Alicia Florez", sourceRecordId="111")
        two = self.player(id="b-two", name="Alicia Florez", sourceRecordId="222")
        reconciled, repairs = reconcile_records([one, two])
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(repairs, [])

    def test_non_basketball_records_are_untouched(self):
        nfl = {
            "id": "nfl", "name": "Player", "primaryCategory": "Athlete",
            "discipline": "American Football", "leagueOrMedium": "NFL",
            "sourceNamespace": "espn", "sourceRecordId": "1",
        }
        reconciled, repairs = reconcile_records([nfl])
        self.assertEqual(reconciled, [nfl])
        self.assertEqual(repairs, [])


if __name__ == "__main__":
    unittest.main()
