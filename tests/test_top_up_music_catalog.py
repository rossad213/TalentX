#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from top_up_music_catalog import (
    MUSIC_OCCUPATIONS,
    collect_candidates,
    eligible,
    merge_candidate,
)


class FakeSession:
    pass


class MusicTopUpTests(unittest.TestCase):
    def test_music_occupation_pool_is_broad(self):
        self.assertGreaterEqual(len(MUSIC_OCCUPATIONS), 12)
        roles = {role for role, _ in MUSIC_OCCUPATIONS.values()}
        for required in {"Musician", "Singer", "Rapper", "Guitarist", "Pianist", "Drummer", "Songwriter", "Composer", "Record producer"}:
            self.assertIn(required, roles)

    def test_eligibility_rejects_weak_or_old_ended_records(self):
        current = datetime.now(timezone.utc).year
        base = {
            "qid": "Q123",
            "name": "Current Artist",
            "sitelinks": 30,
            "birthYear": current - 30,
            "workStartYear": current - 8,
            "workEndYear": None,
            "country": "United States",
            "role": "Singer",
            "discipline": "Vocal",
        }
        self.assertTrue(eligible(base, minimum_sitelinks=5, recent_cutoff=current - 3))
        ended = dict(base, workEndYear=current - 5)
        self.assertFalse(eligible(ended, minimum_sitelinks=5, recent_cutoff=current - 3))
        weak = dict(base, sitelinks=2)
        self.assertFalse(eligible(weak, minimum_sitelinks=5, recent_cutoff=current - 3))

    def test_merge_prefers_specific_music_role(self):
        existing = {
            "qid": "Q123", "name": "Artist", "sitelinks": 20,
            "role": "Musician", "discipline": "Music", "country": "Not listed",
            "birthYear": None, "workStartYear": None, "workEndYear": None,
        }
        incoming = {
            "qid": "Q123", "name": "Artist", "sitelinks": 35,
            "role": "Guitarist", "discipline": "Guitar", "country": "Canada",
            "birthYear": 1995, "workStartYear": 2012, "workEndYear": None,
        }
        merge_candidate(existing, incoming)
        self.assertEqual(existing["role"], "Guitarist")
        self.assertEqual(existing["discipline"], "Guitar")
        self.assertEqual(existing["country"], "Canada")
        self.assertEqual(existing["sitelinks"], 35)
        self.assertEqual(existing["birthYear"], 1995)

    def test_collect_candidates_deduplicates_existing_names_and_ids(self):
        import top_up_music_catalog as module

        original_fetch = module.fetch_page
        try:
            def fake_fetch(session, qid, role, discipline, recent_cutoff, page_size, offset, timeout):
                if offset > 0:
                    return []
                return [
                    {
                        "qid": "Q100", "name": "Existing Artist", "sitelinks": 100,
                        "birthYear": 1990, "workStartYear": 2010, "workEndYear": None,
                        "country": "US", "role": role, "discipline": discipline,
                    },
                    {
                        "qid": "Q200", "name": "New Artist", "sitelinks": 80,
                        "birthYear": 1995, "workStartYear": 2015, "workEndYear": None,
                        "country": "UK", "role": role, "discipline": discipline,
                    },
                ]
            module.fetch_page = fake_fetch
            candidates, errors, requests_made = collect_candidates(
                session=FakeSession(),
                needed=1,
                existing_names={"existingartist"},
                existing_source_ids={"Q999"},
                minimum_sitelinks=5,
                recent_cutoff=datetime.now(timezone.utc).year - 3,
                per_occupation_limit=1,
                page_size=1,
                timeout=1,
                sleep_seconds=0,
            )
            self.assertFalse(errors)
            self.assertGreater(requests_made, 0)
            self.assertEqual({row["qid"] for row in candidates}, {"Q200"})
        finally:
            module.fetch_page = original_fetch


if __name__ == "__main__":
    unittest.main()
