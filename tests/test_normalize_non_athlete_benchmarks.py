#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from normalize_non_athlete_benchmarks import normalize


class NormalizeNonAthleteBenchmarksTests(unittest.TestCase):
    def test_removes_rank_from_original_wikidata_discovery(self):
        records = [{
            "name": "Example Artist",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-non-athlete",
            "benchmarkRank": 101,
            "benchmarkPoolSize": 500,
        }]
        self.assertEqual(normalize(records), 1)
        self.assertNotIn("benchmarkRank", records[0])
        self.assertNotIn("benchmarkPoolSize", records[0])

    def test_removes_rank_from_expanded_music_discovery(self):
        records = [{
            "name": "Expanded Artist",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-music-expanded",
            "benchmarkRank": 2501,
            "benchmarkPoolSize": 5000,
        }]
        self.assertEqual(normalize(records), 1)
        self.assertNotIn("benchmarkRank", records[0])
        self.assertNotIn("benchmarkPoolSize", records[0])
        self.assertIn("not part of curated benchmark", records[0]["rankingStatus"])

    def test_preserves_curated_benchmark_rank(self):
        records = [{
            "name": "Curated Artist",
            "primaryCategory": "Music",
            "sourceNamespace": "curated-non-athlete",
            "benchmarkRank": 1,
            "benchmarkPoolSize": 100,
        }]
        self.assertEqual(normalize(records), 0)
        self.assertEqual(records[0]["benchmarkRank"], 1)
        self.assertEqual(records[0]["benchmarkPoolSize"], 100)


if __name__ == "__main__":
    unittest.main()
