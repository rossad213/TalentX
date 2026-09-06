/* TalentX verified-history coverage presentation.
 * Long chart ranges must not imply that TalentX observed prices before durable
 * event history existed. Full coverage renders normally; partial coverage masks
 * the unobserved prefix; no coverage renders an explicit pending/empty state.
 * Detailed methodology belongs in Data & Rules, not on every profile chart.
 */
(function(){
  if(typeof detailedTrendSvg!=='function'||typeof chartStats!=='function') return;
  const priorDetailedTrendSvg=detailedTrendSvg;
  const priorChartStats=chartStats;
  const EVENT_CATEGORIES=new Set(['Athlete','Music','Actor','Creator']);
  const RULES_CARD_ID='talentxHistoricalChartMethodology';

  function eventCategory(record){
    return EVENT_CATEGORIES.has(String(record?.primaryCategory||''));
  }
  function coverage(record,range=chartRange){
    if(!eventCategory(record)||typeof window.talentxEventCoverage!=='function') return null;
    try{return window.talentxEventCoverage(record,range);}catch{return null;}
  }
  function dateLabel(time){
    if(!Number.isFinite(Number(time))) return '';
    return new Date(Number(time)).toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
  }
  function seriesValues(record){
    return chartSeries(record,chartRange).filter(point=>point&&point.verified===true&&Number.isFinite(Number(point.value)));
  }

  detailedTrendSvg=function(record,height=250){
    const info=coverage(record,chartRange);
    if(!info) return priorDetailedTrendSvg(record,height);
    if(info.status==='complete') return priorDetailedTrendSvg(record,height);

    const current=Math.max(1,Number(localPrice(record))||1);
    if(info.status==='none'){
      return `<div class="stock-chart-wrap event-coverage-empty" style="height:${height}px">
        <div class="event-coverage-empty-inner">
          <strong>No verified event movement found yet for ${esc(chartRange)}</strong>
          <span>TalentX is still expanding verification coverage. This does not mean the price was flat; it means no supported event has been attached to this period yet.</span>
          <small>Current market price ${money(current)}</small>
        </div>
      </div>
      <div class="stock-chart-footer event-coverage-footer"><span>Verification coverage pending for this period</span><span>Current ${money(current)}</span></div>`;
    }

    const start=Number(info.start),now=Number(info.now),coverageStart=Number(info.coverageStart);
    const fraction=(Number.isFinite(start)&&Number.isFinite(now)&&now>start&&Number.isFinite(coverageStart))
      ? Math.max(0,Math.min(1,(coverageStart-start)/(now-start)))
      : 0;
    // detailedTrendSvg uses an SVG plot from x=18 to x=892 in a 1000-unit viewBox.
    const visualPct=1.8+(fraction*87.4);
    const label=dateLabel(coverageStart);
    let output=priorDetailedTrendSvg(record,height);
    output=output.replace('class="stock-chart-wrap"',`class="stock-chart-wrap event-coverage-partial" data-coverage-start="${Math.round(coverageStart)}"`);
    output=output.replace(`style="height:${height}px"`,`style="height:${height}px;--event-coverage-left:${visualPct.toFixed(2)}%"`);
    output=output.replace('</svg>',`</svg><div class="event-coverage-boundary" style="left:${visualPct.toFixed(2)}%"><span>Verified history begins ${esc(label)}</span></div>`);
    output=output.replace(/event-driven ([^<]+) history/,`verified since ${esc(label)} · partial $1 coverage`);
    return output;
  };

  chartStats=function(record){
    const info=coverage(record,chartRange);
    if(!info||info.status==='complete') return priorChartStats(record);
    const current=Math.max(1,Number(localPrice(record))||1);
    if(info.status==='none'){
      return `<div class="chart-stats event-coverage-stats">
        <div class="chart-stat"><small>Current</small><strong>${money(current)}</strong></div>
        <div class="chart-stat"><small>Verification</small><strong>Coverage pending</strong></div>
        <div class="chart-stat"><small>${esc(chartRange)} return</small><strong>—</strong></div>
      </div>`;
    }

    const known=seriesValues(record);
    if(!known.length) return priorChartStats(record);
    const values=known.map(point=>Number(point.value));
    const open=values[0],high=Math.max(...values),low=Math.min(...values),last=values[values.length-1];
    const delta=last-open;
    const pct=open?((delta/open)*100):0;
    const label=dateLabel(info.coverageStart);
    return `<div class="chart-stats event-coverage-stats">
      <div class="chart-stat"><small>Verified open</small><strong>${money(open)}</strong></div>
      <div class="chart-stat"><small>High</small><strong>${money(high)}</strong></div>
      <div class="chart-stat"><small>Low</small><strong>${money(low)}</strong></div>
      <div class="chart-stat"><small>Current</small><strong>${money(last)}</strong></div>
      <div class="chart-stat"><small>Verified return</small><strong class="${delta>=0?'positive':'negative'}">${pct>=0?'+':''}${pct.toFixed(2)}%</strong></div>
      <div class="chart-stat"><small>Coverage</small><strong>Since ${esc(label)}</strong></div>
    </div>`;
  };

  function currentRoute(){
    try{return typeof route!=='undefined'?String(route||''):'';}catch{return '';}
  }

  function addRulesMethodology(){
    const app=document.getElementById('app');
    if(!app) return;
    const existing=document.getElementById(RULES_CARD_ID);
    if(currentRoute()!=='rules'){
      existing?.remove();
      return;
    }
    if(existing) return;

    const card=document.createElement('article');
    card.id=RULES_CARD_ID;
    card.className='card';
    card.style.cssText='margin-top:17px;padding:22px';
    card.innerHTML=`
      <div class="eyebrow">Chart transparency</div>
      <h2 style="margin:6px 0 10px;font-size:1.25rem">Historical chart methodology</h2>
      <p style="margin:0;color:var(--muted);line-height:1.65">TalentX historical charts use real-world events only when the event fact, date, and supporting source are available. Backfilled TalentX prices are simulated model responses to those verified events, reconstructed from the current TalentX price; they are not actual historical security prices. Unsupported or undated events are omitted rather than estimated.</p>
      <div style="margin-top:16px;padding-top:15px;border-top:1px solid var(--line)">
        <strong style="display:block;margin-bottom:7px">Event movement limits</strong>
        <p style="margin:0;color:var(--muted);line-height:1.65">Many athlete game and career-event calculations currently cap a single modeled event move at ±2.50%. Standard Music, Actor, and Creator event refreshes currently cap a single modeled event move at ±1.50%. These are event-level limits, not a universal daily price limit. A displayed move can differ by about 0.01 percentage point when the resulting price is rounded to cents.</p>
      </div>`;
    app.appendChild(card);
  }

  let rulesScheduled=false;
  function scheduleRulesMethodology(){
    if(rulesScheduled) return;
    rulesScheduled=true;
    requestAnimationFrame(()=>{rulesScheduled=false;addRulesMethodology();});
  }
  const app=document.getElementById('app');
  if(app) new MutationObserver(scheduleRulesMethodology).observe(app,{childList:true,subtree:true});
  document.addEventListener('DOMContentLoaded',scheduleRulesMethodology,{once:true});
  scheduleRulesMethodology();

  window.talentxChartCoveragePresentation='verified-range-coverage-v5-rules-methodology';
})();
