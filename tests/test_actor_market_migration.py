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
        self.assertEqual(updated["marketPrice"],90.0)
        self.assertEqual(updated["actorMarketMigrationVersion"],MIGRATION_VERSION)
        self.assertEqual(updated["priceEvents"][-1]["priceAfter"],90.0)
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

    def test_migration_is_actor_only_and_idempotent(self):
        music={"id":"m","primaryCategory":"Music","marketPrice":100}
        unchanged,changed=migrate_record(music,"2026-09-28T12:00:00Z")
        self.assertFalse(changed);self.assertEqual(unchanged,music)
        actor=self.actor(actorMarketMigrationVersion=MIGRATION_VERSION)
        unchanged2,changed2=migrate_record(actor,"2026-09-28T12:00:00Z")
        self.assertFalse(changed2);self.assertEqual(unchanged2,actor)

if __name__=="__main__":unittest.main()
