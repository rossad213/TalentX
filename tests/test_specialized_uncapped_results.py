from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import tennis_event_refresh as tennis_base  # noqa: E402
from golf_event_refresh_results import golf_tournament_move_results  # noqa: E402
from tennis_event_refresh_results import (  # noqa: E402
    flatten_scoreboard_results,
    tennis_match_move_results,
    verified_tennis_record_strength,
)


class SpecializedUncappedResultTests(unittest.TestCase):
    def test_routine_tennis_win_remains_modest(self) -> None:
        move = tennis_match_move_results(
            winner=True,
            round_name="First Round",
            major=False,
            sets_for=2,
            sets_against=1,
            player_record={"sourceRank": 20},
            opponent_record={"sourceRank": 35},
        )
        self.assertGreater(move, 0)
        self.assertLess(move, 0.5)

    def test_shelton_alcaraz_major_qf_prices_as_breakthrough(self) -> None:
        move = tennis_match_move_results(
            winner=True,
            round_name="Quarterfinal",
            major=True,
            sets_for=3,
            sets_against=2,
            player_record={"sourceRank": 8},
            opponent_record={"sourceRank": 3},
        )
        self.assertGreaterEqual(move, 4.0)
        self.assertLessEqual(move, 5.0)

    def test_major_top_three_upset_loss_registers_meaningfully(self) -> None:
        move = tennis_match_move_results(
            winner=False,
            round_name="Quarterfinal",
            major=True,
            sets_for=2,
            sets_against=3,
            player_record={"sourceRank": 3},
            opponent_record={"sourceRank": 8},
        )
        self.assertLess(move, -1.5)
        self.assertGreater(move, -3.0)

    def test_major_tennis_upset_can_exceed_old_cap_when_result_warrants_it(self) -> None:
        move = tennis_match_move_results(
            winner=True,
            round_name="Final",
            major=True,
            sets_for=3,
            sets_against=0,
            player_record={"sourceRank": 600},
            opponent_record={"sourceRank": 1},
            max_move_pct=0.5,
        )
        self.assertGreater(move, 2.5)

    def test_tennis_all_board_infers_draw_tour_and_keeps_one_match(self) -> None:
        payload = {
            "events": [{
                "id": "us-open-2026",
                "name": "US Open",
                "major": True,
                "groupings": [{
                    "grouping": {"slug": "mens-singles", "displayName": "Men's Singles"},
                    "competitions": [{
                        "id": "shelton-alcaraz-qf",
                        "date": "2026-09-09T03:05:00Z",
                        "status": {"type": {"state": "post", "completed": True}},
                        "type": {"slug": "mens-singles", "text": "Men's Singles"},
                        "round": {"displayName": "Quarterfinal"},
                        "competitors": [
                            {
                                "id": "shelton",
                                "winner": True,
                                "athlete": {"displayName": "Ben Shelton"},
                                "linescores": [
                                    {"value": 6, "winner": False},
                                    {"value": 6, "winner": True},
                                    {"value": 6, "winner": True},
                                    {"value": 1, "winner": False},
                                    {"value": 7, "winner": True},
                                ],
                            },
                            {
                                "id": "alcaraz",
                                "winner": False,
                                "athlete": {"displayName": "Carlos Alcaraz"},
                                "linescores": [
                                    {"value": 7, "winner": True},
                                    {"value": 1, "winner": False},
                                    {"value": 3, "winner": False},
                                    {"value": 6, "winner": True},
                                    {"value": 6, "winner": False},
                                ],
                            },
                        ],
                    }],
                }],
            }]
        }
        matches = flatten_scoreboard_results(payload, "https://example.invalid")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["tour"], "ATP")
        self.assertTrue(matches[0]["major"])
        self.assertEqual(matches[0]["round"], "Quarterfinal")
        self.assertEqual(matches[0]["matchKey"], "atp:shelton-alcaraz-qf")

        # Even if a tour-specific endpoint is mislabeled or broad, explicit draw
        # metadata must win so the same men's match cannot also become WTA.
        mislabeled = flatten_scoreboard_results(
            payload,
            "https://example.invalid/wta",
            default_tour="wta",
        )
        self.assertEqual(len(mislabeled), 1)
        self.assertEqual(mislabeled[0]["tour"], "ATP")
        self.assertEqual(mislabeled[0]["matchKey"], "atp:shelton-alcaraz-qf")

    def test_verified_tennis_identity_beats_higher_priced_prototype_duplicate(self) -> None:
        official = {
            "id": "athlete-tennis-ben-shelton",
            "name": "Ben Shelton",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "marketSegment": "Current",
            "sourceNamespace": "curated-individual-sport-roster",
            "sourceType": "official-ranking-roster",
            "verificationStatus": "Official current ranking snapshot",
            "sourceRank": 5,
            "pricingConfidence": 0.7,
            "marketPrice": 110.34,
            "priceEvents": [],
        }
        prototype = {
            "id": "cur-ben-shelton",
            "name": "Ben Shelton",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "marketSegment": "Current",
            "verificationStatus": "Prototype current seed — connect a live source before launch",
            "rosterSourceRank": 5,
            "pricingConfidence": 0.7,
            "marketPrice": 115.51,
            "priceEvents": [],
        }
        alcaraz = {
            "id": "athlete-tennis-carlos-alcaraz",
            "name": "Carlos Alcaraz",
            "primaryCategory": "Athlete",
            "discipline": "Tennis",
            "marketSegment": "Current",
            "sourceNamespace": "curated-individual-sport-roster",
            "sourceType": "official-ranking-roster",
            "verificationStatus": "Official current ranking snapshot",
            "sourceRank": 2,
            "pricingConfidence": 0.7,
            "marketPrice": 139.16,
            "priceEvents": [],
        }
        self.assertGreater(
            verified_tennis_record_strength(official),
            verified_tennis_record_strength(prototype),
        )

        match = {
            "matchKey": "atp:shelton-alcaraz-qf",
            "competitionId": "shelton-alcaraz-qf",
            "tour": "ATP",
            "tournament": "US Open",
            "round": "Quarterfinal",
            "major": True,
            "startedAt": "2026-09-09T03:05:00Z",
            "sourceUrl": "https://example.invalid",
            "competitors": [
                {
                    "name": "Ben Shelton",
                    "normalizedName": "benshelton",
                    "winner": True,
                    "setsWon": 3,
                    "linescores": [],
                },
                {
                    "name": "Carlos Alcaraz",
                    "normalizedName": "carlosalcaraz",
                    "winner": False,
                    "setsWon": 2,
                    "linescores": [],
                },
            ],
        }
        updated, touched, added = tennis_base.apply_live_matches(
            [official, prototype, alcaraz],
            [match],
            max_move_pct=2.5,
        )
        self.assertEqual(touched, 2)
        self.assertEqual(added, 2)
        self.assertGreater(updated[0]["marketPrice"], official["marketPrice"])
        self.assertGreaterEqual(updated[0]["priceEvents"][-1]["movePct"], 4.0)
        self.assertEqual(updated[1]["marketPrice"], prototype["marketPrice"])
        self.assertEqual(updated[0]["lastPriceEvent"], "US Open · Quarterfinal · vs Carlos Alcaraz")

    def test_routine_golf_finish_remains_small(self) -> None:
        move = golf_tournament_move_results(
            finish=35,
            field_size=120,
            score_to_par=-2,
            status="FINISHED",
            major=False,
            player_record={"sourceRank": 30},
        )
        self.assertLess(abs(move), 0.5)

    def test_major_golf_breakthrough_can_exceed_old_cap(self) -> None:
        move = golf_tournament_move_results(
            finish=1,
            field_size=150,
            score_to_par=-18,
            status="FINISHED",
            major=True,
            player_record={"sourceRank": 140},
            max_move_pct=0.5,
        )
        self.assertGreater(move, 2.5)


if __name__ == "__main__":
    unittest.main()
