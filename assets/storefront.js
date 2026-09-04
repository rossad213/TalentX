/* TalentX Store: paid TX Cash packages through secure server-side Stripe Checkout. */
(function(){
  const CHECKOUT_ENDPOINT='https://selifenorvjodihiaexw.supabase.co/functions/v1/create-store-checkout';
  const PUBLISHABLE_KEY='sb_publishable_kHtyJKFgJHZy4kRaL5XJ6Q_gqEphAtY';
  const PACKAGES=[
    {code:'tx500',cash:500,price:'$2.99',note:'Starter boost'},
    {code:'tx1100',cash:1100,price:'$5.99',note:'10% extra'},
    {code:'tx2000',cash:2000,price:'$9.99',note:'20% extra',badge:'Most Popular'},
    {code:'tx4500',cash:4500,price:'$19.99',note:'35% extra'},
    {code:'tx10000',cash:10000,price:'$39.99',note:'50% extra',badge:'Best Value'}
  ];
  let buying=false;

  function formatCash(value){return Number(value||0).toLocaleString('en-US');}
  function currentCash(){try{return Number(state?.cash||0);}catch{return 0;}}
  function purchasedCash(){try{return Number(state?.purchasedCashTotal||0);}catch{return 0;}}
  function isSignedIn(){return Boolean(window.__talentxAuthUser?.id);}

  window.buyTxCash=async function(packageCode){
    if(buying) return;
    if(!isSignedIn()){
      if(typeof toast==='function') toast('Log in or create an account before purchasing TX Cash.');
      if(typeof go==='function') go('login');
      return;
    }
    const pack=PACKAGES.find(item=>item.code===packageCode);
    if(!pack) return;
    buying=true;
    const button=document.querySelector(`[data-tx-package="${packageCode}"]`);
    const prior=button?.textContent;
    if(button){button.disabled=true;button.textContent='Opening checkout…';}
    try{
      const session=await window.talentxSupabase?.auth?.getSession?.();
      const accessToken=session?.data?.session?.access_token;
      if(!accessToken) throw new Error('Your login session expired. Please log in again.');
      const returnUrl=`${window.location.origin}${window.location.pathname}`;
      const response=await fetch(CHECKOUT_ENDPOINT,{
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'Authorization':`Bearer ${accessToken}`,
          'apikey':PUBLISHABLE_KEY
        },
        body:JSON.stringify({packageCode,returnUrl})
      });
      const data=await response.json().catch(()=>({}));
      if(!response.ok||!data.url) throw new Error(data.error||'Could not start checkout.');
      window.location.assign(data.url);
    }catch(error){
      if(typeof toast==='function') toast(error?.message||'Could not start checkout.');
      buying=false;
      if(button){button.disabled=false;button.textContent=prior||'Buy now';}
    }
  };

  function packageCard(pack){
    return `<article class="tx-store-package ${pack.badge?'featured':''}">
      ${pack.badge?`<div class="tx-store-badge">${pack.badge}</div>`:''}
      <div class="tx-store-cash"><strong>${formatCash(pack.cash)}</strong><span>TX Cash</span></div>
      <div class="tx-store-price">${pack.price}</div>
      <div class="tx-store-note">${pack.note}</div>
      <button class="btn primary tx-store-buy" data-tx-package="${pack.code}" onclick="buyTxCash('${pack.code}')">Buy now</button>
    </article>`;
  }

  window.talentxStorefront=function(){
    const params=new URLSearchParams(window.location.search);
    const purchase=params.get('purchase');
    const success=purchase==='success';
    const cancelled=purchase==='cancelled';
    return `<div class="tx-store-page">
      <div class="tx-store-hero">
        <div>
          <div class="eyebrow">TalentX Store</div>
          <h1 class="page-title">Get more TX Cash</h1>
          <p class="page-sub">Use TX Cash to build your virtual portfolio across TalentX.</p>
        </div>
        <div class="tx-store-balance"><small>AVAILABLE BALANCE</small><strong>${money(currentCash())}</strong><span>${purchasedCash()>0?`${money(purchasedCash())} purchased to date`:'Includes your free starting balance'}</span></div>
      </div>
      ${success?'<div class="tx-store-status success"><strong>Payment received.</strong><span>Your TX Cash is being added to your account. The balance normally updates within a few seconds.</span></div>':''}
      ${cancelled?'<div class="tx-store-status"><strong>Checkout canceled.</strong><span>No charge was made. You can choose a package whenever you are ready.</span></div>':''}
      <section class="tx-store-grid">${PACKAGES.map(packageCard).join('')}</section>
      <section class="card tx-store-info">
        <div><strong>What is TX Cash?</strong><span>TX Cash is virtual currency used to buy simulated TalentX positions.</span></div>
        <div><strong>No cash-out</strong><span>TX Cash has no cash value and cannot be redeemed, transferred, or exchanged for real money or prizes.</span></div>
        <div><strong>Fair leaderboards</strong><span>Purchased TX Cash is treated as a deposit, not investment profit, when TalentX calculates leaderboard returns.</span></div>
        <div><strong>Secure checkout</strong><span>Real-money payments are processed through Stripe Checkout. TalentX does not collect or store your card number.</span></div>
      </section>
      <p class="tx-store-legal">All purchases are for virtual, non-redeemable in-app currency. By purchasing, you agree that TX Cash is not an investment, security, deposit account, or real-money wagering product.</p>
    </div>`;
  };

  const priorGo=typeof go==='function'?go:null;
  if(priorGo){
    go=function(next){
      if(next==='store'){
        route='store'; selectedId=null; profileTab='overview';
        if(typeof setActiveNav==='function') setActiveNav();
        if(typeof render==='function') render();
        return;
      }
      return priorGo(next);
    };
  }

  const priorRender=typeof render==='function'?render:null;
  if(priorRender){
    render=function(){
      if(route==='store'){
        const app=document.querySelector('#app');
        if(typeof setActiveNav==='function') setActiveNav();
        if(app) app.innerHTML=window.talentxStorefront();
        if(typeof bindAfterRender==='function') bindAfterRender();
        return;
      }
      return priorRender();
    };
  }

  async function refreshAfterReturn(){
    const params=new URLSearchParams(window.location.search);
    if(params.get('view')!=='store') return;
    if(params.get('purchase')==='success'&&window.talentxAuthAdapter?.refreshAccount){
      for(const delay of [500,1500,3000]){
        await new Promise(resolve=>setTimeout(resolve,delay));
        try{await window.talentxAuthAdapter.refreshAccount();}catch{}
      }
    }
    if(typeof go==='function') go('store');
  }

  setTimeout(refreshAfterReturn,0);
})();
