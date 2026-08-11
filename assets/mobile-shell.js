/* TalentX mobile navigation, filters, and card-based market experience. */
(function(){
  const MOBILE_QUERY='(max-width: 820px)';
  const media=window.matchMedia(MOBILE_QUERY);
  let moreOpen=false;

  function isMobile(){return media.matches;}

  function logoSource(){
    const source=document.querySelector('.brand-logo');
    return source?.getAttribute('src')||source?.src||'';
  }

  function ensureMobileBrand(){
    const topbar=document.querySelector('.topbar');
    if(!topbar) return;
    let brand=topbar.querySelector('.mobile-brand');
    if(!brand){
      brand=document.createElement('button');
      brand.type='button';
      brand.className='mobile-brand';
      brand.setAttribute('aria-label','Go to TalentX dashboard');
      brand.innerHTML='<img alt="" aria-hidden="true"><span>TalentX</span>';
      brand.onclick=()=>go('dashboard');
      topbar.prepend(brand);
    }
    const img=brand.querySelector('img');
    const src=logoSource();
    if(img&&src&&img.getAttribute('src')!==src) img.setAttribute('src',src);
  }

  function ensureBottomNav(){
    let nav=document.querySelector('.mobile-bottom-nav');
    if(nav) return nav;
    nav=document.createElement('nav');
    nav.className='mobile-bottom-nav';
    nav.setAttribute('aria-label','Mobile navigation');
    nav.innerHTML=`
      <button type="button" data-mobile-route="dashboard" aria-label="Dashboard"><span class="mobile-nav-icon">⌂</span><span>Home</span></button>
      <button type="button" data-mobile-route="market" aria-label="Market"><span class="mobile-nav-icon">⌁</span><span>Market</span></button>
      <button type="button" data-mobile-route="portfolio" aria-label="Portfolio"><span class="mobile-nav-icon">▣</span><span>Portfolio</span></button>
      <button type="button" data-mobile-route="watchlist" aria-label="Watchlist"><span class="mobile-nav-icon">☆</span><span>Watchlist</span></button>
      <button type="button" data-mobile-more aria-label="More"><span class="mobile-nav-icon">•••</span><span>More</span></button>`;
    nav.querySelectorAll('[data-mobile-route]').forEach(button=>{
      button.onclick=()=>go(button.dataset.mobileRoute);
    });
    nav.querySelector('[data-mobile-more]').onclick=()=>setMoreOpen(!moreOpen);
    document.body.appendChild(nav);
    return nav;
  }

  function ensureMoreSheet(){
    let overlay=document.querySelector('.mobile-more-overlay');
    if(overlay) return overlay;
    overlay=document.createElement('div');
    overlay.className='mobile-more-overlay';
    overlay.setAttribute('aria-hidden','true');
    overlay.innerHTML=`<div class="mobile-more-backdrop"></div><section class="mobile-more-sheet" role="dialog" aria-modal="true" aria-label="More TalentX pages">
      <div class="mobile-sheet-handle" aria-hidden="true"></div>
      <div class="mobile-sheet-head"><div><small>TalentX</small><strong>More</strong></div><button type="button" class="mobile-sheet-close" aria-label="Close">×</button></div>
      <div class="mobile-more-links">
        <button type="button" data-more-route="leaderboard"><span>♕</span><div><strong>Leaderboard</strong><small>See market rankings</small></div><b>›</b></button>
        <button type="button" data-more-route="rules"><span>◎</span><div><strong>Data &amp; Rules</strong><small>Pricing, retirement, and methodology</small></div><b>›</b></button>
      </div>
    </section>`;
    overlay.querySelector('.mobile-more-backdrop').onclick=()=>setMoreOpen(false);
    overlay.querySelector('.mobile-sheet-close').onclick=()=>setMoreOpen(false);
    overlay.querySelectorAll('[data-more-route]').forEach(button=>{
      button.onclick=()=>{setMoreOpen(false);go(button.dataset.moreRoute);};
    });
    document.body.appendChild(overlay);
    return overlay;
  }

  function setMoreOpen(open){
    moreOpen=Boolean(open)&&isMobile();
    const overlay=ensureMoreSheet();
    overlay.classList.toggle('open',moreOpen);
    overlay.setAttribute('aria-hidden',moreOpen?'false':'true');
    document.body.classList.toggle('mobile-more-open',moreOpen);
    updateBottomNav();
  }
  window.setTalentXMobileMore=setMoreOpen;

  function updateBottomNav(){
    const nav=ensureBottomNav();
    nav.querySelectorAll('[data-mobile-route]').forEach(button=>{
      const active=button.dataset.mobileRoute===route;
      button.classList.toggle('active',active);
      button.setAttribute('aria-current',active?'page':'false');
    });
    const more=nav.querySelector('[data-mobile-more]');
    if(more){
      const active=moreOpen||route==='leaderboard'||route==='rules';
      more.classList.toggle('active',active);
      more.setAttribute('aria-expanded',moreOpen?'true':'false');
    }
  }

  function marketCardHtml(r){
    const change=displayChange(r);
    const place=r.teamOrPlatform&&r.teamOrPlatform!=='—'?r.teamOrPlatform:r.leagueOrMedium;
    return `<button type="button" class="mobile-market-card" onclick="openProfile('${esc(r.id)}')">
      <div class="mobile-market-card-top">
        <div class="mobile-market-person"><div class="small-avatar">${esc(r.avatar||'TX')}</div><div><strong>${esc(r.name)}</strong><span>${esc(r.role||r.discipline)} · ${esc(place||'')}</span></div></div>
        <span class="mobile-market-chevron">›</span>
      </div>
      <div class="mobile-market-price-row"><strong>${money(localPrice(r))}</strong><span class="${change>=0?'positive':'negative'}">${Math.abs(change)<.005?'0.00%':`${change>=0?'+':''}${change.toFixed(2)}%`}</span></div>
      <div class="mobile-market-meta"><span>${esc(r.primaryCategory)}</span><span>${esc(r.discipline)}</span><span>${esc(r.careerStage||'Stage under review')}</span></div>
      <div class="mobile-market-score"><small>Career score</small><strong>${Number(r.careerScore||0).toFixed(1)}</strong><small>Confidence</small><strong>${Math.round(Number(r.pricingConfidence??r.dataConfidence??0)*100)}%</strong></div>
    </button>`;
  }

  function enhanceMarket(){
    const app=document.getElementById('app');
    if(!app||route!=='market') return;
    const controls=[...app.querySelectorAll('.controls')];
    const segmentControls=controls[0];
    const filterControls=controls[1];
    if(segmentControls) segmentControls.classList.add('mobile-segment-controls');
    if(filterControls) filterControls.classList.add('mobile-filter-controls');

    let filterButton=app.querySelector('.mobile-filter-button');
    if(!filterButton&&filterControls){
      filterButton=document.createElement('button');
      filterButton.type='button';
      filterButton.className='mobile-filter-button';
      filterButton.innerHTML='<span>☷</span><strong>Filters &amp; sort</strong><b>›</b>';
      filterButton.onclick=()=>{
        const open=filterControls.classList.toggle('open');
        filterButton.classList.toggle('open',open);
        filterButton.setAttribute('aria-expanded',open?'true':'false');
      };
      filterButton.setAttribute('aria-expanded','false');
      filterControls.parentNode.insertBefore(filterButton,filterControls);
    }

    const tableCard=app.querySelector('.table-card');
    if(!tableCard) return;
    let cards=tableCard.querySelector('.mobile-market-cards');
    if(!cards){
      cards=document.createElement('div');
      cards.className='mobile-market-cards';
      const pagination=tableCard.querySelector('.pagination');
      tableCard.insertBefore(cards,pagination||null);
    }
    const records=filteredRecords();
    const pageSize=typeof PAGE_SIZE!=='undefined'?PAGE_SIZE:50;
    const page=Math.max(1,Number(filters.page)||1);
    const start=(page-1)*pageSize;
    const visible=records.slice(start,start+pageSize);
    cards.innerHTML=visible.length?visible.map(marketCardHtml).join(''):'<div class="mobile-market-empty">No records match these filters.</div>';
  }

  function enhanceCurrentView(){
    ensureMobileBrand();
    ensureBottomNav();
    ensureMoreSheet();
    updateBottomNav();
    enhanceMarket();
    document.body.classList.toggle('talentx-mobile',isMobile());
  }

  const baseRender=render;
  render=function(){
    const result=baseRender.apply(this,arguments);
    requestAnimationFrame(enhanceCurrentView);
    return result;
  };

  media.addEventListener?.('change',()=>{
    if(!isMobile()) setMoreOpen(false);
    enhanceCurrentView();
  });
  window.addEventListener('resize',enhanceCurrentView,{passive:true});
  window.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&moreOpen) setMoreOpen(false);
  });

  requestAnimationFrame(enhanceCurrentView);
})();
