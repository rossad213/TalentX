/* TalentX Supabase auth + server-authoritative signed-in portfolio sync. */
(() => {
  const PROJECT_URL='https://selifenorvjodihiaexw.supabase.co';
  const PUBLISHABLE_KEY='sb_publishable_kHtyJKFgJHZy4kRaL5XJ6Q_gqEphAtY';
  const APP_REDIRECT='https://rossad213.github.io/TalentX/';
  const STARTING_CASH=Number(window.TALENTX_STARTING_CASH||1000);
  const LEGACY_STARTING_CASH=25000;
  if(!window.supabase?.createClient){
    console.warn('TalentX Supabase client library is unavailable.');
    return;
  }

  const client=window.supabase.createClient(PROJECT_URL,PUBLISHABLE_KEY,{
    auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}
  });
  window.talentxSupabase=client;
  window.__talentxAuthUser=null;
  window.__talentxCloudAuthoritative=true;
  let applyingCloud=false;
  let syncTimer=null;

  function notify(message){
    if(typeof toast==='function') toast(message);
    else console.log(message);
  }

  async function syncWatchlist(userId){
    if(!userId||applyingCloud) return;
    const watchlist=[...(state.watchlist||[])].map(String);
    const {error:deleteError}=await client.from('watchlist').delete().eq('user_id',userId);
    if(deleteError) throw deleteError;
    if(watchlist.length){
      const rows=watchlist.map(talentId=>({user_id:userId,talent_id:talentId}));
      const {error}=await client.from('watchlist').insert(rows);
      if(error) throw error;
    }
  }

  async function loadCloudState(userId){
    const [accountRes,holdingsRes,watchRes,txRes]=await Promise.all([
      client.from('account_state').select('*').eq('user_id',userId).maybeSingle(),
      client.from('holdings').select('talent_id,shares,average_cost').eq('user_id',userId),
      client.from('watchlist').select('talent_id').eq('user_id',userId),
      client.from('transactions').select('talent_id,side,shares,execution_price,total_value,created_at,client_event_id').eq('user_id',userId).order('created_at',{ascending:false}).limit(200)
    ]);
    const firstError=[accountRes,holdingsRes,watchRes,txRes].find(r=>r.error)?.error;
    if(firstError) throw firstError;

    const cloudCash=Number(accountRes.data?.virtual_cash??STARTING_CASH);
    const cloudHasNoTrades=!(holdingsRes.data||[]).length && !(txRes.data||[]).length;
    const normalizedCash=(cloudCash===LEGACY_STARTING_CASH&&cloudHasNoTrades)?STARTING_CASH:cloudCash;

    applyingCloud=true;
    try{
      state.cash=normalizedCash;
      state.purchasedCashTotal=Number(accountRes.data?.purchased_cash_total||0);
      state.holdings=Object.fromEntries((holdingsRes.data||[]).filter(row=>Number(row.shares)>0).map(row=>[row.talent_id,Number(row.shares||0)]));
      state.watchlist=(watchRes.data||[]).map(row=>row.talent_id);
      state.transactions=(txRes.data||[]).map(row=>({
        id:row.talent_id,
        mode:row.side,
        shares:Number(row.shares||0),
        price:Number(row.execution_price||0),
        total:Number(row.total_value||0),
        time:Date.parse(row.created_at)||Date.now(),
        clientEventId:row.client_event_id||null
      }));
      // Signed-in cash, holdings and trades are authoritative in Supabase.
      // Market prices remain catalog-driven rather than writable per-account overrides.
      state.prices={};
      if(typeof saveState==='function') saveState();
      if(typeof render==='function') render();
    }finally{
      applyingCloud=false;
    }
    return {
      cash:state.cash,
      purchasedCashTotal:state.purchasedCashTotal,
      holdings:state.holdings,
      transactions:state.transactions
    };
  }

  function scheduleCloudSync(){
    if(applyingCloud||!window.__talentxAuthUser?.id) return;
    clearTimeout(syncTimer);
    syncTimer=setTimeout(()=>{
      syncWatchlist(window.__talentxAuthUser.id).catch(err=>{
        console.warn('TalentX watchlist sync failed',err);
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
      if(data.user) await loadCloudState(data.user.id);
      return data;
    },
    async signup({email,password,name}){
      const {data,error}=await client.auth.signUp({
        email,password,
        options:{data:{display_name:name||''},emailRedirectTo:APP_REDIRECT}
      });
      if(error) throw error;
      window.__talentxAuthUser=data.user||null;
      if(data.session&&data.user) await loadCloudState(data.user.id);
      return data;
    },
    async resendConfirmation(email){
      const {error}=await client.auth.resend({type:'signup',email,options:{emailRedirectTo:APP_REDIRECT}});
      if(error) throw error;
    },
    async logout(){
      const {error}=await client.auth.signOut();
      if(error) throw error;
      window.__talentxAuthUser=null;
    },
    async resetPassword(email){
      const redirectTo=`${APP_REDIRECT}?view=login`;
      const {error}=await client.auth.resetPasswordForEmail(email,{redirectTo});
      if(error) throw error;
    },
    async refreshAccount(){
      const user=window.__talentxAuthUser;
      if(!user?.id) return null;
      return loadCloudState(user.id);
    },
    async syncNow(){
      const user=window.__talentxAuthUser;
      if(!user?.id) return null;
      await syncWatchlist(user.id);
      return loadCloudState(user.id);
    }
  };

  window.resendTalentxConfirmation=async function(){
    const email=document.getElementById('authEmail')?.value?.trim();
    if(!email||!/^\S+@\S+\.\S+$/.test(email)){
      notify('Enter the email you used to create your account first.');
      return;
    }
    const button=document.getElementById('authResendConfirmation');
    if(button){button.disabled=true;button.textContent='Sending…';}
    try{
      await window.talentxAuthAdapter.resendConfirmation(email);
      notify('Fresh confirmation email sent. Use the newest email link.');
    }catch(err){
      console.warn('TalentX confirmation resend error',err);
      notify(err?.message||'Could not resend the confirmation email.');
    }finally{
      if(button){button.disabled=false;button.textContent='Resend confirmation email';}
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
      try{await loadCloudState(user.id);}catch(err){console.warn('TalentX session restore sync failed',err);}
    }
  });

  client.auth.onAuthStateChange((event,session)=>{
    window.__talentxAuthUser=session?.user||null;
    if(event==='SIGNED_OUT') notify('You are logged out.');
  });
})();
