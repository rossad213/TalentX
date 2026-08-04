
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
const CATEGORY_MODEL_FALLBACKS={
  Athlete:{performance:.35,achievements:.25,consistency:.15,potential:.15,availability:.10},
  Music:{performance:.25,consistency:.25,achievements:.20,audience:.20,potential:.10},
  Actor:{performance:.25,consistency:.25,achievements:.20,audience:.20,potential:.10},
  Creator:{audience:.25,performance:.25,potential:.20,consistency:.15,achievements:.15}
};
const METRIC_LABELS={
  performance:'Performance',achievements:'Achievements',consistency:'Consistency',
  potential:'Potential',availability:'Availability',audience:'Audience'
};
function categoryModel(category){
  return taxonomy?.pricingModels?.active?.categories?.[category]?.weights||CATEGORY_MODEL_FALLBACKS[category]||CATEGORY_MODEL_FALLBACKS.Athlete;
}
function categoryFormula(category){
  const weights=categoryModel(category);
  const parts=Object.entries(weights).map(([key,value])=>`${Math.round(Number(value)*100)}% ${METRIC_LABELS[key]||key}`);
  return `${category||'Active'} fundamental score = ${parts.join(' + ')}. Universal career score = 70% category evidence + 30% profession-peer calibration.`;
}

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
let chartRange='1D';

const CHART_RANGE_CONFIG={
  '1D':{duration:8*60*60*1000,points:36},
  '5D':{duration:5*24*60*60*1000,points:48},
  '1M':{duration:30*24*60*60*1000,points:54},
  '6M':{duration:182*24*60*60*1000,points:64},
  'YTD':{duration:null,points:64},
  '1Y':{duration:365*24*60*60*1000,points:72},
  '5Y':{duration:5*365*24*60*60*1000,points:82},
  'Max':{duration:10*365*24*60*60*1000,points:92}
};
const CHART_RANGES=Object.keys(CHART_RANGE_CONFIG);

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

const PRICING_MODEL_VERSION='4.1-event-driven-pricing';
const defaultState={cash:25000,holdings:{},watchlist:[],prices:{},transactions:[],pricingModelVersion:PRICING_MODEL_VERSION};
let state=loadState();

function loadState(){
  try{
    const parsed=JSON.parse(localStorage.getItem('talentx_v2_state'));
    if(!parsed||typeof parsed!=='object') return structuredClone(defaultState);
    const migrated={...structuredClone(defaultState),...parsed};
    if(parsed.pricingModelVersion!==PRICING_MODEL_VERSION){
      migrated.prices={};
      migrated.pricingModelVersion=PRICING_MODEL_VERSION;
    }
    return migrated;
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
function displayChange(r){
  const listed=Number(r.marketPrice||0);
  const current=Number(localPrice(r));
  const recorded=Number(r.dailyChange||0);
  if(!Number.isFinite(listed)||listed<=0||!Number.isFinite(current)) return recorded;
  if(Math.abs(current-listed)<.005) return recorded;
  const prior=listed/(1+recorded/100);
  return Number.isFinite(prior)&&prior>0?((current/prior)-1)*100:recorded;
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
  selectedId=id; route='profile'; profileTab='overview'; chartRange='1D'; setActiveNav(); render(); window.scrollTo({top:0,behavior:'smooth'});
}
function note(){
  const automated=Number(manifest?.automatedRosterVerifiedRecords||0);
  const enriched=Number(manifest?.pricingEnrichedRecords||0);
  const statusText=automated
    ? `${automated.toLocaleString()} athlete profiles were included from point-in-time current-roster feeds. ${enriched.toLocaleString()} currently have statistics-and-awards pricing evidence; the remainder are visibly conservative provisional listings. Status and evidence can change after the timestamps shown.`
    : `This local preview contains the 200-person fallback seed. The GitHub Pages workflow generates and evidence-enriches the larger current catalog before deployment.`;
  return `<div class="notice"><strong>TalentX data notice:</strong> ${statusText} The market is simulated, but prices move only after supported evidence updates or a virtual trade in your browser; charts do not add random fluctuations.</div>`;
}
function avatar(r,large=false){
  return `<div class="avatar ${large?'large':''}">${esc(r.avatar||'TX')}</div>`;
}
function segmentClass(s){
  return s==='Current'?'current':s==='Legacy'?'legacy':'review';
}
function displayTrend(r){
  const base=(r.trend||[]).map(Number).filter(v=>Number.isFinite(v));
  const current=Number(localPrice(r));
  if(!base.length) return Number.isFinite(current)&&current>0?[Number(current.toFixed(2))]:[];
  const output=base.map(v=>Number(v.toFixed(2)));
  const last=Number(output[output.length-1]||0);
  if(Number.isFinite(current)&&current>0&&Math.abs(current-last)>=.005){
    output.push(Number(current.toFixed(2)));
  }
  return output;
}
function resampleValues(values,count){
  if(!values.length) return [];
  if(values.length===1) return Array(count).fill(Number(values[0]));
  return Array.from({length:count},(_,i)=>{
    const position=(i/(count-1))*(values.length-1);
    const lower=Math.floor(position),upper=Math.min(values.length-1,Math.ceil(position));
    const blend=position-lower;
    return Number((values[lower]+(values[upper]-values[lower])*blend).toFixed(2));
  });
}
function chartSeries(r,range=chartRange){
  const config=CHART_RANGE_CONFIG[range]||CHART_RANGE_CONFIG['1D'];
  const current=Math.max(1,Number(localPrice(r))||1);
  const now=Date.now();
  const start=range==='YTD'?new Date(new Date(now).getFullYear(),0,1).getTime():now-config.duration;
  const base=displayTrend(r);
  const values=base.length?resampleValues(base,config.points):Array(config.points).fill(current);
  values[values.length-1]=Number(current.toFixed(2));
  return values.map((value,index)=>({
    value,
    time:start+((now-start)*(index/(values.length-1)))
  }));
}
function formatAxisTime(timestamp,range){
  const date=new Date(Number(timestamp));
  if(range==='1D') return date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  if(range==='5D') return date.toLocaleDateString([],{weekday:'short'});
  if(range==='1M') return date.toLocaleDateString([],{month:'short',day:'numeric'});
  if(['6M','YTD','1Y'].includes(range)) return date.toLocaleDateString([],{month:'short'});
  return date.toLocaleDateString([],{year:'numeric'});
}
function formatHoverTime(timestamp,range){
  const date=new Date(Number(timestamp));
  if(range==='1D') return date.toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'});
  if(range==='5D') return date.toLocaleString([],{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  if(['5Y','Max'].includes(range)) return date.toLocaleDateString([],{month:'short',year:'numeric'});
  return date.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
}
function chartRangeTabs(){
  return `<div class="chart-range-tabs" role="tablist" aria-label="Price chart period">${CHART_RANGES.map(range=>`<button type="button" role="tab" aria-selected="${chartRange===range}" class="${chartRange===range?'active':''}" onclick="setChartRange('${range}')">${range}</button>`).join('')}</div>`;
}
function setChartRange(range){
  if(!CHART_RANGE_CONFIG[range]||chartRange===range) return;
  const y=window.scrollY;
  chartRange=range;
  render();
  requestAnimationFrame(()=>window.scrollTo({top:y,left:0,behavior:'auto'}));
}
function chartStats(r){
  const series=chartSeries(r);
  const a=series.map(point=>point.value); if(!a.length) return '';
  const open=a[0],high=Math.max(...a),low=Math.min(...a),current=a[a.length-1];
  const delta=current-open;
  const pct=open?((delta/open)*100):0;
  const stats=[
    ['Open',money(open)],
    ['High',money(high)],
    ['Low',money(low)],
    ['Current',money(current)],
    ['Change',`${delta>=0?'+':'-'}${money(Math.abs(delta))}`],
    ['Return',`${pct>=0?'+':''}${pct.toFixed(2)}%`]
  ];
  return `<div class="chart-stats">${stats.map(([label,value])=>`<div class="chart-stat"><small>${label}</small><strong class="${label==='Change'||label==='Return'?(delta>=0?'positive':'negative'):''}">${value}</strong></div>`).join('')}</div>`;
}
function detailedTrendSvg(r,height=250){
  const series=chartSeries(r);
  const a=series.map(point=>point.value); if(!a.length) return `<div class="chart-empty">No chart history yet.</div>`;
  const up=a[a.length-1]>=a[0];
  const color=up?'#58ef78':'#ff5e79';
  const W=1000,H=340,padL=18,padR=108,padT=18,padB=38;
  const current=a[a.length-1],open=a[0],high=Math.max(...a),low=Math.min(...a);
  const min=Math.min(...a),max=Math.max(...a),spread=max-min;
  const range=spread<.005?Math.max(1,current*.02):spread*1.12;
  const floor=spread<.005?min-range/2:min-(range*.04),ceil=floor+range;
  const usableW=W-padL-padR,usableH=H-padT-padB;
  const x=i=>padL+(a.length===1?usableW:(i/(a.length-1))*usableW);
  const y=v=>padT+((ceil-v)/(ceil-floor))*usableH;
  const points=a.map((v,i)=>`${x(i).toFixed(2)},${y(v).toFixed(2)}`);
  const linePoints=points.join(' ');
  const fillPoints=`${padL},${H-padB} ${linePoints} ${x(a.length-1)},${H-padB}`;
  const ticks=[0,.25,.5,.75,1].map(t=>Number((ceil-(range*t)).toFixed(2)));
  const highIndex=a.indexOf(high),lowIndex=a.indexOf(low);
  const currentY=y(current),currentX=x(a.length-1);
  const priceTagY=Math.max(padT+12,Math.min(H-padB-12,currentY));
  const priceTagX=Math.min(W-76,currentX+18);
  const tickLines=ticks.map((v,idx)=>`<g><line x1="${padL}" y1="${y(v).toFixed(2)}" x2="${W-padR+8}" y2="${y(v).toFixed(2)}" class="stock-grid-line ${idx===ticks.length-1?'stock-grid-line--base':''}"></line><text x="${W-padR+14}" y="${(y(v)+4).toFixed(2)}" class="stock-y-label">${money(v)}</text></g>`).join('');
  const labelIndexes=[0,Math.floor((a.length-1)/2),a.length-1];
  const xLabels=labelIndexes.map((index,labelIndex)=>`<text x="${x(index).toFixed(2)}" y="${H-10}" text-anchor="${labelIndex===0?'start':labelIndex===2?'end':'middle'}" class="stock-x-label">${esc(formatAxisTime(series[index].time,chartRange))}</text>`).join('');
  const delta=current-open,pct=open?((delta/open)*100):0;
  return `<div class="stock-chart-wrap" style="height:${height}px" data-values="${a.join(',')}" data-times="${series.map(point=>Math.round(point.time)).join(',')}" data-range="${chartRange}" data-floor="${floor}" data-ceil="${ceil}" data-color="${color}">
    <svg class="stock-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Simulated ${chartRange} price chart for ${esc(r.name)}" onpointermove="trackChartPointer(event)" onpointerdown="beginChartPointer(event)" onpointerup="endChartPointer(event)" onpointercancel="endChartPointer(event)" onpointerleave="hideChartPointer(event)">
      <defs>
        <linearGradient id="stock-fill-${esc(r.id)}-${chartRange}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="${color}" stop-opacity="0.30"></stop>
          <stop offset="1" stop-color="${color}" stop-opacity="0.02"></stop>
        </linearGradient>
      </defs>
      ${tickLines}
      <polygon points="${fillPoints}" fill="url(#stock-fill-${esc(r.id)}-${chartRange})"></polygon>
      <line x1="${padL}" x2="${W-padR+8}" y1="${currentY.toFixed(2)}" y2="${currentY.toFixed(2)}" class="stock-price-line"></line>
      <polyline points="${linePoints}" class="stock-line" style="stroke:${color}"></polyline>
      <circle cx="${currentX.toFixed(2)}" cy="${currentY.toFixed(2)}" r="6" class="stock-current-marker" style="fill:${color}"></circle>
      <circle cx="${x(highIndex).toFixed(2)}" cy="${y(high).toFixed(2)}" r="4" class="stock-extreme-marker stock-high-marker"></circle>
      <circle cx="${x(lowIndex).toFixed(2)}" cy="${y(low).toFixed(2)}" r="4" class="stock-extreme-marker stock-low-marker"></circle>
      <rect x="${priceTagX.toFixed(2)}" y="${(priceTagY-14).toFixed(2)}" width="74" height="28" rx="8" class="stock-price-tag-box"></rect>
      <text x="${(priceTagX+37).toFixed(2)}" y="${(priceTagY+4).toFixed(2)}" text-anchor="middle" class="stock-price-tag-text">${money(current)}</text>
      ${xLabels}
    </svg>
    <div class="chart-crosshair" aria-hidden="true"></div>
    <div class="chart-hover-dot" aria-hidden="true"></div>
    <div class="chart-tooltip" role="status"><strong data-chart-price>${money(current)}</strong><span data-chart-time>${esc(formatHoverTime(series[series.length-1].time,chartRange))}</span></div>
  </div>
  <div class="stock-chart-footer"><span class="${delta>=0?'positive':'negative'}">${delta>=0?'+':''}${pct.toFixed(2)}% event-driven ${chartRange} history</span><span>High ${money(high)}</span><span>Low ${money(low)}</span></div>`;
}
function beginChartPointer(event){
  event.preventDefault();
  const wrap=event.currentTarget.closest('.stock-chart-wrap');
  if(wrap) wrap.dataset.dragging='true';
  try{event.currentTarget.setPointerCapture(event.pointerId)}catch{}
  trackChartPointer(event);
}
function endChartPointer(event){
  const wrap=event.currentTarget.closest('.stock-chart-wrap');
  trackChartPointer(event);
  if(wrap) wrap.dataset.dragging='false';
  try{event.currentTarget.releasePointerCapture(event.pointerId)}catch{}
  if(event.pointerType!=='mouse'&&wrap) setTimeout(()=>wrap.classList.remove('is-inspecting'),900);
}
function hideChartPointer(event){
  const wrap=event.currentTarget.closest('.stock-chart-wrap');
  if(wrap&&wrap.dataset.dragging!=='true') wrap.classList.remove('is-inspecting');
}
function trackChartPointer(event){
  const svg=event.currentTarget;
  const wrap=svg.closest('.stock-chart-wrap');
  if(!wrap) return;
  const values=String(wrap.dataset.values||'').split(',').map(Number).filter(Number.isFinite);
  const times=String(wrap.dataset.times||'').split(',').map(Number);
  if(!values.length) return;
  const rect=svg.getBoundingClientRect();
  const padLeft=rect.width*(18/1000),padRight=rect.width*(108/1000);
  const padTop=rect.height*(18/340),padBottom=rect.height*(38/340);
  const usableWidth=Math.max(1,rect.width-padLeft-padRight),usableHeight=Math.max(1,rect.height-padTop-padBottom);
  const pointerX=clamp(event.clientX-rect.left,padLeft,padLeft+usableWidth);
  const ratio=clamp((pointerX-padLeft)/usableWidth,0,1);
  const index=Math.round(ratio*(values.length-1));
  const pointX=padLeft+(index/(Math.max(1,values.length-1)))*usableWidth;
  const floor=Number(wrap.dataset.floor),ceil=Number(wrap.dataset.ceil),value=values[index];
  const pointY=padTop+((ceil-value)/Math.max(.0001,ceil-floor))*usableHeight;
  const crosshair=wrap.querySelector('.chart-crosshair');
  const dot=wrap.querySelector('.chart-hover-dot');
  const tooltip=wrap.querySelector('.chart-tooltip');
  if(!crosshair||!dot||!tooltip) return;
  crosshair.style.left=`${pointX}px`;
  dot.style.left=`${pointX}px`;
  dot.style.top=`${pointY}px`;
  dot.style.background=wrap.dataset.color||'#58ef78';
  tooltip.style.left=`${clamp(pointX,82,Math.max(82,rect.width-82))}px`;
  tooltip.style.top=`${Math.max(54,pointY)}px`;
  const price=tooltip.querySelector('[data-chart-price]');
  const time=tooltip.querySelector('[data-chart-time]');
  if(price) price.textContent=money(value);
  if(time) time.textContent=formatHoverTime(times[index],wrap.dataset.range||'1D');
  wrap.classList.add('is-inspecting');
}
function trendSvg(r,height=130,detailed=false){
  if(detailed) return detailedTrendSvg(r,height);
  const a=displayTrend(r); if(!a.length) return '';
  const min=Math.min(...a),max=Math.max(...a),range=max-min;
  const pts=a.map((v,i)=>`${(i/(a.length-1))*100},${range<.005?50:100-((v-min)/range)*86-7}`).join(' ');
  const up=a[a.length-1]>=a[0];
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Simulated price chart">
  <defs><linearGradient id="fill-${esc(r.id)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${up?'#58ef78':'#ff5e79'}" stop-opacity=".25"/><stop offset="1" stop-color="${up?'#58ef78':'#ff5e79'}" stop-opacity="0"/></linearGradient></defs>
  <polygon points="0,100 ${pts} 100,100" fill="url(#fill-${esc(r.id)})"></polygon>
  <polyline points="${pts}" class="chart-line" style="stroke:${up?'#58ef78':'#ff5e79'}"></polyline></svg>`;
}
function miniCard(r){
  const change=displayChange(r);
  return `<article class="card mini-card" onclick="openProfile('${esc(r.id)}')">
    <div class="person">${avatar(r)}<div class="person-copy"><strong>${esc(r.name)} <span class="ticker">${esc(r.ticker)}</span></strong><span>${esc(r.discipline)} · ${esc(r.leagueOrMedium)}</span></div></div>
    <div class="mini-row"><strong>${money(localPrice(r))}</strong><span class="${change>=0?'positive':'negative'}">${change>=0?'+':''}${change.toFixed(2)}%</span></div>
  </article>`;
}
function currentCounts(){
  return Object.fromEntries(['Athlete','Music','Actor','Creator'].map(c=>[c,currentRecords.filter(r=>r.primaryCategory===c).length]));
}
function dashboard(){
  const counts=currentCounts();
  const movers=[...currentRecords].filter(r=>Math.abs(displayChange(r))>=.005).sort((a,b)=>displayChange(b)-displayChange(a)).slice(0,4);
  const feature=[...currentRecords].sort((a,b)=>b.careerScore-a.careerScore)[0]||currentRecords[0];
  const featureChange=displayChange(feature);
  const sports=Object.entries(currentRecords.filter(r=>r.primaryCategory==='Athlete').reduce((o,r)=>(o[r.discipline]=(o[r.discipline]||0)+1,o),{})).sort((a,b)=>b[1]-a[1]).slice(0,10);
  return `${note()}
  <div class="grid hero-grid">
    <section class="card hero">
      <div class="eyebrow">Current-first TalentX v3</div>
      <h1>Follow careers while<br><span class="gradient">they are still being written.</span></h1>
      <p>The main market prioritizes current talent. The deployed catalog is rebuilt from current team-roster feeds, while historical profiles remain in a separate Legacy market and uncertain profiles remain Under Review.</p>
      <div class="button-row"><button class="btn primary" onclick="setSegment('Current')">Explore current talent</button><button class="btn secondary" onclick="go('rules')">See retirement rules</button></div>
      <div class="stats"><div class="stat"><small>Current catalog</small><strong>${Number(manifest.currentCatalogRecords||manifest.currentSeedRecords||currentRecords.length).toLocaleString()}</strong></div><div class="stat"><small>Legacy catalog</small><strong>${manifest.legacyRecords.toLocaleString()}</strong></div><div class="stat"><small>Under review</small><strong>${manifest.underReviewRecords.toLocaleString()}</strong></div></div>
    </section>
    <section class="card featured">
      <div class="section-head"><h3>Featured current listing</h3><button class="link" onclick="openProfile('${esc(feature.id)}')">View →</button></div>
      <div class="person">${avatar(feature)}<div class="person-copy"><strong>${esc(feature.name)} <span class="ticker">${esc(feature.ticker)}</span></strong><span>${esc(feature.discipline)} · ${esc(feature.leagueOrMedium)}</span></div></div>
      <div class="price-big">${money(localPrice(feature))}</div>
      <div class="${featureChange>=0?'positive':'negative'}">${Math.abs(featureChange)<.005?'0.00% · no new event':`${featureChange>=0?'+':''}${featureChange.toFixed(2)}% latest event move`}</div>
      <div class="chart">${trendSvg(feature)}</div>
    </section>
  </div>
  <section class="section"><div class="section-head"><h2>Latest event movers</h2><button class="link" onclick="setSegment('Current')">Open market →</button></div>${movers.length?`<div class="grid movers">${movers.map(miniCard).join('')}</div>`:`<div class="card empty">No price-changing events have been recorded in the latest refresh.</div>`}</section>
  <section class="section"><div class="section-head"><h2>Browse career categories</h2></div><div class="grid category-grid">
    ${['Athlete','Music','Actor','Creator'].map(c=>`<article class="card category-card" onclick="setCategory('${c}')"><span class="count">${counts[c]}</span><div class="icon">${ICONS[c]}</div><h3>${c==='Music'?'Music':c+'s'}</h3><p>${c==='Athlete'?'Filter by sport, league or tour, team, role, country, and career status.':c==='Music'?'Filter by genre, solo or group, region, activity, and career status.':c==='Actor'?'Filter by film, television, stage, voice, project activity, and status.':'Filter by platform, niche, country, activity, and status.'}</p></article>`).join('')}
  </div></section>
  <section class="section"><div class="section-head"><h2>Sports in the current catalog</h2><button class="link" onclick="setCategory('Athlete')">All sports →</button></div><div class="grid discipline-grid">
    ${sports.map(([name,count])=>`<article class="card discipline" onclick="setDiscipline('${esc(name)}')"><strong>${esc(name)}</strong><span>${count.toLocaleString()} current listing${count===1?'':'s'}</span></article>`).join('')}
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
    'change-desc':(a,b)=>displayChange(b)-displayChange(a),
    'change-asc':(a,b)=>displayChange(a)-displayChange(b),
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
    <td>${money(localPrice(r))}</td><td class="${displayChange(r)>=0?'positive':'negative'}">${displayChange(r)>=0?'+':''}${displayChange(r).toFixed(2)}%</td>
    <td>${Number(r.careerScore).toFixed(1)}</td><td>${Math.round(Number(r.pricingConfidence??r.dataConfidence??0)*100)}%</td>
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
  return `${note()}<div class="eyebrow">${esc(filters.segment)} market</div><h1 class="page-title">Talent market</h1><p class="page-sub">The Current market uses the latest generated roster snapshot. Legacy and Under Review records load separately so historical profiles do not crowd the active experience.</p>
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
  <section class="card table-card"><div class="table-wrap"><table class="market-table"><thead><tr><th>Person</th><th>Category</th><th>Sport / genre / niche</th><th>League / medium</th><th>Career stage</th><th>Market</th><th>Price</th><th>Move</th><th>Score</th><th>Price confidence</th></tr></thead><tbody>${rows.length?rows.map(rowHtml).join(''):`<tr><td colspan="10"><div class="empty">No records match these filters.</div></td></tr>`}</tbody></table></div>
  <div class="pagination"><span>Showing ${arr.length?start+1:0}–${Math.min(start+PAGE_SIZE,arr.length)} of ${arr.length.toLocaleString()}</span><div class="pagination-controls"><button onclick="changePage(-1)" ${filters.page<=1?'disabled':''}>← Previous</button><span>Page ${filters.page} of ${pages}</span><button onclick="changePage(1)" ${filters.page>=pages?'disabled':''}>Next →</button></div></div></section>`;
}
function changePage(delta){filters.page=Math.max(1,filters.page+delta);render();window.scrollTo({top:0,behavior:'smooth'})}
function metricGrid(metrics){
  return `<div class="grid metrics">${Object.entries(metrics).map(([k,v])=>`<div class="metric"><div class="metric-top"><span>${esc(k.replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase()))}</span><b>${Number(v).toFixed(1)}</b></div><div class="track"><span style="width:${Math.max(0,Math.min(100,Number(v)))}%"></span></div></div>`).join('')}</div>`;
}
function priceEventSummary(r){
  if(!r.lastPriceEvent)return '';
  const move=Number(r.lastGameMovePct||0),delta=Number(r.lastGamePerformanceDeltaPct||0);
  const date=r.lastPriceEventAt?new Date(r.lastPriceEventAt):null;
  const dateText=date&&!Number.isNaN(date.getTime())?date.toLocaleString([],{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}):'Time unavailable';
  const stats=Object.entries(r.lastGameStats||{}).filter(([,value])=>Number.isFinite(Number(value))).slice(0,8).map(([key,value])=>`${esc(key.replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase()))}: ${Number(value).toFixed(Number(value)%1?1:0)}`).join(' · ');
  const comparison=Math.abs(delta)<.01?'matched the season baseline':`${Math.abs(delta).toFixed(1)}% ${delta>0?'above':'below'} the player’s season baseline`;
  return `<div class="source-box"><small>Latest completed-game price event</small><strong>${esc(r.lastPriceEvent)}</strong><small>${esc(dateText)} · ${move>=0?'+':''}${move.toFixed(2)}% price move · Box-score performance ${comparison}.</small>${stats?`<small>${stats}</small>`:''}</div>`;
}
function profile(){
  const r=byId(selectedId); if(!r) return `<div class="card empty">Profile not found.</div>`;
  const shares=Number(state.holdings[r.id]||0);
  const change=displayChange(r);
  const metrics=r.modelType.startsWith('Legacy')?r.legacyMetrics:r.activeMetrics;
  const sourceLink=r.sourceUrl?`<a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener">Open source reference ↗</a>`:'';
  return `${note()}<div class="grid detail-grid"><section>
    <article class="card profile-card">
      <div class="profile-head"><div class="profile-id">${avatar(r,true)}<div><h1>${esc(r.name)} <span class="ticker">${esc(r.ticker)}</span></h1><p>${esc(r.role)} · ${esc(r.discipline)} · ${esc(r.leagueOrMedium)}${r.teamOrPlatform&&r.teamOrPlatform!=='—'?`<br>${esc(r.teamOrPlatform)}`:''}</p></div></div>
      <div class="profile-price"><strong>${money(localPrice(r))}</strong><span class="${change>=0?'positive':'negative'}">${Math.abs(change)<.005?'0.00% · no new event':`${change>=0?'+':''}${change.toFixed(2)}% latest event move`}</span></div></div>
      <div class="badge-row"><span class="segment-badge ${segmentClass(r.marketSegment)}">${esc(r.marketSegment)} market</span><span class="status-badge">${esc(r.careerStatus)}</span><span class="stage-badge">${esc(r.careerStage||'Stage under review')}</span><span class="quality-badge">${Math.round(Number(r.pricingConfidence??r.dataConfidence??0)*100)}% price confidence</span></div>
      <div class="profile-chart detailed">${chartRangeTabs()}${trendSvg(r,260,true)}${chartStats(r)}<div class="chart-inspect-help">Move your cursor or drag across the line to inspect recorded event-driven prices. A flat line means no price-changing event occurred.</div></div>
      <div class="tab-row">${['overview','pricing','data'].map(t=>`<button class="${profileTab===t?'active':''}" onclick="setProfileTab('${t}')">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</div>
      ${profileTab==='overview'?`<div class="grid info-grid"><div class="info-box"><small>Market segment</small><strong>${esc(r.marketSegment)}</strong></div><div class="info-box"><small>Career model</small><strong>${esc(r.modelType)}</strong></div><div class="info-box"><small>Career stage</small><strong>${esc(r.careerStage||'Stage under review')}</strong></div><div class="info-box"><small>Career score</small><strong>${Number(r.careerScore).toFixed(1)} / 100</strong></div><div class="info-box"><small>Country</small><strong>${esc(r.country)}</strong></div><div class="info-box"><small>Role</small><strong>${esc(r.role)}</strong></div><div class="info-box"><small>Virtual volume</small><strong>${compact(r.volume)}</strong></div></div><p class="page-sub" style="margin-top:18px">${esc(r.description)}</p>`:''}
      ${profileTab==='pricing'?`<div class="grid info-grid"><div class="info-box"><small>Fundamental value</small><strong>${money(r.fundamentalValue)}</strong></div><div class="info-box"><small>Pricing confidence</small><strong>${Math.round(Number(r.pricingConfidence??r.dataConfidence??0)*100)}%</strong></div><div class="info-box"><small>Pricing evidence</small><strong>${esc(r.pricingDataStatus||'Model status not listed')}</strong></div><div class="info-box"><small>Model version</small><strong>${esc(r.pricingModelVersion||'Legacy prototype')}</strong></div><div class="info-box"><small>Demand premium</small><strong>${r.demandPremiumPct>=0?'+':''}${Number(r.demandPremiumPct).toFixed(2)}%</strong></div><div class="info-box"><small>Momentum</small><strong>${r.momentumPct>=0?'+':''}${Number(r.momentumPct).toFixed(2)}%</strong></div></div>${r.rookiePricing?rookieProfilePricing(r):metricGrid(metrics)}${priceEventSummary(r)}${Array.isArray(r.pricingEvidence)&&r.pricingEvidence.length?`<div class="source-box"><small>Pricing evidence</small><strong>${r.pricingEvidence.length} cited source${r.pricingEvidence.length===1?'':'s'}</strong><small>${r.pricingEvidence.map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener">Open source</a>`).join(' · ')}</small></div>`:''}<div class="formula" style="margin-top:16px">${r.rookiePricing?'Rookie IPO price = draft capital + pre-professional performance + immediate opportunity + position value + development + availability + audience. Draft weight fades as professional evidence arrives.':r.marketSegment==='Legacy'?'Legacy price = weighted legacy score on a non-linear price curve + tightly controlled market activity':categoryFormula(r.primaryCategory)}</div>`:''}
      ${profileTab==='data'?`<div class="grid info-grid"><div class="info-box"><small>Verification</small><strong>${esc(r.verificationStatus)}</strong></div><div class="info-box"><small>Last verified</small><strong>${esc(r.lastVerifiedAt||'Not connected')}</strong></div><div class="info-box"><small>Status source</small><strong>${esc(r.statusSource)}</strong></div></div><div class="source-box"><small>Identity/source snapshot</small><strong>${esc(r.sourceName)}</strong>${sourceLink}<small>Roster verification is a point-in-time snapshot, not a permanent guarantee. TalentX refreshes the catalog on a schedule; market prices and scores remain simulated.</small></div>`:''}
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
  return `${note()}<div class="eyebrow">Saved talent</div><h1 class="page-title">Watchlist</h1><p class="page-sub">Track talent across Current, Legacy, and Under Review markets.</p>${records.length?`<div class="grid watch-grid">${records.map(r=>`<article class="card watch" onclick="openProfile('${esc(r.id)}')"><div class="person">${avatar(r)}<div class="person-copy"><strong>${esc(r.name)}</strong><span>${esc(r.marketSegment)} · ${esc(r.discipline)}</span></div></div><div class="mini-row"><strong>${money(localPrice(r))}</strong><span class="${displayChange(r)>=0?'positive':'negative'}">${displayChange(r)>=0?'+':''}${displayChange(r).toFixed(2)}%</span></div></article>`).join('')}</div>`:`<div class="card empty">Your watchlist is empty.</div>`}`;
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
    ${Object.entries(taxonomy?.pricingModels?.active?.categories||CATEGORY_MODEL_FALLBACKS).map(([category,model])=>{const weights=model.weights||model;return `<article class="card rule"><h3>${esc(category)} model</h3><div class="formula">${esc(categoryFormula(category))}</div><p>${category==='Athlete'?'Audience demand is handled as a small market adjustment rather than replacing verified performance.':'The current generic metrics are interpreted by profession until dedicated evidence feeds are available.'}</p><div class="weight-list">${Object.entries(weights).map(([k,v])=>`<div class="weight-row"><span>${esc(METRIC_LABELS[k]||k)}</span><strong>${Math.round(v*100)}%</strong></div>`).join('')}</div></article>`}).join('')}
    <article class="card rule"><h3>Universal calibration</h3><div class="formula">70% absolute category evidence + 30% profession-peer position</div><p>Evidence quality caps unsupported scores. Curated benchmark records remain temporary until profession-specific data is verified, while roster-only records remain conservatively capped.</p></article>
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
    const loadCurrent=async()=>{
      const generated=await fetch('./data/current_catalog.json');
      if(generated.ok) return generated.json();
      const fallback=await fetch('./data/current_seed.json');
      if(!fallback.ok) throw new Error('current catalog');
      return fallback.json();
    };
    const [cur,tax,man]=await Promise.all([
      loadCurrent(),
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
