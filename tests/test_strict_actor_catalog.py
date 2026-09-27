from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from strict_actor_catalog import resolve_records

class StrictActorCatalogTests(unittest.TestCase):
    def test_music_first_source_actor_moves_to_music_with_musicbrainz_proof(self):
        actor={"id":"cur-bob-dylan","name":"Bob Dylan","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q392","role":"Film actor","discipline":"Film","benchmarkRank":500,"benchmarkPoolSize":1200}
        evidence={"Q392":{"description":"American singer-songwriter","occupations":{"Q177220","Q488205","Q33999"},"musicbrainz":{"72c536dc-7137-4477-a521-567eeb840fa8"}}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(len(resolved),1)
        record=resolved[0]
        self.assertEqual(record["primaryCategory"],"Music")
        self.assertEqual(record["sourceNamespace"],"wikidata-music-resolved-from-actor")
        self.assertTrue(record["musicCategoryVerified"])
        self.assertNotIn("benchmarkRank",record)
        self.assertEqual(summary["movedToMusic"],1)

    def test_existing_music_primary_removes_source_actor_copy(self):
        music={"id":"music","name":"Paul McCartney","primaryCategory":"Music","nonAthleteRosterVersion":"1.0.0"}
        actor={"id":"actor","name":"Paul McCartney","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q2599"}
        evidence={"Q2599":{"description":"English singer, songwriter and musician","occupations":{"Q177220","Q33999"},"musicbrainz":{"ba550d0e-adac-4864-b88b-407cab5e76af"}}}
        resolved,summary=resolve_records([music,actor],evidence)
        self.assertEqual([r["id"] for r in resolved],["music"])
        self.assertEqual(summary["removedDuplicateActorCopies"],1)

    def test_screen_first_actor_stays_actor(self):
        actor={"id":"actor","name":"Example Actor","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q1"}
        evidence={"Q1":{"description":"American actor and singer","occupations":{"Q33999","Q177220"},"musicbrainz":{"mbid"}}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved[0]["primaryCategory"],"Actor")
        self.assertEqual(summary["movedToMusic"],0)

    def test_curated_actor_is_never_reclassified(self):
        actor={"id":"actor","name":"Curated Crossover","primaryCategory":"Actor","nonAthleteRosterVersion":"1.0.0","sourceRecordId":"Q2"}
        evidence={"Q2":{"description":"American singer and actor","occupations":{"Q177220","Q33999"},"musicbrainz":{"mbid"}}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved,[actor])
        self.assertEqual(summary["reviewedSourceActors"],0)

if __name__=="__main__":unittest.main()
