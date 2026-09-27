from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from motorsport_event_refresh import (
    apply_refresh, build_season_evidence, race_result_move,
    races_from_results_pages, standings_from_payload,
)


class MotorsportEventRefreshTests(unittest.TestCase):
    def standings_payload(self):
        return {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [
            {"position": "1", "points": "250", "wins": "5", "Driver": {"driverId": "russell", "givenName": "George", "familyName": "Russell", "nationality": "British"}, "Constructors": [{"name": "Mercedes"}]},
            {"position": "2", "points": "220", "wins": "3", "Driver": {"driverId": "verstappen", "givenName": "Max", "familyName": "Verstappen", "nationality": "Dutch"}, "Constructors": [{"name": "Red Bull Racing"}]},
        ]}]}}}

    def results_page(self):
        return {"MRData": {"RaceTable": {"Races": [{
            "season": "2026", "round": "1", "raceName": "Example Grand Prix", "date": "2026-03-10", "time": "14:00:00Z",
            "Results": [
                {"position": "1", "positionText": "1", "points": "25", "grid": "2", "status": "Finished", "Driver": {"driverId": "russell", "givenName": "George", "familyName": "Russell"}, "Constructor": {"name": "Mercedes"}, "FastestLap": {"rank": "1"}},
                {"position": "2", "positionText": "2", "points": "18", "grid": "1", "status": "Finished", "Driver": {"driverId": "verstappen", "givenName": "Max", "familyName": "Verstappen"}, "Constructor": {"name": "Red Bull Racing"}},
            ],
        }]}}}

    def test_parsers_and_verified_event_ledger(self):
        standings = standings_from_payload(self.standings_payload())
        races = races_from_results_pages([self.results_page()])
        self.assertEqual(len(standings), 2)
        self.assertEqual(len(races), 1)
        stats, events = build_season_evidence(races)
        self.assertEqual(stats["georgerussell"]["wins"], 1)
        self.assertEqual(len(events["maxverstappen"]), 1)
        self.assertTrue(events["georgerussell"][0]["verified"])

    def test_win_is_positive_and_driver_error_dnf_is_negative(self):
        win, _ = race_result_move(actual_position=1, expected_position=5, field_size=20, grid=3, status="Finished", fastest_lap_rank=1, prior_starts=20)
        dnf, _ = race_result_move(actual_position=20, expected_position=3, field_size=20, grid=3, status="Collision", fastest_lap_rank=None, prior_starts=20)
        self.assertGreater(win, 0)
        self.assertLess(dnf, 0)

    def test_first_refresh_backfills_without_double_moving_current_price(self):
        records = [{
            "id": "athlete-motorsport-george-russell", "name": "George Russell",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "sourceNamespace": "curated-individual-sport-roster", "marketPrice": 120.0, "fundamentalValue": 120.0,
            "pricingConfidence": 0.64, "activeMetrics": {"performance": 80, "achievements": 75, "consistency": 75, "potential": 75, "availability": 88, "audience": 80},
            "priceEvents": [], "priceHistory": [],
        }, {
            "id": "wiki-racer", "name": "Generic Racer", "primaryCategory": "Athlete", "discipline": "Motorsport",
            "leagueOrMedium": "International Motorsport", "sourceNamespace": "wikidata-individual-sport", "marketSegment": "Current", "marketPrice": 90.0, "pricingConfidence": 0.56,
        }]
        standings = standings_from_payload(self.standings_payload())
        races = races_from_results_pages([self.results_page()])
        updated, counts = apply_refresh(records, standings, races, season=2026, refreshed_at="2026-03-10T18:00:00Z")
        russell = next(r for r in updated if r["name"] == "George Russell")
        generic = next(r for r in updated if r["name"] == "Generic Racer")
        self.assertEqual(russell["marketPrice"], 120.0)
        self.assertTrue(russell["professionEvidenceVerified"])
        self.assertTrue(russell["priceEvents"][0]["historicalBackfill"])
        self.assertEqual(generic["marketSegment"], "Under Review")
        self.assertEqual(counts["movedToUnderReview"], 1)

    def test_standings_only_participant_is_not_auto_promoted_to_current(self):
        records = [{
            "id": "athlete-motorsport-george-russell", "name": "George Russell",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "Formula 1",
            "sourceNamespace": "curated-individual-sport-roster", "marketPrice": 120.0,
            "pricingConfidence": 0.64, "activeMetrics": {"performance": 80, "achievements": 75, "consistency": 75, "potential": 75, "availability": 88, "audience": 80},
        }, {
            "id": "wiki-max", "name": "Max Verstappen",
            "primaryCategory": "Athlete", "discipline": "Motorsport", "leagueOrMedium": "International Motorsport",
            "sourceNamespace": "wikidata-individual-sport", "marketSegment": "Current",
            "marketPrice": 95.0, "pricingConfidence": 0.56,
        }]
        standings = standings_from_payload(self.standings_payload())
        races = races_from_results_pages([self.results_page()])
        updated, _ = apply_refresh(records, standings, races, season=2026, refreshed_at="2026-03-10T18:00:00Z")
        max_rows = [r for r in updated if r["name"] == "Max Verstappen"]
        self.assertEqual(len(max_rows), 1)
        self.assertEqual(max_rows[0]["marketSegment"], "Under Review")
        self.assertNotEqual(max_rows[0].get("leagueOrMedium"), "Formula 1")

    def test_new_race_applies_once_after_migration(self):
        standings = standings_from_payload(self.standings_payload())
        races = races_from_results_pages([self.results_page()])
        base = [{
            "id": "athlete-motorsport-george-russell", "name": "George Russell", "primaryCategory": "Athlete",
            "discipline": "Motorsport", "leagueOrMedium": "Formula 1", "marketPrice": 100.0,
            "sourceNamespace": "curated-individual-sport-roster",
            "motorsportMarketMigrationVersion": "1.0-motorsport-verified-race-ledger",
            "priceEvents": [{
                "eventKey": "jolpica-f1:2026:0:russell", "eventId": "jolpica-f1:2026:0:russell",
                "eventType": "game", "provider": "Jolpica F1", "verified": True,
                "startedAt": "2026-03-01T14:00:00Z", "movePct": 0.5,
            }],
            "activeMetrics": {"performance": 80, "achievements": 75, "consistency": 75, "potential": 75, "availability": 88, "audience": 80},
        }]
        one, counts = apply_refresh(base, standings, races, season=2026, refreshed_at="2026-03-10T18:00:00Z")
        self.assertEqual(counts["liveEventsApplied"], 1)
        self.assertNotEqual(one[0]["marketPrice"], 100.0)
        two, counts2 = apply_refresh(one, standings, races, season=2026, refreshed_at="2026-03-10T19:00:00Z")
        self.assertEqual(counts2["liveEventsApplied"], 0)
        self.assertEqual(two[0]["marketPrice"], one[0]["marketPrice"])


if __name__ == "__main__":
    unittest.main()
