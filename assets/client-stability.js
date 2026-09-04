/* TalentX client stability + account bootstrap. */
(() => {
  const CATALOG_PRICING_REVISION='20260831-1';
  const STORAGE_KEY='talentx_v2_state';

  function migrateLocalPriceState(){
    try{
      if(typeof state!=='object'||!state) return false;
      if(String(state.catalogPricingRevision||'')===CATALOG_PRICING_REVISION) return false;
      state.prices={};
      state.catalogPricingRevision=CATALOG_PRICING_REVISION;
      localStorage.setItem(STORAGE_KEY,JSON.stringify(state));
      return true;
    }catch(error){
      console.warn('TalentX client pricing-state migration skipped',error);
      return false;
    }
  }

  function normalizedIdentityName(value){
    return String(value||'')
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g,'')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g,'');
  }

  function applyCanonicalListingGuards(){
    try{
      if(typeof currentRecords==='undefined'||!Array.isArray(currentRecords)||!currentRecords.length) return false;

      // Final safety net for stale published overlays. The authoritative curated
      // Rosalía listing is ROIA; an older Music overlay can still contain ROSA.
      const rules=[
        {category:'Music',name:'rosalia',ticker:'ROIA',id:'cur-rosal-a'}
      ];
      let changed=false;

      for(const rule of rules){
        const matches=currentRecords.filter(record=>
          String(record?.primaryCategory||'')===rule.category&&
          normalizedIdentityName(record?.name)===rule.name
        );
        if(matches.length<2) continue;

        const canonical=matches.find(record=>String(record?.id||'')===rule.id)
          ||matches.find(record=>String(record?.ticker||'').toUpperCase()===rule.ticker)
          ||matches[0];
        const staleIds=new Set(matches.filter(record=>record!==canonical).map(record=>String(record?.id||'')).filter(Boolean));
        if(!staleIds.size) continue;

        currentRecords=currentRecords.filter(record=>!staleIds.has(String(record?.id||'')));
        if(typeof selectedId!=='undefined'&&staleIds.has(String(selectedId||''))) selectedId=canonical.id;

        // Preserve watchlist intent without altering holdings or account balances.
        if(typeof state==='object'&&state&&Array.isArray(state.watchlist)){
          const hadStale=state.watchlist.some(id=>staleIds.has(String(id||'')));
          if(hadStale){
            state.watchlist=[...new Set(state.watchlist.map(id=>staleIds.has(String(id||''))?canonical.id:id))];
            try{localStorage.setItem(STORAGE_KEY,JSON.stringify(state));}catch{}
          }
        }
        changed=true;
      }

      if(changed&&typeof render==='function'){
        try{render();}catch{}
      }
      return changed;
    }catch(error){
      console.warn('TalentX canonical listing guard skipped',error);
      return false;
    }
  }

  function startCanonicalListingGuard(){
    let attempts=0;
    const timer=setInterval(()=>{
      attempts+=1;
      const loaded=typeof currentRecords!=='undefined'&&Array.isArray(currentRecords)&&currentRecords.length>0;
      if(loaded){
        applyCanonicalListingGuards();
        clearInterval(timer);
      }else if(attempts>=60){
        clearInterval(timer);
      }
    },200);
  }

  function syncMobileNav(){
    try{
      document.querySelectorAll('.mobile-bottom-nav button[data-route]').forEach(button=>{
        button.classList.toggle('active',button.dataset.route===route);
      });
      const more=document.querySelector('.mobile-bottom-nav .mobile-more');
      if(more) more.classList.toggle('active',['leaderboard','store','rules'].includes(route));
    }catch{}
  }

  function loadScript(src){
    return new Promise((resolve,reject)=>{
      const existing=[...document.scripts].find(script=>script.src&&script.src.includes(src.split('?')[0]));
      if(existing){
        if(existing.dataset.talentxLoaded==='1') return resolve();
        existing.addEventListener('load',resolve,{once:true});
        existing.addEventListener('error',reject,{once:true});
        return;
      }
      const script=document.createElement('script');
      script.src=src;
      script.async=true;
      script.crossOrigin='anonymous';
      script.onload=()=>{script.dataset.talentxLoaded='1';resolve();};
      script.onerror=reject;
      document.head.appendChild(script);
    });
  }

  function loadStylesheet(href){
    if([...document.styleSheets].some(sheet=>sheet.href&&sheet.href.includes(href.split('?')[0]))) return;
    const link=document.createElement('link');
    link.rel='stylesheet';
    link.href=href;
    document.head.appendChild(link);
  }

  async function bootstrapAccounts(){
    try{
      loadStylesheet('./assets/mobile-auth-fixes.css?v=20260903-3');
      await loadScript('./assets/mobile-auth-fixes.js?v=20260903-2');
      if(typeof route!=='undefined'&&(route==='login'||route==='signup')&&typeof render==='function'){
        try{render();}catch{}
      }
      await loadScript('./assets/auth-confirmation-recovery.js?v=20260901-1');
      if(!window.supabase?.createClient){
        await loadScript('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.57.4/dist/umd/supabase.min.js');
      }
      await loadScript('./assets/supabase-auth-sync.js?v=20260903-2');
      await loadScript('./assets/password-requirements.js?v=20260901-1');
      await loadScript('./assets/neon-brand-assets.js?v=20260903-1');
      loadStylesheet('./assets/account-ui.css?v=20260901-3');
      await loadScript('./assets/account-ui.js?v=20260901-2');
      await loadScript('./assets/account-verification-controls.js?v=20260901-1');
      loadStylesheet('./assets/notification-center.css?v=20260901-1');
      await loadScript('./assets/notification-center.js?v=20260901-1');
      await loadScript('./assets/logo-welcome-routing.js?v=20260901-1');
    }catch(error){
      console.warn('TalentX account services could not load; guest mode remains available.',error);
    }
  }

  const priorSetActiveNav=typeof setActiveNav==='function'?setActiveNav:null;
  if(priorSetActiveNav){
    setActiveNav=function(){
      priorSetActiveNav();
      syncMobileNav();
    };
  }

  const migrated=migrateLocalPriceState();
  syncMobileNav();
  startCanonicalListingGuard();
  if(migrated&&typeof render==='function'){
    try{render();}catch{}
  }
  document.addEventListener('DOMContentLoaded',syncMobileNav,{once:true});
  window.talentxCatalogPricingRevision=CATALOG_PRICING_REVISION;
  window.talentxApplyCanonicalListingGuards=applyCanonicalListingGuards;
  bootstrapAccounts();
})();
