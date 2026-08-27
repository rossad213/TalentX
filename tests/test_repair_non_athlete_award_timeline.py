import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from repair_non_athlete_award_timeline import extract_statement_events, repair_record


class AwardTimelineRepairTests(unittest.TestCase):
    def _claim(self, qid, statement_id, when=None, precision=11):
        claim = {
            "id": statement_id,
            "mainsnak": {"datavalue": {"value": {"id": qid}}},
        }
        if when is not None:
            claim["qualifiers"] = {
                "P585": [{
                    "datavalue": {
                        "value": {
                            "time": when,
                            "precision": precision,
                        }
                    }
                }]
            }
        return claim

    def test_exact_day_award_uses_real_event_date(self):
        entity = {"claims": {"P166": [self._claim("QAWARD", "S1", "+2006-01-16T00:00:00Z", 11)]}}
        dated, unresolved, _labels = extract_statement_events(entity, "QPERSON")
        self.assertEqual(len(dated), 1)
        self.assertEqual(len(unresolved), 0)
        self.assertEqual(dated[0]["when"].date().isoformat(), "2006-01-16")

    def test_year_only_award_does_not_invent_a_pricing_day(self):
        entity = {"claims": {"P166": [self._claim("QAWARD", "S1", "+2006-00-00T00:00:00Z", 9)]}}
        dated, unresolved, _labels = extract_statement_events(entity, "QPERSON")
        self.assertEqual(dated, [])
        self.assertEqual(len(unresolved), 1)

    def test_legacy_discovery_date_is_replaced_without_double_counting(self):
        now = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
        record = {
            "id": "cur-example",
            "name": "Example Actor",
            "primaryCategory": "Actor",
            "wikidataSourceRecordId": "QPERSON",
            "marketPrice": 100.72,
            "priceEvents": [{
                "eventKey": "wikidata:award:QPERSON:QAWARD",
                "eventId": "QAWARD",
                "eventType": "award",
                "provider": "Wikidata",
                "startedAt": "2026-08-24T00:00:00Z",
                "name": "Award: Example Award",
                "claimQid": "QAWARD",
                "movePct": 0.72,
                "priceBefore": 100.0,
                "priceAfter": 100.72,
            }],
            "activeMetrics": {"audience": 50},
            "pricingConfidence": 0.6,
        }
        entity = {"claims": {"P166": [self._claim("QAWARD", "S1", "+2006-01-16T00:00:00Z", 11)]}}
        repaired, stats = repair_record(record, entity, {"QAWARD": "Example Award"}, now)
        award_events = [e for e in repaired["priceEvents"] if e.get("eventType") == "award"]
        self.assertEqual(len(award_events), 1)
        self.assertEqual(award_events[0]["startedAt"], "2006-01-16T00:00:00Z")
        self.assertEqual(award_events[0]["eventOccurredAt"], "2006-01-16T00:00:00Z")
        self.assertEqual(repaired["dailyChange"], 0.0)
        self.assertGreaterEqual(stats["corrected"], 1)

    def test_undated_legacy_award_is_removed_from_price_events(self):
        now = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
        record = {
            "id": "cur-example",
            "name": "Example Actor",
            "primaryCategory": "Actor",
            "wikidataSourceRecordId": "QPERSON",
            "marketPrice": 100.72,
            "priceEvents": [{
                "eventKey": "wikidata:award:QPERSON:QAWARD",
                "eventType": "award",
                "provider": "Wikidata",
                "startedAt": "2026-08-24T00:00:00Z",
                "name": "Award: Example Award",
                "claimQid": "QAWARD",
                "movePct": 0.72,
            }],
            "activeMetrics": {"audience": 50},
            "pricingConfidence": 0.6,
        }
        entity = {"claims": {"P166": [self._claim("QAWARD", "S1")]}}
        repaired, _stats = repair_record(record, entity, {"QAWARD": "Example Award"}, now)
        self.assertFalse(any(e.get("eventType") == "award" for e in repaired["priceEvents"]))
        self.assertEqual(len(repaired["unpricedCareerEvidence"]), 1)
        self.assertEqual(repaired["unpricedCareerEvidence"][0]["timelineStatus"], "date-unresolved-no-price-impact")
        self.assertLess(repaired["marketPrice"], 100.72)


if __name__ == "__main__":
    unittest.main()
