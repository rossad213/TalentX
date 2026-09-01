/* TalentX account confirmation recovery UI. */
(() => {
  const priorAuthPage=typeof window.authPage==='function'?window.authPage:null;
  if(!priorAuthPage) return;

  window.authPage=function(mode){
    let html=priorAuthPage(mode);
    html=html
      .replace('Accounts will sync your virtual portfolio, watchlist, transactions, preferences, and leaderboard identity across devices.','Your TalentX account securely syncs your virtual portfolio, watchlist, transactions, preferences, and leaderboard identity across devices.')
      .replace('Sign in will restore your synced TalentX portfolio, watchlist, virtual balance, and market activity once the account backend is connected.','Sign in restores your synced TalentX portfolio, watchlist, virtual balance, and market activity across devices.')
      .replace('Set up the account that will hold your synced TalentX experience.','Create your synced TalentX account.')
      .replace('<div class="auth-note"><strong>Account infrastructure is being prepared.</strong> This form is wired for the future auth adapter, but credentials are not currently submitted or stored.</div>', mode==='signup'
        ? '<div class="auth-note"><strong>Didn’t get a working confirmation link?</strong> Enter your email above, then request a fresh link. <button id="authResendConfirmation" class="auth-link" type="button" onclick="resendTalentxConfirmation()" style="display:block;margin-top:8px">Resend confirmation email</button></div>'
        : '<div class="auth-note"><strong>Secure account access.</strong> TalentX uses Supabase authentication and never stores your password in TalentX portfolio state.</div>');
    return html;
  };

  if(typeof route!=='undefined'&&(route==='signup'||route==='login')){
    try{render();}catch{}
  }
})();