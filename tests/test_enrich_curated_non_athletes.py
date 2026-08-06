#!/usr/bin/env python3
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from enrich_curated_non_athletes import curated_floor, derive_evidence, identity_score, merge_evidence

class CuratedNonAthleteEvidenceTests(unittest.TestCase):
    def test_identity_score_requires_name_and_category_context(self):
        correct={"label":"Taylor Swift","description":"American singer-songwriter","aliases":[]}
        wrong={"label":"Taylor Swift","description":"fictional naval vessel","aliases":[]}
        self.assertGreaterEqual(identity_score('Taylor Swift','Music',correct),116)
        self.assertLess(identity_score('Taylor Swift','Music',wrong),116)
    def test_curated_floor_is_rank_sensitive(self):
        top={"benchmarkRank":1,"benchmarkPoolSize":100}
        bottom={"benchmarkRank":100,"benchmarkPoolSize":100}
        self.assertGreater(curated_floor(top),curated_floor(bottom))
        self.assertEqual(curated_floor(top),82)
        self.assertEqual(curated_floor(bottom),76)
    def test_derive_evidence_reads_work_period_and_awards(self):
        entity={"claims":{
            "P569":[{"mainsnak":{"datavalue":{"value":{"time":"+1989-12-13T00:00:00Z"}}}}],
            "P2031":[{"mainsnak":{"datavalue":{"value":{"time":"+2003-01-01T00:00:00Z"}}}}],
            "P166":[{"mainsnak":{"datavalue":{"value":{"id":"Q1"}}}}],
            "P1411":[{"mainsnak":{"datavalue":{"value":{"id":"Q2"}}}}],
            "P106":[{"mainsnak":{"datavalue":{"value":{"id":"Q177220"}}}}],
        },"sitelinks":{"enwiki":{},"dewiki":{}}}
        evidence=derive_evidence(entity)
        self.assertEqual(evidence['birthYear'],1989)
        self.assertEqual(evidence['workStartYear'],2003)
        self.assertGreater(evidence['yearsActive'],20)
        self.assertEqual(evidence['wikidataAwardsCount'],1)
        self.assertEqual(evidence['wikidataNominationsCount'],1)
    def test_merge_preserves_curated_metrics(self):
        record={"name":"Taylor Swift","primaryCategory":"Music","benchmarkRank":1,"benchmarkPoolSize":100,
                "activeMetrics":{"performance":97,"audience":99},"pricingConfidence":.70,"dataConfidence":.70,
                "pricingEvidence":[]}
        evidence={"birthYear":1989,"age":36,"workStartYear":2003,"workEndYear":None,"yearsActive":23,
                  "wikidataSitelinks":200,"wikidataAwardsCount":30,"wikidataNominationsCount":50,
                  "wikidataOccupationClaims":3,"identityEvidenceConfidence":.88}
        merged=merge_evidence(record,'Q26876',evidence,'2026-08-06T00:00:00Z')
        self.assertEqual(merged['activeMetrics'],record['activeMetrics'])
        self.assertEqual(merged['yearsActive'],23)
        self.assertEqual(merged['curatedEvidenceFloor'],82)
        self.assertTrue(merged['curatedIdentityEvidenceVerified'])
        self.assertGreaterEqual(merged['pricingConfidence'],.88)
        self.assertIn('https://www.wikidata.org/wiki/Q26876',merged['pricingEvidence'])

if __name__=='__main__': unittest.main()
