/* TalentX account-aware Home routing.
 * Preserve the original app dashboard. The public landing page remains an
 * explicit welcome route, while clicking Home always opens the dashboard.
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

    window.talentxAccountAwareHome='explicit-home-always-dashboard-v3';
    window.talentxRefreshAccountHome();
  },0);
})();
