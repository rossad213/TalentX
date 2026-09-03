/* Auth back navigation is rendered by public-home.js.
 * Keep a dashboard fallback if account assets load independently.
 */
(() => {
  if(typeof window.talentxAuthBack!=='function'){
    window.talentxAuthBack=()=>{ if(typeof go==='function') go('dashboard'); };
  }
})();
