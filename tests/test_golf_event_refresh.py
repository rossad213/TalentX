from __future__ import annotations

import unittest

from scripts.explain_event_pricing_only import explain_only
from scripts.golf_event_refresh import (
    apply_live_tournaments,
    flatten_scoreboard,
    golf_tournament_move,
)


class GolfEventRefreshTests(unittest.TestCase):
    def sample_payload(self):
        return {
            "events": [{
                "id": "401999001",
                "name": "Masters Tournament",
                "endDate": "2026-04-12T23:00:00Z",
                "competitions": [{
                    "id": "401999001",
                    "endDate": "2026-04-12T23:00:00Z",
                    "status": {"period": 4, "type": {"completed": True, "state": "post", "name": "STATUS_FINAL"}},
                    "competitors": [
                        {
                            "id": "100",
                            "order": 1,
                            "athlete": {"id": "100", "displayName": "Scottie Scheffler"},
                            "score": "-13",
                            "linescores": [{"value": 68}, {"value": 70}, {"value": 69}, {"value": 68}],
                        },
                        {
                            "id": "200",
                            "order": 8,
                            "athlete": {"id": "200", "displayName": "Rory McIlroy"},
                            "score": "-5",
                            "linescores": [{"value": 71}, {"value": 70}, {"value": 72}, {"value": 70}],
                        },
                        {
                            "id": "300",
                            "order": 70,
                            "athlete": {"id": "300", "displayName": "Justin Thomas"},
                            "score": "+5",
                            "linescores": [{"value": 75}, {"value": 74}],
                        },
                    ],
                }],
            }]
        }

    def test_final_tournament_is_parsed(self):
        rows = flatten_scoreboard(self.sample_payload(), "pga", "https://example.test")
        self.assertEqual(len(rows), 1)
        tournament = rows[0]
        self.assertTrue(tournament["major"])
        self.assertEqual(tournament["fieldSize"], 3)
        self.assertEqual(tournament["competitors"][0]["finish"], 1)
        self.assertEqual(tournament["competitors"][0]["score"], -13.0)
        self.assertEqual(tournament["competitors"][2]["status"], "CUT_OR_INCOMPLETE")

    def test_win_is_positive(self):
        move = golf_tournament_move(
            finish=1,
            field_size=120,
            score_to_par=-14,
            status="FINISHED",
            major=False,
            player_record={"rosterPriority": 1},
        )
        self.assertGreater(move, 0)

    def test_top_ten_for_lower_ranked_player_is_positive(self):
        move = golf_tournament_move(
            finish=8,
            field_size=120,
            score_to_par=-8,
            status="FINISHED",
            major=False,
            player_record={"sourceRank": 60},
        )
        self.assertGreater(move, 0.3)

    def test_elite_player_poor_finish_can_be_negative(self):
        move = golf_tournament_move(
            finish=55,
            field_size=120,
            score_to_par=3,
            status="FINISHED",
            major=False,
            player_record={"rosterPriority": 2},
        )
        self.assertLess(move, 0)

    def test_missed_cut_is_negative(self):
        move = golf_tournament_move(
            finish=80,
            field_size=120,
            score_to_par=5,
            status="CUT_OR_INCOMPLETE",
            major=False,
            player_record={"sourceRank": 20},
        )
        self.assertLess(move, -0.2)

    def test_live_event_only_prices_once(self):
        records = [{
            "id": "golf-scottie",
            "name": "Scottie Scheffler",
            "primaryCategory": "Athlete",
            "discipline": "Golf",
            "marketPrice": 200.0,
            "rosterPriority": 1,
            "priceEvents": [],
        }]
        tournaments = flatten_scoreboard(self.sample_payload(), "pga", "https://example.test")
        first, touched, added = apply_live_tournaments(records, tournaments, max_move_pct=2.5)
        self.assertEqual(touched, 1)
        self.assertEqual(added, 1)
        self.assertGreater(first[0]["marketPrice"], 200.0)
        second, touched2, added2 = apply_live_tournaments(first, tournaments, max_move_pct=2.5)
        self.assertEqual(touched2, 0)
        self.assertEqual(added2, 0)
        self.assertEqual(second[0]["marketPrice"], first[0]["marketPrice"])

    def test_golf_explanation_is_not_replaced_by_generic_game_copy(self):
        records = [{
            "id": "golf-scottie",
            "name": "Scottie Scheffler",
            "primaryCategory": "Athlete",
            "discipline": "Golf",
            "marketPrice": 200.0,
            "rosterPriority": 1,
            "priceEvents": [],
        }]
        tournaments = flatten_scoreboard(self.sample_payload(), "pga", "https://example.test")
        first, _, _ = apply_live_tournaments(records, tournaments, max_move_pct=2.5)
        before = first[0]["priceExplanation"]
        after, changed = explain_only(first[0])
        self.assertFalse(changed)
        self.assertEqual(after["priceExplanation"], before)


if __name__ == "__main__":
    unittest.main()
