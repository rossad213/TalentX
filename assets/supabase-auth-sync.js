/* TalentX Supabase auth + first-pass account sync. */
(() => {
  const PROJECT_URL='https://selifenorvjodihiaexw.supabase.co';
  const PUBLISHABLE_KEY='sb_publishable_kHtyJKFgJHZy4kRaL5XJ6Q_gqEphAtY';
  if(!window.supabase?.createClient){
    console.warn('TalentX Supabase client library is unavailable.');
    return;
  }

  const client=window.supabase.createClient(PROJECT_URL,PUBLISHABLE_KEY,{
    auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}
  });
  window.talentxSupabase=client;
  window.__talentxAuthUser=null;
  let applyingCloud=false;
  let syncTimer=null;

  function notify(message){
    if(typeof toast==='function') toast(message);
    else console.log(message);
  }

  function hasGuestActivity(){
    try{
      return Math.abs(Number(state.cash||25000)-25000)>.005 ||
        Object.keys(state.holdings||{}).length>0 ||
        (state.watchlist||[]).length>0 ||
        (state.transactions||[]).length>0;
    }catch{return false;}
  }

  function transactionKey(tx,index){
    const time=Number(tx?.time||0);
    return [tx?.id||'talent',tx?.mode||'trade',tx?.shares||0,time||index].join(':');
  }

  async function uploadLocalState(userId){
    if(!userId||applyingCloud) return;
    const holdings=Object.entries(state.holdings||{}).filter(([,shares])=>Number(shares)>0);
    const watchlist=[...(state.watchlist||[])];
    const txs=[...(state.transactions||[])].slice(0,200);

    const {error:accountError}=await client.from('account_state').upsert({
      user_id:userId,
      virtual_cash:Number(state.cash||0),
      pricing_state_revision:String(state.pricingStateRevision||state.pricingModelVersion||'')||null,
      updated_at:new Date().toISOString()
    },{onConflict:'user_id'});
    if(accountError) throw accountError;

    const {error:deleteHoldingsError}=await client.from('holdings').delete().eq('user_id',userId);
    if(deleteHoldingsError) throw deleteHoldingsError;
    if(holdings.length){
      const rows=holdings.map(([talentId,shares])=>({user_id:userId,talent_id:talentId,shares:Number(shares),average_cost:0}));
      const {error}=await client.from('holdings').insert(rows);
      if(error) throw error;
    }

    const {error:deleteWatchError}=await client.from('watchlist').delete().eq('user_id',userId);
    if(deleteWatchError) throw deleteWatchError;
    if(watchlist.length){
      const rows=watchlist.map(talentId=>({user_id:userId,talent_id:String(talentId)}));
      const {error}=await client.from('watchlist').insert(rows);
      if(error) throw error;
    }

    if(txs.length){
      const rows=txs.map((tx,index)=>({
        user_id:userId,
        talent_id:String(tx.id||''),
        side:tx.mode==='sell'?'sell':'buy',
        shares:Number(tx.shares||0),
        execution_price:Number(tx.price||0),
        total_value:Number(tx.total||((Number(tx.price)||0)*(Number(tx.shares)||0))),
        created_at:new Date(Number(tx.time)||Date.now()).toISOString(),
        client_event_id:transactionKey(tx,index)
      })).filter(row=>row.talent_id&&row.shares>0&&row.execution_price>=0&&row.total_value>=0);
      if(rows.length){
        const {error}=await client.from('transactions').upsert(rows,{onConflict:'user_id,client_event_id',ignoreDuplicates:true});
        if(error) throw error;
      }
    }
  }

  async function loadCloudState(userId,{allowGuestImport=true}={}){
    const [accountRes,holdingsRes,watchRes,txRes]=await Promise.all([
      client.from('account_state').select('*').eq('user_id',userId).maybeSingle(),
      client.from('holdings').select('talent_id,shares').eq('user_id',userId),
      client.from('watchlist').select('talent_id').eq('user_id',userId),
      client.from('transactions').select('talent_id,side,shares,execution_price,total_value,created_at,client_event_id').eq('user_id',userId).order('created_at',{ascending:false}).limit(200)
    ]);
    const firstError=[accountRes,holdingsRes,watchRes,txRes].find(r=>r.error)?.error;
    if(firstError) throw firstError;

    const cloudPristine=Number(accountRes.data?.virtual_cash??25000)===25000 &&
      !(holdingsRes.data||[]).length && !(watchRes.data||[]).length && !(txRes.data||[]).length;

    if(allowGuestImport&&cloudPristine&&hasGuestActivity()){
      await uploadLocalState(userId);
      notify('Your guest portfolio was added to your TalentX account.');
      return;
    }

    applyingCloud=true;
    try{
      state.cash=Number(accountRes.data?.virtual_cash??25000);
      state.holdings=Object.fromEntries((holdingsRes.data||[]).map(row=>[row.talent_id,Number(row.shares||0)]));
      state.watchlist=(watchRes.data||[]).map(row=>row.talent_id);
      state.transactions=(txRes.data||[]).map(row=>({
        id:row.talent_id,
        mode:row.side,
        shares:Number(row.shares||0),
        price:Number(row.execution_price||0),
        total:Number(row.total_value||0),
        time:Date.parse(row.created_at)||Date.now()
      }));
      // Authoritative market prices remain catalog-driven; never cloud-sync stale simulated overrides.
      state.prices={};
      if(typeof saveState==='function') saveState();
      if(typeof render==='function') render();
    }finally{
      applyingCloud=false;
    }
  }

  function scheduleCloudSync(){
    if(applyingCloud||!window.__talentxAuthUser?.id) return;
    clearTimeout(syncTimer);
    syncTimer=setTimeout(()=>{
      uploadLocalState(window.__talentxAuthUser.id).catch(err=>{
        console.warn('TalentX cloud sync failed',err);
        notify('Cloud sync could not finish. Your local data is still safe.');
      });
    },700);
  }

  if(typeof saveState==='function'){
    const localSave=saveState;
    saveState=function(){
      const result=localSave.apply(this,arguments);
      scheduleCloudSync();
      return result;
    };
  }

  window.talentxAuthAdapter={
    async login({email,password}){
      const {data,error}=await client.auth.signInWithPassword({email,password});
      if(error) throw error;
      window.__talentxAuthUser=data.user||null;
      if(data.user) await loadCloudState(data.user.id,{allowGuestImport:true});
      return data;
    },
    async signup({email,password,name}){
      const {data,error}=await client.auth.signUp({
        email,password,
        options:{data:{display_name:name||''}}
      });
      if(error) throw error;
      window.__talentxAuthUser=data.user||null;
      if(data.session&&data.user) await loadCloudState(data.user.id,{allowGuestImport:true});
      return data;
    },
    async logout(){
      const {error}=await client.auth.signOut();
      if(error) throw error;
      window.__talentxAuthUser=null;
    },
    async resetPassword(email){
      const redirectTo=`${window.location.origin}${window.location.pathname}?view=login`;
      const {error}=await client.auth.resetPasswordForEmail(email,{redirectTo});
      if(error) throw error;
    },
    async syncNow(){
      if(window.__talentxAuthUser?.id) await uploadLocalState(window.__talentxAuthUser.id);
    }
  };

  window.submitTalentxAuth=async function(mode){
    const email=document.getElementById('authEmail')?.value?.trim();
    const password=document.getElementById('authPassword')?.value||'';
    const confirm=document.getElementById('authConfirm')?.value||'';
    const name=document.getElementById('authName')?.value?.trim()||'';
    if(!email||!/^\S+@\S+\.\S+$/.test(email)){notify('Enter a valid email address.');return;}
    if(password.length<8){notify('Use at least 8 characters for your password.');return;}
    if(mode==='signup'&&password!==confirm){notify('Passwords do not match.');return;}
    const button=document.querySelector('.auth-submit');
    if(button){button.disabled=true;button.textContent=mode==='signup'?'Creating account…':'Logging in…';}
    try{
      const result=await window.talentxAuthAdapter[mode]({email,password,name});
      if(mode==='signup'&&!result.session){
        notify('Account created. Check your email to confirm your TalentX account.');
      }else{
        notify(mode==='signup'?'Welcome to TalentX.':'Welcome back to TalentX.');
        if(typeof go==='function') go('market');
      }
    }catch(err){
      console.warn('TalentX auth error',err);
      notify(err?.message||'Account request failed.');
    }finally{
      if(button){button.disabled=false;button.textContent=mode==='signup'?'Create account':'Log in';}
    }
  };

  window.talentxAuthPlaceholder=async function(feature){
    if(feature!=='Password reset'){notify(`${feature} is not available yet.`);return;}
    const email=document.getElementById('authEmail')?.value?.trim();
    if(!email){notify('Enter your email first, then choose Forgot password.');return;}
    try{
      await window.talentxAuthAdapter.resetPassword(email);
      notify('Password reset email sent.');
    }catch(err){notify(err?.message||'Could not send the reset email.');}
  };

  client.auth.getSession().then(async({data})=>{
    const user=data.session?.user||null;
    window.__talentxAuthUser=user;
    if(user){
      try{await loadCloudState(user.id,{allowGuestImport:false});}catch(err){console.warn('TalentX session restore sync failed',err);}
    }
  });

  client.auth.onAuthStateChange((event,session)=>{
    window.__talentxAuthUser=session?.user||null;
    if(event==='SIGNED_OUT') notify('You are logged out.');
  });
})();