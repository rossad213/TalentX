/*
 * TalentX historical chart reconstruction
 *
 * Charts remain flat between supported events. This module prefers explicit
 * dated price history, then dated event history, and finally reconstructs the
 * catalog's saved trend across the prior six months. It never adds randomness.
 */
(function(){
  const DAY=24*60*60*1000;
  const RECONSTRUCTED_TREND_SPAN=182*DAY;

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
      if(Array.isArray(item)) return {time:txDate(item[0]),value:txNumber(item[1])};
      if(!item||typeof item!=='object') return null;
      return {
        time:txDate(item.time??item.timestamp??item.date??item.eventDate??item.asOf),
        value:txNumber(item.value??item.price??item.marketPrice??item.close)
      };
    }).filter(point=>point&&Number.isFinite(point.time)&&Number.isFinite(point.value)&&point.value>0)
      .sort((a,b)=>a.time-b.time);
  }

  function txEventPoints(record,current){
    const candidates=[record.priceEvents,record.eventHistory,record.events,record.marketEvents];
    const source=candidates.find(Array.isArray)||[];
    const events=source.map(item=>{
      if(!item||typeof item!=='object') return null;
      return {
        time:txDate(item.time??item.timestamp??item.date??item.eventDate??item.asOf),
        price:txNumber(item.price??item.marketPrice??item.value??item.close),
        movePct:txNumber(item.movePct??item.priceMovePct??item.changePct??item.returnPct??item.impactPct)
      };
    }).filter(event=>event&&Number.isFinite(event.time)).sort((a,b)=>a.time-b.time);

    if(!events.length){
      const time=txDate(record.lastPriceEventAt??record.lastPriceEventDate??record.lastGameDate??record.lastEventAt);
      const movePct=txNumber(record.lastGameMovePct??record.lastEventMovePct??record.dailyChange);
      if(Number.isFinite(time)&&Number.isFinite(movePct)&&Math.abs(movePct)>.0001){
        events.push({time,price:current,movePct});
      }
    }
    if(!events.length) return [];

    let after=current;
    const points=[];
    for(let i=events.length-1;i>=0;i--){
      const event=events[i];
      if(Number.isFinite(event.price)&&event.price>0) after=event.price;
      const pct=Number.isFinite(event.movePct)?event.movePct:NaN;
      const before=Number.isFinite(pct)&&Math.abs(1+pct/100)>.0001?after/(1+pct/100):after;
      points.unshift({time:event.time-1,value:Number(before.toFixed(2))});
      points.unshift({time:event.time,value:Number(after.toFixed(2))});
      after=before;
    }
    return points.sort((a,b)=>a.time-b.time);
  }

  function txReconstructedTrendPoints(record,current,now){
    const trend=(Array.isArray(record.trend)?record.trend:[])
      .map(txNumber)
      .filter(value=>Number.isFinite(value)&&value>0);
    if(!trend.length) return [];
    if(Math.abs(trend[trend.length-1]-current)>.005) trend.push(current);
    if(trend.length===1) return [{time:now-RECONSTRUCTED_TREND_SPAN,value:trend[0]},{time:now,value:current}];
    const start=now-RECONSTRUCTED_TREND_SPAN;
    return trend.map((value,index)=>({
      time:start+((now-start)*(index/(trend.length-1))),
      value:Number(value.toFixed(2))
    }));
  }

  function txStepSeries(points,start,now,count,current){
    const ordered=points.filter(point=>point.time<=now).sort((a,b)=>a.time-b.time);
    let opening=current;
    for(const point of ordered){
      if(point.time<=start) opening=point.value;
      else break;
    }
    const timeline=[{time:start,value:opening},...ordered.filter(point=>point.time>start&&point.time<now),{time:now,value:current}];
    return Array.from({length:count},(_,index)=>{
      const time=start+((now-start)*(index/(count-1)));
      let value=timeline[0].value;
      for(const point of timeline){
        if(point.time<=time) value=point.value;
        else break;
      }
      if(index===count-1) value=current;
      return {time,value:Number(value.toFixed(2))};
    });
  }

  chartSeries=function(record,range=chartRange){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    const current=Math.max(1,Number(localPrice(record))||1);
    const now=Date.now();
    const start=txRangeStart(range,now);
    const explicit=txExplicitPricePoints(record);
    const events=explicit.length?explicit:txEventPoints(record,current);
    const points=events.length?events:txReconstructedTrendPoints(record,current,now);
    return txStepSeries(points,start,now,config.points,current);
  };
})();
