/* TalentX Phase 3: trade ticket, confirmations, portfolio P/L, and transaction history. */
(function(){
  const fmt=(n)=>money(Number(n||0));
  const escHtml=(v)=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
  const VERIFIED_TRADE_ENDPOINT='https://selifenorvjodihiaexw.supabase.co/functions/v1/execute-trade';
  const PUBLISHABLE_KEY='sb_publishable_kHtyJKFgJHZy4kRaL5XJ6Q_gqEphAtY';
  let pendingTrade=null;

  function transactionLots(id){
    let shares=0,cost=0;
    [...(state.transactions||[])].reverse().filter(t=>t.id===id).forEach(t=>{
      const qty=Number(t.shares||0),price=Number(t.price||0);
      if(t.mode==='buy'){
        cost+=qty*price; shares+=qty;
      }else if(shares>0){
        const sold=Math.min(shares,qty),avg=cost/shares;
        shares-=sold; cost-=avg*sold;
      }
    });
    return {shares,cost,average:shares>0?cost/shares:0};
  }

  function quote(record,mode,shares){
    const n=Math.max(1,Math.floor(Number(shares)||1));
    const price=localPrice(record);
    const impact=Math.min(.025,n/(300+Math.sqrt(Math.max(1,record.volume||1)))*.2);
    const execution=price*(1+(mode==='buy'?impact:-impact)/2);
    const total=execution*n;
    return {n,price,impact,execution,total};
  }

  function closeTradeLayer(){document.querySelector('.trade-overlay')?.remove();pendingTrade=null;}
  window.closeTradeTicket=closeTradeLayer;
  window.adjustTradeShares=function(delta){
    const input=document.querySelector('#ticketShares');if(!input)return;
    input.value=Math.max(1,Math.floor(Number(input.value||1)+Number(delta||0)));
    window.updateTradeTicket();
  };
  window.updateTradeTicket=function(){
    if(!pendingTrade)return;
    const input=document.querySelector('#ticketShares'),record=byId(pendingTrade.id);if(!input||!record)return;
    const q=quote(record,pendingTrade.mode,input.value);input.value=q.n;
    const owned=Number(state.holdings[record.id]||0);
    const invalid=pendingTrade.mode==='buy'?q.total>state.cash:q.n>owned;
    const set=(sel,val)=>{const el=document.querySelector(sel);if(el)el.textContent=val;};
    set('[data-ticket-exec]',fmt(q.execution));
    set('[data-ticket-impact]',`${(q.impact*100).toFixed(3)}%`);
    set('[data-ticket-total]',fmt(q.total));
    set('[data-ticket-after]',pendingTrade.mode==='buy'?fmt(state.cash-q.total):`${Math.max(0,owned-q.n)} shares`);
    const btn=document.querySelector('[data-confirm-trade]');
    if(btn){btn.disabled=invalid;btn.textContent=invalid?(pendingTrade.mode==='buy'?'Not enough cash':'Not enough shares'):`Confirm ${pendingTrade.mode}`;}
  };

  window.openTradeTicket=function(id,mode){
    const record=byId(id);if(!record)return;
    closeTradeLayer();pendingTrade={id,mode:mode||tradeMode};
    const owned=Number(state.holdings[id]||0),q=quote(record,pendingTrade.mode,1);
    const overlay=document.createElement('div');overlay.className='trade-overlay';
    overlay.innerHTML=`<section class="trade-ticket" role="dialog" aria-modal="true" aria-label="${pendingTrade.mode==='buy'?'Buy':'Sell'} ${escHtml(record.name)}"><div class="ticket-head"><div><small>${pendingTrade.mode==='buy'?'Buy':'Sell'} order</small><h2>${escHtml(record.name)} <span>${escHtml(record.ticker)}</span></h2></div><button onclick="closeTradeTicket()" aria-label="Close">×</button></div><div class="ticket-price"><span>Current price</span><strong>${fmt(q.price)}</strong></div><div class="ticket-owned">You own <strong>${owned}</strong> shares · Available cash <strong>${fmt(state.cash)}</strong></div><label class="ticket-label">Shares</label><div class="share-stepper"><button onclick="adjustTradeShares(-1)">−</button><input id="ticketShares" type="number" min="1" step="1" value="1" oninput="updateTradeTicket()"><button onclick="adjustTradeShares(1)">+</button></div><div class="ticket-summary"><div><span>Estimated execution price</span><strong data-ticket-exec>${fmt(q.execution)}</strong></div><div><span>Estimated market impact</span><strong data-ticket-impact>${(q.impact*100).toFixed(3)}%</strong></div><div class="ticket-total"><span>${pendingTrade.mode==='buy'?'Estimated total':'Estimated proceeds'}</span><strong data-ticket-total>${fmt(q.total)}</strong></div><div><span>${pendingTrade.mode==='buy'?'Cash after trade':'Shares after trade'}</span><strong data-ticket-after>${pendingTrade.mode==='buy'?fmt(state.cash-q.total):`${Math.max(0,owned-1)} shares`}</strong></div></div><div class="ticket-actions"><button class="btn ghost" onclick="closeTradeTicket()">Cancel</button><button class="btn ${pendingTrade.mode==='buy'?'primary':'danger'}" data-confirm-trade onclick="confirmTradeTicket()">Confirm ${pendingTrade.mode}</button></div><p class="ticket-note">Virtual trade only. Signed-in trades are verified by TalentX before cash or holdings change.</p></section>`;
    overlay.addEventListener('click',e=>{if(e.target===overlay)closeTradeLayer();});document.body.appendChild(overlay);window.updateTradeTicket();
  };

  async function executeSignedInTrade(record,mode,shares){
    const sessionResult=await window.talentxSupabase?.auth?.getSession?.();
    const accessToken=sessionResult?.data?.session?.access_token;
    if(!accessToken) throw new Error('Your login session expired. Please log in again.');
    const clientEventId=(globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`);
    const response=await fetch(VERIFIED_TRADE_ENDPOINT,{
      method:'POST',
      headers:{
        'Content-Type':'application/json',
        'Authorization':`Bearer ${accessToken}`,
        'apikey':PUBLISHABLE_KEY
      },
      body:JSON.stringify({talentId:record.id,side:mode,shares,clientEventId})
    });
    const data=await response.json().catch(()=>({}));
    if(!response.ok||!data.ok) throw new Error(data.error||'Trade could not be completed.');
    if(window.talentxAuthAdapter?.refreshAccount) await window.talentxAuthAdapter.refreshAccount();
    return {
      n:Number(data.shares||shares),
      price:Number(data.marketPrice||record.marketPrice||0),
      impact:Number(data.impact||0),
      execution:Number(data.executionPrice||0),
      total:Number(data.total||0)
    };
  }

  window.confirmTradeTicket=async function(){
    if(!pendingTrade)return;
    const record=byId(pendingTrade.id),input=document.querySelector('#ticketShares');if(!record||!input)return;
    const requestedMode=pendingTrade.mode;
    const q=quote(record,requestedMode,input.value),owned=Number(state.holdings[record.id]||0),cashBefore=state.cash;
    if(requestedMode==='buy'&&q.total>state.cash){toast('Not enough virtual cash');return;}
    if(requestedMode==='sell'&&q.n>owned){toast('You do not own that many shares');return;}

    if(window.__talentxAuthUser?.id){
      const btn=document.querySelector('[data-confirm-trade]');
      const oldText=btn?.textContent;
      if(btn){btn.disabled=true;btn.textContent='Verifying trade…';}
      try{
        const verified=await executeSignedInTrade(record,requestedMode,q.n);
        closeTradeLayer();
        showTradeConfirmation({record,mode:requestedMode,q:verified});
        if(typeof render==='function') render();
      }catch(err){
        toast(err?.message||'Trade could not be completed.');
        if(btn){btn.disabled=false;btn.textContent=oldText||`Confirm ${requestedMode}`;}
      }
      return;
    }

    // Guest mode remains a local prototype. Real-money TX Cash purchases require an account.
    if(requestedMode==='buy'){
      state.cash-=q.total;state.holdings[record.id]=owned+q.n;state.prices[record.id]=q.price*(1+q.impact);
    }else{
      state.cash+=q.total;state.holdings[record.id]=owned-q.n;if(state.holdings[record.id]<=0)delete state.holdings[record.id];state.prices[record.id]=Math.max(1,q.price*(1-q.impact));
    }
    state.transactions.unshift({id:record.id,mode:requestedMode,shares:q.n,price:q.execution,total:q.total,time:Date.now(),cashBefore,cashAfter:state.cash});
    state.transactions=state.transactions.slice(0,200);saveState();
    closeTradeLayer();showTradeConfirmation({record,mode:requestedMode,q});render();
  };

  function showTradeConfirmation({record,mode,q}){
    const totals=portfolioTotals();
    const overlay=document.createElement('div');overlay.className='trade-overlay confirmation-overlay';
    overlay.innerHTML=`<section class="trade-ticket trade-confirm" role="dialog" aria-modal="true"><div class="confirm-icon">✓</div><h2>${mode==='buy'?'Purchase':'Sale'} complete</h2><p>You ${mode==='buy'?'bought':'sold'} <strong>${q.n} share${q.n===1?'':'s'}</strong> of ${escHtml(record.name)}.</p><div class="ticket-summary"><div><span>Average execution price</span><strong>${fmt(q.execution)}</strong></div><div><span>${mode==='buy'?'Total cost':'Total proceeds'}</span><strong>${fmt(q.total)}</strong></div><div><span>Available cash</span><strong>${fmt(state.cash)}</strong></div><div><span>Portfolio value</span><strong>${fmt(totals.total)}</strong></div></div><div class="ticket-actions"><button class="btn ghost" onclick="this.closest('.trade-overlay').remove()">Done</button><button class="btn primary" onclick="this.closest('.trade-overlay').remove();go('portfolio')">View portfolio</button></div></section>`;
    document.body.appendChild(overlay);
  }

  executeTrade=function(id){openTradeTicket(id,tradeMode);};

  const baseProfile=profile;
  profile=function(){
    let html=baseProfile();
    const r=byId(selectedId);if(!r)return html;
    html=html.replace(`onclick="executeTrade('${esc(r.id)}')"`,`onclick="openTradeTicket('${esc(r.id)}','${tradeMode}')"`);
    return html;
  };

  portfolio=function(){
    const totals=portfolioTotals();
    const entries=Object.entries(state.holdings).map(([id,n])=>[byId(id),Number(n)]).filter(([r,n])=>r&&n>0).sort((a,b)=>localPrice(b[0])*b[1]-localPrice(a[0])*a[1]);
    const rows=entries.map(([r,n])=>{
      const lot=transactionLots(r.id),value=localPrice(r)*n,cost=lot.average*n,gain=value-cost,pct=cost?gain/cost*100:0;
      return `<tr onclick="openProfile('${esc(r.id)}')"><td><div class="name-cell"><div class="small-avatar">${esc(r.avatar)}</div><div><b>${esc(r.name)}</b><small>${esc(r.ticker)} · ${esc(r.discipline)}</small></div></div></td><td>${n}</td><td>${fmt(lot.average)}</td><td>${fmt(localPrice(r))}</td><td><b>${fmt(value)}</b></td><td class="${gain>=0?'positive':'negative'}">${gain>=0?'+':'-'}${fmt(Math.abs(gain))}<small>${pct>=0?'+':''}${pct.toFixed(2)}%</small></td></tr>`;
    }).join('');
    const history=(state.transactions||[]).slice(0,30).map(t=>{const r=byId(t.id);if(!r)return '';const total=Number(t.total||Number(t.price||0)*Number(t.shares||0));return `<div class="transaction-row"><div class="transaction-icon ${t.mode}">${t.mode==='buy'?'↓':'↑'}</div><div><strong>${t.mode==='buy'?'Bought':'Sold'} ${Number(t.shares)} ${esc(r.ticker)}</strong><small>${new Date(t.time).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</small></div><div><strong>${fmt(total)}</strong><small>${fmt(t.price)} each</small></div></div>`;}).join('');
    const storageCopy=window.__talentxAuthUser?.id?'Signed-in cash, holdings and trades are secured to your TalentX account.':'Guest holdings are saved only in this browser.';
    return `${note()}<div class="eyebrow">Your virtual account</div><h1 class="page-title">Portfolio</h1><p class="page-sub">${storageCopy}</p><div class="grid portfolio-stats"><div class="card summary"><small>Total portfolio</small><strong>${fmt(totals.total)}</strong></div><div class="card summary"><small>Invested value</small><strong>${fmt(totals.holdings)}</strong></div><div class="card summary"><small>Available cash</small><strong class="positive">${fmt(state.cash)}</strong></div></div><section class="card table-card section"><div class="section-head"><h2>Holdings</h2><small>${entries.length} position${entries.length===1?'':'s'}</small></div>${entries.length?`<div class="table-wrap"><table class="market-table"><thead><tr><th>Holding</th><th>Shares</th><th>Average cost</th><th>Current price</th><th>Market value</th><th>Gain / loss</th></tr></thead><tbody>${rows}</tbody></table></div>`:`<div class="empty">No holdings yet. Open a player profile and place a virtual buy order.</div>`}</section><section class="card section transaction-card"><div class="section-head"><h2>Transaction history</h2><small>Most recent 30 trades</small></div>${history||'<div class="empty">No transactions yet.</div>'}</section>`;
  };

  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTradeLayer();});
})();
