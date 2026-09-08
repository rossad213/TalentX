/* TalentX lightweight search discovery. Reuses currentRecords already loaded by app.js. */
(function(){
  const STORAGE_KEY='talentx_recent_searches_v1';
  const SEARCH_RENDER_DEBOUNCE_MS=140;
  let panel=null;
  let input=null;
  let readyTimer=null;
  let marketRenderTimer=null;
  const searchCache=new WeakMap();

  const escSearch=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#039;'}[m]));
  const readRecents=()=>{
    try{
      const value=JSON.parse(localStorage.getItem(STORAGE_KEY)||'[]');
      return Array.isArray(value)?value.slice(0,6):[];
    }catch{return [];}
  };
  const writeRecents=value=>{
    try{localStorage.setItem(STORAGE_KEY,JSON.stringify(value.slice(0,6)));}catch{}
  };
  function remember(item){
    if(!item||!item.label)return;
    const next=[item,...readRecents().filter(x=>(item.id&&x.id!==item.id)||(!item.id&&x.label!==item.label))];
    writeRecents(next);
  }
  function currentList(){
    try{return Array.isArray(currentRecords)?currentRecords:[];}catch{return [];}
  }
  function priceText(r){
    try{return money(localPrice(r));}catch{return money(Number(r.marketPrice||0));}
  }
  function changeValue(r){
    try{return Number(displayChange(r)||0);}catch{return Number(r.dailyChange||0);}
  }
  function searchable(r){
    const cached=searchCache.get(r);
    if(cached)return cached;
    const name=String(r.name||'').toLowerCase();
    const ticker=String(r.ticker||'').toLowerCase();
    const search=String(r.searchText||[r.name,r.ticker,r.team,r.leagueOrMedium,r.discipline,r.role].filter(Boolean).join(' ')).toLowerCase();
    const value={name,ticker,search,nameParts:name.split(/\s+/)};
    searchCache.set(r,value);
    return value;
  }
  function rankMatches(query){
    const q=query.trim().toLowerCase();
    if(!q)return [];
    const best=[];
    for(const r of currentList()){
      const fields=searchable(r);
      let score=0;
      if(fields.name===q||fields.ticker===q)score=100;
      else if(fields.name.startsWith(q)||fields.ticker.startsWith(q))score=80;
      else if(fields.nameParts.some(part=>part.startsWith(q)))score=65;
      else if(fields.search.includes(q))score=40;
      if(!score)continue;
      const item={r,score};
      let index=best.findIndex(existing=>score>existing.score||(score===existing.score&&Number(r.careerScore||0)>Number(existing.r.careerScore||0)));
      if(index<0)index=best.length;
      best.splice(index,0,item);
      if(best.length>6)best.pop();
    }
    return best.map(item=>item.r);
  }
  function discoveryPicks(){
    return [...currentList()].sort((a,b)=>Number(b.careerScore||0)-Number(a.careerScore||0)).slice(0,5);
  }
  function suggestionHtml(r){
    const change=changeValue(r);
    const meta=[r.discipline,r.leagueOrMedium||r.team,r.role].filter(Boolean).slice(0,2).join(' · ');
    return `<button class="search-suggestion" type="button" data-search-id="${escSearch(r.id)}"><span class="search-suggestion-avatar">${escSearch(r.avatar||r.ticker||'TX')}</span><span class="search-suggestion-copy"><strong>${escSearch(r.name)}${r.ticker?` <span class="ticker">${escSearch(r.ticker)}</span>`:''}</strong><span>${escSearch(meta||r.primaryCategory||'TalentX listing')}</span></span><span class="search-suggestion-price"><strong>${escSearch(priceText(r))}</strong><span class="${change>=0?'positive':'negative'}">${change>=0?'+':''}${change.toFixed(2)}%</span></span></button>`;
  }
  function cancelMarketRender(){
    if(marketRenderTimer){clearTimeout(marketRenderTimer);marketRenderTimer=null;}
  }
  function applyMarketSearch(){
    marketRenderTimer=null;
    if(route!=='market'){
      route='market';
      filters.segment='Current';
      setActiveNav();
    }
    render();
  }
  function scheduleMarketSearch(value){
    filters.query=String(value??'');
    filters.page=1;
    cancelMarketRender();
    marketRenderTimer=setTimeout(applyMarketSearch,SEARCH_RENDER_DEBOUNCE_MS);
  }
  function ensurePanel(){
    if(panel&&panel.isConnected)return panel;
    panel=document.createElement('div');
    panel.className='talentx-search-panel';
    panel.setAttribute('role','listbox');
    document.querySelector('.global-search')?.appendChild(panel);
    panel.addEventListener('pointerdown',e=>e.preventDefault());
    panel.addEventListener('click',e=>{
      const result=e.target.closest('[data-search-id]');
      if(result){
        const r=currentList().find(x=>String(x.id)===String(result.dataset.searchId));
        if(r){remember({id:r.id,label:r.name});cancelMarketRender();closePanel();input.value='';filters.query='';openProfile(r.id);}
        return;
      }
      const recent=e.target.closest('[data-recent-index]');
      if(recent){
        const item=readRecents()[Number(recent.dataset.recentIndex)];
        if(!item)return;
        if(item.id&&currentList().some(r=>String(r.id)===String(item.id))){cancelMarketRender();closePanel();input.value='';filters.query='';openProfile(item.id);}
        else{input.value=item.label||'';input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}
      }
      if(e.target.closest('[data-clear-recents]')){writeRecents([]);renderPanel(input.value);}
    });
    return panel;
  }
  function openPanel(){ensurePanel()?.classList.add('open');}
  function closePanel(){panel?.classList.remove('open');}
  function renderPanel(value=''){
    const el=ensurePanel();if(!el)return;
    const q=String(value||'').trim();
    if(q){
      const matches=rankMatches(q);
      el.innerHTML=`<div class="search-panel-head"><strong>${matches.length?'Top matches':'Search TalentX'}</strong><span></span></div><div class="search-suggestions">${matches.length?matches.map(suggestionHtml).join(''):`<div class="search-empty">No direct profile matches yet. The Market below is still filtering for “${escSearch(q)}”.</div>`}</div>`;
      openPanel();return;
    }
    const recents=readRecents();
    const picks=discoveryPicks();
    el.innerHTML=`${recents.length?`<div class="search-panel-head"><strong>Recent</strong><button class="search-panel-clear" type="button" data-clear-recents>Clear</button></div><div class="search-recents">${recents.map((x,i)=>`<button class="search-recent" type="button" data-recent-index="${i}">↻ ${escSearch(x.label)}</button>`).join('')}</div>`:''}<div class="search-panel-head"><strong>Discover</strong><span></span></div><div class="search-suggestions">${picks.map(suggestionHtml).join('')}</div>`;
    openPanel();
  }
  function attach(){
    const candidate=document.querySelector('#globalSearch');
    if(!candidate){readyTimer=setTimeout(attach,150);return;}
    input=candidate;
    input.setAttribute('autocomplete','off');
    input.setAttribute('aria-haspopup','listbox');
    input.addEventListener('focus',()=>renderPanel(input.value));
    // app.js has a legacy input handler that redraws the full Market on every
    // keystroke. Capture this event first so suggestions remain immediate while
    // the expensive Market redraw happens only after the user pauses typing.
    input.addEventListener('input',event=>{
      event.stopImmediatePropagation();
      scheduleMarketSearch(input.value);
      renderPanel(input.value);
    },true);
    input.addEventListener('keydown',e=>{
      if(e.key==='Escape'){closePanel();input.blur();}
      if(e.key==='Enter'&&input.value.trim()){
        remember({label:input.value.trim()});
        closePanel();
        cancelMarketRender();
        applyMarketSearch();
      }
    });
    document.addEventListener('pointerdown',e=>{if(!e.target.closest('.global-search'))closePanel();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',attach,{once:true});else attach();
  window.addEventListener('beforeunload',()=>{if(readyTimer)clearTimeout(readyTimer);cancelMarketRender();});
})();
