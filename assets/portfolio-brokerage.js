/* TalentX brokerage-style portfolio dashboard. */
(function(){
  if(window.__talentxPortfolioBrokerageV1)return;
  window.__talentxPortfolioBrokerageV1=true;

  const STARTING_CASH=Number(window.TALENTX_STARTING_CASH||1000);
  const RANGE_MAP={day:'1D',week:'1W',month:'1M',quarter:'3M',year:'1Y'};
  const RANGE_MS={day:24*60*60*1000,week:7*24*60*60*1000,month:30*24*60*60*1000,quarter:91*24*60*60*1000,year:365*24*60*60*1000};
  let brokerRange='month';

  const html=value=>String(value??'').replace(/[&<>"']/g,char=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[char]));
  const num=(value,fallback=0)=>{const parsed=Number(value);return Number.isFinite(parsed)?parsed:fallback;};
  const cash=value=>typeof money==='function'?money(num(value)):new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(num(value));
  const percent=value=>`${num(value)>=0?'+':''}${num(value).toFixed(2)}%`;
  const transactions=()=>Array.isArray(state?.transactions)?state.transactions:[];
  const contributedCapital=()=>STARTING_CASH+Math.max(0,num(state?.purchasedCashTotal));

  function costBasisById(){
    const lots={};
    [...transactions()].sort((a,b)=>num(a.time)-num(b.time)).forEach(tx=>{
      const id=String(tx.id||'');if(!id)return;
      const shares=Math.max(0,num(tx.shares)),price=Math.max(0,num(tx.price));
      const lot=lots[id]||(lots[id]={shares:0,cost:0});
      if(tx.mode==='buy'){
        lot.cost+=shares*price;lot.shares+=shares;
      }else if(tx.mode==='sell'&&lot.shares>0){
        const sold=Math.min(shares,lot.shares),average=lot.cost/lot.shares;
        lot.shares-=sold;lot.cost-=sold*average;
      }
    });
    return lots;
  }

  function snapshot(){
    const lots=costBasisById();
    const rows=Object.entries(state?.holdings||{}).map(([id,quantity])=>{
      const record=byId(id),shares=num(quantity);if(!record||shares<=0)return null;
      const price=num(localPrice(record)),value=price*shares,lot=lots[id]||{shares,cost:price*shares};
      const average=lot.shares>0?lot.cost/lot.shares:price,costValue=average*shares,gain=value-costValue;
      return {record,shares,price,value,average,cost:costValue,gain,returnPct:costValue?gain/costValue*100:0};
    }).filter(Boolean).sort((a,b)=>b.value-a.value);
    const invested=rows.reduce((sum,row)=>sum+row.value,0);
    const basis=rows.reduce((sum,row)=>sum+row.cost,0);
    const accountCash=num(state?.cash);
    const total=invested+accountCash;
    const capital=Math.max(1,contributedCapital());
    const totalReturn=total-capital;
    return {rows,invested,basis,cash:accountCash,total,capital,totalReturn,totalReturnPct:totalReturn/capital*100};
  }

  function priceSeries(record,rangeKey){
    const range=RANGE_MAP[rangeKey]||'1M';
    try{
      const series=typeof chartSeries==='function'?chartSeries(record,range):[];
      if(Array.isArray(series)&&series.length) return series.map(point=>({time:num(point.time),value:num(point.value,num(localPrice(record)))}));
    }catch{}
    const current=num(localPrice(record));
    const move=num(typeof displayChange==='function'?displayChange(record):0);
    const prior=Math.abs(move)>0?current/(1+move/100):current;
    const now=Date.now(),start=now-(RANGE_MS[rangeKey]||RANGE_MS.month);
    return [{time:start,value:prior||current},{time:now,value:current}];
  }

  function priceAt(series,time,fallback){
    if(!series.length)return fallback;
    if(time<=series[0].time)return series[0].value;
    if(time>=series[series.length-1].time)return series[series.length-1].value;
    let left=series[0];
    for(let index=1;index<series.length;index++){
      const right=series[index];
      if(right.time>=time){
        const ratio=(time-left.time)/Math.max(1,right.time-left.time);
        return left.value+(right.value-left.value)*Math.max(0,Math.min(1,ratio));
      }
      left=right;
    }
    return fallback;
  }

  function accountHistory(rangeKey,snap){
    const now=Date.now(),start=now-(RANGE_MS[rangeKey]||RANGE_MS.month),points=48;
    const relevantTx=transactions().filter(tx=>num(tx.time)>=start&&num(tx.time)<=now);
    const ids=new Set([...snap.rows.map(row=>row.record.id),...relevantTx.map(tx=>String(tx.id||'')).filter(Boolean)]);
    const seriesById={};
    ids.forEach(id=>{
      const record=byId(id);if(record)seriesById[id]=priceSeries(record,rangeKey);
    });
    return Array.from({length:points},(_,index)=>{
      const time=start+(now-start)*(index/(points-1));
      let pointCash=snap.cash;
      const pointHoldings=Object.fromEntries(Object.entries(state?.holdings||{}).map(([id,shares])=>[id,num(shares)]));
      for(const tx of transactions()){
        const txTime=num(tx.time);if(!txTime||txTime<=time)continue;
        const id=String(tx.id||''),shares=Math.max(0,num(tx.shares));
        const total=Math.max(0,num(tx.total,num(tx.price)*shares));
        if(tx.mode==='buy'){
          pointCash+=total;
          pointHoldings[id]=num(pointHoldings[id])-shares;
        }else if(tx.mode==='sell'){
          pointCash-=total;
          pointHoldings[id]=num(pointHoldings[id])+shares;
        }
      }
      let value=pointCash;
      Object.entries(pointHoldings).forEach(([id,shares])=>{
        if(shares<=0)return;
        const record=byId(id);if(!record)return;
        const price=priceAt(seriesById[id]||[],time,num(localPrice(record)));
        value+=shares*price;
      });
      return {time,value:Math.max(0,value)};
    });
  }

  function todayPnl(snap){
    return snap.rows.reduce((sum,row)=>{
      const series=priceSeries(row.record,'day');
      const current=row.price;
      let prior=series.length?num(series[0].value,current):current;
      if(Math.abs(prior-current)<.005){
        const move=num(typeof displayChange==='function'?displayChange(row.record):0);
        if(Math.abs(move)>.005) prior=current/(1+move/100);
      }
      return sum+(current-prior)*row.shares;
    },0);
  }

  function performanceChart(snap){
    const series=accountHistory(brokerRange,snap),values=series.map(point=>point.value);
    if(!values.length)return '';
    const min=Math.min(...values),max=Math.max(...values),spread=Math.max(1,max-min);
    const width=900,height=250,padX=20,padTop=22,padBottom=30;
    const x=index=>padX+(index/(Math.max(1,values.length-1)))*(width-padX*2);
    const y=value=>padTop+((max-value)/spread)*(height-padTop-padBottom);
    const points=values.map((value,index)=>`${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
    const start=values[0],end=values[values.length-1],delta=end-start,change=start?delta/start*100:0;
    return `<section class="card broker-performance-card">
      <div class="broker-performance-head"><div><small>PORTFOLIO PERFORMANCE</small><h2>Account value trend</h2><p>Estimated from stored trades and TalentX price history.</p></div><div><strong class="${delta>=0?'positive':'negative'}">${delta>=0?'+':''}${cash(delta)}</strong><span class="${delta>=0?'positive':'negative'}">${percent(change)}</span></div></div>
      <div class="broker-range-tabs">${[['day','1D'],['week','1W'],['month','1M'],['quarter','3M'],['year','1Y']].map(([key,label])=>`<button type="button" class="${brokerRange===key?'active':''}" onclick="setPortfolioRange('${key}')">${label}</button>`).join('')}</div>
      <div class="broker-portfolio-chart"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Portfolio performance chart"><line x1="${padX}" y1="${height-padBottom}" x2="${width-padX}" y2="${height-padBottom}" class="broker-chart-axis"></line><polyline points="${points}" class="broker-chart-line ${delta>=0?'up':'down'}"></polyline></svg><div class="broker-chart-labels"><span>${new Date(series[0].time).toLocaleDateString([],{month:'short',day:'numeric'})}</span><strong>${cash(end)}</strong><span>Today</span></div></div>
      <p class="broker-chart-disclosure">Long-range talent histories can include reconstructed context until enough verified TalentX history exists. Purchased TX Cash is treated as contributed capital in return calculations, not profit.</p>
    </section>`;
  }

  function categoryAllocation(snap){
    if(!snap.rows.length)return '<div class="p4-empty">Make a trade to see category allocation.</div>';
    const groups={};
    snap.rows.forEach(row=>{const category=String(row.record.primaryCategory||'Other');groups[category]=(groups[category]||0)+row.value;});
    const total=Math.max(1,snap.invested);
    return `<div class="broker-category-allocation">${Object.entries(groups).sort((a,b)=>b[1]-a[1]).map(([category,value])=>{
      const share=value/total*100;
      return `<div class="broker-category-row"><span><b>${html(category==='Music'?'Music':category)}</b><small>${cash(value)}</small></span><span class="broker-category-track"><i style="width:${Math.max(2,share)}%"></i></span><strong>${share.toFixed(1)}%</strong></div>`;
    }).join('')}</div>`;
  }

  function winnerLoser(snap){
    if(!snap.rows.length)return '';
    const winner=[...snap.rows].sort((a,b)=>b.gain-a.gain)[0];
    const loser=[...snap.rows].sort((a,b)=>a.gain-b.gain)[0];
    const card=(row,label)=>`<button type="button" class="card broker-highlight" onclick="openProfile('${html(row.record.id)}')"><small>${label}</small><div><strong>${html(row.record.name)}</strong><span>${html(row.record.ticker||'')}</span></div><b class="${row.gain>=0?'positive':'negative'}">${row.gain>=0?'+':''}${cash(row.gain)} · ${percent(row.returnPct)}</b></button>`;
    return `<div class="grid broker-highlight-grid">${card(winner,'Biggest winner')}${card(loser,'Biggest laggard')}</div>`;
  }

  function holdingsTable(snap){
    if(!snap.rows.length)return '<div class="card empty">No holdings yet. Open a talent profile and place a virtual buy order.</div>';
    return `<section class="card table-card p4-holdings broker-holdings"><div class="section-head"><h2>Holdings</h2><small>${snap.rows.length} position${snap.rows.length===1?'':'s'}</small></div><div class="table-wrap"><table class="market-table"><thead><tr><th>Holding</th><th>Shares</th><th>Avg. cost</th><th>Price</th><th>Value</th><th>Gain / loss</th><th>Allocation</th></tr></thead><tbody>${snap.rows.map(row=>`<tr onclick="openProfile('${html(row.record.id)}')"><td><b>${html(row.record.name)}</b><small>${html(row.record.ticker)} · ${html(row.record.leagueOrMedium)}</small></td><td>${row.shares}</td><td>${cash(row.average)}</td><td>${cash(row.price)}</td><td><b>${cash(row.value)}</b></td><td class="${row.gain>=0?'positive':'negative'}">${row.gain>=0?'+':''}${cash(row.gain)}<small>${percent(row.returnPct)}</small></td><td>${(row.value/Math.max(1,snap.invested)*100).toFixed(1)}%</td></tr>`).join('')}</tbody></table></div></section>`;
  }

  function transactionHistory(){
    const rows=[...transactions()].sort((a,b)=>num(b.time)-num(a.time)).slice(0,50);
    return `<section class="card section transaction-card broker-transactions"><div class="section-head"><h2>Transaction history</h2><small>${rows.length?`Latest ${rows.length} trade${rows.length===1?'':'s'}`:'No trades yet'}</small></div>${rows.length?`<div class="broker-transaction-list">${rows.map(tx=>{
      const record=byId(tx.id),shares=Math.max(0,num(tx.shares)),price=Math.max(0,num(tx.price)),total=Math.max(0,num(tx.total,price*shares)),buy=tx.mode==='buy';
      return `<button type="button" class="transaction-row broker-transaction" ${record?`onclick="openProfile('${html(record.id)}')"`:''}><div class="transaction-icon ${buy?'buy':'sell'}">${buy?'↓':'↑'}</div><div><strong>${buy?'Bought':'Sold'} ${shares} ${html(record?.ticker||'shares')}</strong><small>${record?html(record.name):'Talent'} · ${new Date(num(tx.time,Date.now())).toLocaleString([],{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'})}</small></div><div><strong>${cash(total)}</strong><small>${cash(price)} each</small></div></button>`;
    }).join('')}</div>`:'<div class="empty">Your completed buy and sell orders will appear here.</div>'}</section>`;
  }

  window.setPortfolioRange=function(value){
    if(!RANGE_MAP[value]||brokerRange===value)return;
    brokerRange=value;
    if(typeof render==='function')render();
  };

  portfolio=function(){
    const snap=snapshot(),dayPnl=todayPnl(snap);
    const purchased=Math.max(0,num(state?.purchasedCashTotal));
    const storageCopy=window.__talentxAuthUser?.id?'Signed-in cash, holdings and completed trades are secured to your TalentX account.':'Guest holdings and trades are saved only in this browser.';
    const largest=snap.rows[0],cashReserve=snap.total?snap.cash/snap.total*100:100;
    return `${note()}<div class="p4-title-row broker-portfolio-title"><div><div class="eyebrow">Your virtual account</div><h1 class="page-title">Portfolio</h1><p class="page-sub">${storageCopy}</p></div><div class="broker-title-actions"><button class="btn ghost" onclick="go('leaderboard')">Leaderboard</button><button class="btn secondary" onclick="go('store')">Add TX Cash</button></div></div>
      <div class="grid portfolio-stats p4-stats broker-stats">
        <div class="card summary"><small>Total portfolio</small><strong>${cash(snap.total)}</strong><span>${cash(snap.invested)} invested</span></div>
        <div class="card summary"><small>Today's market P/L</small><strong class="${dayPnl>=0?'positive':'negative'}">${dayPnl>=0?'+':''}${cash(dayPnl)}</strong><span>Current positions</span></div>
        <div class="card summary"><small>Total return</small><strong class="${snap.totalReturn>=0?'positive':'negative'}">${snap.totalReturn>=0?'+':''}${cash(snap.totalReturn)}</strong><span>${percent(snap.totalReturnPct)} · ${purchased?`net of ${cash(purchased)} added TX Cash`:`vs ${cash(STARTING_CASH)} start`}</span></div>
        <div class="card summary"><small>Available cash</small><strong>${cash(snap.cash)}</strong><span>${cashReserve.toFixed(1)}% of account</span></div>
      </div>
      ${performanceChart(snap)}
      ${winnerLoser(snap)}
      <div class="grid p4-dashboard broker-dashboard"><section class="card"><div class="section-head"><h2>Allocation by category</h2><small>${cash(snap.invested)} invested</small></div>${categoryAllocation(snap)}</section><section class="card p4-health"><h2>Portfolio health</h2><div><span>Positions</span><strong>${snap.rows.length}</strong></div><div><span>Largest position</span><strong>${largest?(largest.value/Math.max(1,snap.invested)*100).toFixed(1)+'%':'—'}</strong></div><div><span>Cash reserve</span><strong>${cashReserve.toFixed(1)}%</strong></div><div><span>Cost basis</span><strong>${cash(snap.basis)}</strong></div><p>${snap.rows.length<3?'Building a few distinct positions can reduce dependence on one talent listing.':largest&&largest.value/snap.invested>.5?'More than half of your invested value is concentrated in one position.':'Your invested value is spread across multiple positions.'}</p></section></div>
      ${holdingsTable(snap)}${transactionHistory()}`;
  };
})();
