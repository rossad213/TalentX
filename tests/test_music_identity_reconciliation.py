from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
from reconcile_music_identities import reconcile_records

class MusicIdentityReconciliationTests(unittest.TestCase):
    def test_preferred_baseline_id_wins_and_richer_market_state_is_merged(self):
        baseline={"id":"cur-beyonce","name":"Beyoncé","primaryCategory":"Music","nonAthleteRosterVersion":"1.0.0","wikidataSourceRecordId":"Q36153","marketPrice":256.31,"fundamentalValue":256.31,"priceEvents":[{"eventKey":"award:1","eventType":"award","startedAt":"2026-02-01T00:00:00Z","priceAfter":260.0}],"priceHistory":[{"time":"2026-02-01T00:00:00Z","eventId":"award:1","phase":"close","price":260.0}]}
        alias={"id":"cur-beyonc","name":"Beyoncé","primaryCategory":"Music","nonAthleteRosterVersion":"1.0.0","wikidataSourceRecordId":"Q36153","marketPrice":262.59,"lastPriceEventAt":"2026-09-20T00:00:00Z","lastPriceEventId":"billboard:1","priceEvents":[{"eventKey":"billboard:1","eventType":"music-chart-outcome","startedAt":"2026-09-20T00:00:00Z","priceAfter":262.59}],"priceHistory":[{"time":"2026-09-20T00:00:00Z","eventId":"billboard:1","phase":"close","price":262.59}]}
        reconciled,repairs=reconcile_records([baseline,alias],{"cur-beyonce"})
        self.assertEqual(len(reconciled),1); record=reconciled[0]
        self.assertEqual(record["id"],"cur-beyonce"); self.assertEqual(record["marketPrice"],262.59)
        self.assertEqual({e["eventKey"] for e in record["priceEvents"]},{"award:1","billboard:1"})
        self.assertEqual(len(record["priceHistory"]),2); self.assertEqual(record["musicCanonicalAliasIds"],["cur-beyonc"]); self.assertEqual(len(repairs),1)

    def test_shared_musicbrainz_identity_collapses_source_duplicates(self):
        one={"id":"one","name":"Example Artist","primaryCategory":"Music","sourceNamespace":"wikidata-music-strict","musicBrainzArtistIds":["abc"]}
        two={"id":"two","name":"Example Artist","primaryCategory":"Music","sourceNamespace":"wikidata-music-expanded","musicBrainzArtistIds":["abc"]}
        reconciled,repairs=reconcile_records([one,two]); self.assertEqual(len(reconciled),1); self.assertEqual(len(repairs),1)

    def test_same_name_without_shared_identity_evidence_is_preserved(self):
        one={"id":"one","name":"Jordan Lee","primaryCategory":"Music","sourceNamespace":"wikidata-music-strict","sourceRecordId":"Q1"}
        two={"id":"two","name":"Jordan Lee","primaryCategory":"Music","sourceNamespace":"wikidata-music-strict","sourceRecordId":"Q2"}
        reconciled,repairs=reconcile_records([one,two]); self.assertEqual(len(reconciled),2); self.assertEqual(repairs,[])

if __name__=="__main__": unittest.main()
