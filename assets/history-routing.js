/* TalentX URL routing and browser history integration. */
(function(){
  const ROUTES=new Set(['dashboard','market','profile','portfolio','leaderboard','watchlist','rules']);
  const DEFAULT_FILTERS={segment:'Current',category:'All',discipline:'All',league:'All',status:'All',stage:'All',sort:'score-desc',query:'',page:1};
  let applyingLocation=false;
  let initialHydrationDone=false;

  function cleanText(value,fallback=''){
    const text=String(value??'').trim();
    return text||fallback;
  }

  function readLocation(){
    const params=new URLSearchParams(window.location.search);
    const nextRoute=ROUTES.has(params.get('view'))?params.get('view'):'dashboard';
    const nextFilters={...DEFAULT_FILTERS};
    if(params.has('segment')) nextFilters.segment=cleanText(params.get('segment'),'Current');
    if(params.has('category')) nextFilters.category=cleanText(params.get('category'),'All');
    if(params.has('discipline')) nextFilters.discipline=cleanText(params.get('discipline'),'All');
    if(params.has('league')) nextFilters.league=cleanText(params.get('league'),'All');
    if(params.has('status')) nextFilters.status=cleanText(params.get('status'),'All');
    if(params.has('stage')) nextFilters.stage=cleanText(params.get('stage'),'All');
    if(params.has('sort')) nextFilters.sort=cleanText(params.get('sort'),'score-desc');
    if(params.has('q')) nextFilters.query=params.get('q')||'';
    if(params.has('page')) nextFilters.page=Math.max(1,Number.parseInt(params.get('page'),10)||1);
    return {
      route:nextRoute,
      selectedId:nextRoute==='profile'?cleanText(params.get('id')):null,
      profileTab:cleanText(params.get('tab'),'overview'),
      chartRange:cleanText(params.get('range'),'1D'),
      filters:nextFilters
    };
  }

  function applyState(state){
    route=state.route;
    selectedId=state.selectedId;
    profileTab=state.profileTab||'overview';
    chartRange=typeof CHART_RANGE_CONFIG!=='undefined'&&CHART_RANGE_CONFIG[state.chartRange]?state.chartRange:'1D';
    Object.assign(filters,DEFAULT_FILTERS,state.filters||{});
    filters.page=Math.max(1,Number.parseInt(filters.page,10)||1);
  }

  function buildUrl(){
    const url=new URL(window.location.href);
    url.search='';
    url.hash='';
    const params=new URLSearchParams();
    if(route!=='dashboard') params.set('view',route);
    if(route==='profile'&&selectedId){
      params.set('id',selectedId);
      if(profileTab&&profileTab!=='overview') params.set('tab',profileTab);
      if(chartRange&&chartRange!=='1D') params.set('range',chartRange);
    }
    if(route==='market'){
      if(filters.segment!==DEFAULT_FILTERS.segment) params.set('segment',filters.segment);
      if(filters.category!==DEFAULT_FILTERS.category) params.set('category',filters.category);
      if(filters.discipline!==DEFAULT_FILTERS.discipline) params.set('discipline',filters.discipline);
      if(filters.league!==DEFAULT_FILTERS.league) params.set('league',filters.league);
      if(filters.status!==DEFAULT_FILTERS.status) params.set('status',filters.status);
      if(filters.stage!==DEFAULT_FILTERS.stage) params.set('stage',filters.stage);
      if(filters.sort!==DEFAULT_FILTERS.sort) params.set('sort',filters.sort);
      if(filters.query) params.set('q',filters.query);
      if(Number(filters.page)>1) params.set('page',String(filters.page));
    }
    const query=params.toString();
    url.search=query?`?${query}`:'';
    return `${url.pathname}${url.search}`;
  }

  function currentDepth(){
    return Number(history.state?.talentxDepth||0);
  }

  function saveCurrentScroll(){
    const state={...(history.state||{}),talentx:true,talentxDepth:currentDepth(),scrollY:window.scrollY||0};
    history.replaceState(state,'',window.location.href);
  }

  function commitUrl(mode='push',currentScrollSaved=false){
    if(applyingLocation) return;
    const target=buildUrl();
    const current=`${window.location.pathname}${window.location.search}`;
    if(target===current){
      history.replaceState({...(history.state||{}),talentx:true,talentxDepth:currentDepth(),scrollY:window.scrollY||0},'',target);
      return;
    }
    if(mode==='push'){
      if(!currentScrollSaved) saveCurrentScroll();
      history.pushState({talentx:true,talentxDepth:currentDepth()+1,scrollY:0},'',target);
    }else{
      history.replaceState({...(history.state||{}),talentx:true,talentxDepth:currentDepth(),scrollY:window.scrollY||0},'',target);
    }
    updateDocumentTitle();
    ensureBackButton();
  }

  async function ensureDataForState(state){
    if(typeof ensureHistorical!=='function') return;
    if(state.route==='market'&&state.filters?.segment&&state.filters.segment!=='Current'&&!historicalLoaded){
      await ensureHistorical();
      return;
    }
    if(state.route==='profile'&&state.selectedId&&typeof byId==='function'&&!byId(state.selectedId)&&!historicalLoaded){
      await ensureHistorical();
    }
  }

  async function applyLocation({renderView=true,restoreScroll=true}={}){
    applyingLocation=true;
    const state=readLocation();
    applyState(state);
    try{
      await ensureDataForState(state);
    }catch(err){
      console.warn('TalentX could not load historical data for this URL.',err);
    }
    if(renderView&&typeof render==='function') render();
    const input=document.getElementById('globalSearch');
    if(input&&input.value!==filters.query) input.value=filters.query;
    updateDocumentTitle();
    setTimeout(ensureBackButton,0);
    if(restoreScroll){
      const y=Number(history.state?.scrollY||0);
      requestAnimationFrame(()=>window.scrollTo({top:y,left:0,behavior:'auto'}));
    }
    applyingLocation=false;
  }

  function updateDocumentTitle(){
    let title='TalentX — The Market for Talent';
    if(route==='profile'&&selectedId&&typeof byId==='function'){
      const record=byId(selectedId);
      if(record?.name) title=`${record.name} — TalentX`;
    }else if(route==='market'){
      const focus=filters.discipline!=='All'?filters.discipline:(filters.category!=='All'?(filters.category==='Music'?'Music':`${filters.category}s`):'Market');
      title=`${focus} — TalentX`;
    }else if(route!=='dashboard'){
      const labels={portfolio:'Portfolio',leaderboard:'Leaderboard',watchlist:'Watchlist',rules:'Data & Rules'};
      title=`${labels[route]||'TalentX'} — TalentX`;
    }
    document.title=title;
  }

  function ensureBackButton(){
    const app=document.getElementById('app');
    if(!app) return;
    const shouldShow=currentDepth()>0&&(route==='market'||route==='profile');
    let bar=app.querySelector('.talentx-backbar');
    if(!shouldShow){
      if(bar) bar.remove();
      return;
    }
    if(!bar){
      bar=document.createElement('div');
      bar.className='talentx-backbar';
      bar.innerHTML='<button type="button" class="talentx-back-btn" aria-label="Go back to the previous TalentX view">← Back</button>';
      app.prepend(bar);
    }
    const button=bar.querySelector('.talentx-back-btn');
    if(button) button.onclick=()=>window.history.back();
  }

  if('scrollRestoration' in history) history.scrollRestoration='manual';

  // Read the URL immediately so the first async app render lands on the requested view.
  const initialState=readLocation();
  applyState(initialState);
  history.replaceState({...(history.state||{}),talentx:true,talentxDepth:Number(history.state?.talentxDepth||0),scrollY:Number(history.state?.scrollY||0)},'',window.location.href);

  const baseRender=render;
  render=function(){
    const result=baseRender.apply(this,arguments);
    requestAnimationFrame(()=>{
      updateDocumentTitle();
      ensureBackButton();
    });
    return result;
  };

  function wrapSync(name,mode='push'){
    const base=window[name];
    if(typeof base!=='function') return;
    window[name]=function(){
      const saveBefore=mode==='push';
      if(saveBefore) saveCurrentScroll();
      const result=base.apply(this,arguments);
      commitUrl(mode,saveBefore);
      return result;
    };
  }

  function wrapAsync(name,mode='push'){
    const base=window[name];
    if(typeof base!=='function') return;
    window[name]=async function(){
      const saveBefore=mode==='push';
      if(saveBefore) saveCurrentScroll();
      const result=await base.apply(this,arguments);
      commitUrl(mode,saveBefore);
      return result;
    };
  }

  wrapSync('go','push');
  wrapSync('openProfile','push');
  wrapSync('setCategory','push');
  wrapSync('setDiscipline','push');
  wrapAsync('setSegment','push');
  wrapSync('setFilter','push');
  wrapSync('changePage','push');
  wrapSync('setProfileTab','replace');
  wrapSync('setChartRange','replace');

  // Replace the old custom-stack behavior with actual browser history.
  window.talentXBack=function(){
    if(currentDepth()>0) history.back();
  };

  const search=document.getElementById('globalSearch');
  if(search){
    search.addEventListener('input',()=>{
      const startedOutsideMarket=route!=='market';
      if(startedOutsideMarket) saveCurrentScroll();
      setTimeout(()=>commitUrl(startedOutsideMarket?'push':'replace',startedOutsideMarket),0);
    },true);
  }

  window.addEventListener('popstate',()=>{
    applyLocation({renderView:true,restoreScroll:true});
  });

  // The app loads its catalog asynchronously. Once it is ready, resolve Legacy/direct profile URLs.
  const hydrateTimer=setInterval(async()=>{
    if(initialHydrationDone||!Array.isArray(currentRecords)||!currentRecords.length) return;
    initialHydrationDone=true;
    clearInterval(hydrateTimer);
    await applyLocation({renderView:true,restoreScroll:false});
    commitUrl('replace');
  },50);
  setTimeout(()=>clearInterval(hydrateTimer),15000);
})();
