/* TalentX verified-event chart presentation fixes.
 * - Chart pricing is supplied by the dedicated event-chart safety layer.
 * - This file only controls presentation: staircase rendering, clean axes, and date labels.
 * - It must never create or force a price movement on its own.
 */
(function(){
  if(typeof chartSeries!=='function'||typeof detailedTrendSvg!=='function') return;

  const priorProfile=typeof profile==='function'?profile:null;

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
  function eventIdentity(event){
    return String(event?.eventKey||event?.eventId||'');
  }
  function verifiedEventPoints(record){
    const events=Array.isArray(record?.priceEvents)?record.priceEvents:[];
    const directIds=new Set(events.map(eventIdentity).filter(Boolean));
    const points=[];

    for(const event of events){
      if(!event||typeof event!=='object'||event.verified===false||event.synthetic===true||event.reconstructed===true) continue;
      const time=asTime(event.startedAt??event.time??event.date??event.eventDate);
      const before=asPrice(event.priceBefore);
      const after=asPrice(event.priceAfter??event.price??event.marketPrice);
      if(!Number.isFinite(time)||!Number.isFinite(after)) continue;
      if(Number.isFinite(before)) points.push({time:time-1000,value:before,verified:true});
      points.push({time,value:after,verified:true});
    }

    const history=Array.isArray(record?.priceHistory)?record.priceHistory:[];
    for(const item of history){
      if(!item||typeof item!=='object') continue;
      const historyType=String(item.historyType||'verified').toLowerCase();
      const eventType=String(item.eventType||'').toLowerCase();
      const historyId=String(item.eventId||item.eventKey||'');
      if(historyType==='reconstructed'||item.reconstructed===true||item.synthetic===true) continue;
      if(eventType==='market'||eventType==='historical-baseline') continue;
      if(historyId&&directIds.has(historyId)) continue;
      const time=asTime(item.time??item.timestamp??item.date??item.eventDate??item.asOf);
      const value=asPrice(item.price??item.value??item.marketPrice??item.close);
      if(Number.isFinite(time)&&Number.isFinite(value)) points.push({time,value,verified:true});
    }

    points.sort((a,b)=>a.time-b.time||a.value-b.value);
    const deduped=[];
    for(const point of points){
      const prior=deduped[deduped.length-1];
      if(prior&&prior.time===point.time&&Math.abs(prior.value-point.value)<.005) continue;
      deduped.push(point);
    }
    return deduped;
  }

  function minTickFor(price){
    if(price>=100) return .05;
    if(price>=20) return .02;
    return .01;
  }
  function niceStep(raw,minStep){
    const target=Math.max(Number(raw)||0,minStep);
    const magnitude=Math.pow(10,Math.floor(Math.log10(target)));
    const normalized=target/magnitude;
    const factors=[1,2,2.5,5,10];
    const factor=factors.find(value=>value>=normalized)||10;
    return Math.max(minStep,factor*magnitude);
  }
  function nextNiceStep(step){
    return niceStep(step*1.001,minTickFor(step*100));
  }
  function rounded(value){
    return Number(Number(value).toFixed(8));
  }
  function cleanAxis(values,current){
    let min=Math.min(...values),max=Math.max(...values);
    const minTick=minTickFor(current);
    if(max-min<.005){
      const span=Math.max(minTick*4,current*.005);
      min=current-span/2;
      max=current+span/2;
    }
    let step=niceStep((max-min)/4,minTick);
    let floor=Math.floor((min+1e-9)/step)*step;
    let ceil=Math.ceil((max-1e-9)/step)*step;
    let intervals=Math.round((ceil-floor)/step);
    while(intervals>4){
      step=nextNiceStep(step);
      floor=Math.floor((min+1e-9)/step)*step;
      ceil=Math.ceil((max-1e-9)/step)*step;
      intervals=Math.round((ceil-floor)/step);
    }
    while(intervals<4){
      if(intervals%2===0) floor-=step;
      else ceil+=step;
      intervals++;
    }
    floor=rounded(floor);ceil=rounded(ceil);step=rounded(step);
    const ticks=Array.from({length:5},(_,index)=>rounded(ceil-step*index));
    return {floor,ceil,step,ticks};
  }

  function staircasePoints(values,x,y){
    if(!values.length) return '';
    const output=[`${x(0).toFixed(2)},${y(values[0]).toFixed(2)}`];
    for(let index=1;index<values.length;index++){
      const nextX=x(index).toFixed(2);
      output.push(`${nextX},${y(values[index-1]).toFixed(2)}`);
      output.push(`${nextX},${y(values[index]).toFixed(2)}`);
    }
    return output.join(' ');
  }

  detailedTrendSvg=function(r,height=250){
    const series=chartSeries(r);
    const a=series.map(point=>point.value); if(!a.length) return `<div class="chart-empty">No chart history yet.</div>`;
    const verifiedMode=verifiedEventPoints(r).length>0;
    const up=a[a.length-1]>=a[0];
    const color=up?'#58ef78':'#ff5e79';
    const W=1000,H=340,padL=18,padR=108,padT=18,padB=38;
    const current=a[a.length-1],open=a[0],high=Math.max(...a),low=Math.min(...a);
    const axis=cleanAxis(a,current),floor=axis.floor,ceil=axis.ceil;
    const usableW=W-padL-padR,usableH=H-padT-padB;
    const x=i=>padL+(a.length===1?usableW:(i/(a.length-1))*usableW);
    const y=v=>padT+((ceil-v)/Math.max(.0001,ceil-floor))*usableH;
    const ordinaryPoints=a.map((v,i)=>`${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    const linePoints=verifiedMode?staircasePoints(a,x,y):ordinaryPoints;
    const fillPoints=`${padL},${H-padB} ${linePoints} ${x(a.length-1)},${H-padB}`;
    const highIndex=a.indexOf(high),lowIndex=a.indexOf(low);
    const currentY=y(current),currentX=x(a.length-1);
    const priceTagY=Math.max(padT+12,Math.min(H-padB-12,currentY));
    const priceTagX=Math.min(W-76,currentX+18);
    const tickLines=axis.ticks.map((v,idx)=>`<g><line x1="${padL}" y1="${y(v).toFixed(2)}" x2="${W-padR+8}" y2="${y(v).toFixed(2)}" class="stock-grid-line ${idx===axis.ticks.length-1?'stock-grid-line--base':''}"></line><text x="${W-padR+14}" y="${(y(v)+4).toFixed(2)}" class="stock-y-label">${money(v)}</text></g>`).join('');
    const labelIndexes=[0,Math.floor((a.length-1)/2),a.length-1];
    const xLabels=labelIndexes.map((index,labelIndex)=>`<text x="${x(index).toFixed(2)}" y="${H-10}" text-anchor="${labelIndex===0?'start':labelIndex===2?'end':'middle'}" class="stock-x-label">${esc(formatAxisTime(series[index].time,chartRange))}</text>`).join('');
    const delta=current-open,pct=open?((delta/open)*100):0;
    return `<div class="stock-chart-wrap" style="height:${height}px" data-values="${a.join(',')}" data-times="${series.map(point=>Math.round(point.time)).join(',')}" data-range="${chartRange}" data-floor="${floor}" data-ceil="${ceil}" data-color="${color}">
      <svg class="stock-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Simulated ${chartRange} price chart for ${esc(r.name)}" onpointermove="trackChartPointer(event)" onpointerdown="beginChartPointer(event)" onpointerup="endChartPointer(event)" onpointercancel="endChartPointer(event)" onpointerleave="hideChartPointer(event)">
        <defs>
          <linearGradient id="stock-fill-${esc(r.id)}-${chartRange}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="${color}" stop-opacity="0.30"></stop>
            <stop offset="1" stop-color="${color}" stop-opacity="0.02"></stop>
          </linearGradient>
        </defs>
        ${tickLines}
        <polygon points="${fillPoints}" fill="url(#stock-fill-${esc(r.id)}-${chartRange})"></polygon>
        <line x1="${padL}" x2="${W-padR+8}" y1="${currentY.toFixed(2)}" y2="${currentY.toFixed(2)}" class="stock-price-line"></line>
        <polyline points="${linePoints}" class="stock-line" style="stroke:${color}"></polyline>
        <circle cx="${currentX.toFixed(2)}" cy="${currentY.toFixed(2)}" r="6" class="stock-current-marker" style="fill:${color}"></circle>
        <circle cx="${x(highIndex).toFixed(2)}" cy="${y(high).toFixed(2)}" r="4" class="stock-extreme-marker stock-high-marker"></circle>
        <circle cx="${x(lowIndex).toFixed(2)}" cy="${y(low).toFixed(2)}" r="4" class="stock-extreme-marker stock-low-marker"></circle>
        <rect x="${priceTagX.toFixed(2)}" y="${(priceTagY-14).toFixed(2)}" width="74" height="28" rx="8" class="stock-price-tag-box"></rect>
        <text x="${(priceTagX+37).toFixed(2)}" y="${(priceTagY+4).toFixed(2)}" text-anchor="middle" class="stock-price-tag-text">${money(current)}</text>
        ${xLabels}
      </svg>
      <div class="chart-crosshair" aria-hidden="true"></div>
      <div class="chart-hover-dot" aria-hidden="true"></div>
      <div class="chart-tooltip" role="status"><strong data-chart-price>${money(current)}</strong><span data-chart-time>${esc(formatHoverTime(series[series.length-1].time,chartRange))}</span></div>
    </div>
    <div class="stock-chart-footer"><span class="${delta>=0?'positive':'negative'}">${delta>=0?'+':''}${pct.toFixed(2)}% event-driven ${chartRange} history</span><span>High ${money(high)}</span><span>Low ${money(low)}</span></div>`;
  };

  function calendarDateLabel(value){
    const text=String(value||'').trim();
    const parsed=Date.parse(text);
    if(!Number.isFinite(parsed)) return '';
    const options={month:'short',day:'numeric'};
    if(dateOnlyUtc(text)) options.timeZone='UTC';
    return new Date(parsed).toLocaleDateString([],options);
  }

  if(priorProfile){
    profile=function(){
      let output=priorProfile();
      try{
        const record=byId(selectedId);
        const label=calendarDateLabel(record?.lastPriceEventAt);
        if(label) output=output.replace(/Latest supported activity(?: · [^<]*)?/,`Latest supported activity · ${esc(label)}`);
      }catch{}
      return output;
    };
  }

  window.talentxVerifiedEventCharts='presentation-only-v4';
})();
