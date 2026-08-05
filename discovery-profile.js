/* TalentX Phase 2: discovery, mobile profiles, related players, and watchlist UX. */
(function(){
  function norm(value){
    return String(value||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,' ').trim();
  }
  function editDistance(a,b){
    if(a===b) return 0;
    if(!a.length) return b.length;
    if(!b.length) return a.length;
    const row=Array.from({length:b.length+1},(_,i)=>i);
    for(let i=1;i<=a.length;i++){
      let previous=row[0]; row[0]=i;
      for(let j=1;j<=b.length;j++){
        const saved=row[j];
        row[j]=Math.min(row[j]+1,row[j-1]+1,previous+(a[i-1]===b[j-1]?0:1));
        previous=saved;
      }
    }
    return row[b.length];
  }
  function discoveryText(r){
    return norm([r.name,r.ticker,r.teamOrPlatform,r.role,r.discipline,r.leagueOrMedium,r.country,r.careerStatus,r.careerStage,r.primaryCategory,r.searchText].filter(Boolean).join(' '));
  }
  function matchScore(r,query){
    const q=norm(query); if(!q) return 0;
    const text=discoveryText(r), words=text.split(/\s+/), tokens=q.split(/\s+/);
    let score=0;
    for(const token of tokens){
      if(text===token) score+=120;
      else if(norm(r.name)===token||norm(r.ticker)===token) score+=100;
      else if(text.includes(token)) score+=55;
      else if(words.some(word=>word.startsWith(token))) score+=38;
      else if(token.length>=4&&words.some(word=>Math.abs(word.length-token.length)<=1&&editDistance(word,token)<=1)) score+=22;
      else return -1;
    }
    if(norm(r.name).startsWith(q)) score+=45;
    if(norm(r.ticker).startsWith(q)) score+=40;
    if(norm(r.teamOrPlatform).includes(q)) score+=25;
    return score;
  }

  filteredRecords=function(){
    let arr=filters.segment==='Current'?currentRecords:historicalRecords.filter(r=>r.marketSegment===filters.segment);
    if(filters.category!=='All') arr=arr.filter(r=>r.primaryCategory===filters.category);
    if(filters.discipline!=='All') arr=arr.filter(r=>r.discipline===filters.discipline);
    if(filters.league!=='All') arr=arr.filter(r=>r.leagueOrMedium===filters.league);
    if(filters.status!=='All') arr=arr.filter(r=>r.careerStatus===filters.status);
    if(filters.stage!=='All') arr=arr.filter(r=>(r.careerStage||'Stage under review')===filters.stage);
    const q=filters.query.trim();
    if(q){
      arr=arr.map(r=>({r,score:matchScore(r,q)})).filter(item=>item.score>=0).sort((a,b)=>b.score-a.score||b.r.careerScore-a.r.careerScore).map(item=>item.r);
      return arr;
    }
    const sorted=[...arr];
    const sorters={'score-desc':(a,b)=>b.careerScore-a.careerScore,'price-desc':(a,b)=>localPrice(b)-localPrice(a),'change-desc':(a,b)=>displayChange(b)-displayChange(a),'change-asc':(a,b)=>displayChange(a)-displayChange(b),'name':(a,b)=>a.name.localeCompare(b.name)};
    sorted.sort(sorters[filters.sort]||sorters['score-desc']);
    return sorted;
  };

  function relatedPlayers(record){
    const price=localPrice(record);
    return currentRecords.filter(r=>r.id!==record.id&&r.primaryCategory===record.primaryCategory).map(r=>{
      let score=0;
      if(r.leagueOrMedium===record.leagueOrMedium) score+=5;
      if(r.discipline===record.discipline) score+=4;
      if(r.role===record.role) score+=4;
      if(r.careerStage===record.careerStage) score+=3;
      if(r.teamOrPlatform===record.teamOrPlatform) score+=1;
      const gap=Math.abs(localPrice(r)-price)/Math.max(1,price);
      score+=Math.max(0,3-gap*6);
      return {r,score};
    }).sort((a,b)=>b.score-a.score||b.r.careerScore-a.r.careerScore).slice(0,4).map(item=>item.r);
  }
  function relatedHtml(r){
    const items=relatedPlayers(r); if(!items.length) return '';
    return `<section class="related-section"><div class="section-head"><h2>Similar talent</h2><small>Based on league, role, career stage, and price</small></div><div class="related-grid">${items.map(item=>{const change=displayChange(item);return `<button class="related-card" onclick="openProfile('${esc(item.id)}')"><div class="person">${avatar(item)}<div class="person-copy"><strong>${esc(item.name)}</strong><span>${esc(item.role)} · ${esc(item.leagueOrMedium)}</span></div></div><div class="mini-row"><strong>${money(localPrice(item))}</strong><span class="${change>=0?'positive':'negative'}">${change>=0?'+':''}${change.toFixed(2)}%</span></div></button>`;}).join('')}</div></section>`;
  }

  const SEEN_KEY='talentx_seen_price_events_v1';
  function seenEvents(){try{return JSON.parse(localStorage.getItem(SEEN_KEY)||'{}')||{};}catch{return {};}}
  function saveSeen(map){try{localStorage.setItem(SEEN_KEY,JSON.stringify(map));}catch{}}
  function prettyKey(key){return String(key||'').replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase());}
  function topStats(record){return Object.entries(record?.lastGameStats||{}).filter(([key,value])=>key!=='teamWon'&&Number.isFinite(Number(value))).sort((a,b)=>Math.abs(Number(b[1]))-Math.abs(Number(a[1]))).slice(0,4);}
  function eventDate(record){const date=record?.lastPriceEventAt?new Date(record.lastPriceEventAt):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleDateString([],{month:'short',day:'numeric'}):'';}
  function insightHtml(record){
    const stats=topStats(record),move=Number(record.lastGameMovePct??record.dailyChange??0),event=String(record.lastPriceEvent||'').trim();
    return `<section class="profile-insights" aria-label="Player at a glance"><div class="insight-head"><h2>At a glance</h2><small>Current profile and latest supported activity</small></div><div class="insight-grid"><div class="insight-card"><small>Team / platform</small><strong>${esc(record.teamOrPlatform&&record.teamOrPlatform!=='—'?record.teamOrPlatform:record.leagueOrMedium)}</strong></div><div class="insight-card"><small>Role</small><strong>${esc(record.role||'Not listed')}</strong></div><div class="insight-card"><small>Career stage</small><strong>${esc(record.careerStage||'Under review')}</strong></div><div class="insight-card"><small>Latest move</small><strong class="${move>=0?'positive':'negative'}">${Math.abs(move)<.005?'No change':`${move>=0?'+':''}${move.toFixed(2)}%`}</strong></div></div><div class="latest-activity-card"><div><small>Latest supported activity${eventDate(record)?` · ${esc(eventDate(record))}`:''}</small><strong>${esc(event||'No new price-changing event')}</strong></div>${event?`<button type="button" class="link insight-why" onclick="openPriceWhy()">Why?</button>`:''}</div>${stats.length?`<div class="quick-stats">${stats.map(([key,value])=>`<div><small>${esc(prettyKey(key))}</small><strong>${esc(Number(value).toFixed(Number(value)%1?1:0))}</strong></div>`).join('')}</div>`:''}</section>`;
  }

  const baseProfile=profile;
  profile=function(){
    const r=byId(selectedId); let html=baseProfile(); if(!r) return html;
    const watching=state.watchlist.includes(r.id);
    html=html.replace('<div class="badge-row">',`<div class="profile-quick-actions"><button class="btn ghost small" onclick="toggleWatch('${esc(r.id)}')">${watching?'★ Watching':'☆ Watch'}</button></div><div class="badge-row">`);
    html=html.replace('<div class="profile-chart detailed">',`${insightHtml(r)}<div class="profile-chart detailed">`);
    const seen=seenEvents(); if(r.lastPriceEventId){seen[r.id]=r.lastPriceEventId;saveSeen(seen);}
    const insertion=relatedHtml(r);
    html=html.replace('</article>\n  </section>\n  <aside class="card trade-card">',`${insertion}</article>\n  </section>\n  <aside class="card trade-card">`);
    return html;
  };

  let watchSort='move';
  window.setWatchSort=function(value){watchSort=value;render();};
  watchlist=function(){
    let records=state.watchlist.map(byId).filter(Boolean);
    const sorters={move:(a,b)=>Math.abs(displayChange(b))-Math.abs(displayChange(a)),gainers:(a,b)=>displayChange(b)-displayChange(a),price:(a,b)=>localPrice(b)-localPrice(a),name:(a,b)=>a.name.localeCompare(b.name)};
    records=[...records].sort(sorters[watchSort]||sorters.move);
    const seen=seenEvents();
    return `${note()}<div class="eyebrow">Saved talent</div><h1 class="page-title">Watchlist</h1><p class="page-sub">Track recent moves and open a profile to see why a price changed.</p><div class="controls watch-controls"><select class="select" onchange="setWatchSort(this.value)"><option value="move" ${watchSort==='move'?'selected':''}>Biggest recent move</option><option value="gainers" ${watchSort==='gainers'?'selected':''}>Top gainers</option><option value="price" ${watchSort==='price'?'selected':''}>Highest price</option><option value="name" ${watchSort==='name'?'selected':''}>Name A–Z</option></select></div>${records.length?`<div class="grid watch-grid">${records.map(r=>{const change=displayChange(r),hasEvent=Boolean(r.lastPriceEvent),isNew=Boolean(r.lastPriceEventId&&seen[r.id]!==r.lastPriceEventId);return `<article class="card watch improved-watch" onclick="openProfile('${esc(r.id)}')"><div class="person">${avatar(r)}<div class="person-copy"><strong>${esc(r.name)}${isNew?'<span class="watch-new-badge">New</span>':''}</strong><span>${esc(r.teamOrPlatform&&r.teamOrPlatform!=='—'?r.teamOrPlatform:r.leagueOrMedium)} · ${esc(r.role)}</span></div></div><div class="mini-row"><strong>${money(localPrice(r))}</strong><span class="${change>=0?'positive':'negative'}">${change>=0?'+':''}${change.toFixed(2)}%</span></div><div class="watch-event ${hasEvent?'':'muted'}">${hasEvent?`Latest: ${esc(r.lastPriceEvent)}`:'No new price-changing event'}</div></article>`;}).join('')}</div>`:`<div class="card empty">Your watchlist is empty. Use the Watch button near the top of any profile.</div>`}`;
  };
})();
