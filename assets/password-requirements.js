/* TalentX signup password requirements and validation. */
(() => {
  const RULES = [
    {key:'length', label:'At least 8 characters', test:value=>value.length>=8},
    {key:'lower', label:'One lowercase letter', test:value=>/[a-z]/.test(value)},
    {key:'upper', label:'One uppercase letter', test:value=>/[A-Z]/.test(value)},
    {key:'digit', label:'One number', test:value=>/\d/.test(value)},
    {key:'symbol', label:'One symbol', test:value=>/[^A-Za-z0-9]/.test(value)}
  ];

  function ensureStyles(){
    if(document.getElementById('talentxPasswordRulesStyle')) return;
    const style=document.createElement('style');
    style.id='talentxPasswordRulesStyle';
    style.textContent=`
      .auth-password-rules{margin:-4px 0 15px;padding:12px 13px;border:1px solid rgba(143,174,200,.15);border-radius:11px;background:#081521}
      .auth-password-rules strong{display:block;margin-bottom:8px;color:#cbd8e2;font-size:11px}
      .auth-password-rule{display:flex;align-items:center;gap:8px;margin-top:6px;color:#7f92a3;font-size:10px;line-height:1.35}
      .auth-password-rule:before{content:'○';color:#667b8d;font-weight:900}
      .auth-password-rule.met{color:#a9bdca}.auth-password-rule.met:before{content:'✓';color:#58ef78}
    `;
    document.head.appendChild(style);
  }

  function validatePassword(value){
    return RULES.every(rule=>rule.test(value));
  }

  function updateChecklist(){
    const input=document.getElementById('authPassword');
    const box=document.getElementById('authPasswordRequirements');
    if(!input||!box) return;
    const value=input.value||'';
    RULES.forEach(rule=>{
      const row=box.querySelector(`[data-password-rule="${rule.key}"]`);
      if(row) row.classList.toggle('met',rule.test(value));
    });
  }

  function injectChecklist(){
    ensureStyles();
    const input=document.getElementById('authPassword');
    const confirm=document.getElementById('authConfirm');
    if(!input||!confirm||document.getElementById('authPasswordRequirements')) return;
    const field=input.closest('.auth-field');
    if(!field) return;
    const box=document.createElement('div');
    box.id='authPasswordRequirements';
    box.className='auth-password-rules';
    box.innerHTML=`<strong>Password requirements</strong>${RULES.map(rule=>`<div class="auth-password-rule" data-password-rule="${rule.key}">${rule.label}</div>`).join('')}`;
    field.insertAdjacentElement('afterend',box);
    input.addEventListener('input',updateChecklist);
    updateChecklist();
  }

  const originalAuthPage=window.authPage;
  if(typeof originalAuthPage==='function'){
    window.authPage=function(mode){
      const html=originalAuthPage.apply(this,arguments);
      if(mode==='signup') setTimeout(injectChecklist,0);
      return html;
    };
  }

  const originalSubmit=window.submitTalentxAuth;
  if(typeof originalSubmit==='function'){
    window.submitTalentxAuth=async function(mode){
      if(mode==='signup'){
        const password=document.getElementById('authPassword')?.value||'';
        if(!validatePassword(password)){
          if(typeof toast==='function') toast('Password must be at least 8 characters and include lowercase, uppercase, a number, and a symbol.');
          return;
        }
      }
      return originalSubmit.apply(this,arguments);
    };
  }

  const observer=new MutationObserver(()=>injectChecklist());
  observer.observe(document.documentElement,{subtree:true,childList:true});
  injectChecklist();
})();
