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
    def test_deterministic(self):
        self.assertEqual(apply_v2(self.record()),apply_v2(self.record()))

if __name__=='__main__': unittest.main()
