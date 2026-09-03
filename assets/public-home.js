/* TalentX public homepage + authentication-ready front door.
 * Account forms intentionally expose integration hooks without storing credentials.
 */
(() => {
  let authReturnState=null;
  const authRoutes=new Set(['login','signup']);

  function captureAuthReturnState(){
    return {
      route:typeof route==='string'&&!authRoutes.has(route)?route:'dashboard',
      selectedId:typeof selectedId!=='undefined'?selectedId:null,
      profileTab:typeof profileTab!=='undefined'?profileTab:'overview',
      tradeMode:typeof tradeMode!=='undefined'?tradeMode:null,
      retirementSelection:typeof retirementSelection!=='undefined'?retirementSelection:null,
      chartRange:typeof chartRange!=='undefined'?chartRange:null,
      filters:typeof filters==='object'&&filters?{...filters}:null,
      scrollY:window.scrollY||0
    };
  }

  function initials(record){
    return String(record?.avatar||record?.name||'TX').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase();
  }
  function safeMove(record){
    try{return Number(displayChange(record)||0)}catch{return Number(record?.dailyChange||0)}
  }
  function currentPrice(record){
    try{return Number(localPrice(record)||record?.marketPrice||0)}catch{return Number(record?.marketPrice||0)}
  }
  function publicStats(){
    const records=Array.isArray(currentRecords)?currentRecords:[];
    const athletes=records.filter(r=>r.primaryCategory==='Athlete').length;
    const categories=new Set(records.map(r=>r.primaryCategory).filter(Boolean)).size;
    const verified=records.filter(r=>Number(r.pricingConfidence||0)>=.8).length;
    return {records:records.length,athletes,categories,verified};
  }
  function marketPreview(){
    const records=Array.isArray(currentRecords)?currentRecords:[];
    const ranked=[...records]
      .filter(r=>Number.isFinite(currentPrice(r))&&currentPrice(r)>0)
      .sort((a,b)=>Number(b.careerScore||0)-Number(a.careerScore||0))
      .slice(0,4);
    if(!ranked.length){
      return `<div class="market-demo-row"><div class="market-demo-person"><span class="market-demo-avatar">TX</span><div><b>Loading current market…</b><small>Live catalog</small></div></div><strong class="market-demo-price">—</strong></div>`;
    }
    return ranked.map(record=>{
      const move=safeMove(record);
      return `<button class="market-demo-row" type="button" onclick="openProfile('${esc(record.id)}')" style="width:100%;border-left:0;border-right:0;border-top:0;background:transparent;color:inherit;text-align:left;cursor:pointer">
        <div class="market-demo-person"><span class="market-demo-avatar">${esc(initials(record))}</span><div><b>${esc(record.name)}</b><small>${esc(record.role||record.discipline||record.primaryCategory)} · ${esc(record.leagueOrMedium||record.primaryCategory||'Talent')}</small></div></div>
        <strong class="market-demo-price">${money(currentPrice(record))}</strong>
        <span class="market-demo-move ${move>=0?'up':'down'}">${move>=0?'+':''}${move.toFixed(2)}%</span>
      </button>`;
    }).join('');
  }
  function statNumber(value){
    const number=Number(value||0);
    if(number>=1000) return `${(number/1000).toFixed(number>=10000?0:1)}K+`;
    return number.toLocaleString();
  }

  window.publicHome=function(){
    const stats=publicStats();
    return `<div class="public-page">
      <header class="public-nav">
        <button class="public-brand" type="button" onclick="go('dashboard')"><img src="./assets/talentx-logo-64.png?v=20260903-1" alt="TalentX"><span><strong>TalentX</strong><small>THE MARKET FOR TALENT</small></span></button>
        <nav class="public-links" aria-label="Homepage"><a href="#features">Features</a><a href="#how">How it works</a><a href="#data">Data & trust</a></nav>
        <div class="public-actions"><button class="public-btn ghost" type="button" onclick="go('login')">Log in</button><button class="public-btn primary" type="button" onclick="go('signup')">Sign up</button></div>
      </header>

      <main>
        <section class="public-hero">
          <div>
            <span class="public-eyebrow"><i></i> Live virtual talent market</span>
            <h1>Track talent like a <span>market.</span></h1>
            <p class="public-hero-copy">TalentX turns verified performance, career evidence, audience signals, and real-world events into an explainable virtual market for athletes, musicians, actors, and creators.</p>
            <div class="public-hero-actions"><button class="public-btn primary" type="button" onclick="go('signup')">Create free account</button><button class="public-btn" type="button" onclick="go('market')">Explore the market as guest →</button></div>
            <div class="public-microcopy"><span>Virtual money only</span><span>Explainable price moves</span><span>No account required to browse</span></div>
          </div>
          <div class="market-demo" aria-label="TalentX market preview">
            <div class="market-demo-head"><strong>Market preview</strong><span class="live-chip"><i></i> Current-first catalog</span></div>
            <div class="market-demo-stats"><div class="market-demo-stat"><small>Current listings</small><strong>${statNumber(stats.records)}</strong></div><div class="market-demo-stat"><small>High-confidence</small><strong>${statNumber(stats.verified)}</strong></div><div class="market-demo-stat"><small>Categories</small><strong>${stats.categories||4}</strong></div></div>
            <div class="market-demo-list">${marketPreview()}</div>
          </div>
        </section>

        <div class="public-band"><div class="public-stats"><div class="public-stat"><strong>${statNumber(stats.records)}</strong><small>Current talent profiles</small></div><div class="public-stat"><strong>${statNumber(stats.athletes)}</strong><small>Current athletes</small></div><div class="public-stat"><strong>4</strong><small>Talent markets</small></div><div class="public-stat"><strong>24/7</strong><small>Market access</small></div></div></div>

        <section class="public-section" id="features">
          <div class="public-section-head"><span class="public-kicker">Built for discovery</span><h2>One market. Every kind of talent.</h2><p>Browse thousands of current profiles, understand why prices move, build a virtual portfolio, and follow the people you think are gaining momentum.</p></div>
          <div class="public-feature-grid">
            <article class="public-feature"><span class="public-feature-icon">⌁</span><h3>Live talent market</h3><p>Search and compare current athletes, musicians, actors, and creators with a consistent virtual pricing framework.</p></article>
            <article class="public-feature"><span class="public-feature-icon">↗</span><h3>Explainable price moves</h3><p>Price history is tied to supported events and evidence instead of unexplained random chart movement.</p></article>
            <article class="public-feature"><span class="public-feature-icon">▣</span><h3>Virtual portfolio</h3><p>Practice conviction with simulated cash, holdings, returns, and transaction history. No real money changes hands.</p></article>
            <article class="public-feature"><span class="public-feature-icon">☆</span><h3>Watchlists</h3><p>Save talent you want to follow so your account can later sync alerts and important market events across devices.</p></article>
            <article class="public-feature"><span class="public-feature-icon">◎</span><h3>Data transparency</h3><p>See pricing confidence, evidence status, career stage, market rules, and the methodology behind the model.</p></article>
            <article class="public-feature"><span class="public-feature-icon">♕</span><h3>Leaderboards</h3><p>Compare virtual portfolio performance once account identities and cloud persistence are connected.</p></article>
          </div>
        </section>

        <section class="public-section" id="how">
          <div class="public-section-head"><span class="public-kicker">How it works</span><h2>Research. Follow. Build conviction.</h2></div>
          <div class="public-steps"><article class="public-step"><span class="public-step-num">01</span><h3>Explore the market</h3><p>Find talent by category, sport, league, role, career stage, score, or price.</p></article><article class="public-step"><span class="public-step-num">02</span><h3>Understand the price</h3><p>Open a profile to inspect supported events, evidence, confidence, and the factors behind valuation.</p></article><article class="public-step"><span class="public-step-num">03</span><h3>Build a virtual portfolio</h3><p>Use simulated cash to follow your thesis and compare how your selections perform over time.</p></article></div>
        </section>

        <section class="public-section" id="data">
          <div class="public-section-head"><span class="public-kicker">Data & trust</span><h2>A market should explain itself.</h2><p>TalentX is designed to keep authoritative catalog pricing separate from browser-local trading and to preserve dated evidence behind meaningful price moves.</p></div>
          <div class="public-trust"><article class="public-trust-card"><h3>Evidence-first pricing</h3><p>Career fundamentals and verified events are distinct. Routine catalog refreshes should not manufacture fake short-term moves.</p><div class="public-trust-list"><span>Pricing confidence displayed</span><span>Event-driven chart history</span><span>Current and historical segments separated</span></div></article><article class="public-trust-card"><h3>Account-ready privacy model</h3><p>The account layer is being prepared so portfolio data can sync without turning the public market into a login wall.</p><div class="public-trust-list"><span>Guest browsing remains available</span><span>Passwords will never be stored in browser state</span><span>Virtual balances stay clearly separated from real money</span></div></article></div>
        </section>

        <section class="public-cta"><div><h2>Your talent market starts here.</h2><p>Create an account when cloud sync launches, or explore the live market now as a guest.</p></div><div class="public-actions"><button class="public-btn" type="button" onclick="go('market')">Browse market</button><button class="public-btn primary" type="button" onclick="go('signup')">Create account</button></div></section>
      </main>
      <footer class="public-footer"><span class="public-footer-brand"><img src="./assets/talentx-logo-64.png?v=20260903-1" alt="">TalentX</span><span>Virtual market prototype · No real money</span><div class="public-footer-links"><button type="button" onclick="go('rules')">Data & Rules</button><button type="button" onclick="go('login')">Log in</button></div></footer>
    </div>`;
  };

  window.authPage=function(mode){
    const signup=mode==='signup';
    return `<div class="public-page auth-shell">
      <aside class="auth-brand-panel">
        <button class="auth-back-brand" type="button" onclick="go('dashboard')"><img src="./assets/talentx-logo-64.png?v=20260903-1" alt="TalentX"><strong>TalentX</strong></button>
        <div class="auth-brand-copy"><span>${signup?'Join TalentX':'Welcome back'}</span><h1>${signup?'Build your market identity.':'Your market, wherever you are.'}</h1><p>${signup?'Accounts will sync your virtual portfolio, watchlist, transactions, preferences, and leaderboard identity across devices.':'Sign in will restore your synced TalentX portfolio, watchlist, virtual balance, and market activity once the account backend is connected.'}</p><div class="auth-points"><div>Keep guest browsing available</div><div>Sync portfolio and watchlist securely</div><div>Power future alerts and leaderboard identity</div></div></div>
        <span class="auth-brand-foot">TalentX · The Market for Talent</span>
      </aside>
      <main class="auth-form-panel"><button class="auth-mobile-back" type="button" onclick="talentxAuthBack()" aria-label="Go back to the previous page">← Back</button><section class="auth-card">
        <h2>${signup?'Create your account':'Log in to TalentX'}</h2><p>${signup?'Set up the account that will hold your synced TalentX experience.':'Access your synced TalentX experience.'}</p>
        ${signup?'<div class="auth-field"><label for="authName">Display name</label><input id="authName" name="name" autocomplete="name" placeholder="Your name"></div>':''}
        <div class="auth-field"><label for="authEmail">Email</label><input id="authEmail" name="email" type="email" autocomplete="email" placeholder="you@example.com"></div>
        <div class="auth-field"><label for="authPassword">Password</label><input id="authPassword" name="password" type="password" autocomplete="${signup?'new-password':'current-password'}" placeholder="${signup?'Create a secure password':'Your password'}"></div>
        ${signup?'<div class="auth-field"><label for="authConfirm">Confirm password</label><input id="authConfirm" name="confirm" type="password" autocomplete="new-password" placeholder="Repeat your password"></div>':'<div class="auth-row"><label class="auth-check"><input type="checkbox"> Keep me signed in</label><button class="auth-link" type="button" onclick="talentxAuthPlaceholder(\'Password reset\')">Forgot password?</button></div>'}
        <button class="auth-submit" type="button" onclick="submitTalentxAuth('${mode}')">${signup?'Create account':'Log in'}</button>
        <div class="auth-divider">or</div><button class="auth-secondary" type="button" onclick="go('market')">Continue as guest</button>
        <div class="auth-switch">${signup?'Already have an account?':'New to TalentX?'} <button type="button" onclick="go('${signup?'login':'signup'}')">${signup?'Log in':'Create account'}</button></div>
        <div class="auth-note"><strong>Account infrastructure is being prepared.</strong> This form is wired for the future auth adapter, but credentials are not currently submitted or stored.</div>
      </section></main>
    </div>`;
  };

  window.talentxAuthPlaceholder=function(feature){
    if(typeof toast==='function') toast(`${feature} will activate with the account backend.`);
  };
  window.submitTalentxAuth=function(mode){
    const email=document.getElementById('authEmail')?.value?.trim();
    const password=document.getElementById('authPassword')?.value||'';
    const confirm=document.getElementById('authConfirm')?.value||'';
    if(!email||!/^\S+@\S+\.\S+$/.test(email)){toast('Enter a valid email address.');return;}
    if(password.length<8){toast('Use at least 8 characters for your password.');return;}
    if(mode==='signup'&&password!==confirm){toast('Passwords do not match.');return;}
    const adapter=window.talentxAuthAdapter;
    if(adapter&&typeof adapter[mode]==='function'){
      adapter[mode]({email,password,name:document.getElementById('authName')?.value?.trim()||''});
      return;
    }
    toast('Account backend is not connected yet — no credentials were sent.');
  };

  const authAwareGo=typeof go==='function'?go:null;
  if(authAwareGo){
    go=function(next){
      const nextRoute=String(next||'');
      if(authRoutes.has(nextRoute)&&!authRoutes.has(route)){
        authReturnState=captureAuthReturnState();
      }
      return authAwareGo.apply(this,arguments);
    };
  }

  window.talentxAuthBack=function(){
    const previous=authReturnState;
    authReturnState=null;
    if(!previous||authRoutes.has(previous.route)){
      if(typeof go==='function') go('dashboard');
      return;
    }
    route=previous.route||'dashboard';
    selectedId=previous.selectedId??null;
    profileTab=previous.profileTab||'overview';
    if(typeof tradeMode!=='undefined'&&previous.tradeMode!==null) tradeMode=previous.tradeMode;
    if(typeof retirementSelection!=='undefined'&&previous.retirementSelection!==null) retirementSelection=previous.retirementSelection;
    if(typeof chartRange!=='undefined'&&previous.chartRange!==null) chartRange=previous.chartRange;
    if(previous.filters&&typeof filters==='object'&&filters) Object.assign(filters,previous.filters);
    setActiveNav();
    render();
    requestAnimationFrame(()=>window.scrollTo({top:Number(previous.scrollY||0),left:0,behavior:'auto'}));
  };

  dashboard=function(){return publicHome();};

  const baseRender=typeof render==='function'?render:null;
  if(baseRender){
    render=function(){
      const isPublic=route==='dashboard'||route==='login'||route==='signup';
      document.body.classList.toggle('public-site-route',isPublic);
      if(route==='login'||route==='signup'){
        const app=document.getElementById('app');
        if(app) app.innerHTML=authPage(route);
        setActiveNav();
        return;
      }
      return baseRender.apply(this,arguments);
    };
  }
  document.body.classList.toggle('public-site-route',route==='dashboard'||route==='login'||route==='signup');
})();
