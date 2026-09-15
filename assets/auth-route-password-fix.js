/* TalentX auth-route stability + password visibility controls. */
(() => {
  const AUTH_ROUTES=new Set(['login','signup']);

  function addStyles(){
    if(document.getElementById('talentxAuthRoutePasswordStyles')) return;
    const style=document.createElement('style');
    style.id='talentxAuthRoutePasswordStyles';
    style.textContent=`
      .auth-password-control{position:relative;width:100%}
      .auth-password-control input{padding-right:46px!important}
      .auth-password-toggle{position:absolute;right:10px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;width:32px;height:32px;border:0;border-radius:8px;background:transparent;color:#8fa2b3;cursor:pointer;padding:0}
      .auth-password-toggle:hover,.auth-password-toggle:focus-visible{color:#edf7ff;background:rgba(255,255,255,.06);outline:none}
      .auth-password-toggle svg{width:18px;height:18px;pointer-events:none}
    `;
    document.head.appendChild(style);
  }

  function eyeSvg(visible){
    return visible
      ? '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 4.2A10.8 10.8 0 0 1 12 4c5.4 0 9 5 9 5a16.3 16.3 0 0 1-3.1 3.7"/><path d="M6.6 6.6C4.3 8.2 3 10 3 10s3.6 5 9 5c1.1 0 2.1-.2 3-.5"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12s3.6-5 9-5 9 5 9 5-3.6 5-9 5-9-5-9-5Z"/><circle cx="12" cy="12" r="2.4"/></svg>';
  }

  function addPasswordToggle(input){
    if(!input||input.dataset.talentxVisibilityReady==='1') return;
    input.dataset.talentxVisibilityReady='1';
    const wrapper=document.createElement('div');
    wrapper.className='auth-password-control';
    input.parentNode.insertBefore(wrapper,input);
    wrapper.appendChild(input);

    const button=document.createElement('button');
    button.type='button';
    button.className='auth-password-toggle';
    button.setAttribute('aria-label','Show password');
    button.setAttribute('aria-pressed','false');
    button.title='Show password';
    button.innerHTML=eyeSvg(false);
    button.addEventListener('click',()=>{
      const show=input.type==='password';
      input.type=show?'text':'password';
      button.setAttribute('aria-label',show?'Hide password':'Show password');
      button.setAttribute('aria-pressed',show?'true':'false');
      button.title=show?'Hide password':'Show password';
      button.innerHTML=eyeSvg(show);
      input.focus({preventScroll:true});
    });
    wrapper.appendChild(button);
  }

  function enhancePasswordFields(){
    addStyles();
    addPasswordToggle(document.getElementById('authPassword'));
    addPasswordToggle(document.getElementById('authConfirm'));
  }

  function ensureAuthUrl(mode){
    try{
      const url=new URL(window.location.href);
      url.search='';
      url.hash='';
      url.searchParams.set('view',mode);
      const target=`${url.pathname}${url.search}`;
      const state={...(history.state||{}),talentx:true,scrollY:0};
      history.replaceState(state,'',target);
    }catch{}
  }

  function forceAuthView(mode){
    if(!AUTH_ROUTES.has(mode)) return;
    try{
      route=mode;
      selectedId=null;
      profileTab='overview';
      document.body.classList.add('public-site-route');
      if(typeof render==='function') render();
      else if(typeof authPage==='function'){
        const app=document.getElementById('app');
        if(app) app.innerHTML=authPage(mode);
      }
      try{setActiveNav();}catch{}
    }catch(error){
      console.warn('TalentX auth view recovery failed',error);
    }
    ensureAuthUrl(mode);
    requestAnimationFrame(enhancePasswordFields);
  }

  function openAuth(mode){
    if(!AUTH_ROUTES.has(mode)) return;
    try{
      if(typeof go==='function') go(mode);
      else forceAuthView(mode);
    }catch{
      forceAuthView(mode);
      return;
    }

    // Keep the URL aligned with the requested auth view so the asynchronous
    // history hydration cannot interpret the bare URL as Welcome/dashboard.
    ensureAuthUrl(mode);
    queueMicrotask(()=>{
      let active=false;
      try{active=route===mode;}catch{}
      if(!active||!document.querySelector('.auth-shell')) forceAuthView(mode);
      else enhancePasswordFields();
    });
    setTimeout(()=>{
      let active=false;
      try{active=route===mode;}catch{}
      if(!active||!document.querySelector('.auth-shell')) forceAuthView(mode);
      else enhancePasswordFields();
    },80);
  }

  function authModeFromControl(control){
    if(!control) return null;
    const raw=`${control.getAttribute('onclick')||''} ${control.dataset?.route||''}`;
    if(/go\(['"]login['"]\)/.test(raw)||control.dataset?.authRoute==='login') return 'login';
    if(/go\(['"]signup['"]\)/.test(raw)||control.dataset?.authRoute==='signup') return 'signup';
    return null;
  }

  document.addEventListener('click',event=>{
    const control=event.target?.closest?.('button,a');
    const mode=authModeFromControl(control);
    if(!mode) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openAuth(mode);
  },true);

  const observer=new MutationObserver(()=>enhancePasswordFields());
  observer.observe(document.documentElement,{childList:true,subtree:true});
  enhancePasswordFields();

  // If an authentication transition completes while the user is still on an
  // auth screen, force the signed-in Home intent instead of falling back to the
  // public welcome page.
  let previousUserId=window.__talentxAuthUser?.id||null;
  setInterval(()=>{
    const currentUserId=window.__talentxAuthUser?.id||null;
    let currentRoute='';
    try{currentRoute=String(route||'');}catch{}
    if(currentUserId&&currentUserId!==previousUserId&&AUTH_ROUTES.has(currentRoute)){
      window.__talentxDashboardIntent=true;
      if(typeof window.talentxGoDashboard==='function') window.talentxGoDashboard();
      else if(typeof go==='function') go('dashboard');
    }
    previousUserId=currentUserId;
  },200);

  window.talentxOpenAuth=openAuth;
})();
