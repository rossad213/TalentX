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

    def test_secondary_actor_occupation_without_screen_first_description_is_removed(self):
        actor={"id":"peyton","name":"Peyton Manning","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q192296"}
        evidence={"Q192296":{"label":"Peyton Manning","description":"American football quarterback","occupations":{"Q33999"},"musicbrainz":set()}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved,[])
        self.assertEqual(summary["removedNonScreenFirst"],1)

    def test_character_name_pointing_to_actor_qid_is_removed(self):
        actor={"id":"jess","name":"Jess Mariano","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q83733"}
        evidence={"Q83733":{"label":"Milo Ventimiglia","description":"American actor","occupations":{"Q33999"},"musicbrainz":set()}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved,[])
        self.assertEqual(summary["removedNameMismatches"],1)

    def test_close_spelling_alias_can_survive_canonical_label_check(self):
        actor={"id":"karisma","name":"Karisma Kapoor","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q464578"}
        evidence={"Q464578":{"label":"Karishma Kapoor","description":"Indian actress","occupations":{"Q33999"},"musicbrainz":set()}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(len(resolved),1)
        self.assertTrue(resolved[0]["actorCategoryVerified"])
        self.assertEqual(summary["verifiedScreenFirst"],1)

    def test_filmmaker_first_identity_is_removed_even_with_actor_occupation(self):
        actor={"id":"spielberg","name":"Steven Spielberg","primaryCategory":"Actor","sourceNamespace":"wikidata-non-athlete","sourceRecordId":"Q8877"}
        evidence={"Q8877":{"label":"Steven Spielberg","description":"American filmmaker and actor","occupations":{"Q2526255","Q33999"},"musicbrainz":set()}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved,[])
        self.assertEqual(summary["removedNonScreenFirst"],1)

    def test_verified_actor_payload_removes_music_search_classification(self):
        actor={
            "id":"x","name":"Example Actor","primaryCategory":"Actor",
            "sourceNamespace":"wikidata-actor-resolved-from-music","sourceRecordId":"Q1",
            "role":"Singer","discipline":"Vocal","leagueOrMedium":"Music",
            "searchText":"example actor music vocal singer",
            "musicCategoryVerified":True,"musicBrainzArtistIds":["mbid"],
        }
        evidence={"Q1":{"label":"Example Actor","description":"American actor and singer","occupations":{"Q33999","Q177220"},"musicbrainz":{"mbid"}}}
        resolved,_=resolve_records([actor],evidence)
        record=resolved[0]
        self.assertEqual(record["primaryCategory"],"Actor")
        self.assertNotEqual(record["leagueOrMedium"],"Music")
        self.assertNotIn("musicCategoryVerified",record)
        self.assertNotIn("musicBrainzArtistIds",record)
        self.assertIn("actor",record["searchText"])
        self.assertNotIn(" music vocal ",f" {record['searchText']} ")

    def test_curated_actor_is_never_reclassified(self):
        actor={"id":"actor","name":"Curated Crossover","primaryCategory":"Actor","nonAthleteRosterVersion":"1.0.0","sourceRecordId":"Q2"}
        evidence={"Q2":{"description":"American singer and actor","occupations":{"Q177220","Q33999"},"musicbrainz":{"mbid"}}}
        resolved,summary=resolve_records([actor],evidence)
        self.assertEqual(resolved,[actor])
        self.assertEqual(summary["reviewedSourceActors"],0)

if __name__=="__main__":unittest.main()
