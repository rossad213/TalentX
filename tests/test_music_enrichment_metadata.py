from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"scripts"))
from enrich_current_catalog import apply_ranked_metrics

class MusicEnrichmentMetadataTests(unittest.TestCase):
    def test_music_status_survives_athlete_enrichment_skip(self):
        record={"id":"music-1","name":"Example Artist","primaryCategory":"Music","pricingDataStatus":"Strict music-source identity verified; streaming/chart/touring evidence pending"}
        updated=apply_ranked_metrics([{"record":record,"ok":False,"reason":"not selected for automated enrichment"}])[0]
        self.assertEqual(updated["pricingDataStatus"],record["pricingDataStatus"])
        self.assertNotIn("pricingEnrichmentError",updated)
        self.assertEqual(updated["pricingEnrichmentSkippedReason"],"not selected for automated enrichment")
    def test_athlete_failure_keeps_roster_provisional_status(self):
        updated=apply_ranked_metrics([{"record":{"id":"athlete-1","primaryCategory":"Athlete"},"ok":False,"reason":"no usable evidence"}])[0]
        self.assertEqual(updated["pricingDataStatus"],"Provisional — roster, experience and role evidence only")
        self.assertEqual(updated["pricingEnrichmentError"],"no usable evidence")
if __name__=="__main__": unittest.main()
