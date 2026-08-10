from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tennis_event_refresh import apply_live_matches, flatten_scoreboard, tennis_match_move


class TennisEventRefreshTests(unittest.TestCase):
    def sample_payload(self):
        return {
            "events": [{
                "id": "421-2026",
                "name": "Example Open",
                "major": False,
                "groupings": [{
                    "grouping": {"slug": "mens-singles", "displayName": "Men's Singles"},
                    "competitions": [{
                        "id": "183335",
                        "date": "2026-08-01T14:40Z",
                        "status": {"type": {"name": "STATUS_FINAL", "state": "post", "completed": True}},
                        "type": {"slug": "mens-singles", "text": "Men's Singles"},
                        "round": {"displayName": "Quarterfinal"},
                        "competitors": [
                            {
                                "id": "101",
                                "winner": True,
                                "athlete": {"id": "101", "displayName": "Player One"},
                                "linescores": [{"value": 6, "winner": True}, {"value": 6, "winner": True}],
                            },
                            {
                                "id": "202",
                                "winner": False,
                                "athlete": {"id": "202", "displayName": "Player Two"},
                                "linescores": [{"value": 3, "winner": False}, {"value": 4, "winner": False}],
                            },
                        ],
                    }],
                }],
            }]
        }

    def test_scoreboard_flattens_completed_singles_match(self):
        matches = flatten_scoreboard(self.sample_payload(), "atp", "https://example.invalid")
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match["round"], "Quarterfinal")
        self.assertEqual(match["competitors"][0]["name"], "Player One")
        self.assertTrue(match["competitors"][0]["winner"])
        self.assertEqual(match["competitors"][0]["setsWon"], 2)

    def test_win_is_positive_and_loss_is_negative(self):
        win = tennis_match_move(
            winner=True,
            round_name="Quarterfinal",
            major=False,
            sets_for=2,
            sets_against=0,
            player_record={"sourceRank": 12},
            opponent_record={"sourceRank": 8},
        )
        loss = tennis_match_move(
            winner=False,
            round_name="Quarterfinal",
            major=False,
            sets_for=0,
            sets_against=2,
            player_record={"sourceRank": 8},
            opponent_record={"sourceRank": 12},
        )
        self.assertGreater(win, 0)
        self.assertLess(loss, 0)
        self.assertLessEqual(abs(win), 2.5)
        self.assertLessEqual(abs(loss), 2.5)

    def test_live_match_is_applied_once(self):
        records = [
            {
                "id": "one",
                "name": "Player One",
                "primaryCategory": "Athlete",
                "discipline": "Tennis",
                "sourceRank": 10,
                "marketPrice": 100.0,
                "pricingConfidence": 0.9,
                "priceEvents": [],
            },
            {
                "id": "two",
                "name": "Player Two",
                "primaryCategory": "Athlete",
                "discipline": "Tennis",
                "sourceRank": 20,
                "marketPrice": 80.0,
                "pricingConfidence": 0.8,
                "priceEvents": [],
            },
        ]
        matches = flatten_scoreboard(self.sample_payload(), "atp", "https://example.invalid")
        updated, touched, added = apply_live_matches(records, matches, max_move_pct=2.5)
        self.assertEqual(touched, 2)
        self.assertEqual(added, 2)
        self.assertGreater(updated[0]["marketPrice"], 100.0)
        self.assertLess(updated[1]["marketPrice"], 80.0)

        rerun, touched_again, added_again = apply_live_matches(updated, matches, max_move_pct=2.5)
        self.assertEqual(touched_again, 0)
        self.assertEqual(added_again, 0)
        self.assertEqual(rerun[0]["marketPrice"], updated[0]["marketPrice"])
        self.assertEqual(rerun[1]["marketPrice"], updated[1]["marketPrice"])


if __name__ == "__main__":
    unittest.main()
