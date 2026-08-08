from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from append_game_event_history import append_events
from non_athlete_event_refresh import apply_events, event_move_pct, qid_for
from normalize_non_athlete_event_timestamps import normalize_record


class NonAthleteEventPricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.music = {
            "id": "music-test",
            "name": "Test Singer",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-music-strict",
            "sourceRecordId": "Q123",
            "marketPrice": 100.0,
            "pricingConfidence": 0.82,
            "activeMetrics": {"audience": 90},
            "trend": [100.0],
            "priceEvents": [],
        }
        self.actor = {
            "id": "actor-test",
            "name": "Test Actor",
            "primaryCategory": "Actor",
            "wikidataSourceRecordId": "Q456",
            "marketPrice": 80.0,
            "pricingConfidence": 0.78,
            "activeMetrics": {"audience": 70},
            "trend": [80.0],
            "priceEvents": [],
        }

    def test_no_supported_event_means_no_price_move(self) -> None:
        updated, count = apply_events(self.music, [])
        self.assertEqual(count, 0)
        self.assertEqual(updated["marketPrice"], 100.0)
        self.assertEqual(updated.get("priceEvents"), [])

    def test_music_release_creates_durable_price_event(self) -> None:
        event = {
            "eventKey": "musicbrainz:abc",
            "eventId": "abc",
            "eventType": "music-release",
            "provider": "MusicBrainz + Wikidata",
            "sourceUrl": "https://musicbrainz.org/release-group/abc",
            "name": "New Album — Album",
            "startedAt": "2026-08-08T00:00:00Z",
            "releaseType": "Album",
        }
        updated, count = apply_events(self.music, [event])
        self.assertEqual(count, 1)
        self.assertGreater(updated["marketPrice"], 100.0)
        self.assertEqual(updated["priceEvents"][0]["priceBefore"], 100.0)
        self.assertEqual(updated["priceEvents"][0]["priceAfter"], updated["marketPrice"])
        self.assertEqual(updated["lastEventType"], "music-release")
        self.assertEqual(updated["priceExplanation"]["headline"], "New album release")

        with_history, added = append_events(updated)
        self.assertEqual(added, 2)
        points = [point for point in with_history["priceHistory"] if point.get("eventId") == "musicbrainz:abc"]
        self.assertEqual(len(points), 2)
        self.assertTrue(all(point.get("eventType") == "music-release" for point in points))

    def test_actor_release_creates_separate_event(self) -> None:
        event = {
            "eventKey": "wikidata:actor-release:Q456:Q999:2026-08-08",
            "eventId": "Q999",
            "eventType": "actor-release",
            "provider": "Wikidata",
            "sourceUrl": "https://www.wikidata.org/wiki/Q999",
            "name": "Released project: Example Film",
            "startedAt": "2026-08-08T00:00:00Z",
        }
        updated, count = apply_events(self.actor, [event])
        self.assertEqual(count, 1)
        self.assertGreater(updated["marketPrice"], 80.0)
        self.assertEqual(updated["lastPriceEventId"], event["eventKey"])
        self.assertEqual(updated["priceExplanation"]["headline"], "New screen release")

    def test_upcoming_actor_project_is_charted_when_verified_not_on_release_day(self) -> None:
        event = {
            "eventKey": "wikidata:actor-upcoming-project:Q456:Q777:2027-01-01",
            "eventId": "Q777",
            "eventType": "actor-upcoming-project",
            "provider": "Wikidata",
            "name": "Upcoming project: Future Film",
            "startedAt": "2027-01-01T00:00:00Z",
        }
        updated, count = apply_events(self.actor, [event])
        self.assertEqual(count, 1)
        normalized, changed = normalize_record(updated, datetime(2026, 8, 8, 12, tzinfo=timezone.utc))
        self.assertEqual(changed, 1)
        stored = normalized["priceEvents"][0]
        self.assertEqual(stored["scheduledFor"], "2027-01-01T00:00:00Z")
        self.assertLessEqual(stored["startedAt"], "2026-08-08T12:00:00Z")
        self.assertEqual(normalized["lastPriceEventAt"], stored["startedAt"])

    def test_duplicate_event_cannot_move_price_twice(self) -> None:
        event = {
            "eventKey": "musicbrainz:dup",
            "eventId": "dup",
            "eventType": "music-release",
            "provider": "MusicBrainz + Wikidata",
            "name": "Single — Single",
            "startedAt": "2026-08-08T00:00:00Z",
            "releaseType": "Single",
        }
        once, count = apply_events(self.music, [event])
        self.assertEqual(count, 1)
        price_once = once["marketPrice"]
        twice, second_count = apply_events(once, [event])
        self.assertEqual(second_count, 0)
        self.assertEqual(twice["marketPrice"], price_once)
        self.assertEqual(len(twice["priceEvents"]), 1)

    def test_multiple_events_are_applied_in_time_order(self) -> None:
        events = [
            {
                "eventKey": "wikidata:nomination:Q456:Q2",
                "eventId": "Q2",
                "eventType": "nomination",
                "provider": "Wikidata",
                "name": "Nomination: Example",
                "startedAt": "2026-08-08T12:00:00Z",
            },
            {
                "eventKey": "wikidata:award:Q456:Q1",
                "eventId": "Q1",
                "eventType": "award",
                "provider": "Wikidata",
                "name": "Award: Example",
                "startedAt": "2026-08-07T12:00:00Z",
            },
        ]
        updated, count = apply_events(self.actor, events)
        self.assertEqual(count, 2)
        self.assertEqual(updated["priceEvents"][-2]["eventType"], "award")
        self.assertEqual(updated["priceEvents"][-1]["eventType"], "nomination")
        self.assertEqual(updated["lastEventType"], "nomination")

    def test_event_moves_are_conservative_and_bounded(self) -> None:
        event = {"eventType": "award"}
        move = event_move_pct(self.music, event)
        self.assertGreater(move, 0)
        self.assertLessEqual(move, 1.5)

    def test_wikidata_identity_resolution_supports_both_catalog_shapes(self) -> None:
        self.assertEqual(qid_for(self.music), "Q123")
        self.assertEqual(qid_for(self.actor), "Q456")


if __name__ == "__main__":
    unittest.main()
