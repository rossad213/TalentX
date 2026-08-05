/* TalentX leaderboard privacy: users are private and unranked until they explicitly opt in. */
(function(){
  const CONSENT_KEY='talentxLeaderboardConsentV1';
  const NAME_KEY='talentxLeaderboardNameV1';
  let boardRange='all';
  const STARTING_CASH=25000;
  const ranges={day:86400000,week:7*86400000,month:30*86400000,all:Infinity};
  const demoUsers=[
    ['MarketMaven',31840,14,0.61],['RookieRadar',29710,21,0.73],['CourtVision',28490,17,0.66],['DiamondHands',27150,12,0.58],['TalentScout',26380,19,0.69],['UpsideOnly',25190,24,0.76],['ValueHunter',24740,16,0.64]
  ];
  const escHtml=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#039;'}[m]));
  const pct=v=>`${v>=0?'+':''}${Number(v||0).toFixed(2)}%`;
  const consented=()=>localStorage.getItem(CONSENT_KEY)==='yes';
  const displayName=()=>localStorage.getItem(NAME_KEY)||'You';
  const rangeStart=range=>ranges[range]===Infinity?0:Date.now()-ranges[range];
  const transactions=()=>Array.isArray(state.transactions)?state.transactions:[];
  function portfolioTotal(){
    let holdings=0;
    for(const [id,n] of Object.entries(state.holdings||{})){
      const r=byId(id);if(r)holdings+=localPrice(r)*(Number(n)||0);
    }
    return holdings+Number(state.cash||0);
  }
  function eligibility(range){
    const trades=transactions().filter(t=>Number(t.time||0)>=rangeStart(range)).length;
    const total=portfolioTotal();
    return {trades,total,eligible:trades>=2&&total>=5000};
  }
  function rowsFor(range){
    const factor={day:.08,week:.28,month:.62,all:1}[range]||1;
    const rows=demoUsers.map(([name,total,trades,score])=>({name,total,returnPct:((total-STARTING_CASH)/STARTING_CASH*100)*factor,trades,score,demo:true}));
    const status=eligibility(range);
    if(consented()&&status.eligible){
      rows.push({name:displayName(),total:status.total,returnPct:((status.total-STARTING_CASH)/STARTING_CASH*100)*factor,trades:status.trades,score:Math.min(.95,.5+Math.min(20,status.trades)*.015),you:true});
    }
    return rows.sort((a,b)=>b.returnPct-a.returnPct||b.score-a.score).map((x,i)=>({...x,rank:i+1}));
  }
  window.setLeaderboardRange=function(value){boardRange=value;render();};
  window.joinTalentXLeaderboard=function(){
    const status=eligibility(boardRange);
    if(!status.eligible){toast('Complete two trades and maintain at least $5,000 before joining');return;}
    const entered=window.prompt('Choose the public display name shown on the leaderboard:',displayName());
    if(entered===null)return;
    const cleaned=entered.trim().slice(0,24);
    if(!cleaned){toast('Enter a display name to join');return;}
    localStorage.setItem(NAME_KEY,cleaned);
    localStorage.setItem(CONSENT_KEY,'yes');
    toast('Leaderboard participation enabled');render();
  };
  window.leaveTalentXLeaderboard=function(){
    localStorage.removeItem(CONSENT_KEY);
    toast('You are now private and unranked');render();
  };
  window.leaderboard=function(){
    const rows=rowsFor(boardRange),status=eligibility(boardRange),joined=consented();
    const you=rows.find(x=>x.you);
    return `${note()}<div class="eyebrow">Community competition</div><h1 class="page-title">Leaderboard</h1><p class="page-sub">Participation is optional. Your portfolio and ranking remain private unless you explicitly join.</p>
    <div class="p4-range-tabs">${[['day','Daily'],['week','Weekly'],['month','Monthly'],['all','All time']].map(([k,l])=>`<button class="${boardRange===k?'active':''}" onclick="setLeaderboardRange('${k}')">${l}</button>`).join('')}</div>
    <section class="card p4-rules"><strong>Privacy and fair ranking</strong><span>Users are private and unranked by default.</span><span>Joining requires explicit permission and a chosen public display name.</span><span>You can leave the leaderboard at any time.</span><span>Ranked by percentage return with at least 2 trades and $5,000 portfolio value.</span></section>
    ${!joined?`<div class="notice"><strong>You are not sharing your ranking.</strong> TalentX will not place your portfolio on the leaderboard without your permission.<div style="margin-top:12px"><button class="btn primary" onclick="joinTalentXLeaderboard()" ${status.eligible?'':'disabled'}>Join leaderboard</button></div>${!status.eligible?`<small style="display:block;margin-top:9px">Eligibility needed: ${status.trades}/2 trades · ${money(status.total)} portfolio value.</small>`:''}</div>`:`<div class="notice"><strong>Leaderboard sharing is on.</strong> Your public name is <b>${escHtml(displayName())}</b>. Only ranking data is shown in this prototype.<div style="margin-top:12px"><button class="btn secondary" onclick="leaveTalentXLeaderboard()">Leave leaderboard</button></div></div>`}
    <section class="card table-card"><div class="table-wrap"><table class="market-table p4-board"><thead><tr><th>Rank</th><th>Trader</th><th>Return</th><th>Portfolio</th><th>Trades</th><th>Status</th></tr></thead><tbody>${rows.map(x=>`<tr class="${x.you?'p4-you':''}"><td><b>${x.rank<=3?['🥇','🥈','🥉'][x.rank-1]:x.rank}</b></td><td><b>${escHtml(x.name)}</b>${x.demo?'<small>Sample competitor</small>':'<small>Shared with permission</small>'}</td><td class="${x.returnPct>=0?'positive':'negative'}"><b>${pct(x.returnPct)}</b></td><td>${money(x.total)}</td><td>${x.trades}</td><td><span class="quality-badge">Eligible</span></td></tr>`).join('')}</tbody></table></div></section>
    ${you?`<div class="card p4-your-rank"><span>Your current rank</span><strong>#${you.rank}</strong><small>${pct(you.returnPct)} for this period</small></div>`:''}`;
  };
})();