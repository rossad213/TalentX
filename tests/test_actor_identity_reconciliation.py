from __future__ import annotations
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from reconcile_actor_identities import reconcile_records

class ActorIdentityReconciliationTests(unittest.TestCase):
    def test_shared_qid_duplicate_collapses_into_preferred_baseline_id(self):
        base={"id":"cur-timothee-chalamet","name":"Timothée Chalamet","primaryCategory":"Actor","nonAthleteRosterVersion":"1.0.0","wikidataSourceRecordId":"Q19877770","marketPrice":240.0,"priceEvents":[],"priceHistory":[]}
        alias={"id":"cur-timoth-e-chalamet","name":"Timothée Chalamet","primaryCategory":"Actor","nonAthleteRosterVersion":"1.0.0","wikidataSourceRecordId":"Q19877770","marketPrice":248.0,"lastPriceEventAt":"2026-09-01T00:00:00Z","priceEvents":[{"eventKey":"award:1","eventType":"award","startedAt":"2026-09-01T00:00:00Z","priceAfter":248.0}],"priceHistory":[{"time":"2026-09-01T00:00:00Z","eventId":"award:1","phase":"close","price":248.0}]}
        out,repairs=reconcile_records([base,alias],{"cur-timothee-chalamet"})
        self.assertEqual(len(out),1);self.assertEqual(out[0]["id"],"cur-timothee-chalamet")
        self.assertEqual(out[0]["marketPrice"],248.0);self.assertEqual(len(out[0]["priceEvents"]),1)
        self.assertEqual(out[0]["actorCanonicalAliasIds"],["cur-timoth-e-chalamet"]);self.assertEqual(len(repairs),1)

    def test_same_name_different_qids_are_preserved(self):
        a={"id":"a","name":"Jordan Lee","primaryCategory":"Actor","sourceRecordId":"Q1"}
        b={"id":"b","name":"Jordan Lee","primaryCategory":"Actor","sourceRecordId":"Q2"}
        out,repairs=reconcile_records([a,b]);self.assertEqual(len(out),2);self.assertEqual(repairs,[])

if __name__=="__main__":unittest.main()
