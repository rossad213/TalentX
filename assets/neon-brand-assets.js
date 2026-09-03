/* TalentX neon brand asset switch. */
(() => {
  const LOGO='./assets/talentx-logo-64.png?v=20260903-1';
  const FAVICON='./assets/talentx-favicon-32.png?v=20260903-1';

  function applyFavicons(){
    document.querySelectorAll('link[rel="icon"],link[rel="shortcut icon"]').forEach(link=>{
      link.setAttribute('href',FAVICON);
      link.setAttribute('type','image/png');
    });
  }

  function applyLogos(root=document){
    root.querySelectorAll?.('img').forEach(img=>{
      const src=img.getAttribute('src')||'';
      if(src.includes('talentx-logo-64.png')||src.includes('talentx-neon-logo-64.png')||src.includes('talentx-neon-brand-v2.png')||img.classList.contains('brand-logo')||img.classList.contains('user-avatar-logo')||img.closest?.('.public-brand,.public-footer-brand,.auth-back-brand')){
        img.setAttribute('src',LOGO);
      }
    });
  }

  function bindWelcomeBrandClicks(){
    document.addEventListener('click',event=>{
      const brand=event.target.closest?.('.brand,.public-brand,.auth-back-brand');
      if(!brand) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if(typeof go==='function') go('welcome');
    },true);
  }

  applyFavicons();
  applyLogos();
  bindWelcomeBrandClicks();

  const observer=new MutationObserver(records=>{
    for(const record of records){
      for(const node of record.addedNodes){
        if(!(node instanceof Element)) continue;
        if(node.matches?.('img')) applyLogos(node.parentElement||document);
        else applyLogos(node);
      }
    }
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});

  window.talentxBrandAssets={logo:LOGO,favicon:FAVICON};
})();
