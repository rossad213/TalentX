
const $ = (s) => document.querySelector(s);
const money = (n) => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number(n||0));
const compact = (n) => Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(Number(n||0));
const esc = (v) => String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
const ICONS={Athlete:'🏆',Music:'♫',Actor:'◉',Creator:'✦'};
const PAGE_SIZE=50;

const ROOKIE_SPORTS={
  NFL:{label:'NFL',maxPicks:257,picksPerRound:32,basePrice:2.50,pricePerPoint:.285,positions:{'Quarterback':100,'Edge rusher':88,'Wide receiver':84,'Offensive tackle':82,'Cornerback':80,'Defensive tackle':74,'Linebacker':70,'Tight end':68,'Running back':64,'Safety':63,'Interior offensive line':60,'Kicker / punter':35}},
  NBA:{label:'NBA',maxPicks:60,picksPerRound:30,basePrice:3.00,pricePerPoint:.345,positions:{'Wing':92,'Guard':86,'Forward':85,'Center':81}},
  WNBA:{label:'WNBA',maxPicks:36,picksPerRound:12,basePrice:2.00,pricePerPoint:.225,positions:{'Wing':92,'Guard':87,'Forward':85,'Center':82}},
  NHL:{label:'NHL',maxPicks:224,picksPerRound:32,basePrice:2.50,pricePerPoint:.255,positions:{'Center':91,'Defenseman':87,'Winger':83,'Goalie':75}},
  MLB:{label:'MLB',maxPicks:615,picksPerRound:30,basePrice:2.50,pricePerPoint:.265,positions:{'Shortstop':93,'Starting pitcher':88,'Center field':85,'Catcher':82,'Outfield':80,'Third base':76,'Second base':75,'First base':70,'Relief pitcher':66}}
};
const ROOKIE_WEIGHTS={draftCapital:.35,preProPerformance:.20,opportunity:.15,positionValue:.10,development:.08,availability:.07,audience:.05};
const ROOKIE_SCENARIOS={
  nflQb:{sport:'NFL',position:'Quarterback',pick:3,prePro:90,opportunity:82,development:91,availability:86,audience:78},
  nflDefender:{sport:'NFL',position:'Edge rusher',pick:14,prePro:88,opportunity:76,development:84,availability:90,audience:55},
  nbaWing:{sport:'NBA',position:'Wing',pick:7,prePro:87,opportunity:74,development:92,availability:88,audience:70},
  nhlCenter:{sport:'NHL',position:'Center',pick:12,prePro:89,opportunity:68,development:94,availability:90,audience:58},
  mlbShortstop:{sport:'MLB',position:'Shortstop',pick:20,prePro:91,opportunity:50,development:89,availability:87,audience:45}
};

let currentRecords=[];
let historicalRecords=[];
let taxonomy=null;
let manifest=null;
let historicalLoaded=false;
let route='dashboard';
let selectedId=null;
let profileTab='overview';
let tradeMode='buy';
let retirementSelection='';

let filters={
  segment:'Current',
  category:'All',
  discipline:'All',
  league:'All',
  status:'All',
  stage:'All',
  sort:'score-desc',
  query:'',
  page:1
};

const defaultState={cash:25000,holdings:{},watchlist:[],prices:{},transactions:[]};
let state=loadState();

function loadState(){
  try{
    const parsed=JSON.parse(localStorage.getItem('talentx_v2_state'));
    return parsed&&typeof parsed==='object'?{...structuredClone(defaultState),...parsed}:structuredClone(defaultState);
  }catch{return structuredClone(defaultState)}
}
function saveState(){
  localStorage.setItem('talentx_v2_state',JSON.stringify(state));
  updateCash();
}
function updateCash(){
  const el=$('#sideCash'); if(el) el.textContent=money(state.cash);
}
function allRecords(){
  return currentRecords.concat(historicalRecords);
}
function byId(id){
  return allRecords().find(r=>r.id===id);
}
function localPrice(r){
  return Number(state.prices[r.id]??r.marketPrice);
}
function toast(msg){
  const el=$('#toast');el.textContent=msg;el.classList.add('show');
  clearTimeout(window.__toast);window.__toast=setTimeout(()=>el.classList.remove('show'),2200);
}
function setActiveNav(){
  document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.route===route));
}
function go(next){
  route=next; selectedId=null; profileTab='overview'; setActiveNav(); render();
}
function openProfile(id){
  selectedId=id; route='profile'; profileTab='overview'; setActiveNav(); render(); window.scrollTo({top:0,behavior:'smooth'});
}
function note(){
  return `<div class="notice"><strong>Prototype notice:</strong> Current-seed names are real people selected for product design, but their status is not yet connected to live roster, chart, project, or platform feeds. All prices, scores, changes, charts, volumes, portfolios, and trades are simulated.</div>`;
}
function avatar(r,large=false){
  return `<div class="avatar ${large?'large':''}">${esc(r.avatar||'TX')}</div>`;
}
function segmentClass(s){
  return s==='Current'?'current':s==='Legacy'?'legacy':'review';
}
function trendSvg(r,height=130){
  const a=(r.trend||[]).map(Number); if(!a.length) return '';
  const min=Math.min(...a),max=Math.max(...a),range=Math.max(1,max-min);
  const pts=a.map((v,i)=>`${(i/(a.length-1))*100},${100-((v-min)/range)*86-7}`).join(' ');
  const up=a[a.length-1]>=a[0];
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Simulated price chart">
  <defs><linearGradient id="fill-${esc(r.id)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${up?'#58ef78':'#ff5e79'}" stop-opacity=".25"/><stop offset="1" stop-color="${up?'#58ef78':'#ff5e79'}" stop-opacity="0"/></linearGradient></defs>
  <polygon points="0,100 ${pts} 100,100" fill="url(#fill-${esc(r.id)})"></polygon>
  <polyline points="${pts}" class="chart-line" style="stroke:${up?'#58ef78':'#ff5e79'}"></polyline></svg>`;
}
function miniCard(r){
  return `<article class="card mini-card" onclick="openProfile('${esc(r.id)}')">
    <div class="person">${avatar(r)}<div class="person-copy"><strong>${esc(r.name)} <span class="ticker">${esc(r.ticker)}</span></strong><span>${esc(r.discipline)} · ${esc(r.leagueOrMedium)}</span></div></div>
    <div class="mini-row"><strong>${money(localPrice(r))}</strong><span class="${r.dailyChange>=0?'positive':'negative'}">${r.dailyChange>=0?'+':''}${Number(r.dailyChange).toFixed(2)}%</span></div>
  </article>`;
}
function currentCounts(){
  return Object.fromEntries(['Athlete','Music','Actor','Creator'].map(c=>[c,currentRecords.filter(r=>r.primaryCategory===c).length]));
}
function dashboard(){
  const counts=currentCounts();
  const movers=[...currentRecords].sort((a,b)=>b.dailyChange-a.dailyChange).slice(0,4);
  const feature=[...currentRecords].sort((a,b)=>b.careerScore-a.careerScore)[0]||currentRecords[0];
  const sports=Object.entries(currentRecords.filter(r=>r.primaryCategory==='Athlete').reduce((o,r)=>(o[r.discipline]=(o[r.discipline]||0)+1,o),{})).sort((a,b)=>b[1]-a[1]).slice(0,10);
  return `${note()}
  <div class="grid hero-grid">
    <section class="card hero">
      <div class="eyebrow">Current-first TalentX v2</div>
      <h1>Follow careers while<br><span class="gradient">they are still being written.</span></h1>
      <p>The main market now prioritizes active athletes, music artists, actors, and creators. Historical profiles move into a separate Legacy market, and uncertain profiles stay out of the active market until their status is verified.</p>
      <div class="button-row"><button class="btn primary" onclick="setSegment('Current')">Explore current talent</button><button class="btn secondary" onclick="go('rules')">See retirement rules</button></div>
      <div class="stats"><div class="stat"><small>Current seed</small><strong>${manifest.currentSeedRecords.toLocaleString()}</strong></div><div class="stat"><small>Legacy catalog</small><strong>${manifest.legacyRecords.toLocaleString()}</strong></div><div class="stat"><small>Under review</small><strong>${manifest.underReviewRecords.toLocaleString()}</strong></div></div>
    </section>
    <section class="card featured">
      <div class="section-head"><h3>Featured current listing</h3><button class="link" onclick="openProfile('${esc(feature.id)}')">View →</button></div>
      <div class="person">${avatar(feature)}<div class="person-copy"><strong>${esc(feature.name)} <span class="ticker">${esc(feature.ticker)}</span></strong><span>${esc(feature.discipline)} · ${esc(feature.leagueOrMedium)}</span></div></div>
      <div class="price-big">${money(localPrice(feature))}</div>
      <div class="${feature.dailyChange>=0?'positive':'negative'}">${feature.dailyChange>=0?'+':''}${feature.dailyChange.toFixed(2)}% simulated today</div>
      <div class="chart">${trendSvg(feature)}</div>
    </section>
  </div>
  <section class="section"><div class="section-head"><h2>Current market movers</h2><button class="link" onclick="setSegment('Current')">Open market →</button></div><div class="grid movers">${movers.map(miniCard).join('')}</div></section>
  <section class="section"><div class="section-head"><h2>Browse career categories</h2></div><div class="grid category-grid">
    ${['Athlete','Music','Actor','Creator'].map(c=>`<article class="card category-card" onclick="setCategory('${c}')"><span class="count">${counts[c]}</span><div class="icon">${ICONS[c]}</div><h3>${c==='Music'?'Music':c+'s'}</h3><p>${c==='Athlete'?'Filter by sport, league or tour, team, role, country, and career status.':c==='Music'?'Filter by genre, solo or group, region, activity, and career status.':c==='Actor'?'Filter by film, television, stage, voice, project activity, and status.':'Filter by platform, niche, country, activity, and status.'}</p></article>`).join('')}
  </div></section>
  <section class="section"><div class="section-head"><h2>Sports in the current seed</h2><button class="link" onclick="setCategory('Athlete')">All sports →</button></div><div class="grid discipline-grid">
    ${sports.map(([name,count])=>`<article class="card discipline" onclick="setDiscipline('${esc(name)}')"><strong>${esc(name)}</strong><span>${count} current-seed listing${count===1?'':'s'}</span></article>`).join('')}
  </div></section>`;
}
async function ensureHistorical(){
  if(historicalLoaded) return;
  const app=$('#app'); if(app) app.innerHTML=`${note()}<div class="card loading">Loading historical and under-review records…</div>`;
  const res=await fetch('./data/legacy_catalog_v2.json');
  if(!res.ok) throw new Error('Unable to load historical catalog');
  historicalRecords=await res.json();
  historicalLoaded=true;
}
async function setSegment(segment){
  if(segment!=='Current') await ensureHistorical();
  filters.segment=segment;filters.page=1;route='market';selectedId=null;setActiveNav();render();
}
function setCategory(category){
  filters.category=category;filters.discipline='All';filters.league='All';filters.status='All';filters.stage='All';filters.page=1;filters.segment='Current';route='market';selectedId=null;setActiveNav();render();
}
function setDiscipline(discipline){
  filters.category='Athlete';filters.discipline=discipline;filters.league='All';filters.status='All';filters.stage='All';filters.page=1;filters.segment='Current';route='market';setActiveNav();render();
}
function setFilter(key,value){
  filters[key]=value; filters.page=1; render();
}
function filteredRecords(){
  let arr=filters.segment==='Current'?currentRecords:historicalRecords.filter(r=>r.marketSegment===filters.segment);
  if(filters.category!=='All') arr=arr.filter(r=>r.primaryCategory===filters.category);
  if(filters.discipline!=='All') arr=arr.filter(r=>r.discipline===filters.discipline);
  if(filters.league!=='All') arr=arr.filter(r=>r.leagueOrMedium===filters.league);
  if(filters.status!=='All') arr=arr.filter(r=>r.careerStatus===filters.status);
  if(filters.stage!=='All') arr=arr.filter(r=>(r.careerStage||'Stage under review')===filters.stage);
  if(filters.query.trim()){
    const q=filters.query.trim().toLowerCase();
    arr=arr.filter(r=>(r.searchText||'').includes(q));
  }
  const sorted=[...arr];
  const sorters={
    'score-desc':(a,b)=>b.careerScore-a.careerScore,
    'price-desc':(a,b)=>localPrice(b)-localPrice(a),
    'change-desc':(a,b)=>b.dailyChange-a.dailyChange,
    'change-asc':(a,b)=>a.dailyChange-b.dailyChange,
    'name':(a,b)=>a.name.localeCompare(b.name)
  };
  sorted.sort(sorters[filters.sort]||sorters['score-desc']);
  return sorted;
}
function optionValues(records,key){
  return [...new Set(records.map(r=>r[key]).filter(v=>v&&v!=='—'))].sort((a,b)=>String(a).localeCompare(String(b)));
}
function rowHtml(r){
  return `<tr onclick="openProfile('${esc(r.id)}')">
    <td><div class="name-cell"><div class="small-avatar">${esc(r.avatar)}</div><div><b>${esc(r.name)} <span class="ticker">${esc(r.ticker)}</span></b><small>${esc(r.role)} · ${esc(r.country)}</small></div></div></td>
    <td>${esc(r.primaryCategory)}</td><td>${esc(r.discipline)}</td><td>${esc(r.leagueOrMedium)}</td>
    <td><span class="stage-badge">${esc(r.careerStage||'Stage under review')}</span></td>
    <td><span class="segment-badge ${segmentClass(r.marketSegment)}">${esc(r.marketSegment)}</span></td>
    <td>${money(localPrice(r))}</td><td class="${r.dailyChange>=0?'positive':'negative'}">${r.dailyChange>=0?'+':''}${r.dailyChange.toFixed(2)}%</td>
    <td>${Number(r.careerScore).toFixed(1)}</td><td>${Math.round(r.dataConfidence*100)}%</td>
  </tr>`;
}
function market(){
  const segmentSource=filters.segment==='Current'?currentRecords:historicalRecords.filter(r=>r.marketSegment===filters.segment);
  const categorySource=filters.category==='All'?segmentSource:segmentSource.filter(r=>r.primaryCategory===filters.category);
  const disciplineOptions=optionValues(categorySource,'discipline');
  const disciplineSource=filters.discipline==='All'?categorySource:categorySource.filter(r=>r.discipline===filters.discipline);
  const leagueOptions=optionValues(disciplineSource,'leagueOrMedium');
  const statusOptions=optionValues(categorySource,'careerStatus');
  const stageOptions=optionValues(categorySource,'careerStage');
  const arr=filteredRecords();
  const pages=Math.max(1,Math.ceil(arr.length/PAGE_SIZE)); filters.page=Math.min(filters.page,pages);
  const start=(filters.page-1)*PAGE_SIZE; const rows=arr.slice(start,start+PAGE_SIZE);
  return `${note()}<div class="eyebrow">${esc(filters.segment)} market</div><h1 class="page-title">Talent market</h1><p class="page-sub">The default market shows current-seed talent. Legacy and under-review records load separately so historical profiles do not crowd the active experience.</p>
  <div class="controls"><div class="pills">${['Current','Legacy','Under Review'].map(s=>`<button class="pill ${filters.segment===s?'active':''}" onclick="setSegment('${s}')">${s}</button>`).join('')}</div></div>
  <div class="controls">
    <select class="select" onchange="setFilter('category',this.value)"><option value="All">All categories</option>${['Athlete','Music','Actor','Creator'].map(c=>`<option value="${c}" ${filters.category===c?'selected':''}>${c==='Music'?'Music':c+'s'}</option>`).join('')}</select>
    <select class="select" onchange="setFilter('discipline',this.value)"><option value="All">All ${filters.category==='Athlete'?'sports':'subcategories'}</option>${disciplineOptions.map(v=>`<option ${filters.discipline===v?'selected':''}>${esc(v)}</option>`).join('')}</select>
    <select class="select" onchange="setFilter('league',this.value)"><option value="All">All leagues / mediums</option>${leagueOptions.map(v=>`<option ${filters.league===v?'selected':''}>${esc(v)}</option>`).join('')}</select>
    <select class="select" onchange="setFilter('status',this.value)"><option value="All">All statuses</option>${statusOptions.map(v=>`<option ${filters.status===v?'selected':''}>${esc(v)}</option>`).join('')}</select>
    <select class="select" onchange="setFilter('stage',this.value)"><option value="All">All career stages</option>${stageOptions.map(v=>`<option ${filters.stage===v?'selected':''}>${esc(v)}</option>`).join('')}</select>
    <div class="spacer"></div>
    <select class="select" onchange="setFilter('sort',this.value)">
      <option value="score-desc" ${filters.sort==='score-desc'?'selected':''}>Highest career score</option>
      <option value="price-desc" ${filters.sort==='price-desc'?'selected':''}>Highest price</option>
      <option value="change-desc" ${filters.sort==='change-desc'?'selected':''}>Top gainers</option>
      <option value="change-asc" ${filters.sort==='change-asc'?'selected':''}>Top decliners</option>
      <option value="name" ${filters.sort==='name'?'selected':''}>Name A–Z</option>
    </select>
  </div>
  <section class="card table-card"><div class="table-wrap"><table class="market-table"><thead><tr><th>Person</th><th>Category</th><th>Sport / genre / niche</th><th>League / medium</th><th>Career stage</th><th>Market</th><th>Price</th><th>Move</th><th>Score</th><th>Confidence</th></tr></thead><tbody>${rows.length?rows.map(rowHtml).join(''):`<tr><td colspan="10"><div class="empty">No records match these filters.</div></td></tr>`}</tbody></table></div>
  <div class="pagination"><span>Showing ${arr.length?start+1:0}–${Math.min(start+PAGE_SIZE,arr.length)} of ${arr.length.toLocaleString()}</span><div class="pagination-controls"><button onclick="changePage(-1)" ${filters.page<=1?'disabled':''}>← Previous</button><span>Page ${filters.page} of ${pages}</span><button onclick="changePage(1)" ${filters.page>=pages?'disabled':''}>Next →</button></div></div></section>`;
}
function changePage(delta){filters.page=Math.max(1,filters.page+delta);render();window.scrollTo({top:0,behavior:'smooth'})}
function metricGrid(metrics){
  return `<div class="grid metrics">${Object.entries(metrics).map(([k,v])=>`<div class="metric"><div class="metric-top"><span>${esc(k.replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase()))}</span><b>${Number(v).toFixed(1)}</b></div><div class="track"><span style="width:${Math.max(0,Math.min(100,Number(v)))}%"></span></div></div>`).join('')}</div>`;
}
function profile(){
  const r=byId(selectedId); if(!r) return `<div class="card empty">Profile not found.</div>`;
  const shares=Number(state.holdings[r.id]||0);
  const metrics=r.modelType.startsWith('Legacy')?r.legacyMetrics:r.activeMetrics;
  const sourceLink=r.sourceUrl?`<a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener">Open source reference ↗</a>`:'';
  return `${note()}<div class="grid detail-grid"><section>
    <article class="card profile-card">
      <div class="profile-head"><div class="profile-id">${avatar(r,true)}<div><h1>${esc(r.name)} <span class="ticker">${esc(r.ticker)}</span></h1><p>${esc(r.role)} · ${esc(r.discipline)} · ${esc(r.leagueOrMedium)}${r.teamOrPlatform&&r.teamOrPlatform!=='—'?`<br>${esc(r.teamOrPlatform)}`:''}</p></div></div>
      <div class="profile-price"><strong>${money(localPrice(r))}</strong><span class="${r.dailyChange>=0?'positive':'negative'}">${r.dailyChange>=0?'+':''}${r.dailyChange.toFixed(2)}% simulated</span></div></div>
      <div class="badge-row"><span class="segment-badge ${segmentClass(r.marketSegment)}">${esc(r.marketSegment)} market</span><span class="status-badge">${esc(r.careerStatus)}</span><span class="stage-badge">${esc(r.careerStage||'Stage under review')}</span><span class="quality-badge">${Math.round(r.dataConfidence*100)}% data confidence</span></div>
      <div class="profile-chart">${trendSvg(r,210)}</div>
      <div class="tab-row">${['overview','pricing','data'].map(t=>`<button class="${profileTab===t?'active':''}" onclick="setProfileTab('${t}')">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</div>
      ${profileTab==='overview'?`<div class="grid info-grid"><div class="info-box"><small>Market segment</small><strong>${esc(r.marketSegment)}</strong></div><div class="info-box"><small>Career model</small><strong>${esc(r.modelType)}</strong></div><div class="info-box"><small>Career stage</small><strong>${esc(r.careerStage||'Stage under review')}</strong></div><div class="info-box"><small>Career score</small><strong>${Number(r.careerScore).toFixed(1)} / 100</strong></div><div class="info-box"><small>Country</small><strong>${esc(r.country)}</strong></div><div class="info-box"><small>Role</small><strong>${esc(r.role)}</strong></div><div class="info-box"><small>Virtual volume</small><strong>${compact(r.volume)}</strong></div></div><p class="page-sub" style="margin-top:18px">${esc(r.description)}</p>`:''}
      ${profileTab==='pricing'?`<div class="grid info-grid"><div class="info-box"><small>Fundamental value</small><strong>${money(r.fundamentalValue)}</strong></div><div class="info-box"><small>Demand premium</small><strong>${r.demandPremiumPct>=0?'+':''}${Number(r.demandPremiumPct).toFixed(2)}%</strong></div><div class="info-box"><small>Momentum</small><strong>${r.momentumPct>=0?'+':''}${Number(r.momentumPct).toFixed(2)}%</strong></div></div>${r.rookiePricing?rookieProfilePricing(r):metricGrid(metrics)}<div class="formula" style="margin-top:16px">${r.rookiePricing?'Rookie IPO price = draft capital + pre-professional performance + immediate opportunity + position value + development + availability + audience. Draft weight fades as professional evidence arrives.':r.marketSegment==='Legacy'?'Legacy price = legacy score anchor + audience demand + post-career relevance + controlled market activity':'Active price = current career score anchor + audience demand + career momentum + controlled market activity'}</div>`:''}
      ${profileTab==='data'?`<div class="grid info-grid"><div class="info-box"><small>Verification</small><strong>${esc(r.verificationStatus)}</strong></div><div class="info-box"><small>Last verified</small><strong>${esc(r.lastVerifiedAt||'Not connected')}</strong></div><div class="info-box"><small>Status source</small><strong>${esc(r.statusSource)}</strong></div></div><div class="source-box"><small>Identity/source snapshot</small><strong>${esc(r.sourceName)}</strong>${sourceLink}<small>Before production launch, every Current listing should have a live status source and a timestamp. Historical source records remain outside the Current market until verified.</small></div>`:''}
    </article>
  </section>
  <aside class="card trade-card"><h2>Virtual order</h2><p>You own <strong>${shares}</strong> shares. Trades only affect your browser’s simulated market.</p>
    <div class="trade-tabs"><button class="${tradeMode==='buy'?'active buy':''}" onclick="setTradeMode('buy')">Buy</button><button class="${tradeMode==='sell'?'active sell':''}" onclick="setTradeMode('sell')">Sell</button></div>
    <div class="field"><label>Number of shares</label><input id="shareInput" type="number" min="1" step="1" value="1" oninput="estimateOrder()"></div>
    <div id="estimate" class="estimate"></div>
    <button class="btn ${tradeMode==='buy'?'primary':'danger'}" onclick="executeTrade('${esc(r.id)}')">Place virtual ${tradeMode} order</button>
    <button class="btn ghost" style="margin-top:9px" onclick="toggleWatch('${esc(r.id)}')">${state.watchlist.includes(r.id)?'★ Remove from watchlist':'☆ Add to watchlist'}</button>
    <div class="source-box"><small>Retirement treatment</small><strong>Price does not automatically become $0.</strong><small>If an active athlete retires, TalentX pauses the listing, verifies the event, changes the pricing model, and reopens the asset at a recalculated legacy anchor.</small></div>
  </aside></div>`;
}
function setProfileTab(tab){profileTab=tab;render()}
function setTradeMode(mode){tradeMode=mode;render()}
function estimateOrder(){
  if(route!=='profile')return;
  const r=byId(selectedId),input=$('#shareInput'),box=$('#estimate'); if(!r||!input||!box)return;
  const n=Math.max(1,Math.floor(Number(input.value)||1)),p=localPrice(r);
  const impact=Math.min(.025,n/(300+Math.sqrt(Math.max(1,r.volume)))*.2);
  const exec=p*(1+(tradeMode==='buy'?impact:-impact)/2),total=exec*n;
  box.innerHTML=`<div><span>Estimated price</span><strong>${money(exec)}</strong></div><div><span>Estimated impact</span><strong>${(impact*100).toFixed(3)}%</strong></div><div><span>${tradeMode==='buy'?'Estimated cost':'Estimated proceeds'}</span><strong>${money(total)}</strong></div>`;
}
function executeTrade(id){
  const r=byId(id),input=$('#shareInput'); if(!r||!input)return;
  const n=Math.max(1,Math.floor(Number(input.value)||1)),p=localPrice(r),impact=Math.min(.025,n/(300+Math.sqrt(Math.max(1,r.volume)))*.2);
  const exec=p*(1+(tradeMode==='buy'?impact:-impact)/2),total=exec*n,current=Number(state.holdings[id]||0);
  if(tradeMode==='buy'){
    if(total>state.cash){toast('Not enough virtual cash');return}
    state.cash-=total;state.holdings[id]=current+n;state.prices[id]=p*(1+impact);
  }else{
    if(n>current){toast('You do not own that many shares');return}
    state.cash+=total;state.holdings[id]=current-n;if(state.holdings[id]<=0)delete state.holdings[id];state.prices[id]=Math.max(1,p*(1-impact));
  }
  state.transactions.unshift({id,mode:tradeMode,shares:n,price:exec,time:Date.now()});state.transactions=state.transactions.slice(0,100);
  saveState();toast(`${tradeMode==='buy'?'Bought':'Sold'} ${n} ${r.ticker} share${n===1?'':'s'}`);render();
}
function toggleWatch(id){
  const i=state.watchlist.indexOf(id); if(i>=0)state.watchlist.splice(i,1);else state.watchlist.push(id);
  saveState();render();toast(i>=0?'Removed from watchlist':'Added to watchlist');
}
function portfolioTotals(){
  let holdings=0;for(const [id,n] of Object.entries(state.holdings)){const r=byId(id);if(r)holdings+=localPrice(r)*n}
  return {holdings,total:holdings+state.cash};
}
function portfolio(){
  const totals=portfolioTotals();
  const entries=Object.entries(state.holdings).map(([id,n])=>[byId(id),n]).filter(([r,n])=>r&&n>0).sort((a,b)=>localPrice(b[0])*b[1]-localPrice(a[0])*a[1]);
  return `${note()}<div class="eyebrow">Your virtual account</div><h1 class="page-title">Portfolio</h1><p class="page-sub">Holdings are saved only in this browser.</p>
  <div class="grid portfolio-stats"><div class="card summary"><small>Total portfolio</small><strong>${money(totals.total)}</strong></div><div class="card summary"><small>Invested value</small><strong>${money(totals.holdings)}</strong></div><div class="card summary"><small>Available cash</small><strong class="positive">${money(state.cash)}</strong></div></div>
  <section class="card table-card section">${entries.length?`<div class="table-wrap"><table class="market-table"><thead><tr><th>Holding</th><th>Market</th><th>Shares</th><th>Price</th><th>Market value</th><th>Status</th></tr></thead><tbody>${entries.map(([r,n])=>`<tr onclick="openProfile('${esc(r.id)}')"><td><div class="name-cell"><div class="small-avatar">${esc(r.avatar)}</div><div><b>${esc(r.name)}</b><small>${esc(r.ticker)} · ${esc(r.discipline)}</small></div></div></td><td>${esc(r.marketSegment)}</td><td>${n}</td><td>${money(localPrice(r))}</td><td><b>${money(localPrice(r)*n)}</b></td><td>${esc(r.careerStatus)}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty">No holdings yet. Open the Current market and place a virtual buy order.</div>`}</section>`;
}
function watchlist(){
  const records=state.watchlist.map(byId).filter(Boolean);
  return `${note()}<div class="eyebrow">Saved talent</div><h1 class="page-title">Watchlist</h1><p class="page-sub">Track talent across Current, Legacy, and Under Review markets.</p>${records.length?`<div class="grid watch-grid">${records.map(r=>`<article class="card watch" onclick="openProfile('${esc(r.id)}')"><div class="person">${avatar(r)}<div class="person-copy"><strong>${esc(r.name)}</strong><span>${esc(r.marketSegment)} · ${esc(r.discipline)}</span></div></div><div class="mini-row"><strong>${money(localPrice(r))}</strong><span class="${r.dailyChange>=0?'positive':'negative'}">${r.dailyChange>=0?'+':''}${r.dailyChange.toFixed(2)}%</span></div></article>`).join('')}</div>`:`<div class="card empty">Your watchlist is empty.</div>`}`;
}
function clamp(n,min,max){return Math.max(min,Math.min(max,Number(n)||0))}
function rookiePositionOptions(sport,selected=''){
  const cfg=ROOKIE_SPORTS[sport]||ROOKIE_SPORTS.NFL;
  return Object.keys(cfg.positions).map(p=>`<option value="${esc(p)}" ${p===selected?'selected':''}>${esc(p)}</option>`).join('');
}
function draftCapitalScore(sport,pick){
  const cfg=ROOKIE_SPORTS[sport]||ROOKIE_SPORTS.NFL;
  const p=clamp(Math.floor(pick),1,cfg.maxPicks);
  return clamp(100-75*Math.sqrt((p-1)/Math.max(1,cfg.maxPicks-1)),15,100);
}
function calculateRookieIpo(values){
  const cfg=ROOKIE_SPORTS[values.sport]||ROOKIE_SPORTS.NFL;
  const pick=clamp(Math.floor(values.pick),1,cfg.maxPicks);
  const position=cfg.positions[values.position]!==undefined?values.position:Object.keys(cfg.positions)[0];
  const factors={
    draftCapital:draftCapitalScore(values.sport,pick),
    preProPerformance:clamp(values.prePro,0,100),
    opportunity:clamp(values.opportunity,0,100),
    positionValue:cfg.positions[position],
    development:clamp(values.development,0,100),
    availability:clamp(values.availability,0,100),
    audience:clamp(values.audience,0,100)
  };
  const score=Object.entries(ROOKIE_WEIGHTS).reduce((sum,[key,w])=>sum+factors[key]*w,0);
  const price=cfg.basePrice+score*cfg.pricePerPoint;
  const uncertainty=.07+(100-factors.opportunity)/100*.035+(100-factors.availability)/100*.025;
  const round=Math.ceil(pick/cfg.picksPerRound);
  const contributions={base:cfg.basePrice};
  Object.entries(ROOKIE_WEIGHTS).forEach(([key,w])=>contributions[key]=factors[key]*w*cfg.pricePerPoint);
  return {sport:values.sport,position,pick,round,factors,score,price,low:Math.max(1,price*(1-uncertainty)),high:price*(1+uncertainty),contributions};
}
function rookieProfilePricing(r){
  const p=r.rookiePricing||{};
  const rows={draftCapital:p.draftCapitalScore,preProPerformance:p.preProPerformanceScore,opportunity:p.opportunityScore,positionValue:p.positionValueScore,development:p.developmentScore,availability:p.availabilityScore,audience:p.audienceScore};
  return `<div class="source-box"><small>Rookie IPO</small><strong>${p.draftSport||r.leagueOrMedium} · Pick ${p.overallPick||'—'} · ${p.position||r.role}</strong><small>Opening price ${money(p.ipoPrice||r.fundamentalValue)}. Draft capital is strongest at listing and fades as professional performance data accumulates.</small></div>${metricGrid(Object.fromEntries(Object.entries(rows).filter(([,v])=>v!==undefined)))}`;
}
function readRookieInputs(){
  const val=id=>Number(document.getElementById(id)?.value||0);
  return {sport:document.getElementById('rookieSport')?.value||'NFL',position:document.getElementById('rookiePosition')?.value||'Quarterback',pick:val('rookiePick'),prePro:val('rookiePrePro'),opportunity:val('rookieOpportunity'),development:val('rookieDevelopment'),availability:val('rookieAvailability'),audience:val('rookieAudience')};
}
function updateRookiePositionOptions(){
  const sport=document.getElementById('rookieSport')?.value||'NFL';
  const cfg=ROOKIE_SPORTS[sport];
  const pos=document.getElementById('rookiePosition'),pick=document.getElementById('rookiePick');
  if(pos){const prior=pos.value;pos.innerHTML=rookiePositionOptions(sport,prior);}
  if(pick){pick.max=cfg.maxPicks;if(Number(pick.value)>cfg.maxPicks)pick.value=cfg.maxPicks;}
}
function setRookieScenario(key){
  const s=ROOKIE_SCENARIOS[key];if(!s)return;
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v};
  set('rookieSport',s.sport);updateRookiePositionOptions();set('rookiePosition',s.position);set('rookiePick',s.pick);set('rookiePrePro',s.prePro);set('rookieOpportunity',s.opportunity);set('rookieDevelopment',s.development);set('rookieAvailability',s.availability);set('rookieAudience',s.audience);updateRookieCalc();
}
function updateRookieCalc(){
  const box=document.getElementById('rookieResult');if(!box)return;
  const result=calculateRookieIpo(readRookieInputs());
  const labels={draftCapital:'Draft capital',preProPerformance:'Pre-pro performance',opportunity:'Immediate opportunity',positionValue:'Position value',development:'Development potential',availability:'Availability',audience:'Audience demand'};
  document.querySelectorAll('[data-rookie-output]').forEach(el=>{const input=document.getElementById(el.dataset.rookieOutput);if(input)el.textContent=input.value});
  box.innerHTML=`<div class="ipo-result-head"><div><small>Rookie IPO price</small><strong>${money(result.price)}</strong></div><div><small>Model score</small><strong>${result.score.toFixed(1)} / 100</strong></div><div><small>Illustrative range</small><strong>${money(result.low)}–${money(result.high)}</strong></div></div><p class="page-sub" style="margin:12px 0">${esc(result.sport)} ${esc(result.position)} · overall pick ${result.pick} · round ${result.round}. This is an explainable simulated opening price, not a forecast.</p><div class="ipo-breakdown"><div><span>Base listing value</span><strong>${money(result.contributions.base)}</strong></div>${Object.entries(ROOKIE_WEIGHTS).map(([key])=>`<div><span>${labels[key]} <small>${Math.round(ROOKIE_WEIGHTS[key]*100)}% · score ${result.factors[key].toFixed(1)}</small></span><strong>+${money(result.contributions[key])}</strong></div>`).join('')}</div><div class="formula" style="margin-top:14px">Draft influence: 35% at IPO → 25% at season start → 15% mid-rookie season → 8% after year one → 0–3% after year two.</div>`;
}
function rookieCalculator(){
  return `<section class="card calculator section"><div class="section-head"><h2>Rookie IPO calculator</h2><span class="quality-badge">Working model</span></div><p class="page-sub">Draft position establishes the opening anchor. Position value, pre-professional performance, expected opportunity, development, availability, and audience demand refine the price.</p><div class="scenario-row"><button class="btn ghost small" onclick="setRookieScenario('nflQb')">NFL QB · pick 3</button><button class="btn ghost small" onclick="setRookieScenario('nflDefender')">NFL edge · pick 14</button><button class="btn ghost small" onclick="setRookieScenario('nbaWing')">NBA wing · pick 7</button><button class="btn ghost small" onclick="setRookieScenario('nhlCenter')">NHL center · pick 12</button><button class="btn ghost small" onclick="setRookieScenario('mlbShortstop')">MLB shortstop · pick 20</button></div><div class="rookie-form"><div class="field"><label>Draft league</label><select id="rookieSport" class="select" onchange="updateRookiePositionOptions();updateRookieCalc()">${Object.keys(ROOKIE_SPORTS).map(s=>`<option value="${s}">${s}</option>`).join('')}</select></div><div class="field"><label>Position</label><select id="rookiePosition" class="select" onchange="updateRookieCalc()">${rookiePositionOptions('NFL','Quarterback')}</select></div><div class="field"><label>Overall draft pick</label><input id="rookiePick" type="number" min="1" max="257" value="3" oninput="updateRookieCalc()"></div>${[['rookiePrePro','Pre-pro performance',90],['rookieOpportunity','Immediate opportunity',82],['rookieDevelopment','Development potential',91],['rookieAvailability','Availability / health',86],['rookieAudience','Audience demand',78]].map(([id,label,value])=>`<div class="field range-field"><label>${label} <output data-rookie-output="${id}">${value}</output></label><input id="${id}" type="range" min="0" max="100" value="${value}" oninput="updateRookieCalc()"></div>`).join('')}</div><div id="rookieResult" class="calculator-result"></div></section>`;
}

function projectRetirement(id){
  const r=currentRecords.find(x=>x.id===id&&x.primaryCategory==='Athlete');if(!r)return null;
  const m=r.legacyMetrics;
  const score=m.legacy*.35+m.audience*.25+m.postCareer*.20+m.recognition*.15+m.liquidity*.05;
  const fundamental=6+score*1.62;
  const projected=fundamental*(1+(r.demandPremiumPct/100)*.55);
  return {score,projected:Math.max(5,projected),current:localPrice(r)};
}
function updateRetirementCalc(){
  const select=$('#retirementSelect'),box=$('#retirementResult'); if(!select||!box)return;
  const id=select.value;retirementSelection=id;const r=currentRecords.find(x=>x.id===id),result=projectRetirement(id);
  if(!r||!result){box.innerHTML='';return}
  const delta=(result.projected/result.current-1)*100;
  box.innerHTML=`<strong>${esc(r.name)}</strong><p class="page-sub" style="margin:5px 0 0">Illustrative model transition only—not a prediction.</p><div class="compare"><div><small>Current simulated price</small><strong>${money(result.current)}</strong></div><div><small>Projected legacy anchor</small><strong>${money(result.projected)}</strong></div></div><p class="${delta>=0?'positive':'negative'}">${delta>=0?'+':''}${delta.toFixed(1)}% model transition. The price remains above zero because achievements, legacy, audience, recognition, and post-career activity remain valuable inputs.</p>`;
}
function rules(){
  const athletes=currentRecords.filter(r=>r.primaryCategory==='Athlete').sort((a,b)=>a.name.localeCompare(b.name));
  const initial=retirementSelection||athletes[0]?.id||'';
  return `${note()}<div class="eyebrow">Lifecycle and scale</div><h1 class="page-title">Data, status & retirement rules</h1><p class="page-sub">TalentX needs different pricing models for active careers and legacy careers. Retirement changes the model; it does not erase the career.</p>
  <div class="grid rules-grid">
    <article class="card rule"><h3>Active career model</h3><div class="formula">30% current performance + 25% potential + 20% achievements + 15% audience + 10% availability</div><div class="weight-list">${Object.entries(taxonomy.pricingModels.active.weights).map(([k,v])=>`<div class="weight-row"><span>${esc(k)}</span><strong>${Math.round(v*100)}%</strong></div>`).join('')}</div></article>
    <article class="card rule"><h3>Legacy career model</h3><div class="formula">35% career legacy + 25% audience + 20% post-career activity + 15% recognition + 5% liquidity</div><div class="weight-list">${Object.entries(taxonomy.pricingModels.legacy.weights).map(([k,v])=>`<div class="weight-row"><span>${esc(k)}</span><strong>${Math.round(v*100)}%</strong></div>`).join('')}</div></article>
    <article class="card rule"><h3>Rookie IPO model</h3><div class="formula">35% draft capital + 20% pre-pro performance + 15% opportunity + 10% position value + 8% development + 7% availability + 5% audience</div><div class="weight-list">${Object.entries(taxonomy.pricingModels.rookie.weights).map(([k,v])=>`<div class="weight-row"><span>${esc(k.replace(/([A-Z])/g,' $1'))}</span><strong>${Math.round(v*100)}%</strong></div>`).join('')}</div><p>Draft capital is an IPO input, not permanent protection. Its weight fades as professional results arrive.</p></article>
    <article class="card rule"><h3>What happens at retirement?</h3><ol><li>Temporarily pause trading when an official retirement is detected.</li><li>Verify the announcement using an approved source.</li><li>Change the profile from Active to Retirement announced, then Retired — Legacy.</li><li>Replace future playing performance with legacy and post-career factors.</li><li>Reopen at the recalculated legacy anchor with a visible event record.</li></ol><p>A retiree can decline, hold value, or even rise. The price should never automatically become zero.</p></article>
    <article class="card rule"><h3>How this grows beyond 5,000</h3><p>The browser should never download one million complete profiles. The production version uses a backend database, server-side filters, cursor pagination, search indexing, and separate status-history and price-event tables.</p><ul><li>GitHub Pages remains a front-end preview only.</li><li>Current listings require source timestamps and scheduled re-verification.</li><li>Historical records remain in Legacy or Under Review until verified.</li><li>Images and biographies should be licensed or sourced under compatible terms.</li></ul></article>
  </div>
  ${rookieCalculator()}
  <section class="card calculator section"><div class="section-head"><h2>Retirement transition preview</h2><span class="quality-badge">Illustrative only</span></div><p class="page-sub">Choose a current-seed athlete to see how TalentX changes the valuation model instead of sending the price to $0.</p><select id="retirementSelect" class="select" onchange="updateRetirementCalc()">${athletes.map(r=>`<option value="${esc(r.id)}" ${r.id===initial?'selected':''}>${esc(r.name)} · ${esc(r.discipline)}</option>`).join('')}</select><div id="retirementResult" class="calculator-result"></div></section>
  <section class="card rule section"><h3>Production status fields</h3><div class="formula">career_status · market_segment · status_source · last_verified_at · source_record_id · status_confidence · status_history</div><p>These fields prevent old datasets from being presented as current. A record enters the Current market only after status verification; otherwise it remains Under Review or Legacy.</p><div class="button-row"><button class="btn secondary" onclick="resetDemo()">Reset virtual account</button></div></section>`;
}
function resetDemo(){
  if(confirm('Reset virtual cash, holdings, watchlist, and local prices?')){state=structuredClone(defaultState);saveState();route='dashboard';render();toast('TalentX prototype reset')}
}
function bindAfterRender(){
  updateCash();
  if(route==='profile') estimateOrder();
  if(route==='rules'){updateRookiePositionOptions();updateRookieCalc();updateRetirementCalc();}
}
function render(){
  const app=$('#app'); if(!app)return;
  setActiveNav();
  if(route==='dashboard') app.innerHTML=dashboard();
  else if(route==='market') app.innerHTML=market();
  else if(route==='profile') app.innerHTML=profile();
  else if(route==='portfolio') app.innerHTML=portfolio();
  else if(route==='watchlist') app.innerHTML=watchlist();
  else if(route==='rules') app.innerHTML=rules();
  bindAfterRender();
}
async function init(){
  try{
    const [cur,tax,man]=await Promise.all([
      fetch('./data/current_seed.json').then(r=>{if(!r.ok)throw new Error('current seed');return r.json()}),
      fetch('./data/taxonomy.json').then(r=>{if(!r.ok)throw new Error('taxonomy');return r.json()}),
      fetch('./data/catalog_manifest.json').then(r=>{if(!r.ok)throw new Error('manifest');return r.json()})
    ]);
    currentRecords=cur;taxonomy=tax;manifest=man;
    const input=$('#globalSearch');
    input.addEventListener('input',e=>{filters.query=e.target.value;filters.page=1;if(route!=='market'){route='market';filters.segment='Current';setActiveNav()}render()});
    render();
  }catch(err){
    console.error(err);
    $('#app').innerHTML=`<div class="notice"><strong>Preview could not load the data files.</strong> Open the site through GitHub Pages or a local web server rather than double-clicking index.html.</div><div class="card empty">Try <code>python3 -m http.server</code> in the project folder, then open <code>http://localhost:8000</code>.</div>`;
  }
}
init();
