#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from resolve_music_screen_conflicts import (
    primary_description_category,
    resolve_records,
    should_move_to_actor,
)


class MusicScreenConflictTests(unittest.TestCase):
    def test_named_screen_first_examples_move_to_actor(self):
        examples = {
            "Zac Efron": "American actor and singer (born 1987)",
            "Tom Hanks": "American actor and film producer (born 1956)",
            "Quentin Tarantino": "American filmmaker and actor (born 1963)",
        }
        for name, description in examples.items():
            with self.subTest(name=name):
                self.assertEqual(primary_description_category(description), "Actor")
                self.assertTrue(should_move_to_actor(description, {"Q33999"}, "Musician"))

    def test_music_first_crossovers_stay_music(self):
        self.assertEqual(primary_description_category("American singer and actress"), "Music")
        self.assertFalse(should_move_to_actor("American singer and actress", {"Q33999"}, "Singer"))
        self.assertEqual(primary_description_category("British musician and actor"), "Music")

    def test_screen_occupation_beats_generic_music_when_description_missing(self):
        self.assertTrue(should_move_to_actor("", {"Q2526255"}, "Musician"))
        self.assertFalse(should_move_to_actor("", {"Q33999"}, "Singer"))

    def test_reclassifies_without_changing_identity_or_price_inputs(self):
        records = [{
            "id": "cur-quentin-tarantino",
            "name": "Quentin Tarantino",
            "ticker": "QT",
            "primaryCategory": "Music",
            "discipline": "Music",
            "leagueOrMedium": "Music",
            "teamOrPlatform": "Independent / label not listed",
            "role": "Musician",
            "country": "United States",
            "sourceNamespace": "wikidata-music-expanded",
            "sourceRecordId": "Q3772",
            "activeMetrics": {"performance": 70, "audience": 80},
            "pricingConfidence": .68,
            "marketPrice": 62,
        }]
        evidence = {
            "Q3772": {
                "description": "American filmmaker and actor (born 1963)",
                "occupations": {"Q1414443", "Q33999"},
            }
        }
        updated, summary = resolve_records(records, evidence)
        self.assertEqual(len(updated), 1)
        record = updated[0]
        self.assertEqual(record["primaryCategory"], "Actor")
        self.assertEqual(record["role"], "Filmmaker")
        self.assertEqual(record["id"], "cur-quentin-tarantino")
        self.assertEqual(record["activeMetrics"]["performance"], 70)
        self.assertEqual(record["pricingConfidence"], .68)
        self.assertEqual(summary["movedToActor"], 1)

    def test_existing_actor_copy_wins_and_music_duplicate_is_removed(self):
        records = [
            {
                "id": "actor-tom-hanks",
                "name": "Tom Hanks",
                "primaryCategory": "Actor",
            },
            {
                "id": "music-tom-hanks",
                "name": "Tom Hanks",
                "primaryCategory": "Music",
                "role": "Musician",
                "sourceNamespace": "wikidata-non-athlete",
                "sourceRecordId": "Q2263",
            },
        ]
        evidence = {
            "Q2263": {
                "description": "American actor and film producer (born 1956)",
                "occupations": {"Q33999"},
            }
        }
        updated, summary = resolve_records(records, evidence)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["primaryCategory"], "Actor")
        self.assertEqual(summary["removedDuplicateMusicCopies"], 1)

    def test_existing_actor_copy_wins_even_when_source_lookup_returns_nothing(self):
        records = [
            {
                "id": "actor-zac-efron",
                "name": "Zac Efron",
                "primaryCategory": "Actor",
            },
            {
                "id": "music-zac-efron",
                "name": "Zac Efron",
                "primaryCategory": "Music",
                "role": "Singer",
                "sourceNamespace": "wikidata-music-expanded",
                "sourceRecordId": "Q45229",
            },
        ]
        updated, summary = resolve_records(records, {})
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["id"], "actor-zac-efron")
        self.assertEqual(summary["deterministicActorCollisionRemovals"], 1)

    def test_known_screen_first_regression_moves_without_network_evidence(self):
        records = [{
            "id": "music-quentin-tarantino",
            "name": "Quentin Tarantino",
            "primaryCategory": "Music",
            "role": "Musician",
            "sourceNamespace": "wikidata-music-expanded",
            "sourceRecordId": "Q3772",
        }]
        updated, summary = resolve_records(records, {})
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["primaryCategory"], "Actor")
        self.assertEqual(summary["regressionGuardMoves"], 1)


if __name__ == "__main__":
    unittest.main()
