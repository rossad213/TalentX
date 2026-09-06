from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class NoHardMovementCapsTests(unittest.TestCase):
    def test_legacy_event_caps_are_absent(self):
        files = {
            "scripts/athlete_career_event_refresh.py": ["MAX_EVENT_MOVE_PCT", "-15.0, 15.0"],
            "scripts/non_athlete_event_refresh.py": ["MAX_EVENT_MOVE_PCT"],
            "scripts/non_athlete_outcome_refresh.py": ["MAX_OUTCOME_MOVE_PCT"],
            "scripts/creator_attention_refresh.py": ["-1.5, 1.5", "-15.0, 15.0"],
            "market_jobs/soccer_json_history.py": ["min(max_move, 1.50)", "min(max_move, 1.25)"],
            "scripts/hourly_price_refresh.py": ["-max_game_move_pct, max_game_move_pct", "-2.25, 2.25"],
        }
        for rel, banned in files.items():
            text = (ROOT / rel).read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"legacy movement cap returned in {rel}: {token}")

if __name__ == "__main__":
    unittest.main()
