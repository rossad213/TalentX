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

  function syncMobileNav(){
    try{
      document.querySelectorAll('.mobile-bottom-nav button[data-route]').forEach(button=>{
        button.classList.toggle('active',button.dataset.route===route);
      });
      const more=document.querySelector('.mobile-bottom-nav .mobile-more');
      if(more) more.classList.toggle('active',['leaderboard','rules'].includes(route));
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

  async function bootstrapAccounts(){
    try{
      await loadScript('./assets/auth-confirmation-recovery.js?v=20260901-1');
      if(!window.supabase?.createClient){
        await loadScript('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.57.4/dist/umd/supabase.min.js');
      }
      await loadScript('./assets/supabase-auth-sync.js?v=20260901-2');
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
  if(migrated&&typeof render==='function'){
    try{render();}catch{}
  }
  document.addEventListener('DOMContentLoaded',syncMobileNav,{once:true});
  window.talentxCatalogPricingRevision=CATALOG_PRICING_REVISION;
  bootstrapAccounts();
})();