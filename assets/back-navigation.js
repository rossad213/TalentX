/* TalentX contextual back navigation for category and profile drill-downs. */
(function(){
  const navigationStack=[];

  const style=document.createElement('style');
  style.textContent=`
    .talentx-backbar{display:flex;align-items:center;margin:0 0 16px;min-height:38px}
    .talentx-back-btn{display:inline-flex;align-items:center;gap:8px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.055);color:#dce9f5;border-radius:999px;padding:8px 13px;font:inherit;font-size:.88rem;font-weight:750;line-height:1;cursor:pointer;transition:background .18s ease,border-color .18s ease,transform .18s ease}
    .talentx-back-btn:hover{background:rgba(255,255,255,.10);border-color:rgba(255,255,255,.24);transform:translateX(-1px)}
    .talentx-back-btn:focus-visible{outline:2px solid #67b8ff;outline-offset:2px}
    @media(max-width:700px){.talentx-backbar{margin-bottom:12px}.talentx-back-btn{padding:9px 13px;font-size:.9rem}}
  `;
  document.head.appendChild(style);

  function cloneFilters(){
    return {...filters};
  }

  function snapshot(){
    return {
      route,
      selectedId,
      profileTab,
      tradeMode,
      retirementSelection,
      chartRange,
      filters:cloneFilters(),
      scrollY:window.scrollY||0
    };
  }

  function pushCurrentView(){
    navigationStack.push(snapshot());
    if(navigationStack.length>30) navigationStack.shift();
  }

  function restoreView(view){
    route=view.route;
    selectedId=view.selectedId;
    profileTab=view.profileTab;
    tradeMode=view.tradeMode;
    retirementSelection=view.retirementSelection;
    chartRange=view.chartRange;
    Object.assign(filters,view.filters||{});
    setActiveNav();
    render();
    requestAnimationFrame(()=>window.scrollTo({top:Number(view.scrollY||0),left:0,behavior:'auto'}));
  }

  window.talentXBack=function(){
    const previous=navigationStack.pop();
    if(!previous) return;
    restoreView(previous);
  };

  function updateBackButton(){
    const app=document.querySelector('#app');
    if(!app) return;
    const existing=app.querySelector('.talentx-backbar');
    const shouldShow=navigationStack.length>0&&(route==='market'||route==='profile');
    if(!shouldShow){
      if(existing) existing.remove();
      return;
    }
    if(existing) return;
    const bar=document.createElement('div');
    bar.className='talentx-backbar';
    bar.innerHTML='<button type="button" class="talentx-back-btn" onclick="talentXBack()" aria-label="Go back to the previous TalentX view">← Back</button>';
    app.prepend(bar);
  }

  const baseRender=render;
  render=function(){
    const result=baseRender.apply(this,arguments);
    requestAnimationFrame(updateBackButton);
    return result;
  };

  const baseOpenProfile=openProfile;
  openProfile=function(id){
    pushCurrentView();
    return baseOpenProfile(id);
  };

  const baseSetCategory=setCategory;
  setCategory=function(category){
    pushCurrentView();
    return baseSetCategory(category);
  };

  const baseSetDiscipline=setDiscipline;
  setDiscipline=function(discipline){
    pushCurrentView();
    return baseSetDiscipline(discipline);
  };

  const baseSetSegment=setSegment;
  setSegment=async function(segment){
    if(route!=='market') pushCurrentView();
    return baseSetSegment(segment);
  };

  const baseGo=go;
  go=function(next){
    navigationStack.length=0;
    return baseGo(next);
  };

  updateBackButton();
})();

/* PWA registration. The service worker deliberately bypasses all /data/ requests. */
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(error=>{
      console.warn('TalentX service worker registration failed',error);
    });
  },{once:true});
}
