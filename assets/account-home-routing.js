/* TalentX account-aware Home routing.
 * ROUTING INVARIANT:
 *   - Home/dashboard controls ALWAYS open the app dashboard.
 *   - TalentX brand/logo controls ALWAYS open the public welcome page.
 * The dashboard route must never silently render Welcome after a user explicitly
 * asks for Home, even while authentication is still bootstrapping.
 */
(() => {
  const appDashboard = typeof dashboard === 'function' ? dashboard : null;
  if (!appDashboard) return;

  let authUser = window.__talentxAuthUser || null;
  let explicitDashboard = false;
  window.__talentxDashboardIntent = false;

  try {
    Object.defineProperty(window,'__talentxAuthUser',{
      configurable:true,
      get(){ return authUser; },
      set(value){
        const changed=(authUser?.id||null)!==(value?.id||null);
        authUser=value||null;
        if(changed && typeof window.talentxRefreshAccountHome==='function'){
          queueMicrotask(()=>window.talentxRefreshAccountHome());
        }
      }
    });
  } catch {}

  function brandTarget(target){
    return target?.closest?.('.brand,.public-brand,.auth-back-brand,.public-footer-brand,.mobile-brand');
  }

  function homeTarget(target){
    return target?.closest?.('button[data-route="dashboard"],button[data-mobile-route="dashboard"]');
  }

  function markDashboardIntent(){
    explicitDashboard=true;
    window.__talentxDashboardIntent=true;
  }

  function clearDashboardIntent(){
    explicitDashboard=false;
    window.__talentxDashboardIntent=false;
  }

  function goWelcomeFromBrand(event){
    const brand=brandTarget(event.target);
    if(!brand) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    clearDashboardIntent();
    if(typeof window.talentxGoWelcome==='function') window.talentxGoWelcome();
    else if(typeof go==='function') go('welcome');
  }

  function goDashboardFromHome(event){
    const home=homeTarget(event.target);
    if(!home) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    markDashboardIntent();
    if(typeof go==='function') go('dashboard');
  }

  // Capture navigation before any inline or legacy handler. This catches both
  // static desktop/mobile Home buttons and dynamically generated mobile Home.
  document.addEventListener('click',event=>{
    if(brandTarget(event.target)) return goWelcomeFromBrand(event);
    if(homeTarget(event.target)) return goDashboardFromHome(event);
  },true);
  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'&&event.key!==' ') return;
    if(brandTarget(event.target)) return goWelcomeFromBrand(event);
    if(homeTarget(event.target)) return goDashboardFromHome(event);
  },true);

  setTimeout(() => {
    if (typeof publicHome !== 'function') return;

    // Initial signed-out boot may still use the public front door, but an
    // explicit dashboard intent can never resolve to publicHome().
    dashboard = function(){
      return (explicitDashboard || window.__talentxDashboardIntent || window.__talentxAuthUser)
        ? appDashboard()
        : publicHome();
    };

    const routedRender = typeof render === 'function' ? render : null;
    if (routedRender) {
      render = function(){
        if(route==='welcome'){
          const app=document.getElementById('app');
          if(app) app.innerHTML=publicHome();
          document.body.classList.add('public-site-route');
          try{setActiveNav();}catch{}
          return;
        }
        const result = routedRender.apply(this,arguments);
        if(route==='dashboard'){
          const wantsDashboard=explicitDashboard || window.__talentxDashboardIntent || window.__talentxAuthUser;
          document.body.classList.toggle('public-site-route',!wantsDashboard);
        }
        return result;
      };
    }

    const routedGo=typeof go==='function'?go:null;
    if(routedGo){
      go=function(next){
        if(next==='welcome'){
          clearDashboardIntent();
          route='welcome';
          selectedId=null;
          profileTab='overview';
          try{setActiveNav();}catch{}
          render();
          return;
        }
        // Hard invariant: any explicit dashboard navigation marks dashboard
        // intent before downstream wrappers get control.
        if(next==='dashboard') markDashboardIntent();
        return routedGo(next);
      };
    }

    window.talentxGoWelcome=function(){
      clearDashboardIntent();
      if(typeof go==='function') go('welcome');
    };

    window.talentxGoDashboard=function(){
      markDashboardIntent();
      if(typeof go==='function') go('dashboard');
    };

    window.talentxRefreshAccountHome=function(){
      if(route==='dashboard' && typeof render==='function'){
        try{render();}catch(error){console.warn('TalentX Home route refresh failed',error);}
      }
    };

    window.talentxAccountAwareHome='locked-logo-welcome-home-dashboard-v6';
    window.talentxRefreshAccountHome();
  },0);
})();
