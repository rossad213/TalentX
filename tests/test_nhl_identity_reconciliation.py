from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_nhl_identities import reconcile_records


class NHLIdentityReconciliationTests(unittest.TestCase):
    def player(self, **updates):
        base = {
            "id": "live-nhl-hockey-8478402",
            "name": "Connor McDavid",
            "primaryCategory": "Athlete",
            "discipline": "Hockey",
            "leagueOrMedium": "NHL",
            "sourceNamespace": "nhl",
            "sourceRecordId": "8478402",
            "sourceName": "NHL current roster API",
            "lastVerifiedAt": "2026-09-21T03:42:21Z",
            "marketPrice": 198.0,
            "fairValue": 198.0,
            "fundamentalValue": 198.0,
            "priceEvents": [{"eventKey": "nhl:new", "startedAt": "2026-09-20T00:00:00Z", "priceAfter": 198.0}],
            "priceHistory": [{"time": "2026-09-20T00:00:00Z", "eventId": "nhl:new", "phase": "close", "price": 198.0}],
        }
        base.update(updates)
        return base

    def test_live_identity_wins_and_alias_history_is_merged(self):
        live = self.player()
        alias = self.player(
            id="cur-connor-mcdavid",
            lastVerifiedAt="2026-08-30T18:43:44Z",
            marketPrice=243.0,
            fairValue=190.0,
            fundamentalValue=190.0,
            priceEvents=[{"eventKey": "nhl:old", "startedAt": "2026-08-10T00:00:00Z", "priceAfter": 180.0}],
            priceHistory=[{"time": "2026-08-10T00:00:00Z", "eventId": "nhl:old", "phase": "close", "price": 180.0}],
        )
        reconciled, repairs = reconcile_records([alias, live])
        self.assertEqual(len(reconciled), 1)
        record = reconciled[0]
        self.assertEqual(record["id"], live["id"])
        self.assertEqual(record["marketPrice"], 198.0)
        self.assertEqual(record["fairValue"], 198.0)
        self.assertEqual({event["eventKey"] for event in record["priceEvents"]}, {"nhl:old", "nhl:new"})
        self.assertEqual(len(record["priceHistory"]), 2)
        self.assertEqual(record["nhlCanonicalAliasIds"], ["cur-connor-mcdavid"])
        self.assertEqual(len(repairs), 1)

    def test_provider_id_reconciles_accented_name_variants(self):
        live = self.player(
            id="live-nhl-hockey-8477956",
            name="David Pastrnak",
            sourceRecordId="8477956",
        )
        alias = self.player(
            id="cur-david-pastr-k",
            name="David Pastrňák",
            sourceRecordId="8477956",
            lastVerifiedAt="2026-08-30T18:43:44Z",
        )
        reconciled, _ = reconcile_records([alias, live])
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["id"], "live-nhl-hockey-8477956")

    def test_same_name_different_provider_ids_are_not_collapsed(self):
        one = self.player(id="nhl-one", sourceRecordId="111")
        two = self.player(id="nhl-two", sourceRecordId="222")
        reconciled, repairs = reconcile_records([one, two])
        self.assertEqual(len(reconciled), 2)
        self.assertEqual(repairs, [])

    def test_non_nhl_records_are_untouched(self):
        nba = {
            "id": "nba", "name": "Player", "primaryCategory": "Athlete",
            "discipline": "Basketball", "leagueOrMedium": "NBA",
            "sourceNamespace": "espn", "sourceRecordId": "1",
        }
        reconciled, repairs = reconcile_records([nba])
        self.assertEqual(reconciled, [nba])
        self.assertEqual(repairs, [])


if __name__ == "__main__":
    unittest.main()
