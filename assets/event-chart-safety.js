/* TalentX event-chart safety layer.
 * Durable priceEvents are preferred for event-driven steps. When a listing has
 * no usable durable event chain (for example MLB after invalid game events were
 * deliberately removed), the already-verified dated priceHistory adapter is
 * allowed to render that safe history instead of forcing a flat chart.
 * Legacy/synthetic history remains excluded by chart-history.js.
 */
(function(){
  if(typeof chartSeries!=='function') return;
  const priorChartSeries=chartSeries;
  const priorDisplayChange=typeof displayChange==='function'?displayChange:null;
  const DAY=24*60*60*1000;
  const EVENT_CATEGORIES=new Set(['Athlete','Music','Actor','Creator']);

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
  function asMove(event,before,after){
    const recorded=Number(event?.movePct);
    if(Number.isFinite(recorded)&&Math.abs(recorded)<=15) return recorded;
    if(Number.isFinite(before)&&before>0&&Number.isFinite(after)&&after>0){
      const derived=((after/before)-1)*100;
      if(Number.isFinite(derived)&&Math.abs(derived)<=15) return derived;
    }
    return NaN;
  }
  function pctGap(a,b){
    return Number.isFinite(a)&&a>0&&Number.isFinite(b)&&b>0?Math.abs((b/a)-1):Infinity;
  }
  function rangeStart(range,now){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    if(range==='YTD') return new Date(new Date(now).getFullYear(),0,1).getTime();
    return now-(config.duration||DAY);
  }

  function rebaseEvents(events,record){
    const current=Math.max(1,Number(record?.marketPrice)||Number(localPrice(record))||1);
    let after=current;
    const rebuilt=[];
    for(let index=events.length-1;index>=0;index--){
      const item=events[index];
      const move=asMove(item.event,item.before,item.after);
      if(!Number.isFinite(move)||Math.abs(move)>15) continue;
      const denominator=1+(move/100);
      if(!Number.isFinite(denominator)||denominator<=0) continue;
      const before=after/denominator;
      if(!Number.isFinite(before)||before<=0) continue;
      rebuilt.push({time:item.time,before,after,event:{...item.event,movePct:move,chartPriceRebased:true}});
      after=before;
    }
    rebuilt.reverse();
    return rebuilt;
  }

  function durableEvents(record){
    const raw=Array.isArray(record?.priceEvents)?record.priceEvents:[];
    const events=[];
    for(const event of raw){
      if(!event||typeof event!=='object'||event.verified===false||event.synthetic===true||event.reconstructed===true) continue;
      const time=asTime(event.startedAt??event.time??event.date??event.eventDate);
      const before=asPrice(event.priceBefore);
      const after=asPrice(event.priceAfter??event.price??event.marketPrice);
      const move=asMove(event,before,after);
      if(!Number.isFinite(time)||!Number.isFinite(move)||Math.abs(move)>15) continue;
      events.push({time,before,after,event:{...event,movePct:move}});
    }
    events.sort((a,b)=>a.time-b.time);
    if(!events.length) return [];

    let chain=[];
    for(const item of events){
      if(!Number.isFinite(item.before)||!Number.isFinite(item.after)) continue;
      if(pctGap(item.before,item.after)>.15) continue;
      if(!chain.length){chain=[item];continue;}
      const prior=chain[chain.length-1];
      if(pctGap(prior.after,item.before)>.08) chain=[item];
      else chain.push(item);
    }

    const current=Math.max(1,Number(record?.marketPrice)||Number(localPrice(record))||1);
    if(chain.length&&pctGap(chain[chain.length-1].after,current)<=.25) return chain;
    return rebaseEvents(events,record);
  }

  function eventPoints(record){
    const points=[];
    for(const item of durableEvents(record)){
      if(Number.isFinite(item.before)) points.push({time:item.time-1000,value:item.before});
      if(Number.isFinite(item.after)) points.push({time:item.time,value:item.after});
    }
    return points.sort((a,b)=>a.time-b.time);
  }

  function coverage(record,range=chartRange){
    const now=Date.now();
    const start=rangeStart(range,now);
    const points=eventPoints(record).filter(point=>point.time<=now);
    if(!points.length) return {status:'none',range,start,now,coverageStart:null,points:[]};
    const beforeStart=points.filter(point=>point.time<=start);
    if(beforeStart.length) return {status:'complete',range,start,now,coverageStart:start,points};
    const first=points.find(point=>point.time>start&&point.time<=now);
    if(!first) return {status:'none',range,start,now,coverageStart:null,points:[]};
    return {status:'partial',range,start,now,coverageStart:first.time,points};
  }

  function stepSeries(record,range,coverageInfo){
    const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
    const count=Math.max(2,Number(config.points)||48);
    const current=Math.max(1,Number(localPrice(record))||1);
    const now=coverageInfo.now;
    const start=coverageInfo.start;
    const points=coverageInfo.points;
    if(!points.length){
      return Array.from({length:count},(_,index)=>({
        time:start+((now-start)*(index/(count-1))),
        value:Number(current.toFixed(2)),
        verified:false,
        coverageStatus:'none',
        coverageStart:null
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
      const verified=coverageInfo.status==='complete'||(coverageInfo.coverageStart!==null&&time>=coverageInfo.coverageStart);
      return {time,value:Number(value.toFixed(2)),verified,coverageStatus:coverageInfo.status,coverageStart:coverageInfo.coverageStart};
    });
  }

  chartSeries=function(record,range=chartRange){
    if(EVENT_CATEGORIES.has(String(record?.primaryCategory||''))){
      // Complete NFL point-in-time replay lives in priceHistory by design. Use
      // that event-aligned series instead of the mutable live priceEvents ledger,
      // which may contain older pricing-model versions or omit a recovered game.
      if(
        String(record?.leagueOrMedium||'').toUpperCase()==='NFL'&&
        record?.priceHistoryStatus==='source-backed-full-point-in-time-nfl-replay'
      ){
        return priorChartSeries(record,range);
      }
      // While MLB game repricing is intentionally protected, its verified dated
      // priceHistory is the authoritative chart source. Preserved non-game events
      // (draft/signing/team change) must not override that history and make recent
      // ranges look flat.
      if(String(record?.leagueOrMedium||'').toUpperCase()==='MLB'&&record?.priceHistoryStatus==='verified'){
        return priorChartSeries(record,range);
      }
      const info=coverage(record,range);
      if(!info.points.length) return priorChartSeries(record,range);
      return stepSeries(record,range,info);
    }
    return priorChartSeries(record,range);
  };

  // app.js historically preferred lastGameMovePct whenever lastPriceEventId was
  // present. That hides a valid non-game/daily move after an MLB repair because
  // lastGameMovePct is intentionally zero. Prefer the nonzero recorded move and,
  // when no event pointer survives, derive the visible change from the persisted
  // previousMarketPrice. Local virtual trades still compound from the same prior.
  if(priorDisplayChange){
    displayChange=function(record){
      const listed=Number(record?.marketPrice||0);
      const current=Number(localPrice(record));
      const gameMove=Number(record?.lastGameMovePct);
      const dailyMove=Number(record?.dailyChange);
      let recorded=Number.isFinite(gameMove)&&Math.abs(gameMove)>.0005?gameMove:(Number.isFinite(dailyMove)?dailyMove:0);
      const previous=Number(record?.previousMarketPrice);
      if(Math.abs(recorded)<=.0005&&Number.isFinite(previous)&&previous>0&&Number.isFinite(listed)&&listed>0&&Math.abs(listed-previous)>=.005){
        recorded=((listed/previous)-1)*100;
      }
      if(!Number.isFinite(listed)||listed<=0||!Number.isFinite(current)) return recorded;
      if(Math.abs(current-listed)<.005) return recorded;
      const prior=Number.isFinite(previous)&&previous>0?previous:(listed/(1+recorded/100));
      return Number.isFinite(prior)&&prior>0?((current/prior)-1)*100:recorded;
    };
  }

  window.talentxEventCoverage=coverage;
  window.talentxDurablePriceEvents=durableEvents;
  window.talentxEventChartSafety='durable-events-with-nfl-replay-v8';
})();