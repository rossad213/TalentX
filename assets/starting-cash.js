/* TalentX starting virtual cash migration: pristine $25,000 accounts move to $1,000. */
(() => {
  const STARTING_CASH=1000;
  const LEGACY_STARTING_CASH=25000;
  window.TALENTX_STARTING_CASH=STARTING_CASH;

  function isPristineAccount(){
    try{
      return Object.keys(state?.holdings||{}).length===0 &&
        (state?.transactions||[]).length===0;
    }catch{return false;}
  }

  function applyStartingCash(){
    try{
      if(typeof defaultState==='object'&&defaultState) defaultState.cash=STARTING_CASH;
    }catch{}
    try{
      if(typeof state==='object'&&state&&Number(state.cash)===LEGACY_STARTING_CASH&&isPristineAccount()){
        state.cash=STARTING_CASH;
      }
    }catch{}
  }

  applyStartingCash();

  if(typeof saveState==='function'){
    const originalSaveState=saveState;
    saveState=function(){
      applyStartingCash();
      return originalSaveState.apply(this,arguments);
    };
    saveState();
  }
})();
