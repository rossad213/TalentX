/*
 * TalentX historical chart reconstruction
 *
 * Short-range charts use only recorded price events. Longer ranges may use a
 * deterministic reconstructed history for visual context until enough genuine
 * TalentX history exists. Reconstruction never affects pricing or portfolios.
 */
(function(){
  const DAY=24*60*60*1000;
  const RECONSTRUCTED_SPAN=365*DAY;
  const SHORT_RANGES=new Set(['1D','5D']);

  function txDate(value){
    if(value===null||value===undefined||value==='') return NaN;
    if(typeof value==='number') return value<1e12?value*1000:value;
    const parsed=Date.parse(value);
    return Number.isFinite(parsed)?parsed:NaN;
  }

  function txNumber(value){
    const parsed=Number(value);
    return Number.isFinite(parsed)?parsed:NaN;
  }

  function txRangeStart(range,now){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    if(range==='YTD') return new Date(new Date(now).getFullYear(),0,1).getTime();
    return now-(config.duration||DAY);
  }

  function txExplicitPricePoints(record){
    const candidates=[record.priceHistory,record.historicalPrices,record.marketHistory,record.chartHistory];
    const source=candidates.find(Array.isArray)||[];
    return source.map(item=>{
      if(Array.isArray(item)) return {time:txDate(item[0]),value:txNumber(item[1]),verified:true};
      if(!item||typeof item!=='object') return null;
      return {
        time:txDate(item.time??item.timestamp??item.date??item.eventDate??item.asOf),
        value:txNumber(item.value??item.price??item.marketPrice??item.close),
        verified:item.reconstructed!==true&&item.synthetic!==true
      };
    }).filter(point=>point&&Number.isFinite(point.time)&&Number.isFinite(point.value)&&point.value>0)
      .sort((a,b)=>a.time-b.time);
  }

  function txEventPoints(record,current){
    const candidates=[record.priceEvents,record.eventHistory,record.events,record.marketEvents];
    const source=candidates.find(Array.isArray)||[];
    const events=source.map(item=>{
      if(!item||typeof item!=='object'||item.reconstructed===true||item.synthetic===true) return null;
      return {
        time:txDate(item.time??item.timestamp??item.date??item.eventDate??item.asOf),
        price:txNumber(item.price??item.marketPrice??item.value??item.close),
        movePct:txNumber(item.movePct??item.priceMovePct??item.changePct??item.returnPct??item.impactPct)
      };
    }).filter(event=>event&&Number.isFinite(event.time)).sort((a,b)=>a.time-b.time);

    if(!events.length){
      const time=txDate(record.lastPriceEventAt??record.lastPriceEventDate??record.lastGameDate??record.lastEventAt);
      const movePct=txNumber(record.lastGameMovePct??record.lastEventMovePct??record.dailyChange);
      if(Number.isFinite(time)&&Number.isFinite(movePct)) events.push({time,price:current,movePct});
    }
    if(!events.length) return [];

    let after=current;
    const points=[];
    for(let i=events.length-1;i>=0;i--){
      const event=events[i];
      if(Number.isFinite(event.price)&&event.price>0) after=event.price;
      const pct=Number.isFinite(event.movePct)?event.movePct:0;
      const before=Math.abs(1+pct/100)>.0001?after/(1+pct/100):after;
      points.unshift({time:event.time-1,value:Number(before.toFixed(2)),verified:true});
      points.unshift({time:event.time,value:Number(after.toFixed(2)),verified:true});
      after=before;
    }
    return points.sort((a,b)=>a.time-b.time);
  }

  function txHash(text){
    let hash=2166136261;
    for(let i=0;i<text.length;i++){
      hash^=text.charCodeAt(i);
      hash=Math.imul(hash,16777619);
    }
    return hash>>>0;
  }

  function txNoise(seed,index){
    let x=(seed+Math.imul(index+1,0x9e3779b1))>>>0;
    x^=x<<13;x^=x>>>17;x^=x<<5;
    return ((x>>>0)/4294967295)*2-1;
  }

  function txVolatility(record){
    const stage=String(record.careerStage||'').toLowerCase();
    const games=Math.max(0,Number(record.professionalGames)||0);
    const metrics=record.activeMetrics&&typeof record.activeMetrics==='object'?record.activeMetrics:{};
    const consistency=Math.max(0,Math.min(100,Number(metrics.consistency)||70));
    if(stage.includes('rookie')||games<20) return 0.022;
    if(stage.includes('emerging')||games<80) return 0.018;
    if(consistency>=85&&games>=200) return 0.009;
    if(consistency>=75&&games>=100) return 0.012;
    return 0.015;
  }

  function txOneYearReconstruction(record,current,now){
    const seed=txHash(String(record.id||record.name||record.ticker||'talentx'));
    const count=53;
    const start=now-RECONSTRUCTED_SPAN;
    const volatility=txVolatility(record);
    const careerScore=Math.max(0,Math.min(100,Number(record.careerScore)||60));
    const savedTrend=(Array.isArray(record.trend)?record.trend:[]).map(txNumber).filter(v=>Number.isFinite(v)&&v>0);
    const savedStart=savedTrend.length>1?savedTrend[0]:NaN;
    let annualReturn=Number.isFinite(savedStart)?current/savedStart-1:(careerScore-55)/100*0.22;
    annualReturn=Math.max(-0.28,Math.min(0.42,annualReturn));

    const raw=[1];
    let value=1;
    for(let i=1;i<count;i++){
      const cycle=Math.sin((i/count)*Math.PI*4+(seed%628)/100)*volatility*0.45;
      const shock=txNoise(seed,i)*volatility;
      const drift=Math.log(1+annualReturn)/(count-1);
      value*=Math.exp(drift+cycle+shock);
      raw.push(value);
    }
    const scale=current/raw[raw.length-1];
    return raw.map((item,index)=>({
      time:start+(RECONSTRUCTED_SPAN*index/(count-1)),
      value:Number(Math.max(1,item*scale).toFixed(2)),
      reconstructed:true
    }));
  }

  function txMergePoints(reconstructed,dated){
    if(!reconstructed.length) return dated;
    if(!dated.length) return reconstructed;
    const firstDated=dated[0].time;
    const merged=[...reconstructed.filter(point=>point.time<firstDated),...dated].sort((a,b)=>a.time-b.time);
    const output=[];
    for(const point of merged){
      const prior=output[output.length-1];
      if(prior&&Math.abs(prior.time-point.time)<1000) output[output.length-1]=point;
      else output.push(point);
    }
    return output;
  }

  function txLinearSeries(points,start,now,count,current){
    const ordered=points.filter(point=>point.time<=now).sort((a,b)=>a.time-b.time);
    if(!ordered.length) return Array.from({length:count},(_,i)=>({time:start+(now-start)*i/(count-1),value:current}));
    return Array.from({length:count},(_,index)=>{
      const time=start+((now-start)*(index/(count-1)));
      let left=ordered[0],right=ordered[ordered.length-1];
      for(let i=1;i<ordered.length;i++){
        if(ordered[i].time>=time){right=ordered[i];left=ordered[i-1];break;}
        left=ordered[i];
      }
      let value=left.value;
      if(right.time>left.time&&time>left.time){
        const ratio=Math.max(0,Math.min(1,(time-left.time)/(right.time-left.time)));
        value=left.value+(right.value-left.value)*ratio;
      }
      if(index===count-1)value=current;
      return {time,value:Number(value.toFixed(2))};
    });
  }

  function txStepSeries(points,start,now,count,current){
    const ordered=points.filter(point=>point.time<=now&&point.verified!==false).sort((a,b)=>a.time-b.time);
    let opening=current;
    for(const point of ordered){
      if(point.time<=start) opening=point.value;
      else break;
    }
    const inRange=ordered.filter(point=>point.time>start&&point.time<=now);
    return Array.from({length:count},(_,index)=>{
      const time=start+((now-start)*(index/(count-1)));
      let value=opening;
      for(const point of inRange){
        if(point.time<=time)value=point.value;
        else break;
      }
      if(index===count-1)value=current;
      return {time,value:Number(value.toFixed(2))};
    });
  }

  chartSeries=function(record,range=chartRange){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    const current=Math.max(1,Number(localPrice(record))||1);
    const now=Date.now();
    const start=txRangeStart(range,now);

    if(SHORT_RANGES.has(range)){
      // Never use reconstructed trend or generic price-history samples here.
      // Every short-range movement must come from a recorded pricing event.
      const events=txEventPoints(record,current);
      return txStepSeries(events,start,now,config.points,current);
    }

    const explicit=txExplicitPricePoints(record);
    const dated=explicit.length?explicit:txEventPoints(record,current);
    const reconstructed=txOneYearReconstruction(record,current,now);
    const points=txMergePoints(reconstructed,dated);
    return txLinearSeries(points,start,now,config.points,current);
  };

  window.talentxChartHistoryDisclosure='1D and 5D show recorded valuations only. Older chart values may be reconstructed for visual context until verified TalentX history is available.';
})();
