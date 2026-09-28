from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"scripts"))
from pricing_model import apply_pricing_to_records, build_benchmark_ranks

class CuratedBenchmarkPricingTests(unittest.TestCase):
    def curated(self,name,rank,confidence):
        return {
            "id":name.lower().replace(" ","-"),
            "name":name,
            "primaryCategory":"Actor",
            "benchmarkRank":rank,
            "benchmarkPoolSize":100,
            "nonAthleteRosterVersion":"1.0.0",
            "sourceName":"TalentX non-athlete roster",
            "pricingConfidence":confidence,
            "dataConfidence":confidence,
            "careerStatus":"Active",
            "marketSegment":"Current",
            "careerStage":"Active career",
            "pricingDataStatus":"Curated benchmark — profession evidence pending",
            "activeMetrics":{"performance":85,"achievements":85,"consistency":85,"potential":85,"availability":80,"audience":85},
        }

    def test_explicit_pool_size_is_not_diluted_by_discovery_rows(self):
        records=[self.curated("Leonardo DiCaprio",3,.84),self.curated("Shah Rukh Khan",4,.94)]
        records += [{"name":f"Discovery {i}","primaryCategory":"Actor"} for i in range(1000)]
        ranks=build_benchmark_ranks(records)
        self.assertEqual(ranks[("Actor","leonardodicaprio")],(3,100))
        self.assertEqual(ranks[("Actor","shahrukhkhan")],(4,100))

    def test_identity_confidence_cannot_reverse_reviewed_actor_order(self):
        high=self.curated("Leonardo DiCaprio",3,.78)
        low=self.curated("Shah Rukh Khan",4,.94)
        priced=apply_pricing_to_records([high,low],{},benchmark_records=[high,low],calibration_reference=[high,low])
        by_name={row["name"]:row for row in priced}
        self.assertGreaterEqual(by_name["Leonardo DiCaprio"]["fundamentalValue"],by_name["Shah Rukh Khan"]["fundamentalValue"])

    def test_generic_evidence_enrichment_cannot_disable_curated_actor_prior(self):
        leonardo=self.curated("Leonardo DiCaprio",3,.78)
        shah=self.curated("Shah Rukh Khan",4,.94)
        leonardo["pricingDataStatus"]="Evidence enriched — generic source evidence"
        shah["pricingDataStatus"]="Evidence enriched — generic source evidence"
        # Deliberately give Shah much stronger generic metrics. The reviewed
        # Actor benchmark remains the temporary fundamental prior until a
        # profession-specific screen model replaces it.
        leonardo["activeMetrics"]={
            "performance":72,"achievements":70,"consistency":74,
            "potential":66,"availability":80,"audience":71,
        }
        shah["activeMetrics"]={
            "performance":98,"achievements":98,"consistency":98,
            "potential":94,"availability":95,"audience":99,
        }
        priced=apply_pricing_to_records(
            [leonardo,shah],{},
            benchmark_records=[leonardo,shah],
            calibration_reference=[leonardo,shah],
        )
        by_name={row["name"]:row for row in priced}
        self.assertEqual(by_name["Leonardo DiCaprio"]["benchmarkRank"],3)
        self.assertEqual(by_name["Shah Rukh Khan"]["benchmarkRank"],4)
        self.assertIsNotNone(
            by_name["Leonardo DiCaprio"]["pricingAudit"].get("curatedBenchmarkScore")
        )
        self.assertGreaterEqual(
            by_name["Leonardo DiCaprio"]["fundamentalValue"],
            by_name["Shah Rukh Khan"]["fundamentalValue"],
        )

    def test_generic_evidence_enrichment_cannot_disable_curated_music_prior(self):
        high=self.curated("Rauw Alejandro",57,.80)
        low=self.curated("Fuerza Regida",58,.95)
        for record in (high,low):
            record["primaryCategory"]="Music"
            record["pricingDataStatus"]="Evidence enriched — generic source evidence"
        high["activeMetrics"]={"performance":60,"achievements":60,"consistency":60,"potential":60,"availability":80,"audience":60}
        low["activeMetrics"]={"performance":99,"achievements":99,"consistency":99,"potential":99,"availability":99,"audience":99}
        priced=apply_pricing_to_records(
            [high,low],{},
            benchmark_records=[high,low],
            calibration_reference=[high,low],
        )
        by_name={row["name"]:row for row in priced}
        self.assertGreaterEqual(
            by_name["Rauw Alejandro"]["fundamentalValue"],
            by_name["Fuerza Regida"]["fundamentalValue"],
        )

    def test_adjacent_music_benchmark_order_is_stable(self):
        def music(name,rank,confidence):
            r=self.curated(name,rank,confidence);r["primaryCategory"]="Music";return r
        high=music("Rauw Alejandro",57,.78);low=music("Fuerza Regida",58,.94)
        priced=apply_pricing_to_records([high,low],{},benchmark_records=[high,low],calibration_reference=[high,low])
        by_name={row["name"]:row for row in priced}
        self.assertGreaterEqual(by_name["Rauw Alejandro"]["fundamentalValue"],by_name["Fuerza Regida"]["fundamentalValue"])

if __name__=="__main__":unittest.main()
