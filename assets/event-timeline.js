/* TalentX profile event timeline.
 * Renders only durable saved priceEvents and never changes pricing.
 */
(function(){
  const SVG_NS='http://www.w3.org/2000/svg';
  const MAX_COLLAPSED=8;
  let expanded=false;

  const html=value=>String(value??'').replace(/[&<>"']/g,char=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[char]));
  const money=value=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(value||0));
  const number=value=>{const parsed=Number(value);return Number.isFinite(parsed)?parsed:null;};
  const time=value=>{const parsed=Date.parse(String(value||''));return Number.isFinite(parsed)?parsed:null;};

  function selectedRecord(){
    try{
      return typeof byId==='function'&&typeof selectedId!=='undefined'&&selectedId?byId(selectedId):null;
    }catch{return null;}
  }

  function eventKey(event,index){
    return String(event?.eventKey||event?.eventId||`${event?.startedAt||'event'}-${index}`);
  }

  function eventMove(event){
    const direct=number(event?.movePct);
    if(direct!==null)return direct;
    const before=number(event?.priceBefore),after=number(event?.priceAfter);
    return before&&after?((after/before)-1)*100:0;
  }

  function typeLabel(type){
    const labels={
      game:'Game',
      'music-release':'Music release',
      'music-chart-outcome':'Chart performance',
      'actor-release':'Screen release',
      'actor-upcoming-project':'Upcoming project',
      'actor-box-office-outcome':'Box office',
      'audience-attention-outcome':'Audience attention',
      award:'Award',
      nomination:'Nomination'
    };
    return labels[String(type||'').toLowerCase()]||String(type||'Market event').replace(/-/g,' ').replace(/^./,c=>c.toUpperCase());
  }

  function formatDate(value){
    const parsed=time(value);
    if(parsed===null)return 'Date unavailable';
    return new Date(parsed).toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
  }

  function normalizedEvents(record){
    const source=Array.isArray(record?.priceEvents)?record.priceEvents:[];
    return source.map((event,index)=>{
      if(!event||typeof event!=='object')return null;
      const started=time(event.startedAt??event.time??event.date);
      const before=number(event.priceBefore);
      const after=number(event.priceAfter??event.price);
      if(started===null||after===null)return null;
      return {...event,_key:eventKey(event,index),_time:started,_before:before,_after:after,_move:eventMove(event)};
    }).filter(Boolean).sort((a,b)=>b._time-a._time);
  }

  function row(event){
    const up=event._move>0,down=event._move<0;
    const moveClass=up?'positive':down?'negative':'neutral';
    const moveText=`${up?'+':''}${event._move.toFixed(2)}%`;
    const beforeAfter=event._before!==null?`${money(event._before)} → ${money(event._after)}`:money(event._after);
    return `<button class="tx-event-row" type="button" data-event-key="${html(event._key)}">
      <span class="tx-event-rail"><span class="tx-event-dot ${moveClass}"></span></span>
      <span class="tx-event-main">
        <span class="tx-event-date">${html(formatDate(event.startedAt))} · ${html(typeLabel(event.eventType))}</span>
        <strong>${html(event.name||typeLabel(event.eventType))}</strong>
        <small>${html(beforeAfter)}</small>
      </span>
      <span class="tx-event-move ${moveClass}">${html(moveText)}</span>
    </button>`;
  }

  function renderTimeline(){
    const record=selectedRecord();
    const chart=document.querySelector('.profile-chart.detailed');
    if(!record||!chart){document.querySelector('#talentxEventTimeline')?.remove();return;}
    const events=normalizedEvents(record);
    let timeline=document.querySelector('#talentxEventTimeline');
    if(!timeline){
      timeline=document.createElement('section');
      timeline.id='talentxEventTimeline';
      timeline.className='tx-event-timeline';
      chart.insertAdjacentElement('afterend',timeline);
    }
    const visible=expanded?events:events.slice(0,MAX_COLLAPSED);
    timeline.innerHTML=`<div class="tx-event-head">
      <div><small>PRICE HISTORY</small><h3>Recent market events</h3><p>Every item below is a saved price-changing event from TalentX data.</p></div>
      <span>${events.length} event${events.length===1?'':'s'}</span>
    </div>
    <div class="tx-event-list">${visible.length?visible.map(row).join(''):`<div class="tx-event-empty">No verified price-changing events have been recorded for this profile yet.</div>`}</div>
    ${events.length>MAX_COLLAPSED?`<button class="tx-event-more" type="button">${expanded?'Show recent only':`Show all ${events.length} events`}</button>`:''}`;

    timeline.querySelectorAll('.tx-event-row').forEach(button=>button.addEventListener('click',()=>{
      const event=events.find(item=>item._key===button.dataset.eventKey);
      if(event)openDetail(event);
    }));
    timeline.querySelector('.tx-event-more')?.addEventListener('click',()=>{expanded=!expanded;renderTimeline();});
    addChartMarkers(events);
  }

  function detailFacts(event){
    const facts=[];
    const keys=[
      ['chartRank','Chart rank'],['boxOfficeToCostRatio','Box office / cost'],['attentionRatio','Attention ratio'],
      ['performanceDeltaPct','Performance vs expectation'],['productionDeltaPct','Production delta'],['efficiencyDeltaPct','Efficiency delta']
    ];
    for(const [key,label] of keys){
      const value=event[key];
      if(value===undefined||value===null||value==='')continue;
      let display=String(value);
      if(key==='chartRank')display=`#${value}`;
      if(key==='boxOfficeToCostRatio'||key==='attentionRatio')display=`${Number(value).toFixed(2)}×`;
      if(key.endsWith('Pct'))display=`${Number(value)>=0?'+':''}${Number(value).toFixed(1)}%`;
      facts.push([label,display]);
    }
    if(event.stats&&typeof event.stats==='object'){
      Object.entries(event.stats).filter(([,value])=>['string','number','boolean'].includes(typeof value)).slice(0,8).forEach(([key,value])=>{
        facts.push([key.replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase()),String(value)]);
      });
    }
    return facts;
  }

  function openDetail(event){
    document.querySelector('.tx-event-overlay')?.remove();
    const overlay=document.createElement('div');
    overlay.className='tx-event-overlay';
    const facts=detailFacts(event);
    const sources=[event.sourceUrl,event.secondarySourceUrl].filter(Boolean);
    overlay.innerHTML=`<section class="tx-event-sheet" role="dialog" aria-modal="true" aria-label="Market event details">
      <div class="tx-event-sheet-head"><div><small>${html(formatDate(event.startedAt))} · ${html(typeLabel(event.eventType))}</small><h2>${html(event.name||typeLabel(event.eventType))}</h2></div><button type="button" class="tx-event-close" aria-label="Close">×</button></div>
      <div class="tx-event-price-change">
        <div><small>Before</small><strong>${event._before!==null?money(event._before):'—'}</strong></div>
        <div class="tx-event-arrow">→</div>
        <div><small>After</small><strong>${money(event._after)}</strong></div>
        <div class="tx-event-detail-move ${event._move>=0?'positive':'negative'}">${event._move>=0?'+':''}${event._move.toFixed(2)}%</div>
      </div>
      ${event.provider?`<div class="tx-event-provider"><small>Verified by</small><strong>${html(event.provider)}</strong></div>`:''}
      ${event.scheduledFor?`<div class="tx-event-provider"><small>Scheduled for</small><strong>${html(formatDate(event.scheduledFor))}</strong></div>`:''}
      ${facts.length?`<div class="tx-event-facts">${facts.map(([label,value])=>`<div><small>${html(label)}</small><strong>${html(value)}</strong></div>`).join('')}</div>`:''}
      ${sources.length?`<div class="tx-event-sources"><small>Supporting evidence</small>${sources.map((url,index)=>`<a href="${html(url)}" target="_blank" rel="noopener">Open ${index?'secondary':'primary'} source ↗</a>`).join('')}</div>`:''}
    </section>`;
    overlay.addEventListener('click',eventClick=>{if(eventClick.target===overlay)overlay.remove();});
    overlay.querySelector('.tx-event-close').addEventListener('click',()=>overlay.remove());
    document.body.appendChild(overlay);
  }

  function addChartMarkers(events){
    const wrap=document.querySelector('.stock-chart-wrap');
    const svg=wrap?.querySelector('svg.stock-chart');
    if(!wrap||!svg)return;
    svg.querySelector('.tx-chart-event-markers')?.remove();
    const times=String(wrap.dataset.times||'').split(',').map(Number).filter(Number.isFinite);
    const floor=number(wrap.dataset.floor),ceil=number(wrap.dataset.ceil);
    if(times.length<2||floor===null||ceil===null||ceil<=floor)return;
    const start=times[0],end=times[times.length-1];
    const inRange=events.filter(event=>event._time>=start&&event._time<=end);
    if(!inRange.length)return;
    const group=document.createElementNS(SVG_NS,'g');
    group.setAttribute('class','tx-chart-event-markers');
    const W=1000,H=340,padL=18,padR=108,padT=18,padB=38;
    const usableW=W-padL-padR,usableH=H-padT-padB;
    for(const event of inRange){
      const ratio=(event._time-start)/Math.max(1,end-start);
      const x=padL+Math.max(0,Math.min(1,ratio))*usableW;
      const y=padT+((ceil-event._after)/(ceil-floor))*usableH;
      if(!Number.isFinite(y))continue;
      const circle=document.createElementNS(SVG_NS,'circle');
      circle.setAttribute('cx',x.toFixed(2));circle.setAttribute('cy',Math.max(padT,Math.min(H-padB,y)).toFixed(2));circle.setAttribute('r','7');
      circle.setAttribute('class',`tx-chart-event-dot ${event._move>=0?'positive':'negative'}`);
      circle.setAttribute('tabindex','0');circle.setAttribute('role','button');circle.setAttribute('aria-label',`${event.name||typeLabel(event.eventType)} ${event._move>=0?'+':''}${event._move.toFixed(2)} percent`);
      const title=document.createElementNS(SVG_NS,'title');title.textContent=`${formatDate(event.startedAt)} · ${event.name||typeLabel(event.eventType)} · ${event._move>=0?'+':''}${event._move.toFixed(2)}%`;
      circle.appendChild(title);
      const activate=eventClick=>{eventClick.stopPropagation();openDetail(event);};
      circle.addEventListener('click',activate);circle.addEventListener('keydown',eventKey=>{if(eventKey.key==='Enter'||eventKey.key===' '){eventKey.preventDefault();activate(eventKey);}});
      group.appendChild(circle);
    }
    svg.appendChild(group);
  }

  document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelector('.tx-event-overlay')?.remove();});
  const app=document.getElementById('app');
  if(app)new MutationObserver(()=>requestAnimationFrame(renderTimeline)).observe(app,{childList:true,subtree:true});
  document.addEventListener('DOMContentLoaded',renderTimeline);
  requestAnimationFrame(renderTimeline);
  window.renderTalentxEventTimeline=renderTimeline;
})();
