/* TalentX watchlist + portfolio event notifications. */
(() => {
  let user=null, notifications=[], prefs=null, scanTimer=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const client=()=>window.talentxSupabase||null;
  const records=()=>{try{return Array.isArray(currentRecords)?currentRecords:[];}catch{return [];}};
  const byId=id=>records().find(r=>String(r.id)===String(id));
  const fmtDate=v=>{const d=new Date(v);return Number.isNaN(d.getTime())?'':d.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});};
  const latestEvent=r=>{
    const list=Array.isArray(r?.priceEvents)?r.priceEvents.filter(e=>e&&e.synthetic!==true&&e.reconstructed!==true):[];
    const sorted=[...list].sort((a,b)=>Date.parse(b.startedAt||b.eventDate||b.date||0)-Date.parse(a.startedAt||a.eventDate||a.date||0));
    return sorted[0]||null;
  };
  const eventTime=(r,e)=>e?.startedAt||e?.eventDate||e?.date||r?.lastPriceEventAt||null;
  const movePct=(r,e)=>{
    const direct=Number(e?.movePct??e?.priceMovePct??e?.outcomeMovePct);
    if(Number.isFinite(direct)) return direct;
    const last=Number(r?.lastGameMovePct);
    if(Number.isFinite(last)&&last!==0) return last;
    const before=Number(r?.previousMarketPrice), after=Number(r?.marketPrice);
    return Number.isFinite(before)&&before>0&&Number.isFinite(after)?((after-before)/before)*100:0;
  };
  const eventKey=(r,e,type,time)=>`${type}:${r.id}:${e?.eventKey||e?.eventId||e?.id||time}`;

  function ensureBell(){
    const actions=document.querySelector('.top-actions'); if(!actions||!user) return null;
    let b=document.getElementById('talentxNotificationBell');
    if(!b){b=document.createElement('button');b.id='talentxNotificationBell';b.className='tx-notification-bell';b.type='button';b.setAttribute('aria-label','Notifications');b.innerHTML='<span class="tx-bell-icon">♢</span><span class="tx-notification-count" hidden>0</span>';b.onclick=e=>{e.stopPropagation();togglePanel();};actions.insertBefore(b,document.getElementById('talentxAccountTrigger')||null);}
    updateCount(); return b;
  }
  function updateCount(){const n=notifications.filter(x=>!x.read_at).length;const el=document.querySelector('.tx-notification-count');if(el){el.textContent=n>99?'99+':String(n);el.hidden=!n;}}
  function closePanel(){document.getElementById('talentxNotificationPanel')?.remove();}
  function renderRows(){
    if(!notifications.length) return '<div class="tx-notification-empty"><strong>No alerts yet</strong><span>Verified events for talent you follow or own will appear here.</span></div>';
    return notifications.map(n=>`<button class="tx-notification-row ${n.read_at?'':'unread'}" data-id="${esc(n.id)}" data-talent="${esc(n.talent_id)}" type="button"><span class="tx-notification-dot"></span><span class="tx-notification-main"><strong>${esc(n.title)}</strong><span>${esc(n.body||'')}</span><small>${esc(fmtDate(n.event_at))} · ${n.alert_type==='portfolio'?'Portfolio':'Watchlist'}</small></span></button>`).join('');
  }
  function togglePanel(){
    const existing=document.getElementById('talentxNotificationPanel');if(existing){existing.remove();return;}
    const bell=ensureBell();if(!bell)return;const rect=bell.getBoundingClientRect();
    const panel=document.createElement('section');panel.id='talentxNotificationPanel';panel.className='tx-notification-panel';panel.innerHTML=`<header><div><strong>Notifications</strong><span>Verified TalentX events</span></div><button id="txMarkAllRead" type="button">Mark all read</button></header><div class="tx-notification-list">${renderRows()}</div>`;document.body.appendChild(panel);
    const w=panel.offsetWidth;panel.style.left=`${Math.max(12,Math.min(innerWidth-w-12,rect.right-w))}px`;panel.style.top=`${Math.min(innerHeight-panel.offsetHeight-12,rect.bottom+8)}px`;
    panel.querySelector('#txMarkAllRead')?.addEventListener('click',e=>{e.stopPropagation();markAllRead();});
    panel.querySelectorAll('.tx-notification-row').forEach(row=>row.addEventListener('click',async()=>{await markRead(row.dataset.id);closePanel();if(typeof openProfile==='function')openProfile(row.dataset.talent);}));
  }
  async function loadNotifications(){if(!user||!client())return;const {data,error}=await client().from('notifications').select('*').eq('user_id',user.id).order('event_at',{ascending:false}).limit(100);if(error){console.warn('TalentX notifications load failed',error);return;}notifications=data||[];ensureBell();updateCount();}
  async function markRead(id){if(!id||!client())return;const now=new Date().toISOString();const {error}=await client().from('notifications').update({read_at:now}).eq('id',id);if(!error){notifications=notifications.map(n=>n.id===id?{...n,read_at:now}:n);updateCount();}}
  async function markAllRead(){if(!user||!client())return;const now=new Date().toISOString();const {error}=await client().from('notifications').update({read_at:now}).eq('user_id',user.id).is('read_at',null);if(!error){notifications=notifications.map(n=>({...n,read_at:n.read_at||now}));updateCount();closePanel();togglePanel();}}

  async function loadPrefs(){const {data}=await client().from('profiles').select('watchlist_alerts_enabled,portfolio_alerts_enabled').eq('id',user.id).maybeSingle();prefs=data||{};}
  async function scan(){
    if(!user||!client()||!records().length)return;
    await loadPrefs();
    const [w,h]=await Promise.all([client().from('watchlist').select('talent_id').eq('user_id',user.id),client().from('holdings').select('talent_id,shares').eq('user_id',user.id).gt('shares',0)]);
    if(w.error||h.error)return;
    const sources=[];
    if(prefs?.watchlist_alerts_enabled!==false) (w.data||[]).forEach(x=>sources.push({id:x.talent_id,type:'watchlist'}));
    if(prefs?.portfolio_alerts_enabled!==false) (h.data||[]).forEach(x=>sources.push({id:x.talent_id,type:'portfolio'}));
    const seen=new Set();const rows=[];const cutoff=Date.now()-7*24*60*60*1000;
    for(const src of sources){
      const r=byId(src.id);if(!r)continue;const e=latestEvent(r);const time=eventTime(r,e);const ts=Date.parse(time||'');if(!Number.isFinite(ts)||ts<cutoff||ts>Date.now()+3600000)continue;
      const move=movePct(r,e);if(Math.abs(move)<0.25)continue;
      const key=eventKey(r,e,src.type,time);if(seen.has(key))continue;seen.add(key);
      const label=e?.name||e?.label||e?.title||e?.eventType||'Verified market event';
      rows.push({user_id:user.id,talent_id:String(r.id),alert_type:src.type,event_key:key,title:`${r.name}: ${label}`,body:`${move>=0?'+':''}${move.toFixed(2)}% verified event move`,event_at:new Date(ts).toISOString()});
    }
    if(rows.length){const {error}=await client().from('notifications').upsert(rows,{onConflict:'user_id,event_key',ignoreDuplicates:true});if(error)console.warn('TalentX notification generation failed',error);}
    await loadNotifications();
  }
  async function bind(){const c=client();if(!c)return false;const {data}=await c.auth.getSession();user=data.session?.user||null;if(user){ensureBell();await loadNotifications();scan();clearInterval(scanTimer);scanTimer=setInterval(scan,5*60*1000);}c.auth.onAuthStateChange(async(_event,session)=>{user=session?.user||null;closePanel();if(user){ensureBell();await loadNotifications();scan();}else{document.getElementById('talentxNotificationBell')?.remove();notifications=[];}});return true;}
  document.addEventListener('click',e=>{const p=document.getElementById('talentxNotificationPanel'),b=document.getElementById('talentxNotificationBell');if(p&&!p.contains(e.target)&&!b?.contains(e.target))closePanel();});
  window.addEventListener('resize',closePanel);
  const timer=setInterval(async()=>{if(await bind())clearInterval(timer);},300);setTimeout(()=>clearInterval(timer),15000);
})();