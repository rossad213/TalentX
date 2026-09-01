/* TalentX Rookie IPO policy.
 * This file is the client-side source of truth for rookie IPO ceilings and opening-price display.
 * It mutates the shared ROOKIE_SPORTS configuration so the calculator and profile use the same policy.
 */
(() => {
  const policy=Object.freeze({
    NFL:Object.freeze({basePrice:4,ipoCeiling:135}),
    NBA:Object.freeze({basePrice:4,ipoCeiling:155}),
    WNBA:Object.freeze({basePrice:4,ipoCeiling:95}),
    NHL:Object.freeze({basePrice:4,ipoCeiling:120}),
    MLB:Object.freeze({basePrice:4,ipoCeiling:95})
  });

  Object.entries(policy).forEach(([sport,values])=>{
    const cfg=ROOKIE_SPORTS[sport];
    if(!cfg) return;
    cfg.basePrice=values.basePrice;
    cfg.ipoCeiling=values.ipoCeiling;
    // pricePerPoint belonged to the old compressed linear IPO model.
    // Remove it after the calibrated nonlinear policy is installed so there is
    // no second client-side dollar scale to accidentally reuse.
    delete cfg.pricePerPoint;
  });

  calculateRookieIpo = function(values){
    const cfg=ROOKIE_SPORTS[values.sport]||ROOKIE_SPORTS.NFL;
    const pick=clamp(Math.floor(values.pick),1,cfg.maxPicks);
    const position=cfg.positions[values.position]!==undefined?values.position:Object.keys(cfg.positions)[0];
    const factors={
      draftCapital:draftCapitalScore(values.sport,pick),
      preProPerformance:clamp(values.prePro,0,100),
      opportunity:clamp(values.opportunity,0,100),
      positionValue:cfg.positions[position],
      development:clamp(values.development,0,100),
      availability:clamp(values.availability,0,100),
      audience:clamp(values.audience,0,100)
    };
    const weighted=Object.entries(ROOKIE_WEIGHTS).map(([key,w])=>[key,factors[key]*w]);
    const score=weighted.reduce((sum,[,value])=>sum+value,0);
    const base=Number(cfg.basePrice)||4;
    const ceiling=Number(cfg.ipoCeiling)||105;
    const price=base+ceiling*Math.pow(score/100,2);
    const uncertainty=.07+(100-factors.opportunity)/100*.035+(100-factors.availability)/100*.025;
    const round=Math.ceil(pick/cfg.picksPerRound);
    const variable=Math.max(0,price-base);
    const totalWeighted=Math.max(.001,weighted.reduce((sum,[,value])=>sum+value,0));
    const contributions={base};
    weighted.forEach(([key,value])=>contributions[key]=variable*(value/totalWeighted));
    return {sport:values.sport,position,pick,round,factors,score,price,low:Math.max(1,price*(1-uncertainty)),high:price*(1+uncertainty),contributions};
  };

  rookieProfilePricing = function(r){
    const p=r.rookiePricing||{};
    const rows={draftCapital:p.draftCapitalScore,preProPerformance:p.preProPerformanceScore,opportunity:p.opportunityScore,positionValue:p.positionValueScore,development:p.developmentScore,availability:p.availabilityScore,audience:p.audienceScore};
    const opening=p.calibratedIpoPrice ?? p.ipoPrice ?? r.fundamentalValue;
    return `<div class="source-box"><small>Rookie IPO</small><strong>${p.draftSport||r.leagueOrMedium} · Pick ${p.overallPick||'—'} · ${p.position||r.role}</strong><small>Calibrated opening price ${money(opening)}. Draft capital is strongest at listing and fades as verified professional performance data accumulates.</small></div>${metricGrid(Object.fromEntries(Object.entries(rows).filter(([,v])=>v!==undefined)))}`;
  };

  window.TALENTX_ROOKIE_IPO_POLICY=policy;
})();
