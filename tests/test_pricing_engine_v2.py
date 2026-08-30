#!/usr/bin/env python3
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from pricing_engine_v2 import apply_v2, evidence_confidence

class PricingEngineV2Tests(unittest.TestCase):
    def record(self, **updates):
        base={"id":"x","primaryCategory":"Athlete","careerStage":"Established","professionalGames":900,
              "careerScore":85,"pricingConfidence":.9,"activeMetrics":{"performance":88,"achievements":86,
              "consistency":90,"potential":75,"availability":90,"audience":82},"momentumPct":0,
              "demandPremiumPct":0,"lastGameMovePct":0,"marketPrice":200,"fundamentalValue":190,
              "trend":[180,200],"starter":False,"careerStatus":"Active"}
        base.update(updates);return base
    def music_record(self, **updates):
        base={"id":"music","primaryCategory":"Music","careerStage":"Active career","careerScore":80,
              "pricingConfidence":.70,"dataConfidence":.70,"activeMetrics":{"performance":88,"achievements":87,
              "consistency":89,"potential":80,"availability":82,"audience":91},"momentumPct":0,
              "demandPremiumPct":0,"lastGameMovePct":0,"marketPrice":150,"fundamentalValue":145,
              "trend":[140,150],"careerStatus":"Active"}
        base.update(updates);return base
    def rookie_record(self, league='NFL', score=94, influence=100, **updates):
        base=self.record(
            id=f'rookie-{league.lower()}',leagueOrMedium=league,careerStage='Rookie',professionalGames=0,
            pricingConfidence=.78,careerScore=55,marketPrice=45,fundamentalValue=44,
            activeMetrics={"performance":45,"achievements":18,"consistency":38,"potential":94,"availability":82,"audience":70},
            rookiePricing={"draftSport":league,"rookieScore":score,"draftInfluencePct":influence,
                           "overallPick":1,"professionalEvidencePct":100-influence})
        base.update(updates);return base
    def test_adds_v2_fields(self):
        r=apply_v2(self.record())
        for key in ('talentScore','marketScore','confidenceScore','situationScore','expectedValueScore','fairValue'):
            self.assertIn(key,r)
        self.assertEqual(r['pricingEngine'],'v2')
    def test_newcomer_is_discounted_for_uncertainty(self):
        veteran=apply_v2(self.record())
        rookie=apply_v2(self.record(careerStage='Rookie',professionalGames=12,pricingConfidence=.7,
            activeMetrics={"performance":88,"achievements":25,"consistency":55,"potential":98,"availability":90,"audience":82}))
        self.assertLess(rookie['confidenceScore'],veteran['confidenceScore'])
        self.assertLess(rookie['fairValue'],veteran['fairValue'])
    def test_top_nfl_rookie_keeps_meaningful_ipo_anchor(self):
        rookie=apply_v2(self.rookie_record('NFL',score=94,influence=100))
        self.assertGreater(rookie['fairValue'],110)
        self.assertAlmostEqual(rookie['fairValue'],rookie['pricingV2']['rookieIpoAnchor'],places=2)
        self.assertGreater(rookie['fairValue'],rookie['pricingV2']['genericFairValue'])
    def test_top_nba_rookie_has_higher_ceiling_than_nfl(self):
        nfl=apply_v2(self.rookie_record('NFL',score=94,influence=100))
        nba=apply_v2(self.rookie_record('NBA',score=94,influence=100))
        self.assertGreater(nba['fairValue'],nfl['fairValue'])
        self.assertGreater(nba['fairValue'],125)
    def test_rookie_anchor_fades_into_professional_model(self):
        opening=apply_v2(self.rookie_record('NFL',score=94,influence=100))
        transition=apply_v2(self.rookie_record('NFL',score=94,influence=50,professionalGames=10))
        self.assertLess(transition['fairValue'],opening['fairValue'])
        expected=(transition['pricingV2']['rookieIpoAnchor']+transition['pricingV2']['genericFairValue'])/2
        self.assertAlmostEqual(transition['fairValue'],expected,places=2)
    def test_single_game_cannot_create_twenty_percent_base_reprice(self):
        neutral=apply_v2(self.record(lastGameMovePct=0))
        great=apply_v2(self.record(lastGameMovePct=2.5))
        self.assertLess((great['fairValue']/neutral['fairValue']-1)*100,5)
    def test_verified_situation_change_moves_price_without_changing_talent(self):
        neutral=apply_v2(self.record(situationAdjustmentPct=0))
        favorable=apply_v2(self.record(situationAdjustmentPct=12,roleStatus='starter'))
        self.assertEqual(neutral['talentScore'],favorable['talentScore'])
        self.assertEqual(neutral['confidenceScore'],favorable['confidenceScore'])
        self.assertGreater(favorable['situationScore'],neutral['situationScore'])
        self.assertGreater(favorable['fairValue'],neutral['fairValue'])
        self.assertLess((favorable['fairValue']/neutral['fairValue']-1)*100,8)
    def test_curated_music_review_has_evidence_floor(self):
        curated=self.music_record(nonAthleteRosterVersion='1.0.0',benchmarkRank=1,benchmarkPoolSize=100,
                                  yearsActive=None,curatedEvidenceFloor=82)
        self.assertGreaterEqual(evidence_confidence(curated),82)
    def test_generic_wikidata_discovery_confidence_is_capped(self):
        discovered=self.music_record(sourceNamespace='wikidata-non-athlete',yearsActive=25,
                                     pricingConfidence=.94,dataConfidence=.94)
        self.assertLessEqual(evidence_confidence(discovered),76)
    def test_stronger_curated_artist_stays_above_generic_longevity_proxy(self):
        curated=apply_v2(self.music_record(
            id='taylor',nonAthleteRosterVersion='1.0.0',benchmarkRank=1,benchmarkPoolSize=100,
            yearsActive=None,curatedEvidenceFloor=82,
            activeMetrics={"performance":96,"achievements":97,"consistency":96,"potential":88,"availability":84,"audience":99}))
        discovered=apply_v2(self.music_record(
            id='generic',sourceNamespace='wikidata-non-athlete',yearsActive=25,pricingConfidence=.94,dataConfidence=.94,
            activeMetrics={"performance":88,"achievements":86,"consistency":90,"potential":68,"availability":78,"audience":91}))
        self.assertGreater(curated['fairValue'],discovered['fairValue'])
    def test_deterministic(self):
        self.assertEqual(apply_v2(self.record()),apply_v2(self.record()))

if __name__=='__main__': unittest.main()
