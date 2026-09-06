/* Keep detailed profile pricing events out of the default view.
 * The existing event timeline still owns event data and source details; this
 * disclosure layer only controls whether the event rows are visible.
 */
(() => {
  const STYLE_ID='talentx-event-timeline-collapse-style';
  let scheduled=false;

  function ensureStyle(){
    if(document.getElementById(STYLE_ID)) return;
    const style=document.createElement('style');
    style.id=STYLE_ID;
    style.textContent=`
      #talentxEventTimeline:not(.tx-events-user-open) .tx-event-list,
      #talentxEventTimeline:not(.tx-events-user-open) .tx-event-more:not(.tx-event-disclosure){display:none!important}
      #talentxEventTimeline:not(.tx-events-user-open) .tx-event-head{margin-bottom:0}
      .tx-event-disclosure{display:block;width:100%;margin-top:10px;padding:11px 12px;border:1px solid rgba(138,191,255,.2);border-radius:10px;background:rgba(138,191,255,.06);color:#8abfff;font:inherit;font-size:.8rem;font-weight:800;cursor:pointer}
      .tx-event-disclosure:hover,.tx-event-disclosure:focus-visible{background:rgba(138,191,255,.11);outline:none}
      #talentxEventTimeline.tx-events-user-open .tx-event-disclosure{margin-bottom:4px}
    `;
    document.head.appendChild(style);
  }

  function apply(){
    scheduled=false;
    ensureStyle();
    const timeline=document.getElementById('talentxEventTimeline');
    if(!timeline) return;

    let recordId='';
    try{recordId=typeof selectedId!=='undefined'?String(selectedId||''):'';}catch{}
    if(timeline.dataset.collapseRecord!==recordId){
      timeline.dataset.collapseRecord=recordId;
      timeline.dataset.userExpanded='false';
      timeline.classList.remove('tx-events-user-open');
    }

    let button=timeline.querySelector('.tx-event-disclosure');
    if(!button){
      button=document.createElement('button');
      button.type='button';
      button.className='tx-event-disclosure';
      const head=timeline.querySelector('.tx-event-head');
      if(head) head.insertAdjacentElement('afterend',button);
      else timeline.prepend(button);
      button.addEventListener('click',()=>{
        const open=timeline.dataset.userExpanded!=='true';
        timeline.dataset.userExpanded=open?'true':'false';
        timeline.classList.toggle('tx-events-user-open',open);
        button.setAttribute('aria-expanded',open?'true':'false');
        button.textContent=open?'Hide pricing events':'View more';
      });
    }

    const open=timeline.dataset.userExpanded==='true';
    timeline.classList.toggle('tx-events-user-open',open);
    button.setAttribute('aria-expanded',open?'true':'false');
    button.setAttribute('aria-controls','talentxEventTimeline');
    button.textContent=open?'Hide pricing events':'View more';
  }

  function schedule(){
    if(scheduled) return;
    scheduled=true;
    requestAnimationFrame(apply);
  }

  const app=document.getElementById('app');
  if(app) new MutationObserver(schedule).observe(app,{childList:true,subtree:true});
  document.addEventListener('DOMContentLoaded',schedule,{once:true});
  schedule();
  window.talentxEventTimelineDisclosure='collapsed-by-default-v1';
})();
