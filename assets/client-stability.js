/* TalentX client stability guard.
 * Keeps browser-local virtual trading state separate from authoritative catalog repricing
 * and keeps the mobile navigation active state synchronized with the current route.
 */
(() => {
  const CATALOG_PRICING_REVISION='20260831-1';
  const STORAGE_KEY='talentx_v2_state';

  function migrateLocalPriceState(){
    try{
      if(typeof state!=='object'||!state) return;
      if(String(state.catalogPricingRevision||'')===CATALOG_PRICING_REVISION) return;

      // Only browser-local price overrides are invalidated. Preserve cash,
      // holdings, watchlist, transactions, and every other virtual-account field.
      state.prices={};
      state.catalogPricingRevision=CATALOG_PRICING_REVISION;
      localStorage.setItem(STORAGE_KEY,JSON.stringify(state));
    }catch(error){
      console.warn('TalentX client pricing-state migration skipped',error);
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

  const priorSetActiveNav=typeof setActiveNav==='function'?setActiveNav:null;
  if(priorSetActiveNav){
    setActiveNav=function(){
      priorSetActiveNav();
      syncMobileNav();
    };
  }

  migrateLocalPriceState();
  syncMobileNav();
  document.addEventListener('DOMContentLoaded',syncMobileNav,{once:true});

  window.talentxCatalogPricingRevision=CATALOG_PRICING_REVISION;
})();
