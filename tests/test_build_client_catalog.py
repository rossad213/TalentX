from __future__ import annotations

import unittest

from scripts.build_client_catalog import compact_record


class BuildClientCatalogTests(unittest.TestCase):
    def test_expected_value_score_is_available_to_market_table(self):
        compact = compact_record({
            "id": "nfl-test",
            "name": "NFL Test",
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "careerScore": 63.4,
            "expectedValueScore": 51.52,
            "marketPrice": 90.31,
        })
        self.assertEqual(compact["careerScore"], 63.4)
        self.assertEqual(compact["expectedValueScore"], 51.52)
        self.assertEqual(compact["marketPrice"], 90.31)


if __name__ == "__main__":
    unittest.main()
