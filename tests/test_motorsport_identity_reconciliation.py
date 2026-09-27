from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_motorsport_identities import reconcile_records


class MotorsportIdentityReconciliationTests(unittest.TestCase):
    def test_official_f1_identity_wins_and_merges_history(self):
        official = {
            "id": "athlete-motorsport-max-verstappen", "name": "Max Verstappen",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "sourceNamespace": "curated-individual-sport-roster", "sourceType": "official-ranking-roster",
            "marketPrice": 110.0, "pricingConfidence": 0.64,
            "priceHistory": [{"time": "2026-08-01T00:00:00Z", "eventId": "a", "phase": "close", "price": 110}],
        }
        seed = {
            "id": "cur-max-verstappen", "name": "Max Verstappen",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "marketPrice": 150.0, "pricingConfidence": 0.9,
            "priceHistory": [{"time": "2026-08-02T00:00:00Z", "eventId": "b", "phase": "close", "price": 150}],
        }
        rows, repairs = reconcile_records([official, seed])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "athlete-motorsport-max-verstappen")
        self.assertEqual({p["eventId"] for p in rows[0]["priceHistory"]}, {"a", "b"})
        self.assertEqual(rows[0]["motorsportCanonicalAliasIds"], ["cur-max-verstappen"])
        self.assertEqual(len(repairs), 1)

    def test_kimi_full_name_alias_collapses(self):
        official = {
            "id": "athlete-motorsport-kimi-antonelli", "name": "Kimi Antonelli",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "sourceNamespace": "curated-individual-sport-roster",
        }
        wiki = {
            "id": "wiki-kimi", "name": "Andrea Kimi Antonelli",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "International Motorsport",
            "sourceNamespace": "wikidata-individual-sport",
        }
        rows, repairs = reconcile_records([official, wiki])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "athlete-motorsport-kimi-antonelli")
        self.assertEqual(len(repairs), 1)

    def test_non_motorsport_is_untouched(self):
        nfl = {"id": "nfl", "name": "Max Verstappen", "primaryCategory": "Athlete", "discipline": "American Football"}
        rows, repairs = reconcile_records([nfl])
        self.assertEqual(rows, [nfl])
        self.assertEqual(repairs, [])


if __name__ == "__main__":
    unittest.main()
