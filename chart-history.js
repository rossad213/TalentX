/*
 * TalentX source-backed historical chart adapter.
 *
 * Historical charts must never invent movement. Every plotted change comes from
 * a dated saved price event or an explicit dated price observation. When there
 * is not enough verified history, the chart remains flat/partial and the
 * coverage layer explains that the period is not yet covered.
 */
(function(){
  const DAY=24*60*60*1000;

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

  function explicitPricePoints(record){
    const candidates=[record.priceHistory,record.historicalPrices,record.marketHistory,record.chartHistory];
    const source=candidates.find(Array.isArray)||[];
    return source.map(item=>{
      if(Array.isArray(item)){
        const time=txDate(item[0]),value=txNumber(item[1]);
        return Number.isFinite(time)&&Number.isFinite(value)&&value>0?{time,value,verified:true}:null;
      }
      if(!item||typeof item!=='object'||item.reconstructed===true||item.synthetic===true) return null;
      const historyType=String(item.historyType||'').toLowerCase();
      if(historyType==='reconstructed'||historyType==='synthetic') return null;
      const time=txDate(item.time??item.timestamp??item.date??item.eventDate??item.asOf);
      const value=txNumber(item.value??item.price??item.marketPrice??item.close);
      if(!Number.isFinite(time)||!Number.isFinite(value)||value<=0) return null;
      return {time,value,verified:true};
    }).filter(Boolean).sort((a,b)=>a.time-b.time);
  }

  function eventPoints(record){
    const events=Array.isArray(record.priceEvents)?record.priceEvents:[];
    const points=[];
    for(const event of events){
      if(!event||typeof event!=='object'||event.verified===false||event.reconstructed===true||event.synthetic===true) continue;
      const time=txDate(event.startedAt??event.time??event.date??event.eventDate??event.asOf);
      const before=txNumber(event.priceBefore);
      const after=txNumber(event.priceAfter??event.price??event.marketPrice??event.value);
      if(!Number.isFinite(time)||!Number.isFinite(after)||after<=0) continue;
      if(Number.isFinite(before)&&before>0) points.push({time:time-1000,value:before,verified:true});
      points.push({time,value:after,verified:true});
    }
    return points.sort((a,b)=>a.time-b.time);
  }

  function dedupe(points){
    const ordered=points.filter(point=>point&&Number.isFinite(point.time)&&Number.isFinite(point.value)&&point.value>0).sort((a,b)=>a.time-b.time);
    const output=[];
    for(const point of ordered){
      const prior=output[output.length-1];
      if(prior&&prior.time===point.time) output[output.length-1]=point;
      else output.push(point);
    }
    return output;
  }

  function sourceBackedPoints(record){
    return dedupe([...explicitPricePoints(record),...eventPoints(record)]);
  }

  function stepSeries(record,range){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    const count=Math.max(2,Number(config.points)||48);
    const now=Date.now();
    const start=txRangeStart(range,now);
    const current=Math.max(1,Number(localPrice(record))||1);
    const points=sourceBackedPoints(record).filter(point=>point.time<=now);

    if(!points.length){
      return Array.from({length:count},(_,index)=>({
        time:start+((now-start)*(index/(count-1))),
        value:Number(current.toFixed(2)),
        verified:false,
        coverageStatus:'none'
      }));
    }

    let opening=points[0].value;
    const beforeStart=points.filter(point=>point.time<=start);
    if(beforeStart.length) opening=beforeStart[beforeStart.length-1].value;
    const inRange=points.filter(point=>point.time>start&&point.time<=now);
    const firstVerified=beforeStart.length?start:(inRange[0]?.time??null);

    return Array.from({length:count},(_,index)=>{
      const time=start+((now-start)*(index/(count-1)));
      let value=opening;
      for(const point of inRange){
        if(point.time<=time) value=point.value;
        else break;
      }
      return {
        time,
        value:Number(value.toFixed(2)),
        verified:firstVerified!==null&&time>=firstVerified,
        coverageStatus:beforeStart.length?'complete':'partial',
        coverageStart:firstVerified
      };
    });
  }

  chartSeries=function(record,range=chartRange){
    return stepSeries(record,range);
  };

  window.talentxChartHistoryDisclosure='Charts show only dated source-backed events or recorded TalentX observations. Historical TalentX prices created during backfill are simulated model responses to verified real-world events; missing history is shown as missing rather than invented.';
  window.talentxChartHistoryMode='source-backed-only-v3-no-reconstruction';
})();
