#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from enrich_current_catalog import award_points, resolve_award_details, resolve_award_names  # noqa: E402
from hourly_price_refresh import NFL_AWARD_EVIDENCE_VERSION  # noqa: E402
from hourly_price_refresh_nfl import (  # noqa: E402
    NFL_FUNDAMENTAL_EVIDENCE_VERSION,
    _established_fundamental_signals,
    nfl_model_backfill_select_records,
)
from hourly_price_refresh_nfl_rb import nfl_position_cohort_key  # noqa: E402
from pricing_model import rookie_influence  # noqa: E402


class NFLEstablishedEvidenceTests(unittest.TestCase):
    def rb_record(self, *, player_id: str, draft_year: int, experience: int, career_games: int) -> dict:
        return {
            "id": player_id,
            "name": player_id,
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": "Running Back",
            "careerStatus": "Active",
            "careerStage": "Established" if draft_year <= 2024 else "Early Career",
            "draftYear": draft_year,
            "draftRound": 1,
            "draftPick": 10,
            "experienceYears": experience,
            "professionalGames": career_games,
        }

    @staticmethod
    def rb_season(*, games: int, carries: int, rush_yards: int, receptions: int, rec_yards: int, rush_tds: int = 0, rec_tds: int = 0) -> dict:
        return {
            "gamesPlayed": games,
            "rushingAttempts": carries,
            "rushingYards": rush_yards,
            "yardsPerRushAttempt": rush_yards / carries if carries else 0.0,
            "rushingTouchdowns": rush_tds,
            "receptions": receptions,
            "receivingYards": rec_yards,
            "yardsPerReception": rec_yards / receptions if receptions else 0.0,
            "receivingTouchdowns": rec_tds,
        }

    def stable_signal(self, record: dict, history: dict[int, dict], career: dict) -> tuple[dict, dict]:
        item = {
            "record": dict(record),
            "career": career,
            "signals": {
                "recentProduction": 0.0,
                "careerProduction": 250.0,
                "efficiency": 0.0,
                "awardPoints": 0.0,
            },
        }
        result = _established_fundamental_signals(record, item, history, 2026)
        self.assertIsNotNone(result)
        return result  # type: ignore[return-value]

    def test_two_game_rate_spike_does_not_overturn_stronger_established_rb_efficiency(self):
        bijan = self.rb_record(player_id="bijan", draft_year=2023, experience=4, career_games=53)
        chuba = self.rb_record(player_id="chuba", draft_year=2021, experience=6, career_games=81)

        bijan_history = {
            2024: self.rb_season(games=17, carries=300, rush_yards=1440, receptions=60, rec_yards=540),
            2025: self.rb_season(games=17, carries=320, rush_yards=1536, receptions=65, rec_yards=585),
            2026: self.rb_season(games=2, carries=37, rush_yards=155, receptions=11, rec_yards=99, rec_tds=1),
        }
        chuba_history = {
            2024: self.rb_season(games=17, carries=280, rush_yards=1176, receptions=45, rec_yards=360),
            2025: self.rb_season(games=17, carries=270, rush_yards=1134, receptions=40, rec_yards=340),
            2026: self.rb_season(games=2, carries=22, rush_yards=102, receptions=5, rec_yards=49, rush_tds=1, rec_tds=2),
        }
        bijan_career = self.rb_season(games=53, carries=850, rush_yards=4080, receptions=170, rec_yards=1530)
        chuba_career = self.rb_season(games=81, carries=900, rush_yards=3780, receptions=150, rec_yards=1275)

        bijan_signals, bijan_detail = self.stable_signal(bijan, bijan_history, bijan_career)
        chuba_signals, chuba_detail = self.stable_signal(chuba, chuba_history, chuba_career)

        # Chuba owns the tiny current-season YPC/YPR sample in this fixture, but
        # established career/multi-season efficiency remains stronger for Bijan.
        self.assertGreater(bijan_signals["efficiency"], chuba_signals["efficiency"])
        self.assertLess(bijan_detail["currentSeasonEfficiencyWeight"], 0.25)
        self.assertLess(chuba_detail["currentSeasonEfficiencyWeight"], 0.20)
        self.assertEqual(bijan_detail["window"], "established-multiseason")

    def test_second_year_player_stays_on_ipo_transition_without_veteran_smoothing(self):
        second_year = self.rb_record(player_id="year-two", draft_year=2025, experience=2, career_games=20)
        history = {
            2025: self.rb_season(games=17, carries=200, rush_yards=900, receptions=35, rec_yards=280),
            2026: self.rb_season(games=2, carries=30, rush_yards=180, receptions=6, rec_yards=54),
        }
        career = self.rb_season(games=20, carries=230, rush_yards=1080, receptions=41, rec_yards=334)
        _, detail = self.stable_signal(second_year, history, career)
        self.assertEqual(detail["window"], "rookie-ipo-transition")
        self.assertEqual(detail["currentSeasonProductionWeight"], 1.0)

    def test_model_rollout_backfills_pending_nfl_beyond_game_participant_cap(self):
        records = [
            {
                "id": "allen",
                "name": "Josh Allen",
                "leagueOrMedium": "NFL",
                "sourceNamespace": "espn",
                "sourceRecordId": "1",
                "careerStatus": "Active",
                "marketPrice": 160,
            },
            {
                "id": "dak",
                "name": "Dak Prescott",
                "leagueOrMedium": "NFL",
                "sourceNamespace": "espn",
                "sourceRecordId": "2",
                "careerStatus": "Active",
                "marketPrice": 170,
                "nflFundamentalEvidenceVersion": NFL_FUNDAMENTAL_EVIDENCE_VERSION,
                "nflAwardEvidenceVersion": NFL_AWARD_EVIDENCE_VERSION,
            },
            {
                "id": "nba",
                "name": "NBA Player",
                "leagueOrMedium": "NBA",
                "sourceNamespace": "espn",
                "sourceRecordId": "3",
                "careerStatus": "Active",
                "marketPrice": 200,
            },
        ]
        selected = nfl_model_backfill_select_records(
            records,
            {("espn", "3")},
            max_athletes=1,
        )
        self.assertIn(0, selected)
        self.assertIn(2, selected)
        self.assertNotIn(1, selected)

    def test_award_evidence_version_mismatch_is_backfilled_even_with_current_fundamentals(self):
        records = [{
            "id": "garrett",
            "name": "Myles Garrett",
            "leagueOrMedium": "NFL",
            "sourceNamespace": "espn",
            "sourceRecordId": "3122132",
            "careerStatus": "Active",
            "marketPrice": 190,
            "nflFundamentalEvidenceVersion": NFL_FUNDAMENTAL_EVIDENCE_VERSION,
            "nflAwardEvidenceVersion": "old-award-model",
        }]
        selected = nfl_model_backfill_select_records(records, set(), max_athletes=1)
        self.assertEqual(selected, [0])

    def test_nfl_cohorts_are_position_aware_not_universal(self):
        qb = {
            "leagueOrMedium": "NFL",
            "role": "Quarterback",
        }
        rb = {
            "leagueOrMedium": "NFL",
            "role": "Running Back",
        }
        self.assertEqual(nfl_position_cohort_key(qb), ("NFL", "QB"))
        self.assertEqual(nfl_position_cohort_key(rb), ("NFL", "RB"))
        self.assertNotEqual(nfl_position_cohort_key(qb), ("NFL", "UNIVERSAL"))

    def test_second_year_keeps_ipo_influence_and_year_three_hands_off(self):
        year_two = self.rb_record(player_id="year-two", draft_year=2025, experience=2, career_games=20)
        year_three = self.rb_record(player_id="year-three", draft_year=2024, experience=3, career_games=36)
        self.assertGreater(rookie_influence(year_two), 0.0)
        self.assertEqual(rookie_influence(year_three), 0.0)

    def test_award_reference_titles_are_resolved_for_achievement_semantics(self):
        payload = {
            "count": 2,
            "items": [
                {"$ref": "https://example.test/award/1"},
                {"$ref": "https://example.test/award/2"},
            ],
        }

        def fake_fetch(url: str, _timeout: float):
            if url.endswith("/1"):
                return {"displayName": "AP Most Valuable Player"}
            if url.endswith("/2"):
                return {"displayName": "First-Team All-Pro"}
            raise AssertionError(url)

        with patch("enrich_current_catalog.fetch_json", side_effect=fake_fetch):
            names = resolve_award_names(payload, 1.0)
        points, scored_names = award_points(payload, names)

        self.assertIn("AP Most Valuable Player", scored_names)
        self.assertIn("First-Team All-Pro", scored_names)
        self.assertGreater(points, 6.0)

    def test_award_reference_resolution_preserves_season_year(self):
        payload = {
            "count": 1,
            "items": [{"$ref": "https://example.test/award/1"}],
        }

        def fake_fetch(url: str, _timeout: float):
            self.assertTrue(url.endswith("/1"))
            return {
                "displayName": "NFL Defensive Player of the Year",
                "season": {"year": 2025},
            }

        with patch("enrich_current_catalog.fetch_json", side_effect=fake_fetch):
            details = resolve_award_details(payload, 1.0)

        self.assertEqual(details, [{
            "name": "NFL Defensive Player of the Year",
            "year": 2025,
        }])


if __name__ == "__main__":
    unittest.main()
