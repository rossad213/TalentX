#!/usr/bin/env python3
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from pricing_engine_v2 import apply_v2, evidence_confidence, market_score

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
    def test_nfl_confidence_matures_without_120_game_cliff(self):
        hurts=evidence_confidence(self.record(leagueOrMedium='NFL',professionalGames=95,experienceYears=7))
        dak=evidence_confidence(self.record(leagueOrMedium='NFL',professionalGames=141,experienceYears=11))
        self.assertGreater(hurts,90)
        self.assertLess(dak-hurts,2)

    def test_nfl_confidence_does_not_double_count_achievements(self):
        low=evidence_confidence(self.record(
            leagueOrMedium='NFL',professionalGames=95,
            activeMetrics={"performance":88,"achievements":20,"consistency":40,"potential":75,"availability":90,"audience":60}))
        high=evidence_confidence(self.record(
            leagueOrMedium='NFL',professionalGames=95,
            activeMetrics={"performance":88,"achievements":99,"consistency":99,"potential":75,"availability":90,"audience":60}))
        self.assertEqual(low,high)

    def test_nfl_missing_game_total_uses_experience_fallback(self):
        established=evidence_confidence(self.record(
            leagueOrMedium='NFL',professionalGames=0,experienceYears=6))
        self.assertGreater(established,90)

    def test_nfl_market_score_is_independent_of_talent_and_game_event(self):
        record=self.record(
            leagueOrMedium='NFL',
            activeMetrics={"performance":88,"achievements":86,"consistency":90,"potential":75,
                           "availability":90,"audience":62,"attention":70},
            nflLiquidityScore=55,lastGameMovePct=18)
        low=market_score(record,20)
        high=market_score({**record,"lastGameMovePct":-18},95)
        self.assertEqual(low,high)
        self.assertAlmostEqual(low,62*.42+70*.28+55*.15+72*.15,places=2)

    def test_nfl_qb_position_context_is_modest_not_dominant(self):
        common={
            "leagueOrMedium":"NFL",
            "activeMetrics":{"performance":90,"achievements":80,"consistency":90,"potential":75,
                             "availability":90,"audience":62,"attention":70},
            "nflLiquidityScore":55,
        }
        qb=market_score(self.record(role="Quarterback",**common),85)
        edge=market_score(self.record(role="Defensive End",**common),85)
        self.assertGreater(qb,edge)
        self.assertLess(qb-edge,3.0)

    def test_nfl_active_injury_reduces_situation_not_talent(self):
        healthy=apply_v2(self.record(
            leagueOrMedium="NFL",role="Defensive End",
            nflInjuryActive=False,
        ))
        injured=apply_v2(self.record(
            leagueOrMedium="NFL",role="Defensive End",
            nflInjuryActive=True,nflInjuryStatus="Physically Unable to Perform",nflInjuryType="Knee",
        ))
        self.assertEqual(healthy["talentScore"],injured["talentScore"])
        self.assertLess(injured["situationScore"],healthy["situationScore"])
        self.assertLess(injured["fairValue"],healthy["fairValue"])

    def test_nfl_role_player_is_spread_below_premium_price_band(self):
        role_player=apply_v2(self.record(
            leagueOrMedium='NFL',role='Wide Receiver',professionalGames=74,
            pricingConfidence=.84,
            activeMetrics={"performance":46,"achievements":35,"consistency":48,
                           "potential":52,"availability":90,"audience":48}))
        self.assertEqual(role_player['pricingModelVersion'],'6.4-nfl-career-tier-scale')
        self.assertLess(role_player['pricingV2']['nflCareerTierMultiplier'],0.80)
        self.assertLess(role_player['fairValue'],role_player['pricingV2']['genericFairValue']*0.80)
        self.assertLess(role_player['fairValue'],80)

    def test_elite_nfl_talent_keeps_full_career_scale(self):
        elite=apply_v2(self.record(
            leagueOrMedium='NFL',role='Quarterback',professionalGames=140,
            activeMetrics={"performance":96,"achievements":96,"consistency":95,
                           "potential":90,"availability":94,"audience":98}))
        self.assertEqual(elite['pricingV2']['nflCareerTierMultiplier'],1.0)
        self.assertEqual(elite['fairValue'],elite['pricingV2']['genericFairValue'])

    def test_established_star_talent_is_not_compressed(self):
        star=apply_v2(self.record(
            leagueOrMedium='NFL',role='Quarterback',professionalGames=100,
            activeMetrics={"performance":78,"achievements":72,"consistency":74,
                           "potential":72,"availability":92,"audience":94}))
        self.assertGreaterEqual(star['talentScore'],70)
        self.assertEqual(star['pricingV2']['nflCareerTierMultiplier'],1.0)
        self.assertEqual(star['fairValue'],star['pricingV2']['genericFairValue'])

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

    def test_nhl_fair_value_does_not_reapply_prior_game_move(self):
        base=self.record(leagueOrMedium='NHL',discipline='Hockey',role='C',lastGameMovePct=0)
        moved={**base,'lastGameMovePct':18.0}
        self.assertEqual(apply_v2(base)['fairValue'],apply_v2(moved)['fairValue'])
        self.assertEqual(market_score(base,80),market_score(moved,80))
    def test_basketball_fair_value_does_not_reapply_prior_game_move(self):
        for league in ('NBA','WNBA'):
            base=self.record(leagueOrMedium=league,discipline='Basketball',role='G',lastGameMovePct=0)
            moved={**base,'lastGameMovePct':18.0}
            self.assertEqual(apply_v2(base)['fairValue'],apply_v2(moved)['fairValue'])
            self.assertEqual(market_score(base,80),market_score(moved,80))
    def test_tennis_verified_matches_raise_confidence_without_changing_talent(self):
        base=self.record(
            discipline='Tennis',leagueOrMedium='ATP',professionalGames=0,yearsActive=None,
            pricingConfidence=.64,dataConfidence=.64,
            activeMetrics={"performance":88,"achievements":84,"consistency":85,"potential":82,"availability":88,"audience":85},
            priceEvents=[])
        verified_events=[
            {"eventKey":f"espn-tennis:atp:{i}","eventType":"game","sport":"tennis","tour":"ATP","verified":True}
            for i in range(180)
        ]
        thin=apply_v2(base)
        mature=apply_v2({**base,"priceEvents":verified_events})
        self.assertEqual(thin["talentScore"],mature["talentScore"])
        self.assertGreater(mature["confidenceScore"],75)
        self.assertGreater(mature["confidenceScore"],thin["confidenceScore"]+25)
        self.assertGreater(mature["fairValue"],thin["fairValue"]*1.30)

    def test_top_ten_tennis_rank_supplies_mature_confidence_floor(self):
        ranked=self.record(
            discipline='Tennis',leagueOrMedium='ATP',professionalGames=0,yearsActive=None,
            pricingConfidence=.64,dataConfidence=.64,sourceRank=2,priceEvents=[],
            activeMetrics={"performance":90,"achievements":88,"consistency":86,"potential":92,"availability":88,"audience":90})
        unranked={**ranked,"sourceRank":None}
        self.assertGreaterEqual(evidence_confidence(ranked),90)
        self.assertGreater(evidence_confidence(ranked),evidence_confidence(unranked)+30)
        self.assertGreater(apply_v2(ranked)["fairValue"],apply_v2(unranked)["fairValue"]*1.30)

    def test_tennis_rank_floors_scale_smoothly_across_top_100(self):
        common=self.record(
            discipline='Tennis',leagueOrMedium='WTA',professionalGames=0,yearsActive=None,
            pricingConfidence=.64,dataConfidence=.64,priceEvents=[],
            activeMetrics={"performance":84,"achievements":80,"consistency":82,"potential":82,"availability":88,"audience":82})
        expected={1:90,20:86,40:82,80:78,150:72}
        for rank,floor in expected.items():
            with self.subTest(rank=rank):
                self.assertGreaterEqual(evidence_confidence({**common,"sourceRank":rank}),floor)

    def test_tennis_match_count_dedupes_same_provider_competition_across_tours(self):
        from pricing_engine_v2 import tennis_verified_match_count
        record=self.record(
            discipline='Tennis',
            priceEvents=[
                {"eventKey":"espn-tennis:atp:182190","eventId":"182190","eventType":"game","sport":"tennis","tour":"ATP","verified":True},
                {"eventKey":"espn-tennis:wta:182190","eventId":"182190","eventType":"game","sport":"tennis","tour":"WTA","verified":True},
            ])
        self.assertEqual(tennis_verified_match_count(record),1)

    def test_tennis_fair_value_does_not_reapply_prior_match_move(self):
        base=self.record(
            discipline='Tennis',leagueOrMedium='WTA',professionalGames=0,
            pricingConfidence=.64,
            priceEvents=[
                {"eventKey":"espn-tennis:wta:1","eventType":"game","sport":"tennis","tour":"WTA","verified":True}
            ],
            lastGameMovePct=0)
        moved={**base,'lastGameMovePct':18.0}
        self.assertEqual(apply_v2(base)['fairValue'],apply_v2(moved)['fairValue'])
        self.assertEqual(market_score(base,80),market_score(moved,80))

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
    def test_strict_music_identity_does_not_equal_mature_pricing_evidence(self):
        discovered=self.music_record(sourceNamespace='wikidata-music-strict',musicCategoryVerified=True,
            musicBrainzArtistIds=['mbid'],yearsActive=25,pricingConfidence=.68,dataConfidence=.90,priceEvents=[])
        self.assertLessEqual(evidence_confidence(discovered),60)
        priced=apply_v2(discovered)
        self.assertEqual(priced['pricingModelVersion'],'6.2-music-evidence-confidence')
        self.assertEqual(priced['pricingV2']['musicIdentityConfidenceScore'],95.0)
        self.assertEqual(priced['pricingV2']['musicPricingEvidenceCeiling'],60.0)

    def test_verified_music_outcomes_raise_pricing_evidence_ceiling(self):
        base=self.music_record(sourceNamespace='wikidata-music-strict',musicCategoryVerified=True,
            musicBrainzArtistIds=['mbid'],yearsActive=15,pricingConfidence=.68,dataConfidence=.90,priceEvents=[])
        enriched={**base,'priceEvents':[{'eventKey':f'chart:{i}','eventType':'music-chart-outcome','verified':True} for i in range(5)]}
        self.assertGreater(evidence_confidence(enriched),evidence_confidence(base))
        self.assertGreater(apply_v2(enriched)['pricingV2']['musicPricingEvidenceCeiling'],60)

    def test_music_event_move_stays_in_event_ledger_not_fair_value(self):
        base=self.music_record(lastGameMovePct=0)
        moved={**base,'lastGameMovePct':12.0}
        self.assertEqual(apply_v2(base)['fairValue'],apply_v2(moved)['fairValue'])
        self.assertEqual(market_score(base,80),market_score(moved,80))

    def test_source_backed_actor_identity_does_not_equal_mature_pricing_evidence(self):
        actor=self.music_record(
            primaryCategory='Actor',sourceNamespace='wikidata-non-athlete',sourceRecordId='Q42',
            yearsActive=35,pricingConfidence=.92,dataConfidence=.92,priceEvents=[])
        self.assertLessEqual(evidence_confidence(actor),78)
        priced=apply_v2(actor)
        self.assertEqual(priced['pricingModelVersion'],'6.5-actor-career-first-scale')
        self.assertEqual(priced['pricingV2']['actorIdentityConfidenceScore'],90.0)
        self.assertEqual(priced['pricingV2']['actorPricingEvidenceCeiling'],78.0)

    def test_source_discovered_actor_is_not_double_discounted_after_confidence(self):
        actor=self.music_record(
            primaryCategory='Actor',sourceNamespace='wikidata-non-athlete',sourceRecordId='Q42',
            pricingConfidence=.90,dataConfidence=.90,priceEvents=[])
        priced=apply_v2(actor)
        self.assertEqual(priced['pricingV2']['actorDiscoveryFairValueMultiplier'],1.0)
        self.assertEqual(priced['fairValue'],priced['pricingV2']['genericFairValue'])

    def test_direct_actor_evidence_refines_confidence_without_market_scale_haircut(self):
        base=self.music_record(
            primaryCategory='Actor',sourceNamespace='wikidata-non-athlete',sourceRecordId='Q42',
            pricingConfidence=.90,dataConfidence=.90,priceEvents=[])
        enriched={**base,'priceEvents':[
            {'eventKey':f'box:{i}','eventType':'actor-box-office-outcome','verified':True}
            for i in range(5)
        ]}
        thin=apply_v2(base)
        mature=apply_v2(enriched)
        self.assertEqual(thin['pricingV2']['actorDiscoveryFairValueMultiplier'],1.0)
        self.assertEqual(mature['pricingV2']['actorDiscoveryFairValueMultiplier'],1.0)
        self.assertGreater(mature['confidenceScore'],thin['confidenceScore'])
        self.assertGreater(mature['fairValue'],thin['fairValue'])

    def test_actor_direct_evidence_cannot_dominate_equal_career_strength(self):
        common=dict(
            primaryCategory='Actor',sourceNamespace='wikidata-non-athlete',
            yearsActive=20,pricingConfidence=.90,dataConfidence=.90,
            activeMetrics={"performance":86,"achievements":86,"consistency":86,"potential":78,"availability":84,"audience":82},
        )
        thin=apply_v2(self.music_record(sourceRecordId='Q1',priceEvents=[],**common))
        rich=apply_v2(self.music_record(
            sourceRecordId='Q2',
            priceEvents=[{'eventKey':f'box:{i}','eventType':'actor-box-office-outcome','verified':True} for i in range(6)],
            **common,
        ))
        self.assertLess((rich['fairValue']/thin['fairValue']-1.0)*100.0,8.0)

    def test_curated_actor_keeps_reviewed_baseline_fundamental_in_v2(self):
        actor=self.music_record(
            primaryCategory='Actor',nonAthleteRosterVersion='1.0.0',
            benchmarkRank=3,benchmarkPoolSize=100,
            fundamentalValue=249.80,marketPrice=249.80,
            activeMetrics={'performance':60,'achievements':60,'consistency':60,'potential':60,'availability':80,'audience':60},
        )
        priced=apply_v2(actor)
        self.assertEqual(priced['fairValue'],249.80)
        self.assertEqual(priced['pricingV2']['actorCuratedBaselinePrior'],249.80)

    def test_verified_actor_outcomes_raise_pricing_evidence_ceiling(self):
        base=self.music_record(
            primaryCategory='Actor',sourceNamespace='wikidata-non-athlete',sourceRecordId='Q42',
            yearsActive=15,pricingConfidence=.90,dataConfidence=.90,priceEvents=[])
        enriched={**base,'priceEvents':[
            {'eventKey':f'box:{i}','eventType':'actor-box-office-outcome','verified':True}
            for i in range(4)
        ]}
        self.assertGreater(evidence_confidence(enriched),evidence_confidence(base))
        self.assertGreater(apply_v2(enriched)['pricingV2']['actorPricingEvidenceCeiling'],60)

    def test_actor_event_move_stays_in_event_ledger_not_fair_value(self):
        base=self.music_record(primaryCategory='Actor',lastGameMovePct=0)
        moved={**base,'lastGameMovePct':12.0}
        self.assertEqual(apply_v2(base)['fairValue'],apply_v2(moved)['fairValue'])
        self.assertEqual(market_score(base,80),market_score(moved,80))

    def test_stronger_curated_artist_stays_above_generic_longevity_proxy(self):
        curated=apply_v2(self.music_record(
            id='taylor',nonAthleteRosterVersion='1.0.0',benchmarkRank=1,benchmarkPoolSize=100,
            yearsActive=None,curatedEvidenceFloor=82,
            activeMetrics={"performance":96,"achievements":97,"consistency":96,"potential":88,"availability":84,"audience":99}))
        discovered=apply_v2(self.music_record(
            id='generic',sourceNamespace='wikidata-non-athlete',yearsActive=25,pricingConfidence=.94,dataConfidence=.94,
            activeMetrics={"performance":88,"achievements":86,"consistency":90,"potential":68,"availability":78,"audience":91}))
        self.assertGreater(curated['fairValue'],discovered['fairValue'])
    def test_unverified_motorsport_discovery_cannot_escape_evidence_ceiling(self):
        discovered=apply_v2(self.record(
            discipline='Motorsport',leagueOrMedium='International Motorsport',
            sourceNamespace='wikidata-individual-sport',professionEvidenceVerified=False,
            professionalGames=0,pricingConfidence=.56,dataConfidence=.56,
            marketPrice=95,fundamentalValue=90))
        self.assertLessEqual(discovered['confidenceScore'],56)
        self.assertLessEqual(discovered['fairValue'],62)
        self.assertEqual(discovered['pricingV2']['motorsportEvidenceGateCeiling'],62.0)
        self.assertEqual(discovered['pricingModelVersion'],'6.1-motorsport-verified-race-ledger')

    def test_verified_f1_races_create_mature_confidence(self):
        events=[{
            'eventKey':f'jolpica-f1:2026:{i}:russell','eventType':'game',
            'provider':'Jolpica F1','verified':True
        } for i in range(1,15)]
        f1=self.record(
            discipline='Motorsport',leagueOrMedium='Formula 1',
            professionEvidenceVerified=True,motorsportSeasonStarts=14,
            motorsportChampionshipRank=1,sourceRank=1,priceEvents=events,
            pricingConfidence=.90,dataConfidence=.90)
        self.assertGreaterEqual(evidence_confidence(f1),90)
        priced=apply_v2(f1)
        self.assertGreater(priced['fairValue'],62)
        self.assertEqual(priced['pricingV2']['motorsportVerifiedRaceCount'],14)

    def test_motorsport_game_move_stays_in_event_ledger_not_fair_value(self):
        record=self.record(
            discipline='Motorsport',leagueOrMedium='Formula 1',
            professionEvidenceVerified=True,motorsportSeasonStarts=12,
            motorsportChampionshipRank=4)
        positive=market_score({**record,'lastGameMovePct':20},85)
        negative=market_score({**record,'lastGameMovePct':-20},85)
        self.assertEqual(positive,negative)

    def test_deterministic(self):
        self.assertEqual(apply_v2(self.record()),apply_v2(self.record()))

if __name__=='__main__': unittest.main()
