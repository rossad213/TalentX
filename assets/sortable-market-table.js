/* TalentX clickable, persistent market-table sorting. */
(function(){
  const STORAGE_KEY='talentx_market_column_sort_v1';
  const columns={
    person:{label:'Person',type:'text',value:r=>r.name},
    category:{label:'Category',type:'text',value:r=>r.primaryCategory},
    discipline:{label:'Subcategory',type:'text',value:r=>r.discipline},
    league:{label:'Meduim',type:'text',value:r=>r.leagueOrMedium},
    stage:{label:'Career stage',type:'stage',value:r=>r.careerStage||'Stage under review'},
    market:{label:'Market',type:'text',value:r=>r.marketSegment},
    price:{label:'Price',type:'number',value:r=>localPrice(r)},
    move:{label:'Move',type:'number',value:r=>displayChange(r)},
    score:{label:'Score',type:'number',value:r=>Number(r.careerScore||0)},
    confidence:{label:'Price confidence',type:'number',value:r=>Number(r.pricingConfidence??r.dataConfidence??0)}
  };
  const stageOrder=['Pre-debut','Rookie','Early Career','Emerging','Prime','Established','Veteran','Late Career','Retired','Stage under review'];
  let current={key:'score',direction:'desc'};
  try{
    const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||'null');
    if(saved&&columns[saved.key]&&['asc','desc'].includes(saved.direction))current=saved;
  }catch{}

  function compare(a,b,column,direction){
    const av=column.value(a),bv=column.value(b);
    let result=0;
    if(column.type==='number'){
      result=(Number(av)||0)-(Number(bv)||0);
    }else if(column.type==='stage'){
      const ai=stageOrder.indexOf(String(av)),bi=stageOrder.indexOf(String(bv));
      result=(ai<0?999:ai)-(bi<0?999:bi);
      if(!result)result=String(av).localeCompare(String(bv),undefined,{sensitivity:'base'});
    }else{
      result=String(av||'').localeCompare(String(bv||''),undefined,{numeric:true,sensitivity:'base'});
    }
    if(!result)result=String(a.name||'').localeCompare(String(b.name||''),undefined,{sensitivity:'base'});
    return direction==='desc'?-result:result;
  }

  const baseFilteredRecords=filteredRecords;
  filteredRecords=function(){
    const records=baseFilteredRecords();
    const column=columns[current.key];
    return column?[...records].sort((a,b)=>compare(a,b,column,current.direction)):records;
  };

  window.sortMarketColumn=function(key){
    if(!columns[key])return;
    if(current.key===key){
      current={key,direction:current.direction==='asc'?'desc':'asc'};
    }else{
      current={key,direction:columns[key].type==='number'?'desc':'asc'};
    }
    filters.page=1;
    filters.sort=`column-${current.key}-${current.direction}`;
    try{localStorage.setItem(STORAGE_KEY,JSON.stringify(current));}catch{}
    render();
  };

  function header(key){
    const column=columns[key],active=current.key===key;
    const direction=active?current.direction:'none';
    const arrow=active?(current.direction==='asc'?'▲':'▼'):'↕';
    const aria=active?(current.direction==='asc'?'ascending':'descending'):'none';
    return `<th aria-sort="${aria}" class="market-sort-th ${active?'is-sorted':''}"><button type="button" class="market-sort-button" onclick="sortMarketColumn('${key}')" title="Sort by ${column.label}"><span>${column.label}</span><span class="market-sort-arrow" aria-hidden="true">${arrow}</span></button></th>`;
  }

  const baseMarket=market;
  market=function(){
    let html=baseMarket();
    const labels={person:'Person',category:'Category',discipline:'Sport / genre / niche',league:'League / medium',stage:'Career stage',market:'Market',price:'Price',move:'Move',score:'Score',confidence:'Price confidence'};
    Object.entries(labels).forEach(([key,label])=>{
      html=html.replace(`<th>${label}</th>`,header(key));
    });
    return html;
  };
})();
