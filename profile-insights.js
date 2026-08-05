/* TalentX Phase 2: at-a-glance profile insights and watchlist activity indicators. */
(function(){
  const SEEN_KEY='talentx_seen_price_events_v1';
  function seenEvents(){
    try{return JSON.parse(localStorage.getItem(SEEN_KEY)||'{}')||{};}catch{return {};}
  }
  function saveSeen(map){try{localStorage.setItem(SEEN_KEY,JSON.stringify(map));}catch{}}
  function prettyKey(key){return String(key||'').replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase());}
  function topStats(record){
    return Object.entries(record?.lastGameStats||{})
      .filter(([key,value])=>key!=='teamWon'&&Number.isFinite(Number(value)))
      .sort((a,b)=>Math.abs(Number(b[1]))-Math.abs(Number(a[1])))
      .slice(0,4);
  }
  function eventDate(record){
    const date=record?.lastPriceEventAt?new Date(record.lastPriceEventAt):null;
    return date&&!Number.isNaN(date.getTime())?date.toLocaleDateString([],{month:'short',day:'numeric'}):'';
  }
  function insightHtml(record){
    const stats=topStats(record);
    const move=Number(record.lastGameMovePct??record.dailyChange??0);
    const event=String(record.lastPriceEvent||'').trim();
    return `<section class="profile-insights" aria-label="Player at a glance">
      <div class="insight-head"><h2>At a glance</h2><small>Current profile and latest supported activity</small></div>
      <div class="insight-grid">
        <div class="insight-card"><small>Team / platform</small><strong>${esc(record.teamOrPlatform&&record.teamOrPlatform!=='—'?record.teamOrPlatform:record.leagueOrMedium)}</strong></div>
        <div class="insight-card"><small>Role</small><strong>${esc(record.role||'Not listed')}</strong></div>
        <div class="insight-card"><small>Career stage</small><strong>${esc(record.careerStage||'Under review')}</strong></div>
        <div class="insight-card"><small>Latest move</small><strong class="${move>=0?'positive':'negative'}">${Math.abs(move)<.005?'No change':`${move>=0?'+':''}${move.toFixed(2)}%`}</strong></div>
      </div>
      <div class="latest-activity-card">
        <div><small>Latest supported activity${eventDate(record)?` · ${esc(eventDate(record))}`:''}</small><strong>${esc(event||'No new price-changing event')}</strong></div>
        ${event?`<button type="button" class="link insight-why" onclick="openPriceWhy()">Why?</button>`:''}
      </div>
      ${stats.length?`<div class="quick-stats">${stats.map(([key,value])=>`<div><small>${esc(prettyKey(key))}</small><strong>${esc(Number(value).toFixed(Number(value)%1?1:0))}</strong></div>`).join('')}</div>`:''}
    </section>`;
  }

  const priorProfile=profile;
  profile=function(){
    const record=byId(selectedId);
    let html=priorProfile();
    if(!record) return html;
    const seen=seenEvents();
    if(record.lastPriceEventId){seen[record.id]=record.lastPriceEventId;saveSeen(seen);}
    const insert=insightHtml(record);
    return html.replace('<div class="profile-chart detailed">',`${insert}<div class="profile-chart detailed">`);
  };

  const priorWatchlist=watchlist;
  watchlist=function(){
    let html=priorWatchlist();
    const seen=seenEvents();
    for(const record of state.watchlist.map(byId).filter(Boolean)){
      const isNew=Boolean(record.lastPriceEventId&&seen[record.id]!==record.lastPriceEventId);
      if(!isNew) continue;
      const needle=`<strong>${esc(record.name)}</strong>`;
      html=html.replace(needle,`${needle}<span class="watch-new-badge">New</span>`);
    }
    return html;
  };
})();
