#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nfl_production_pricing import MODEL_VERSION, production_fair_value  # noqa: E402
from repair_nfl_production_prices_safe import SAFE_REPAIR_VERSION, repair_catalog  # noqa: E402


class NFLRookieUsageHandoffTests(unittest.TestCase):
    def hunter_shape(self, *, meaningful_usage: bool = False) -> dict:
        current_year = datetime.now(timezone.utc).year
        raw = {
            "recentProduction": 0.0,
            "careerProduction": 0.0,
            "efficiency": 0.0,
            "usage": 0.0,
            "careerUsage": 0.0,
            "awardPoints": 0.0,
        }
        if meaningful_usage:
            raw.update(
                {
                    "recentProduction": 18.0,
                    "careerProduction": 18.0,
                    "efficiency": 22.0,
                    "usage": 6.0,
                    "careerUsage": 6.0,
                }
            )
        return {
            "id": "hunter-shape",
            "name": "Jarquez Hunter Shape",
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": "Running Back",
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": 23,
            "experienceYears": 2,
            "starter": False,
            "professionalGames": 5,
            "draftYear": current_year - 1,
            "draftRound": 4,
            "draftPick": 117,
            "careerScore": 35.0,
            "marketPrice": 5.0,
            "previousMarketPrice": 5.0,
            "trend": [5.0],
            "priceEvents": [],
            "priceHistory": [],
            "activeMetrics": {
                "performance": 36,
                "achievements": 14,
                "consistency": 38,
                "potential": 70,
                "availability": 82,
                "audience": 40,
            },
            "pricingEvidenceSummary": {"rawSignals": raw},
        }

    def test_zero_usage_appearances_keep_time_decayed_rookie_ipo_support(self):
        record = self.hunter_shape(meaningful_usage=False)
        _, fair, explanation = production_fair_value(record)

        self.assertIsNotNone(explanation)
        self.assertEqual(explanation["rookieEvidenceGames"], 0.0)
        self.assertEqual(explanation["rookieInfluence"], 0.60)
        self.assertGreater(explanation["rookieIpoAnchor"], 20.0)
        self.assertEqual(
            explanation["rookieAnchorReconstruction"]["source"],
            "reconstructed-from-draft-metadata",
        )
        self.assertGreater(fair, 5.0)

    def test_meaningful_usage_transitions_player_off_reconstructed_ipo(self):
        record = self.hunter_shape(meaningful_usage=True)
        _, fair, explanation = production_fair_value(record)

        self.assertIsNotNone(explanation)
        self.assertIsNone(explanation["rookieIpoAnchor"])
        self.assertEqual(explanation["rookieInfluence"], 0.0)
        self.assertAlmostEqual(fair, explanation["careerFairValue"], places=2)

    def test_safe_repair_updates_old_zero_usage_handoff_even_with_safe_marker(self):
        record = self.hunter_shape(meaningful_usage=False)
        record["nflProductionPriceModelVersion"] = "3.0-nfl-position-normalized-sample-stable-rookie-ipo"
        record["nflProductionRebaseVersion"] = SAFE_REPAIR_VERSION

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "sports.json"
            draft = directory / "draft.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repriced, synchronized, sample_repairs, draft_repairs = repair_catalog(
                path,
                repaired_at="2026-09-16T00:00:00Z",
                draft_metadata_path=draft,
            )
            updated = json.loads(path.read_text(encoding="utf-8"))[0]

        self.assertEqual(repriced, 1)
        self.assertEqual(synchronized, 1)
        self.assertEqual(sample_repairs, 0)
        self.assertEqual(draft_repairs, 0)
        self.assertGreater(updated["marketPrice"], 5.0)
        self.assertEqual(updated["nflProductionPriceModelVersion"], MODEL_VERSION)
        self.assertEqual(updated["nflProductionRebaseVersion"], SAFE_REPAIR_VERSION)
        self.assertEqual(updated["nflProductionRebase"]["reason"], "meaningful-usage-rookie-ipo-restored")

    def test_safe_repair_does_not_reprice_meaningful_usage_player_for_model_bump_alone(self):
        record = self.hunter_shape(meaningful_usage=True)
        record["marketPrice"] = 88.88
        record["previousMarketPrice"] = 88.88
        record["trend"] = [88.88]
        record["nflProductionPriceModelVersion"] = "3.0-nfl-position-normalized-sample-stable-rookie-ipo"
        record["nflProductionRebaseVersion"] = SAFE_REPAIR_VERSION

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "sports.json"
            draft = directory / "draft.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repriced, synchronized, _, _ = repair_catalog(
                path,
                repaired_at="2026-09-16T00:00:00Z",
                draft_metadata_path=draft,
            )
            updated = json.loads(path.read_text(encoding="utf-8"))[0]

        self.assertEqual(repriced, 0)
        self.assertEqual(synchronized, 1)
        self.assertEqual(updated["marketPrice"], 88.88)


if __name__ == "__main__":
    unittest.main()
