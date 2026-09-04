/* TalentX brokerage-style talent profile enhancement. */
(function(){
  if(window.__talentxProfileBrokerageV1)return;
  window.__talentxProfileBrokerageV1=true;

  const html=value=>String(value??'').replace(/[&<>"']/g,char=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[char]));
  const num=(value,fallback=0)=>{const parsed=Number(value);return Number.isFinite(parsed)?parsed:fallback;};
  const pct=value=>`${num(value)>=0?'+':''}${num(value).toFixed(2)}%`;
  const moneyText=value=>typeof money==='function'?money(num(value)):new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(num(value));

  // Keep the price chart focused on the five periods users expect on a brokerage profile.
  try{
    if(typeof CHART_RANGE_CONFIG!=='undefined'){
      CHART_RANGE_CONFIG['1W']={duration:7*24*60*60*1000,points:48};
      CHART_RANGE_CONFIG['3M']={duration:91*24*60*60*1000,points:60};
    }
    if(typeof CHART_RANGES!=='undefined'&&Array.isArray(CHART_RANGES)){
      CHART_RANGES.splice(0,CHART_RANGES.length,'1D','1W','1M','3M','1Y');
    }
    if(typeof chartRange!=='undefined'&&!['1D','1W','1M','3M','1Y'].includes(chartRange)) chartRange='1D';
  }catch{}

  if(typeof formatAxisTime==='function'){
    formatAxisTime=function(timestamp,range){
      const date=new Date(Number(timestamp));
      if(range==='1D') return date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
      if(range==='1W') return date.toLocaleDateString([],{weekday:'short'});
      if(range==='1M') return date.toLocaleDateString([],{month:'short',day:'numeric'});
      if(['3M','1Y'].includes(range)) return date.toLocaleDateString([],{month:'short'});
      return date.toLocaleDateString([],{month:'short',year:'numeric'});
    };
  }
  if(typeof formatHoverTime==='function'){
    formatHoverTime=function(timestamp,range){
      const date=new Date(Number(timestamp));
      if(range==='1D') return date.toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'});
      if(range==='1W') return date.toLocaleString([],{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      return date.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
    };
  }

  function clean(value){
    const text=String(value??'').trim();
    return text&&text!=='—'&&text.toLowerCase()!=='unknown'?text:'';
  }

  function actionBar(record){
    const shares=Math.max(0,num(state?.holdings?.[record.id]));
    const positionValue=shares*num(localPrice(record));
    const watching=Array.isArray(state?.watchlist)&&state.watchlist.includes(record.id);
    return `<div class="broker-profile-actions">
      <div class="broker-position-summary">
        <small>Your position</small>
        <strong>${shares?`${shares} share${shares===1?'':'s'} · ${moneyText(positionValue)}`:'No position yet'}</strong>
      </div>
      <div class="broker-action-buttons">
        <button type="button" class="btn ghost" onclick="toggleWatch('${html(record.id)}')">${watching?'★ Watching':'☆ Watch'}</button>
        <button type="button" class="btn ghost broker-sell" ${shares?'': 'disabled'} onclick="openTradeTicket('${html(record.id)}','sell')">Sell</button>
        <button type="button" class="btn primary" onclick="openTradeTicket('${html(record.id)}','buy')">Buy</button>
      </div>
    </div>`;
  }

  function careerFacts(record){
    const facts=[];
    const push=(label,value)=>{const text=clean(value);if(text)facts.push([label,text]);};
    push('Category',record.primaryCategory);
    push(record.primaryCategory==='Athlete'?'Sport':'Specialty',record.discipline);
    push(record.primaryCategory==='Creator'?'Primary medium / platform':'League / medium',record.leagueOrMedium);
    push(record.primaryCategory==='Creator'?'Platform / brand':'Team / platform',record.teamOrPlatform);
    push('Role',record.role);
    push('Country',record.country);
    push('Career status',record.careerStatus);
    push('Career stage',record.careerStage);
    if(num(record.professionalGames)>0) facts.push(['Professional games',Math.round(num(record.professionalGames)).toLocaleString()]);
    if(num(record.draftYear)>0) facts.push(['Draft year',String(Math.round(num(record.draftYear)))]);
    if(num(record.draftPick)>0) facts.push(['Draft pick',`#${Math.round(num(record.draftPick))}`]);
    if(!facts.length)return '';
    return `<section class="broker-career-panel">
      <div class="broker-section-head"><div><small>PROFILE</small><h2>Career & platform</h2></div><span>${html(record.primaryCategory||'Talent')}</span></div>
      <div class="broker-career-grid">${facts.map(([label,value])=>`<div><small>${html(label)}</small><strong>${html(value)}</strong></div>`).join('')}</div>
      ${clean(record.description)?`<p>${html(record.description)}</p>`:''}
    </section>`;
  }

  function activeWeights(record){
    if(String(record.modelType||'').toLowerCase().includes('legacy')){
      return {legacy:.35,audience:.25,postCareer:.20,recognition:.15,liquidity:.05};
    }
    try{return typeof categoryModel==='function'?categoryModel(record.primaryCategory):{};}catch{return {};}
  }

  function activeMetrics(record){
    if(String(record.modelType||'').toLowerCase().includes('legacy')) return record.legacyMetrics||{};
    return record.activeMetrics||{};
  }

  function metricLabel(key){
    const labels={
      performance:'Performance',achievements:'Achievements',consistency:'Consistency',potential:'Potential',availability:'Availability',audience:'Audience',
      legacy:'Career legacy',postCareer:'Post-career activity',recognition:'Recognition',liquidity:'Market liquidity'
    };
    return labels[key]||String(key||'').replace(/([A-Z])/g,' $1').replace(/^./,char=>char.toUpperCase());
  }

  function recentCatalysts(record){
    const source=Array.isArray(record.priceEvents)?record.priceEvents:[];
    const events=source.map((event,index)=>{
      if(!event||typeof event!=='object')return null;
      const stamp=Date.parse(event.startedAt||event.time||event.date||'');
      const direct=Number(event.movePct);
      const before=Number(event.priceBefore),after=Number(event.priceAfter??event.price);
      const move=Number.isFinite(direct)?direct:(before>0&&after>0?((after/before)-1)*100:0);
      return {name:String(event.name||event.eventType||'Market event'),time:Number.isFinite(stamp)?stamp:0,move,index};
    }).filter(Boolean).sort((a,b)=>b.time-a.time||b.index-a.index).slice(0,3);
    if(!events.length&&record.lastPriceEvent){
      events.push({name:String(record.lastPriceEvent),time:Date.parse(record.lastPriceEventAt||'')||0,move:num(record.lastGameMovePct??record.dailyChange)});
    }
    if(!events.length)return '<div class="broker-no-catalyst">No recorded price-changing event yet.</div>';
    return `<div class="broker-catalysts">${events.map(event=>`<div><span><small>${event.time?new Date(event.time).toLocaleDateString([],{month:'short',day:'numeric'}):'Recent'}</small><strong>${html(event.name)}</strong></span><b class="${event.move>=0?'positive':'negative'}">${pct(event.move)}</b></div>`).join('')}</div>`;
  }

  function priceStory(record){
    const current=num(localPrice(record));
    const fundamental=num(record.fundamentalValue,current);
    const confidence=Math.round(num(record.pricingConfidence??record.dataConfidence)*100);
    const adjustment=fundamental>0?((current/fundamental)-1)*100:num(record.demandPremiumPct);
    const latest=num(record.lastGameMovePct??record.dailyChange);
    const weights=activeWeights(record),metrics=activeMetrics(record);
    const drivers=Object.entries(weights).map(([key,weight])=>({
      key,label:metricLabel(key),weight:num(weight),score:num(metrics?.[key],NaN)
    })).filter(item=>Number.isFinite(item.score)).sort((a,b)=>(b.score*b.weight)-(a.score*a.weight)).slice(0,6);
    return `<section class="broker-price-story">
      <div class="broker-section-head"><div><small>VALUATION</small><h2>Why this price?</h2></div><button type="button" class="broker-explain-btn" onclick="openPriceWhy()">Explain latest move</button></div>
      <div class="broker-price-summary">
        <div><small>Current price</small><strong>${moneyText(current)}</strong></div>
        <div><small>Fundamental value</small><strong>${moneyText(fundamental)}</strong></div>
        <div><small>Market adjustment</small><strong class="${adjustment>=0?'positive':'negative'}">${pct(adjustment)}</strong></div>
        <div><small>Price confidence</small><strong>${confidence}%</strong></div>
      </div>
      ${drivers.length?`<div class="broker-driver-list">${drivers.map(driver=>`<div class="broker-driver"><span><b>${html(driver.label)}</b><small>${Math.round(driver.weight*100)}% model weight</small></span><span class="broker-driver-track"><i style="width:${Math.max(2,Math.min(100,driver.score))}%"></i></span><strong>${driver.score.toFixed(0)}</strong></div>`).join('')}</div>`:''}
      <div class="broker-catalyst-head"><div><small>RECENT CATALYSTS</small><strong>Events affecting price</strong></div><span class="${latest>=0?'positive':'negative'}">Latest ${pct(latest)}</span></div>
      ${recentCatalysts(record)}
      <p class="broker-disclosure">TalentX prices are simulated. Fundamentals use evidence-weighted career inputs; supported events can move the market price without changing the underlying career score.</p>
    </section>`;
  }

  const baseProfile=profile;
  profile=function(){
    const record=byId(selectedId);
    let output=baseProfile();
    if(!record)return output;

    const bar=actionBar(record);
    const quickActionPattern=/<div class="profile-quick-actions">[\s\S]*?<\/div><div class="badge-row">/;
    if(quickActionPattern.test(output)) output=output.replace(quickActionPattern,`${bar}<div class="badge-row">`);
    else output=output.replace('<div class="badge-row">',`${bar}<div class="badge-row">`);

    const detailPanels=`<div class="broker-profile-detail-grid">${careerFacts(record)}${priceStory(record)}</div>`;
    output=output.replace('<div class="tab-row">',`${detailPanels}<div class="tab-row">`);

    const signedIn=Boolean(window.__talentxAuthUser?.id);
    output=output.replace('Trades only affect your browser’s simulated market.',signedIn?'Signed-in trades are verified and saved to your TalentX account.':'Guest trades are simulated and stored only in this browser.');
    output=output.replace('player profile','talent profile');
    return output;
  };
})();
