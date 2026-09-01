/* Route TalentX brand/logo clicks to the public welcome page. */
(() => {
  function isBrandTarget(target){
    return target?.closest?.('.brand,.public-brand,.auth-back-brand,.public-footer-brand');
  }
  document.addEventListener('click',event=>{
    const brand=isBrandTarget(event.target);
    if(!brand) return;
    event.preventDefault();
    event.stopPropagation();
    if(typeof window.talentxGoWelcome==='function') window.talentxGoWelcome();
    else if(typeof go==='function') go('welcome');
  },true);
  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'&&event.key!==' ') return;
    const brand=isBrandTarget(event.target);
    if(!brand) return;
    event.preventDefault();
    if(typeof window.talentxGoWelcome==='function') window.talentxGoWelcome();
    else if(typeof go==='function') go('welcome');
  },true);
})();