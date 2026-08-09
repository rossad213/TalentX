/* TalentX event-chart safety layer.
 * Durable priceEvents are the only source allowed to create event-driven steps.
 * Legacy priceHistory may come from an older pricing scale and is never allowed
 * to manufacture a current chart move. Verified event percentages may be
 * rebased onto the current valuation scale after a pricing-model migration.
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
      if(!Number.isFinite(move)||Math.abs(move)>.15*100) continue;
      const denominator=1+(move/100);
      if(!Number.isFinite(denominator)||denominator<=0) continue;
      const before=after/denominator;
      if(!Number.isFinite(before)||before<=0) continue;
      rebuilt.push({
        time:item.time,
        before,
        after,
        event:{...item.event,movePct:move,chartPriceRebased:true}
      });
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
      if(!Number.isFinite(time)||!Number.isFinite(move)) continue;
      // Verified event policies are bounded. An implausibly large event move is
      // malformed evidence and is ignored rather than displayed.
      if(Math.abs(move)>15) continue;
      events.push({time,before,after,event:{...event,movePct:move}});
    }
    events.sort((a,b)=>a.time-b.time);
    if(!events.length) return [];

    // Prefer the stored price chain when it is internally continuous and still
    // on the current valuation scale.
    let chain=[];
    for(const item of events){
      if(!Number.isFinite(item.before)||!Number.isFinite(item.after)) continue;
      if(pctGap(item.before,item.after)>.15) continue;
      if(!chain.length){
        chain=[item];
        continue;
      }
      const prior=chain[chain.length-1];
      if(pctGap(prior.after,item.before)>.08) chain=[item];
      else chain.push(item);
    }

    const current=Math.max(1,Number(record?.marketPrice)||Number(localPrice(record))||1);
    if(chain.length&&pctGap(chain[chain.length-1].after,current)<=.25) return chain;

    // A pricing-model migration can invalidate absolute historical dollars while
    // leaving the verified event and its percentage move perfectly valid. In
    // that case reconstruct the event chain backward from today's market price.
    // This preserves real event direction/magnitude without inventing movement.
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
    if(!points.length){
      return {status:'none',range,start,now,coverageStart:null,points:[]};
    }
    const beforeStart=points.filter(point=>point.time<=start);
    if(beforeStart.length){
      return {status:'complete',range,start,now,coverageStart:start,points};
    }
    const first=points.find(point=>point.time>start&&point.time<=now);
    if(!first){
      return {status:'none',range,start,now,coverageStart:null,points:[]};
    }
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
      if(index===count-1) value=current;
      const verified=coverageInfo.status==='complete'||(coverageInfo.coverageStart!==null&&time>=coverageInfo.coverageStart);
      return {
        time,
        value:Number(value.toFixed(2)),
        verified,
        coverageStatus:coverageInfo.status,
        coverageStart:coverageInfo.coverageStart
      };
    });
  }

  chartSeries=function(record,range=chartRange){
    if(EVENT_CATEGORIES.has(String(record?.primaryCategory||''))){
      const info=coverage(record,range);
      return stepSeries(record,range,info);
    }
    return priorChartSeries(record,range);
  };

  window.talentxEventCoverage=coverage;
  window.talentxDurablePriceEvents=durableEvents;
  window.talentxEventChartSafety='durable-price-events-coverage-aware-v3-rebased';
})();
