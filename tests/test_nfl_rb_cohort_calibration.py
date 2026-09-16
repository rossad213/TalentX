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

import nfl_rb_cohort_calibration as rb  # noqa: E402
from repair_nfl_production_prices_safe import repair_catalog  # noqa: E402


class NFLRBCohortCalibrationTests(unittest.TestCase):
    def record(
        self,
        player_id: str,
        name: str,
        *,
        market_price: float,
        stale_recent: float,
        career: float,
        usage: float,
        awards: float,
        rushing_yards: float,
        rushing_tds: float,
        carries: float,
        receptions: float,
        receiving_yards: float,
        receiving_tds: float,
        age: float = 24,
        old_sample: int = 1,
    ) -> dict:
        year = datetime.now(timezone.utc).year
        ypc = rushing_yards / carries if carries else 0.0
        ypr = receiving_yards / receptions if receptions else 0.0
        return {
            "id": player_id,
            "name": name,
            "primaryCategory": "Athlete",
            "discipline": "American Football",
            "leagueOrMedium": "NFL",
            "role": "Running Back",
            "careerStatus": "Active",
            "careerStage": "Early Career",
            "age": age,
            "experienceYears": 3,
            "starter": True,
            "professionalGames": 40,
            "marketPrice": market_price,
            "previousMarketPrice": market_price,
            "trend": [market_price],
            "priceHistory": [],
            "priceEvents": [
                {
                    "eventType": "game",
                    "eventKey": f"{player_id}-week1",
                    "startedAt": f"{year}-09-13T17:00:00Z",
                    "stats": {
                        "car": carries,
                        "rushingYards": rushing_yards,
                        "yardsPerRushAttempt": ypc,
                        "rushingTouchdowns": rushing_tds,
                        "receptions": receptions,
                        "receivingYards": receiving_yards,
                        "yardsPerReception": ypr,
                        "receivingTouchdowns": receiving_tds,
                    },
                    "priceBefore": market_price,
                    "priceAfter": market_price,
                }
            ],
            "activeMetrics": {
                "performance": 80,
                "achievements": 50,
                "consistency": 80,
                "potential": 85,
                "availability": 75,
                "audience": 50,
            },
            "pricingEvidenceSummary": {
                "recentSampleGamesEstimate": old_sample,
                "rawSignals": {
                    "recentProduction": stale_recent,
                    "careerProduction": career,
                    "efficiency": 110.0,
                    "usage": usage,
                    "careerUsage": 50.0,
                    "awardPoints": awards,
                },
            },
        }

    def walker(self) -> dict:
        # The failure shape from the live catalog: 2025 full-season production
        # was stored as recent while one 2026 game was incorrectly treated as a
        # nine-game sample because generic usage was 17.
        return self.record(
            "walker", "Kenneth Walker III",
            market_price=223.05,
            stale_recent=162.1333,
            career=233.6173,
            usage=17.0,
            awards=3.0,
            rushing_yards=173,
            rushing_tds=1,
            carries=23,
            receptions=3,
            receiving_yards=18,
            receiving_tds=1,
            old_sample=9,
        )

    def gibbs(self) -> dict:
        return self.record(
            "gibbs", "Jahmyr Gibbs",
            market_price=129.03,
            stale_recent=34.25,
            career=326.5019,
            usage=1.0,
            awards=0.0,
            rushing_yards=156,
            rushing_tds=2,
            carries=29,
            receptions=5,
            receiving_yards=30,
            receiving_tds=0,
            old_sample=1,
        )

    def filler(self, index: int) -> dict:
        return self.record(
            f"filler-{index}", f"Filler RB {index}",
            market_price=70.0,
            stale_recent=15.0 + index,
            career=100.0 + index * 15,
            usage=1.0,
            awards=0.0,
            rushing_yards=45 + index * 7,
            rushing_tds=1 if index % 3 == 0 else 0,
            carries=12,
            receptions=2 + (index % 3),
            receiving_yards=10 + index * 3,
            receiving_tds=0,
            old_sample=1,
        )

    def test_event_window_replaces_stale_recent_and_fake_nine_game_sample(self):
        records = [self.walker(), self.gibbs(), *[self.filler(i) for i in range(8)]]
        context = rb.prepare_rb_context(records)
        walker = records[0]
        gibbs = records[1]

        self.assertAlmostEqual(
            walker["pricingEvidenceSummary"]["rawSignals"]["recentProduction"],
            33.5667,
            places=4,
        )
        self.assertAlmostEqual(
            gibbs["pricingEvidenceSummary"]["rawSignals"]["recentProduction"],
            34.25,
            places=4,
        )
        rb.apply_rb_percentiles(walker, context)
        self.assertEqual(walker["pricingEvidenceSummary"]["recentSampleGamesEstimate"], 1)
        self.assertAlmostEqual(walker["pricingEvidenceSummary"]["recentSampleWeight"], 1 / 6, places=4)

    def test_sparse_awards_do_not_create_fifty_point_achievement_gap(self):
        records = [self.walker(), self.gibbs(), *[self.filler(i) for i in range(8)]]
        context = rb.prepare_rb_context(records)
        walker = records[0]
        gibbs = records[1]
        rb.apply_rb_percentiles(walker, context)
        rb.apply_rb_percentiles(gibbs, context)

        walker_achievement = walker["pricingEvidenceSummary"]["percentiles"]["awardPoints"]
        gibbs_achievement = gibbs["pricingEvidenceSummary"]["percentiles"]["awardPoints"]
        self.assertLess(abs(walker_achievement - gibbs_achievement), 0.15)
        self.assertLess(walker_achievement, 0.95)
        self.assertGreater(gibbs_achievement, 0.50)

    def test_safe_repair_reorders_live_failure_shape_without_player_overrides(self):
        records = [self.walker(), self.gibbs(), *[self.filler(i) for i in range(8)]]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            catalog = directory / "sports.json"
            draft = directory / "draft.json"
            catalog.write_text(json.dumps(records), encoding="utf-8")
            draft.write_text(json.dumps({"records": []}), encoding="utf-8")
            repair_catalog(
                catalog,
                repaired_at="2026-09-16T00:00:00Z",
                draft_metadata_path=draft,
            )
            updated = {item["id"]: item for item in json.loads(catalog.read_text(encoding="utf-8"))}

        walker = updated["walker"]
        gibbs = updated["gibbs"]
        self.assertLess(walker["marketPrice"], 223.05)
        self.assertGreater(gibbs["marketPrice"], 129.03)
        self.assertGreater(gibbs["marketPrice"], walker["marketPrice"])
        self.assertEqual(walker["nflProductionRebase"]["reason"], "rb-current-season-cohort-calibration")
        self.assertEqual(gibbs["nflProductionRebase"]["reason"], "rb-current-season-cohort-calibration")
        self.assertEqual(walker["nflRbCohortCalibrationVersion"], rb.RB_COHORT_CALIBRATION_VERSION)
        self.assertEqual(gibbs["nflRbCohortCalibrationVersion"], rb.RB_COHORT_CALIBRATION_VERSION)


if __name__ == "__main__":
    unittest.main()
