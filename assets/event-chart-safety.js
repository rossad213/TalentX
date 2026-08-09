/* TalentX event-chart safety layer.
 * Durable priceEvents are the only source allowed to create event-driven steps.
 * Legacy priceHistory may come from an older pricing scale and is never allowed
 * to manufacture a current chart move. If the durable event chain is stale or
 * discontinuous, event-driven categories show a flat current price instead.
 */
(function(){
  if(typeof chartSeries!=='function') return;
  const priorChartSeries=chartSeries;
  const DAY=24*60*60*1000;
  const EVENT_CATEGORIES=new Set(['Athlete','Music','Actor']);

  function dateOnlyUtc(value){
    return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T00:00:00(?:\.000)?Z$/i.test(value.trim());
  }
  function asTime(value){
    if(value===null||value===undefined||value==='') return NaN;
    if(typeof value==='number') return value<1e12?value*1000:value;
    const text=String(value).trim();
    const parsed=Date.parse(text);
    if(!Number.isFinite(parsed)) return NaN;
    return dateOnlyUtc(text)?parsed+12*60*60*1000:parsed;
  }
  function asPrice(value){
    const parsed=Number(value);
    return Number.isFinite(parsed)&&parsed>0?parsed:NaN;
  }
  function pctGap(a,b){
    return Number.isFinite(a)&&a>0&&Number.isFinite(b)&&b>0?Math.abs((b/a)-1):Infinity;
  }
  function rangeStart(range,now){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    if(range==='YTD') return new Date(new Date(now).getFullYear(),0,1).getTime();
    return now-(config.duration||DAY);
  }

  function durableEvents(record){
    const raw=Array.isArray(record?.priceEvents)?record.priceEvents:[];
    const events=[];
    for(const event of raw){
      if(!event||typeof event!=='object'||event.verified===false||event.synthetic===true||event.reconstructed===true) continue;
      const time=asTime(event.startedAt??event.time??event.date??event.eventDate);
      const before=asPrice(event.priceBefore);
      const after=asPrice(event.priceAfter??event.price??event.marketPrice);
      if(!Number.isFinite(time)||!Number.isFinite(after)) continue;
      // TalentX event policies are deliberately bounded. A giant single-event
      // jump indicates an old price scale or malformed history, not a real move.
      if(Number.isFinite(before)&&pctGap(before,after)>.15) continue;
      events.push({time,before,after,event});
    }
    events.sort((a,b)=>a.time-b.time);
    if(!events.length) return [];

    // Keep only the newest continuous chain. A pricing-model migration can leave
    // older event prices on a different scale; never draw a bridge across it.
    let chain=[];
    for(const item of events){
      if(!chain.length){
        chain=[item];
        continue;
      }
      const prior=chain[chain.length-1];
      const expected=prior.after;
      const nextOpen=Number.isFinite(item.before)?item.before:item.after;
      if(pctGap(expected,nextOpen)>.08) chain=[item];
      else chain.push(item);
    }

    const current=Math.max(1,Number(localPrice(record))||1);
    const last=chain[chain.length-1];
    // Event-driven market price should remain close to the last durable event.
    // A large mismatch means the chain belongs to an obsolete pricing scale.
    if(pctGap(last.after,current)>.25) return [];
    return chain;
  }

  function eventPoints(record){
    const points=[];
    for(const item of durableEvents(record)){
      if(Number.isFinite(item.before)) points.push({time:item.time-1000,value:item.before});
      points.push({time:item.time,value:item.after});
    }
    return points;
  }

  function stepSeries(record,range,points){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    const count=Math.max(2,Number(config.points)||48);
    const current=Math.max(1,Number(localPrice(record))||1);
    const now=Date.now();
    const start=rangeStart(range,now);
    if(!points.length){
      return Array.from({length:count},(_,index)=>({
        time:start+((now-start)*(index/(count-1))),
        value:Number(current.toFixed(2))
      }));
    }
    const ordered=points.filter(point=>point.time<=now).sort((a,b)=>a.time-b.time);
    let opening=current;
    const beforeStart=ordered.filter(point=>point.time<=start);
    if(beforeStart.length) opening=beforeStart[beforeStart.length-1].value;
    else if(ordered.length) opening=ordered[0].value;
    const inRange=ordered.filter(point=>point.time>start&&point.time<=now);
    return Array.from({length:count},(_,index)=>{
      const time=start+((now-start)*(index/(count-1)));
      let value=opening;
      for(const point of inRange){
        if(point.time<=time) value=point.value;
        else break;
      }
      if(index===count-1) value=current;
      return {time,value:Number(value.toFixed(2))};
    });
  }

  chartSeries=function(record,range=chartRange){
    if(EVENT_CATEGORIES.has(String(record?.primaryCategory||''))){
      return stepSeries(record,range,eventPoints(record));
    }
    return priorChartSeries(record,range);
  };

  window.talentxEventChartSafety='durable-price-events-current-scale-v1';
})();
