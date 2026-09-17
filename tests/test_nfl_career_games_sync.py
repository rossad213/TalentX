#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_nfl_career_games import SYNC_VERSION, career_games_from_payload, sync_records


class NFLCareerGamesSyncTests(unittest.TestCase):
    def payload(self):
        return {
            "categories": [
                {
                    "name": "passing",
                    "names": ["gamesPlayed", "passingYards"],
                    "statistics": [
                        {"season": {"year": 2024}, "stats": ["12", "3000"]},
                        {"season": {"year": 2025}, "stats": ["17", "4100"]},
                    ],
                },
                {
                    "name": "general",
                    "names": ["gamesPlayed"],
                    "statistics": [
                        {"season": {"year": 2024}, "stats": ["10"]},
                        {"season": {"year": 2025}, "stats": ["17"]},
                    ],
                },
            ]
        }

    def test_sums_max_games_per_season_without_double_counting_categories(self):
        self.assertEqual(career_games_from_payload(self.payload()), 29)

    def test_recent_drafted_espn_player_gets_monotonic_game_count_without_price_change(self):
        records = [{
            "name": "System Test Player",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "123",
            "draftYear": 2024,
            "professionalGames": 0,
            "marketPrice": 55.25,
            "pricingEvidenceSummary": {},
        }]

        def fake_fetch(url, timeout):
            self.assertIn("athletes/123/stats", url)
            self.assertGreater(timeout, 0)
            return self.payload()

        counts = sync_records(
            records,
            fetcher=fake_fetch,
            workers=1,
            cache_hours=0,
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(counts["updated"], 1)
        self.assertEqual(records[0]["professionalGames"], 29)
        self.assertEqual(records[0]["pricingEvidenceSummary"]["professionalGames"], 29)
        self.assertEqual(records[0]["marketPrice"], 55.25)
        self.assertEqual(records[0]["nflCareerGamesSyncVersion"], SYNC_VERSION)

    def test_partial_provider_history_never_reduces_existing_career_games(self):
        records = [{
            "name": "System Test Player",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "123",
            "draftYear": 2024,
            "professionalGames": 35,
            "marketPrice": 80.0,
        }]
        sync_records(
            records,
            fetcher=lambda _url, _timeout: self.payload(),
            workers=1,
            cache_hours=0,
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(records[0]["professionalGames"], 35)
        self.assertEqual(records[0]["marketPrice"], 80.0)

    def test_old_draft_class_is_not_requested(self):
        records = [{
            "name": "Veteran",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "999",
            "draftYear": 2020,
            "professionalGames": 80,
        }]
        called = []

        def fake_fetch(url, timeout):
            called.append(url)
            return self.payload()

        counts = sync_records(
            records,
            fetcher=fake_fetch,
            workers=1,
            cache_hours=0,
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        )
        self.assertEqual(counts["eligible"], 0)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
