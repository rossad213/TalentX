/* Keep the visible TalentX Rookie IPO calculator on the same scale as pricing engine v2. */
(() => {
  const ceilings = {NFL:135, NBA:155, WNBA:95, NHL:120, MLB:95};

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
    const base=4;
    const ceiling=ceilings[values.sport]||105;
    const price=base+ceiling*Math.pow(score/100,2);
    const uncertainty=.07+(100-factors.opportunity)/100*.035+(100-factors.availability)/100*.025;
    const round=Math.ceil(pick/cfg.picksPerRound);
    const variable=Math.max(0,price-base);
    const totalWeighted=Math.max(.001,weighted.reduce((sum,[,value])=>sum+value,0));
    const contributions={base};
    weighted.forEach(([key,value])=>contributions[key]=variable*(value/totalWeighted));
    return {sport:values.sport,position,pick,round,factors,score,price,low:Math.max(1,price*(1-uncertainty)),high:price*(1+uncertainty),contributions};
  };
})();
