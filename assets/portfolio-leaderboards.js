/* TalentX Phase 4: portfolio analytics and fair leaderboard preview. */
(function(){
  const STARTING_CASH=Number(window.TALENTX_STARTING_CASH||1000);
  const MIN_PORTFOLIO_VALUE=200;
  let perfRange='all';
  let boardRange='all';
  const ranges={day:86400000,week:7*86400000,month:30*86400000,all:Infinity};
  const escHtml=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#039;'}[m]));
  const pct=v=>`${v>=0?'+':''}${Number(v||0).toFixed(2)}%`;

  function transactions(){return Array.isArray(state.transactions)?state.transactions:[];}
  function costBasisById(){
    const lots={};
    [...transactions()].reverse().forEach(tx=>{
      const id=String(tx.id||''); if(!id)return;
      const shares=Math.max(0,Number(tx.shares)||0),price=Math.max(0,Number(tx.price)||0);
      const lot=lots[id]||(lots[id]={shares:0,cost:0});
      if(tx.mode==='buy'){lot.cost+=shares*price;lot.shares+=shares;}
      else if(tx.mode==='sell'&&lot.shares>0){const sold=Math.min(shares,lot.shares),avg=lot.cost/lot.shares;lot.cost-=sold*avg;lot.shares-=sold;}
    });
    return lots;
  }
  function portfolioSnapshot(){
    const lots=costBasisById();
    const rows=Object.entries(state.holdings||{}).map(([id,n])=>{
      const r=byId(id),shares=Number(n)||0;if(!r||shares<=0)return null;
      const price=localPrice(r),value=price*shares,lot=lots[id]||{shares,cost:price*shares};
      const avg=lot.shares>0?lot.cost/lot.shares:price,cost=avg*shares,gain=value-cost;
      return {r,shares,price,value,avg,cost,gain,returnPct:cost?gain/cost*100:0};
    }).filter(Boolean).sort((a,b)=>b.value-a.value);
    const invested=rows.reduce((s,x)=>s+x.value,0),total=invested+Number(state.cash||0),basis=rows.reduce((s,x)=>s+x.cost,0),gain=invested-basis;
    return {rows,invested,total,basis,gain,returnPct:basis?gain/basis*100:0,cash:Number(state.cash||0)};
  }
  function rangeStart(range){return ranges[range]===Infinity?0:Date.now()-ranges[range];}
  function performanceForRange(range){
    const start=rangeStart(range),txs=transactions().filter(t=>Number(t.time||0)>=start);
    let realized=0,buyCost=0,sellValue=0;
    txs.forEach(t=>{const total=(Number(t.price)||0)*(Number(t.shares)||0);if(t.mode==='buy')buyCost+=total;else sellValue+=total;});
    realized=sellValue-buyCost;
    const snap=portfolioSnapshot();
    const base=Math.max(1,snap.total-realized);
    return {dollar:realized,pct:realized/base*100,trades:txs.length};
  }
  function allocationHtml(snap){
    if(!snap.rows.length)return '<div class="p4-empty">Make a trade to see portfolio allocation.</div>';
    const total=Math.max(1,snap.invested);
    return `<div class="p4-allocation">${snap.rows.slice(0,8).map(x=>{const share=x.value/total*100;return `<button onclick="openProfile('${esc(x.r.id)}')"><span><b>${esc(x.r.ticker)}</b><small>${esc(x.r.name)}</small></span><span class="p4-bar"><i style="width:${Math.max(2,share)}%"></i></span><strong>${share.toFixed(1)}%</strong></button>`}).join('')}</div>`;
  }
  function holdingsHtml(snap){
    if(!snap.rows.length)return '<div class="card empty">No holdings yet. Buy talent from a player profile.</div>';
    return `<section class="card table-card p4-holdings"><div class="table-wrap"><table class="market-table"><thead><tr><th>Holding</th><th>Shares</th><th>Avg. cost</th><th>Price</th><th>Value</th><th>Gain / loss</th><th>Allocation</th></tr></thead><tbody>${snap.rows.map(x=>`<tr onclick="openProfile('${esc(x.r.id)}')"><td><b>${esc(x.r.name)}</b><small>${esc(x.r.ticker)} · ${esc(x.r.leagueOrMedium)}</small></td><td>${x.shares}</td><td>${money(x.avg)}</td><td>${money(x.price)}</td><td><b>${money(x.value)}</b></td><td class="${x.gain>=0?'positive':'negative'}">${x.gain>=0?'+':''}${money(x.gain)}<small>${pct(x.returnPct)}</small></td><td>${(x.value/Math.max(1,snap.invested)*100).toFixed(1)}%</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  window.setPortfolioRange=function(value){perfRange=value;render();};
  portfolio=function(){
    const s=portfolioSnapshot(),p=performanceForRange(perfRange);
    return `${note()}<div class="p4-title-row"><div><div class="eyebrow">Your virtual account</div><h1 class="page-title">Portfolio</h1><p class="page-sub">Performance is calculated from trades saved in this browser.</p></div><button class="btn secondary" onclick="go('leaderboard')">View leaderboard</button></div>
    <div class="p4-range-tabs">${[['day','1D'],['week','1W'],['month','1M'],['all','All']].map(([k,l])=>`<button class="${perfRange===k?'active':''}" onclick="setPortfolioRange('${k}')">${l}</button>`).join('')}</div>
    <div class="grid portfolio-stats p4-stats"><div class="card summary"><small>Total portfolio</small><strong>${money(s.total)}</strong></div><div class="card summary"><small>Range performance</small><strong class="${p.dollar>=0?'positive':'negative'}">${p.dollar>=0?'+':''}${money(p.dollar)}</strong><span>${pct(p.pct)} · ${p.trades} trade${p.trades===1?'':'s'}</span></div><div class="card summary"><small>Unrealized gain/loss</small><strong class="${s.gain>=0?'positive':'negative'}">${s.gain>=0?'+':''}${money(s.gain)}</strong><span>${pct(s.returnPct)}</span></div><div class="card summary"><small>Available cash</small><strong>${money(s.cash)}</strong></div></div>
    <div class="grid p4-dashboard"><section class="card"><div class="section-head"><h2>Allocation</h2><small>${money(s.invested)} invested</small></div>${allocationHtml(s)}</section><section class="card p4-health"><h2>Portfolio health</h2><div><span>Positions</span><strong>${s.rows.length}</strong></div><div><span>Largest position</span><strong>${s.rows.length?(s.rows[0].value/Math.max(1,s.invested)*100).toFixed(1)+'%':'—'}</strong></div><div><span>Cash reserve</span><strong>${(s.cash/Math.max(1,s.total)*100).toFixed(1)}%</strong></div><p>${s.rows.length<3?'A more diversified portfolio may reduce dependence on one player.':s.rows[0]&&s.rows[0].value/s.invested>.5?'More than half of your invested value is in one player.':'Your portfolio is reasonably spread across holdings.'}</p></section></div>
    ${holdingsHtml(s)}${typeof window.talentxTransactionHistoryHtml==='function'?window.talentxTransactionHistoryHtml():''}`;
  };

  const demoUsers=[
    ['MarketMaven',1273.6,14,0.61],['RookieRadar',1188.4,21,0.73],['CourtVision',1139.6,17,0.66],['DiamondHands',1086,12,0.58],['TalentScout',1055.2,19,0.69],['UpsideOnly',1007.6,24,0.76],['ValueHunter',989.6,16,0.64]
  ];
  function leaderboardRows(range){
    const factor={day:.08,week:.28,month:.62,all:1}[range]||1;
    const user=portfolioSnapshot();
    const userTrades=transactions().filter(t=>Number(t.time||0)>=rangeStart(range)).length;
    const eligible=userTrades>=2&&user.total>=MIN_PORTFOLIO_VALUE;
    const rows=demoUsers.map(([name,total,trades,score],i)=>({name,total,returnPct:((total-STARTING_CASH)/STARTING_CASH*100)*factor,trades,score,demo:true}));
    rows.push({name:'You',total:user.total,returnPct:((user.total-STARTING_CASH)/STARTING_CASH*100)*factor,trades:userTrades,score:Math.min(.95,.5+Math.min(20,userTrades)*.015),you:true,eligible});
    return rows.filter(x=>!x.you||x.eligible).sort((a,b)=>b.returnPct-a.returnPct||b.score-a.score).map((x,i)=>({...x,rank:i+1}));
  }
  window.setLeaderboardRange=function(value){boardRange=value;render();};
  window.leaderboard=function(){
    const rows=leaderboardRows(boardRange),you=rows.find(x=>x.you),user=portfolioSnapshot();
    const eligible=transactions().filter(t=>Number(t.time||0)>=rangeStart(boardRange)).length>=2&&user.total>=MIN_PORTFOLIO_VALUE;
    return `${note()}<div class="eyebrow">Community competition</div><h1 class="page-title">Leaderboard</h1><p class="page-sub">This static prototype uses sample competitors. Your real browser portfolio is inserted when eligible; shared multi-user rankings require account and backend support.</p>
    <div class="p4-range-tabs">${[['day','Daily'],['week','Weekly'],['month','Monthly'],['all','All time']].map(([k,l])=>`<button class="${boardRange===k?'active':''}" onclick="setLeaderboardRange('${k}')">${l}</button>`).join('')}</div>
    <section class="card p4-rules"><strong>Fair ranking rules</strong><span>Ranked by percentage return, not total dollars.</span><span>Minimum 2 completed trades and $200 portfolio value.</span><span>Duplicate or reversed rapid trades do not improve eligibility.</span></section>
    ${!eligible?`<div class="notice"><strong>You are not ranked yet.</strong> Complete at least two trades in this period while maintaining a portfolio value of $200 or more.</div>`:''}
    <section class="card table-card"><div class="table-wrap"><table class="market-table p4-board"><thead><tr><th>Rank</th><th>Trader</th><th>Return</th><th>Portfolio</th><th>Trades</th><th>Status</th></tr></thead><tbody>${rows.map(x=>`<tr class="${x.you?'p4-you':''}"><td><b>${x.rank<=3?['🥇','🥈','🥉'][x.rank-1]:x.rank}</b></td><td><b>${escHtml(x.name)}</b>${x.demo?'<small>Sample competitor</small>':'<small>Your browser portfolio</small>'}</td><td class="${x.returnPct>=0?'positive':'negative'}"><b>${pct(x.returnPct)}</b></td><td>${money(x.total)}</td><td>${x.trades}</td><td><span class="quality-badge">Eligible</span></td></tr>`).join('')}</tbody></table></div></section>
    ${you?`<div class="card p4-your-rank"><span>Your current rank</span><strong>#${you.rank}</strong><small>${pct(you.returnPct)} for this period</small></div>`:''}`;
  };

  const oldGo=go;
  go=function(next){if(next==='leaderboard'){route='leaderboard';selectedId=null;profileTab='overview';setActiveNav();render();return;}oldGo(next);};
  const oldRender=render;
  render=function(){if(route==='leaderboard'){const app=document.querySelector('#app');setActiveNav();app.innerHTML=leaderboard();bindAfterRender();return;}oldRender();};
  const oldSetActiveNav=setActiveNav;
  setActiveNav=function(){oldSetActiveNav();document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.route===route));};
})();