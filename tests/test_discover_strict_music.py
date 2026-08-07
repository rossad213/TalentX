#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from discover_strict_music import (
    SCREEN_OCCUPATIONS,
    STRICT_MUSIC_OCCUPATIONS,
    eligible,
    sparql_query,
)


class StrictMusicDiscoveryTests(unittest.TestCase):
    def test_generic_musician_occupation_is_not_a_source(self):
        self.assertNotIn("Q639669", STRICT_MUSIC_OCCUPATIONS)

    def test_query_requires_musicbrainz_and_excludes_screen_occupations(self):
        query = sparql_query("Q177220", 2023, 100, 0)
        self.assertIn("wdt:P434 ?mbid", query)
        self.assertIn("FILTER NOT EXISTS", query)
        for qid in SCREEN_OCCUPATIONS:
            self.assertIn(f"wd:{qid}", query)

    def test_candidate_requires_musicbrainz_identity(self):
        candidate = {
            "qid": "Q1",
            "name": "Example Singer",
            "sitelinks": 50,
            "musicBrainzArtistId": "",
            "birthYear": 1990,
            "workStartYear": 2010,
            "workEndYear": None,
        }
        self.assertFalse(eligible(candidate, 5, 2023))
        candidate["musicBrainzArtistId"] = "abc-123"
        self.assertTrue(eligible(candidate, 5, 2023))


if __name__ == "__main__":
    unittest.main()
