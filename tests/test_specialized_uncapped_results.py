from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from golf_event_refresh_results import golf_tournament_move_results  # noqa: E402
from tennis_event_refresh_results import tennis_match_move_results  # noqa: E402


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
