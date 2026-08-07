#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from strict_music_catalog import filter_records


class StrictMusicCatalogTests(unittest.TestCase):
    def test_curated_music_is_preserved(self):
        records = [{
            "name": "Lady Gaga",
            "primaryCategory": "Music",
            "nonAthleteRosterVersion": "1.0.0",
        }]
        filtered, summary = filter_records(records, {})
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["primaryCategory"], "Music")
        self.assertEqual(summary["excludedFromMusic"], 0)

    def test_screen_first_singer_moves_to_actor(self):
        records = [{
            "name": "Zac Efron",
            "primaryCategory": "Music",
            "sourceRecordId": "Q100",
            "role": "Singer",
        }]
        evidence = {"Q100": {
            "description": "American actor and singer",
            "occupations": {"Q33999", "Q177220"},
            "musicbrainz": {"mbid-zac"},
        }}
        filtered, summary = filter_records(records, evidence)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["primaryCategory"], "Actor")
        self.assertEqual(summary["movedToActor"], 1)

    def test_music_first_crossover_stays_music(self):
        records = [{
            "name": "Example Singer",
            "primaryCategory": "Music",
            "sourceRecordId": "Q101",
            "role": "Singer",
        }]
        evidence = {"Q101": {
            "description": "American singer and actress",
            "occupations": {"Q33999", "Q177220"},
            "musicbrainz": {"mbid-singer"},
        }}
        filtered, summary = filter_records(records, evidence)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["primaryCategory"], "Music")
        self.assertTrue(filtered[0]["musicCategoryVerified"])
        self.assertEqual(summary["verifiedDiscoveredMusic"], 1)

    def test_generic_musician_without_specific_profession_is_excluded(self):
        records = [{
            "name": "Incidental Music Credit",
            "primaryCategory": "Music",
            "sourceRecordId": "Q102",
            "role": "Musician",
        }]
        evidence = {"Q102": {
            "description": "American filmmaker",
            "occupations": {"Q639669"},
            "musicbrainz": {"mbid-generic"},
        }}
        filtered, summary = filter_records(records, evidence)
        self.assertEqual(filtered, [])
        self.assertEqual(summary["excludedFromMusic"], 1)

    def test_missing_musicbrainz_identity_is_excluded(self):
        records = [{
            "name": "Unverified Singer",
            "primaryCategory": "Music",
            "sourceRecordId": "Q103",
            "role": "Singer",
        }]
        evidence = {"Q103": {
            "description": "American singer",
            "occupations": {"Q177220"},
            "musicbrainz": set(),
        }}
        filtered, summary = filter_records(records, evidence)
        self.assertEqual(filtered, [])
        self.assertEqual(summary["excludedFromMusic"], 1)

    def test_missing_source_evidence_fails_closed(self):
        records = [{
            "name": "Unknown Person",
            "primaryCategory": "Music",
            "sourceRecordId": "Q104",
            "role": "Singer",
        }]
        filtered, summary = filter_records(records, {})
        self.assertEqual(filtered, [])
        self.assertEqual(summary["excludedFromMusic"], 1)


if __name__ == "__main__":
    unittest.main()
