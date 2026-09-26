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

  function nflReplayPoints(record){
    if(String(record?.leagueOrMedium||'').toUpperCase()!=='NFL') return [];
    if(String(record?.priceHistoryStatus||'')!=='source-backed-full-point-in-time-nfl-replay') return [];
    const history=Array.isArray(record?.priceHistory)?record.priceHistory:[];
    return dedupe(history.map(item=>{
      if(!item||typeof item!=='object') return null;
      const source=String(item.source||'');
      const historyType=String(item.historyType||'');
      if(source!=='verified-nfl-event-replay'&&historyType!=='verified-event-replay') return null;
      const time=txDate(item.time??item.timestamp??item.date);
      const value=txNumber(item.price??item.value??item.marketPrice);
      if(!Number.isFinite(time)||!Number.isFinite(value)||value<=0) return null;
      return {time,value,verified:true,eventId:String(item.eventId||''),phase:String(item.phase||'')};
    }).filter(Boolean));
  }

  function nflEventAlignedSeries(record,range){
    const replay=nflReplayPoints(record);
    if(!replay.length) return null;
    const now=Date.now();
    const start=txRangeStart(range,now);
    const eligible=replay.filter(point=>point.time<=now);
    if(!eligible.length) return null;

    const beforeStart=eligible.filter(point=>point.time<=start);
    const inRange=eligible.filter(point=>point.time>start&&point.time<=now);
    let opening;
    let coverageStart;
    if(beforeStart.length){
      opening=beforeStart[beforeStart.length-1].value;
      coverageStart=start;
    }else if(inRange.length){
      opening=inRange[0].value;
      coverageStart=inRange[0].time;
    }else{
      return null;
    }

    const output=[{
      time:start,
      value:Number(opening.toFixed(2)),
      verified:beforeStart.length>0,
      coverageStatus:beforeStart.length?'complete':'partial',
      coverageStart
    }];
    let last=opening;
    for(const point of inRange){
      last=point.value;
      output.push({
        ...point,
        value:Number(point.value.toFixed(2)),
        verified:true,
        coverageStatus:beforeStart.length?'complete':'partial',
        coverageStart
      });
    }
    output.push({
      time:now,
      value:Number(last.toFixed(2)),
      verified:true,
      coverageStatus:beforeStart.length?'complete':'partial',
      coverageStart
    });
    return dedupe(output);
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
    const nflReplay=nflEventAlignedSeries(record,range);
    if(nflReplay) return nflReplay;
    return stepSeries(record,range);
  };

  window.talentxChartHistoryDisclosure='Charts show only dated source-backed events or recorded TalentX observations. Historical TalentX prices created during backfill are simulated model responses to verified real-world events; missing history is shown as missing rather than invented.';
  window.talentxChartHistoryMode='source-backed-only-v4-nfl-event-aligned';
})();
