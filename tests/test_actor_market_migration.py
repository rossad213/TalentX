from __future__ import annotations
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from migrate_actor_market_state import MIGRATION_VERSION, canonicalize_events, migrate_record

class ActorMarketMigrationTests(unittest.TestCase):
    def actor(self,**updates):
        base={
            "id":"actor","name":"Test Actor","primaryCategory":"Actor",
            "sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q1",
            "fundamentalValue":90.0,"fairValue":90.0,"marketPrice":120.0,
            "priceEvents":[],"priceHistory":[],
        }
        base.update(updates);return base

    def test_upcoming_project_date_variants_collapse(self):
        record=self.actor(priceEvents=[
            {"eventKey":"wikidata:actor-upcoming-project:Q1:Q9:2026-10-16","eventId":"Q9","eventType":"actor-upcoming-project","provider":"Wikidata","workQid":"Q9","startedAt":"2026-08-01T00:00:00Z","movePct":.16},
            {"eventKey":"wikidata:actor-upcoming-project:Q1:Q9:2026-10-23","eventId":"Q9","eventType":"actor-upcoming-project","provider":"Wikidata","workQid":"Q9","startedAt":"2026-08-02T00:00:00Z","movePct":.16},
        ])
        events,aliases,removed=canonicalize_events(record)
        self.assertEqual(removed,1)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["eventKey"],"wikidata:actor-upcoming-project:Q1:Q9")

    def test_alias_box_office_events_collapse_by_source_identity(self):
        record=self.actor(priceEvents=[
            {"eventKey":"the-numbers:alias-a:Q9:2026-09-04","eventType":"actor-box-office-outcome","provider":"The Numbers","workQid":"Q9","startedAt":"2026-09-04T00:00:00Z","movePct":2.4},
            {"eventKey":"the-numbers:alias-b:Q9:2026-09-04","eventType":"actor-box-office-outcome","provider":"The Numbers","workQid":"Q9","startedAt":"2026-09-04T00:00:00Z","movePct":2.5},
        ])
        events,_,removed=canonicalize_events(record)
        self.assertEqual(removed,1)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["eventKey"],"the-numbers:Q1:Q9:2026-09-04")
        self.assertEqual(events[0]["movePct"],2.5)

    def test_duplicate_alias_outcomes_do_not_inflate_evidence_maturity_before_rebase(self):
        record=self.actor(
            sourceNamespace="wikidata-non-athlete",
            priceEvents=[
                {"eventKey":"the-numbers:alias-a:Q9:2026-09-04","eventType":"actor-box-office-outcome","provider":"The Numbers","workQid":"Q9","startedAt":"2026-09-04T00:00:00Z","movePct":2.4,"verified":True},
                {"eventKey":"the-numbers:alias-b:Q9:2026-09-04","eventType":"actor-box-office-outcome","provider":"The Numbers","workQid":"Q9","startedAt":"2026-09-04T00:00:00Z","movePct":2.5,"verified":True},
            ],
            activeMetrics={"performance":80,"achievements":75,"consistency":78,"potential":70,"availability":85,"audience":82},
            pricingConfidence=.90,dataConfidence=.90,
        )
        updated,changed=migrate_record(record,"2026-09-28T12:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["actorMarketDuplicateEventsRemoved"],1)
        self.assertEqual(updated["pricingV2"]["actorDirectEvidenceWeight"],2.5)
        self.assertEqual(len([e for e in updated["priceEvents"] if e.get("eventType")=="actor-box-office-outcome"]),1)

    def test_market_rebase_ends_at_current_fair_value(self):
        record=self.actor(priceEvents=[
            {"eventKey":"wikidata:award:Q1:Q2","eventId":"Q2","eventType":"award","provider":"Wikidata","startedAt":"2020-01-01T00:00:00Z","movePct":1.0},
            {"eventKey":"the-numbers:old:Q9:2026-09-04","eventType":"actor-box-office-outcome","provider":"The Numbers","workQid":"Q9","startedAt":"2026-09-04T00:00:00Z","movePct":2.0},
        ])
        updated,changed=migrate_record(record,"2026-09-28T12:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["marketPrice"],updated["fairValue"])
        self.assertEqual(updated["marketPrice"],updated["fundamentalValue"])
        self.assertEqual(updated["actorMarketMigrationVersion"],MIGRATION_VERSION)
        self.assertEqual(updated["priceEvents"][-1]["priceAfter"],updated["marketPrice"])
        self.assertEqual(updated["dailyChange"],0.0)
        self.assertEqual(updated["priceHistoryStatus"],"source-backed-partial-history")

    def test_stale_music_metadata_is_removed_from_actor(self):
        record=self.actor(
            sourceNamespace="wikidata-music-strict",
            musicCategoryVerified=True,
            musicBrainzArtistIds=["x"],
            verifiedMusicOccupations=["Singer"],
            pricingDataStatus="Strict music-source identity verified; streaming/chart/touring evidence pending",
        )
        updated,changed=migrate_record(record,"2026-09-28T12:00:00Z")
        self.assertTrue(changed)
        self.assertEqual(updated["sourceNamespace"],"wikidata-actor-resolved-from-music")
        self.assertNotIn("musicCategoryVerified",updated)
        self.assertNotIn("musicBrainzArtistIds",updated)
        self.assertIn("Screen-career",updated["pricingDataStatus"])

    def test_v1_epoch_record_rebases_again_after_v64_fair_value_change(self):
        record=self.actor(
            fundamentalValue=86.95,
            fairValue=86.95,
            marketPrice=170.56,
            previousMarketPrice=170.56,
            actorMarketMigrationVersion="1.0-actor-source-first-market-epoch",
            actorMarketMigratedAt="2026-09-28T22:01:01Z",
            actorMarketMigrationTargetPrice=170.56,
            priceEvents=[],
            priceHistory=[{
                "time":"2026-09-28T22:01:01Z",
                "eventId":"current-market-price",
                "eventType":"market-observation",
                "phase":"close",
                "price":170.56,
            }],
        )
        migrated,changed=migrate_record(record,"2026-09-28T23:30:00Z")
        self.assertTrue(changed)
        self.assertEqual(migrated["actorMarketMigrationVersion"],MIGRATION_VERSION)
        self.assertAlmostEqual(migrated["marketPrice"],migrated["fundamentalValue"],places=2)
        self.assertLess(migrated["marketPrice"],100.0)
        self.assertEqual(migrated["actorMarketMigrationPriorMarketPrice"],170.56)

    def test_actor_wide_attention_signal_is_deduped_across_projects(self):
        record=self.actor(
            sourceRecordId="Q4491",
            priceEvents=[
                {
                    "eventKey":"wikimedia:attention:Q4491:Q133273688:cool",
                    "eventType":"actor-attention-outcome",
                    "name":"Audience attention cool: 0.64× baseline",
                    "provider":"Wikimedia Analytics API",
                    "startedAt":"2026-09-11T04:39:03Z",
                    "movePct":-0.292,
                    "workQid":"Q133273688",
                },
                {
                    "eventKey":"wikimedia:attention:Q4491:Q135285630:cool",
                    "eventType":"actor-attention-outcome",
                    "name":"Audience attention cool: 0.64× baseline",
                    "provider":"Wikimedia Analytics API",
                    "startedAt":"2026-09-11T04:39:03Z",
                    "movePct":-0.286,
                    "workQid":"Q135285630",
                },
            ],
        )
        events,aliases,removed=canonicalize_events(record)
        attention=[e for e in events if e.get("eventType")=="actor-attention-outcome"]
        self.assertEqual(len(attention),1)
        self.assertEqual(removed,1)
        self.assertEqual(
            attention[0]["eventKey"],
            "wikimedia:attention:Q4491:2026-09-11T04:39:03Z",
        )

    def test_same_snapshot_attention_tiers_collapse_to_strongest_move(self):
        record=self.actor(
            sourceRecordId="Q1",
            priceEvents=[
                {"eventKey":"a","eventType":"actor-attention-outcome","outcomeTier":"hot","provider":"Wikimedia Analytics API","startedAt":"2026-09-20T12:00:00Z","movePct":0.51},
                {"eventKey":"b","eventType":"actor-attention-outcome","outcomeTier":"warm","provider":"Wikimedia Analytics API","startedAt":"2026-09-20T12:00:00Z","movePct":0.27},
            ],
        )
        events,_,removed=canonicalize_events(record)
        attention=[event for event in events if event.get("eventType")=="actor-attention-outcome"]
        self.assertEqual(len(attention),1)
        self.assertEqual(removed,1)
        self.assertEqual(attention[0]["movePct"],0.51)
        self.assertEqual(attention[0]["eventKey"],"wikimedia:attention:Q1:2026-09-20T12:00:00Z")

    def test_distinct_intraday_attention_observations_are_preserved(self):
        record=self.actor(
            sourceRecordId="Q1",
            priceEvents=[
                {"eventKey":"a","eventType":"actor-attention-outcome","provider":"Wikimedia Analytics API","startedAt":"2026-09-20T12:00:00Z","movePct":0.51},
                {"eventKey":"b","eventType":"actor-attention-outcome","provider":"Wikimedia Analytics API","startedAt":"2026-09-20T18:00:00Z","movePct":-0.20},
            ],
        )
        events,_,removed=canonicalize_events(record)
        self.assertEqual(len([event for event in events if event.get("eventType")=="actor-attention-outcome"]),2)
        self.assertEqual(removed,0)

    def test_orphan_legacy_recorded_award_history_is_removed(self):
        record=self.actor(
            priceEvents=[],
            priceHistory=[{
                "time":"2026-08-24T18:58:33Z",
                "eventId":"wikidata:award:Q43387663:Q2089918",
                "eventType":"recorded-event",
                "phase":"close",
                "price":170.19,
            }],
        )
        migrated,changed=migrate_record(record,"2026-09-28T23:30:00Z")
        self.assertTrue(changed)
        self.assertFalse(any(
            str(point.get("eventId") or "").startswith("wikidata:award:")
            for point in migrated["priceHistory"]
        ))

    def test_actor_without_surviving_priced_event_clears_stale_last_event_metadata(self):
        record=self.actor(
            lastPriceEventAt="2026-08-24T18:58:33Z",
            lastPriceEventId="wikidata:award:Q43387663:Q2089918",
            lastPriceEvent="Award: old recorded-only event",
            lastEventMovePct=1.0,
            lastEventType="award",
            lastEventSource="Wikidata",
            priceEvents=[],
        )
        migrated,changed=migrate_record(record,"2026-09-28T23:45:00Z")
        self.assertTrue(changed)
        for field in (
            "lastPriceEventAt","lastPriceEventId","lastPriceEvent",
            "lastEventMovePct","lastEventType","lastEventSource",
        ):
            self.assertNotIn(field,migrated)

    def test_migration_is_actor_only_and_idempotent(self):
        music={"id":"m","primaryCategory":"Music","marketPrice":100}
        unchanged,changed=migrate_record(music,"2026-09-28T12:00:00Z")
        self.assertFalse(changed);self.assertEqual(unchanged,music)
        actor=self.actor(actorMarketMigrationVersion=MIGRATION_VERSION)
        unchanged2,changed2=migrate_record(actor,"2026-09-28T12:00:00Z")
        self.assertFalse(changed2);self.assertEqual(unchanged2,actor)

if __name__=="__main__":unittest.main()
