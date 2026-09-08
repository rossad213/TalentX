/* TalentX account-aware Home routing.
 * Preserve the original app dashboard. The public landing page remains an
 * explicit welcome route, while clicking Home always opens the dashboard.
 * Every TalentX brand/logo target routes back to the welcome page.
 */
(() => {
  const appDashboard = typeof dashboard === 'function' ? dashboard : null;
  if (!appDashboard) return;

  let authUser = window.__talentxAuthUser || null;
  let explicitDashboard = false;
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
    return target?.closest?.('.brand,.public-brand,.auth-back-brand,.public-footer-brand');
  }

  function goWelcomeFromBrand(event){
    const brand=brandTarget(event.target);
    if(!brand) return;
    event.preventDefault();
    event.stopPropagation();
    if(typeof window.talentxGoWelcome==='function') window.talentxGoWelcome();
    else if(typeof go==='function') go('welcome');
  }

  // Capture brand clicks before any legacy inline onclick handler can redirect
  // the logo to the dashboard.
  document.addEventListener('click',goWelcomeFromBrand,true);
  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'&&event.key!==' ') return;
    if(!brandTarget(event.target)) return;
    goWelcomeFromBrand(event);
  },true);

  setTimeout(() => {
    if (typeof publicHome !== 'function') return;

    dashboard = function(){
      return (explicitDashboard || window.__talentxAuthUser) ? appDashboard() : publicHome();
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
          document.body.classList.toggle('public-site-route',!(explicitDashboard || window.__talentxAuthUser));
        }
        return result;
      };
    }

    const routedGo=typeof go==='function'?go:null;
    if(routedGo){
      go=function(next){
        if(next==='welcome'){
          explicitDashboard=false;
          route='welcome';
          selectedId=null;
          profileTab='overview';
          try{setActiveNav();}catch{}
          render();
          return;
        }
        if(next==='dashboard') explicitDashboard=true;
        return routedGo(next);
      };
    }

    window.talentxGoWelcome=function(){
      if(typeof go==='function') go('welcome');
    };

    window.talentxRefreshAccountHome=function(){
      if(route==='dashboard' && typeof render==='function'){
        try{render();}catch(error){console.warn('TalentX Home route refresh failed',error);}
      }
    };

    window.talentxAccountAwareHome='logo-welcome-home-dashboard-v4';
    window.talentxRefreshAccountHome();
  },0);
})();
