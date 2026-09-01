/* TalentX signed-in account menu + settings. */
(() => {
  let profile=null;
  let currentUser=null;
  let syncState='synced';
  let syncMessage='Cloud sync ready';
  let authSubscription=null;

  const escHtml=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const initials=value=>String(value||'TX').trim().split(/\s+/).map(part=>part[0]||'').join('').slice(0,2).toUpperCase()||'TX';
  const displayName=()=>profile?.display_name||currentUser?.user_metadata?.display_name||currentUser?.email?.split('@')[0]||'TalentX member';

  function notify(message){
    if(typeof toast==='function') toast(message);
    else console.log(message);
  }

  function closePopover(){
    document.getElementById('talentxAccountPopover')?.remove();
  }

  function setSync(stateName,message){
    syncState=stateName||'synced';
    syncMessage=message||'Cloud sync ready';
    updateTrigger();
    const status=document.getElementById('accountSettingsSyncStatus');
    if(status) status.textContent=syncMessage;
    const dot=document.getElementById('accountSettingsSyncDot');
    if(dot) dot.className=`account-sync-dot ${syncState==='synced'?'':syncState}`;
  }

  async function loadProfile(){
    if(!currentUser?.id||!window.talentxSupabase) return null;
    const {data,error}=await window.talentxSupabase.from('profiles').select('display_name,avatar_url,leaderboard_opt_in,created_at').eq('id',currentUser.id).maybeSingle();
    if(error){
      console.warn('TalentX profile load failed',error);
      return null;
    }
    profile=data||null;
    updateTrigger();
    return profile;
  }

  function ensureTrigger(){
    const actions=document.querySelector('.top-actions');
    if(!actions) return null;
    const legacy=actions.querySelector('.user-avatar-logo');
    if(legacy) legacy.style.display='none';
    let button=document.getElementById('talentxAccountTrigger');
    if(!button){
      button=document.createElement('button');
      button.id='talentxAccountTrigger';
      button.type='button';
      button.className='account-trigger';
      button.setAttribute('aria-haspopup','menu');
      button.onclick=togglePopover;
      actions.appendChild(button);
    }
    return button;
  }

  function updateTrigger(){
    const button=ensureTrigger();
    if(!button) return;
    if(!currentUser){
      button.className='account-trigger guest';
      button.innerHTML='<span class="account-trigger-copy"><strong>Log in</strong><small></small></span>';
      button.onclick=()=>{closePopover();if(typeof go==='function')go('login');};
      return;
    }
    button.className='account-trigger';
    button.onclick=togglePopover;
    const name=displayName();
    button.innerHTML=`<span class="account-trigger-avatar">${escHtml(initials(name))}</span><span class="account-trigger-copy"><strong>${escHtml(name)}</strong><small>${syncState==='error'?'Sync issue':syncState==='syncing'?'Syncing…':'Account'}</small></span>`;
  }

  function togglePopover(event){
    event?.stopPropagation?.();
    const existing=document.getElementById('talentxAccountPopover');
    if(existing){existing.remove();return;}
    if(!currentUser){if(typeof go==='function')go('login');return;}
    const button=ensureTrigger();
    if(!button) return;
    const rect=button.getBoundingClientRect();
    const popover=document.createElement('div');
    popover.id='talentxAccountPopover';
    popover.className='account-popover';
    popover.setAttribute('role','menu');
    popover.innerHTML=`
      <div class="account-popover-head">
        <strong>${escHtml(displayName())}</strong>
        <span>${escHtml(currentUser.email||'')}</span>
        <div class="account-sync-line"><i class="account-sync-dot ${syncState==='synced'?'':syncState}"></i><span>${escHtml(syncMessage)}</span></div>
      </div>
      <button class="account-menu-action" type="button" data-account-action="settings">Account settings <small>›</small></button>
      <button class="account-menu-action" type="button" data-account-action="sync">Sync now <small>↻</small></button>
      <button class="account-menu-action danger" type="button" data-account-action="logout">Log out</button>`;
    document.body.appendChild(popover);
    const width=popover.offsetWidth;
    const left=Math.min(window.innerWidth-width-12,Math.max(12,rect.right-width));
    popover.style.left=`${left}px`;
    popover.style.top=`${Math.min(window.innerHeight-popover.offsetHeight-12,rect.bottom+8)}px`;
    popover.querySelector('[data-account-action="settings"]')?.addEventListener('click',()=>{closePopover();openSettings();});
    popover.querySelector('[data-account-action="sync"]')?.addEventListener('click',()=>{closePopover();syncNow();});
    popover.querySelector('[data-account-action="logout"]')?.addEventListener('click',()=>{closePopover();logout();});
  }

  function openSettings(){
    if(!currentUser){if(typeof go==='function')go('login');return;}
    document.getElementById('talentxAccountOverlay')?.remove();
    const overlay=document.createElement('div');
    overlay.id='talentxAccountOverlay';
    overlay.className='account-overlay';
    overlay.innerHTML=`
      <section class="account-settings-card" role="dialog" aria-modal="true" aria-labelledby="accountSettingsTitle">
        <header class="account-settings-head"><div><h2 id="accountSettingsTitle">Account settings</h2><p>Manage your TalentX identity and cloud account.</p></div><button class="account-settings-close" type="button" aria-label="Close">×</button></header>
        <div class="account-settings-body">
          <section class="account-section"><h3>Profile</h3>
            <div class="account-field"><label for="accountDisplayName">Display name</label><input id="accountDisplayName" maxlength="60" value="${escHtml(displayName())}"></div>
            <div class="account-field"><label>Email</label><input value="${escHtml(currentUser.email||'')}" disabled></div>
            <div class="account-actions-row"><button class="account-action-btn primary" id="accountSaveProfile" type="button">Save changes</button></div>
          </section>
          <section class="account-section"><h3>Cloud sync</h3>
            <div class="account-sync-card"><div><strong>Your TalentX data</strong><span id="accountSettingsSyncStatus">${escHtml(syncMessage)}</span></div><i id="accountSettingsSyncDot" class="account-sync-dot ${syncState==='synced'?'':syncState}"></i></div>
            <div class="account-actions-row" style="margin-top:12px"><button class="account-action-btn" id="accountSyncNow" type="button">Sync now</button></div>
          </section>
          <section class="account-section"><h3>Security & session</h3><p class="account-security-copy">Your password is handled by Supabase Auth and is never stored in TalentX browser state. Logging out removes this browser session.</p><div class="account-actions-row"><button class="account-action-btn danger" id="accountLogout" type="button">Log out</button></div></section>
        </div>
      </section>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.account-settings-close')?.addEventListener('click',()=>overlay.remove());
    overlay.addEventListener('click',event=>{if(event.target===overlay) overlay.remove();});
    overlay.querySelector('#accountSaveProfile')?.addEventListener('click',saveProfile);
    overlay.querySelector('#accountSyncNow')?.addEventListener('click',syncNow);
    overlay.querySelector('#accountLogout')?.addEventListener('click',()=>{overlay.remove();logout();});
  }

  async function saveProfile(){
    if(!currentUser?.id||!window.talentxSupabase) return;
    const input=document.getElementById('accountDisplayName');
    const name=input?.value?.trim()||'';
    if(name.length<2){notify('Display name must be at least 2 characters.');return;}
    const button=document.getElementById('accountSaveProfile');
    if(button){button.disabled=true;button.textContent='Saving…';}
    try{
      const {error}=await window.talentxSupabase.from('profiles').update({display_name:name,updated_at:new Date().toISOString()}).eq('id',currentUser.id);
      if(error) throw error;
      const {error:userError}=await window.talentxSupabase.auth.updateUser({data:{display_name:name}});
      if(userError) console.warn('TalentX auth metadata name update skipped',userError);
      profile={...(profile||{}),display_name:name};
      notify('Account settings saved.');
      updateTrigger();
    }catch(error){
      console.warn('TalentX profile update failed',error);
      notify(error?.message||'Could not save account settings.');
    }finally{
      if(button){button.disabled=false;button.textContent='Save changes';}
    }
  }

  async function syncNow(){
    if(!currentUser) return;
    setSync('syncing','Syncing your TalentX data…');
    try{
      if(window.talentxAuthAdapter?.syncNow) await window.talentxAuthAdapter.syncNow();
      setSync('synced',`Synced just now`);
      notify('TalentX account synced.');
    }catch(error){
      console.warn('TalentX manual sync failed',error);
      setSync('error','Cloud sync needs attention');
      notify(error?.message||'Cloud sync could not finish.');
    }
  }

  async function logout(){
    try{
      if(window.talentxAuthAdapter?.logout) await window.talentxAuthAdapter.logout();
      else if(window.talentxSupabase) await window.talentxSupabase.auth.signOut();
      currentUser=null;
      profile=null;
      updateTrigger();
      notify('You are logged out.');
      if(typeof go==='function') go('dashboard');
      else window.location.href='./';
    }catch(error){
      console.warn('TalentX logout failed',error);
      notify(error?.message||'Could not log out.');
    }
  }

  async function bindSupabase(){
    const client=window.talentxSupabase;
    if(!client) return false;
    const {data}=await client.auth.getSession();
    currentUser=data.session?.user||null;
    if(currentUser) await loadProfile();
    updateTrigger();
    if(!authSubscription){
      const result=client.auth.onAuthStateChange(async(event,session)=>{
        currentUser=session?.user||null;
        if(currentUser) await loadProfile(); else profile=null;
        updateTrigger();
        closePopover();
        if(event==='SIGNED_IN') setSync('synced','Cloud sync ready');
      });
      authSubscription=result?.data?.subscription||true;
    }
    return true;
  }

  document.addEventListener('click',event=>{
    const popover=document.getElementById('talentxAccountPopover');
    if(popover&&!popover.contains(event.target)&&!document.getElementById('talentxAccountTrigger')?.contains(event.target)) closePopover();
  });
  window.addEventListener('resize',closePopover);
  window.talentxOpenAccountSettings=openSettings;
  window.talentxAccountSyncNow=syncNow;

  ensureTrigger();
  const timer=setInterval(async()=>{
    ensureTrigger();
    if(await bindSupabase()) clearInterval(timer);
  },250);
  setTimeout(()=>clearInterval(timer),15000);
})();
