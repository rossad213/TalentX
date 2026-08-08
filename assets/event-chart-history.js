/* TalentX short-range event chart v2.
 * 1D/5D charts are step charts built only from verified dated price-changing events.
 * No random, reconstructed, or generic hourly snapshot movement is introduced.
 */
(function(){
  if(typeof chartSeries!=='function') return;
  const originalChartSeries=chartSeries;

  function asTime(value){
    if(value===null||value===undefined||value==='') return NaN;
    if(typeof value==='number') return value<1e12?value*1000:value;
    const parsed=Date.parse(value);
    return Number.isFinite(parsed)?parsed:NaN;
  }
  function asPrice(value){
    const parsed=Number(value);
    return Number.isFinite(parsed)&&parsed>0?parsed:NaN;
  }
  function rangeStart(range,now){
    const cfg=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['5D'];
    return now-(cfg.duration||24*60*60*1000);
  }
  function verifiedHistoryPoints(record){
    const history=Array.isArray(record.priceHistory)?record.priceHistory:[];
    return history.map(item=>{
      if(!item||typeof item!=='object') return null;
      const historyType=String(item.historyType||'verified').toLowerCase();
      const eventType=String(item.eventType||'').toLowerCase();
      if(historyType==='reconstructed'||item.reconstructed===true||item.synthetic===true) return null;
      if(eventType==='market'||eventType==='historical-baseline') return null;
      const time=asTime(item.time??item.timestamp??item.date);
      const value=asPrice(item.price??item.value??item.marketPrice);
      if(!Number.isFinite(time)||!Number.isFinite(value)) return null;
      return {time,value,verified:true};
    }).filter(Boolean);
  }
  function directEventPoints(record){
    const events=Array.isArray(record.priceEvents)?record.priceEvents:[];
    const points=[];
    for(const event of events){
      if(!event||typeof event!=='object'||event.verified===false) continue;
      const time=asTime(event.startedAt??event.time??event.date);
      const before=asPrice(event.priceBefore);
      const after=asPrice(event.priceAfter??event.price);
      if(!Number.isFinite(time)||!Number.isFinite(after)) continue;
      if(Number.isFinite(before)) points.push({time:time-1000,value:before,verified:true});
      points.push({time,value:after,verified:true});
    }
    return points;
  }
  function dedupe(points){
    const ordered=points.filter(p=>p&&Number.isFinite(p.time)&&Number.isFinite(p.value)).sort((a,b)=>a.time-b.time);
    const out=[];
    for(const point of ordered){
      const prior=out[out.length-1];
      if(prior&&prior.time===point.time) out[out.length-1]=point;
      else out.push(point);
    }
    return out;
  }
  function eventStepSeries(record,range){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['5D'];
    const current=Math.max(1,Number(localPrice(record))||1);
    const now=Date.now();
    const start=rangeStart(range,now);
    const points=dedupe([...verifiedHistoryPoints(record),...directEventPoints(record)]).filter(point=>point.time<=now);
    if(!points.length){
      return Array.from({length:config.points},(_,i)=>({time:start+(now-start)*i/(config.points-1),value:Number(current.toFixed(2))}));
    }

    let opening=current;
    const beforeStart=points.filter(point=>point.time<=start);
    if(beforeStart.length) opening=beforeStart[beforeStart.length-1].value;
    else {
      const firstAfter=points.find(point=>point.time>start);
      if(firstAfter) opening=firstAfter.value;
    }
    const inRange=points.filter(point=>point.time>start&&point.time<=now);
    return Array.from({length:config.points},(_,index)=>{
      const time=start+((now-start)*(index/(config.points-1)));
      let value=opening;
      for(const point of inRange){
        if(point.time<=time) value=point.value;
        else break;
      }
      if(index===config.points-1) value=current;
      return {time,value:Number(value.toFixed(2))};
    });
  }

  chartSeries=function(record,range=chartRange){
    if(range==='1D'||range==='5D') return eventStepSeries(record,range);
    return originalChartSeries(record,range);
  };
  window.talentxShortRangeChartMode='verified-price-changing-events-v2';
})();
