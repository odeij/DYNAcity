const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_access.js`,'utf8');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const c0=[35.52,33.9],sy=111320,sx=sy*Math.cos(c0[1]*Math.PI/180);
const p=(x,y)=>[c0[0]+x/sx,c0[1]+y/sy];
// Four homes, a clinic and a park, two candidate sites and two crossings (the second changes nothing).
function bundle() {
  const homes=[p(0,0),p(100,0),p(200,0),p(300,0)];
  const scen=(method,N,K,alpha,uses,links,edges,rw,extra={})=>({method,budget:N,link_budget:K,alpha,uses,links,objective:1.2,
    gap_pct:method==='exact'||method==='exact_no_links'?(method==='exact'?0:null):0.8,optimal:method==='exact',residents_within:rw,edges,...extra});
  return {schema:'dynacity-access-view/1',label:'With rail bed',threshold_m:500,uses:['medical','parks'],
    homes:{lonlat:homes.flat(),residents:[10,20,30,40],total_residents:100},
    places:[{lonlat:p(0,50),use:0,name:'Clinic A',source:'OpenStreetMap'},{lonlat:p(300,50),use:1,name:'',source:'OpenStreetMap'}],
    sites:[{lonlat:p(150,-50),kind:'bbed',state:'stalled_or_cancelled',area_m2:400,length_m:null,allowed:[0,1]},
      {lonlat:p(250,-50),kind:'railbed',state:null,area_m2:null,length_m:100,allowed:[1]}],
    links:[{group:0,kind:'crossing',path:[p(200,-20),p(200,20)],mid:p(200,0),length_m:40,name:'Charles Helou',saved_m:600},
      {group:1,kind:'crossing',path:[p(50,-20),p(50,20)],mid:p(50,0),length_m:40,name:'',saved_m:null}],
    groups:[{name:'crossing/9',inert:false,links:[0]},{name:'crossing/0',inert:true,links:[1]}],patterns:2,
    search_space:[[0,1,2],[2,3]],
    baseline:{residents_within:{medical:0.5,parks:0.3},provider:{medical:[0,-1,-1,0],parks:[1,1,-1,-1]},via:{medical:[-1,-1,-1,-1],parks:[-1,-1,-1,-1]}},
    scenarios:{
      'exact_no_links|10|0|1':scen('exact_no_links',10,0,1,[[0,0]],[],{medical:[1,2,-1,0],parks:[]},{medical:0.7,parks:0.3}),
      'exact|10|2|1':scen('exact',10,2,1,[[0,0]],['crossing/9'],{medical:[1,2,-1,0,2,2,0,1],parks:[]},{medical:1.0,parks:0.3}),
      'exact|10|1|4':scen('exact',10,1,4,[[0,0]],['crossing/9'],{medical:[1,2,-1,0],parks:[]},{medical:0.7,parks:0.3}),
      'greedy|10|1|4':scen('greedy',10,1,4,[[1,1]],[],{medical:[],parks:[2,3,-1,0]},{medical:0.5,parks:0.6}),
      'nsga2|10|1|4':scen('nsga2',10,1,4,[[0,0]],[],{medical:[1,2,-1,0],parks:[]},{medical:0.7,parks:0.3},{gap_pct:0.2}),
    },
    summary:{greedy:{cases:3,median:0.6,worst:1.2,over_1pct:1,at_optimum:0},nsga2:{cases:3,median:0.1,worst:0.3,over_1pct:0,at_optimum:1},
      exact:{cases:3,all_optimal:true},site_use_choices:3,sites:2,link_groups:2,inert_groups:['crossing/0'],patterns:2},
    story:{plan:'exact_no_links|10|0|1',links:'exact|10|2|1',methods:{exact:'exact|10|1|4',greedy:'greedy|10|1|4',nsga2:'nsga2|10|1|4'}},
    forecast:{years:[2,4,6],chance:{stable_built:[0.017,0.028,0.035]},buildings:[{lonlat:p(400,0),state:'stable_built'}],
      note:'Every building in a state gets the same chance: the table has no building covariates.'},
    check:{max_abs_diff_m:0.00002,tolerance_m:0.001,scenarios:6},record:{commit:'abcdef123'}};
}
function context({lens='access',calm=false,dark=false,now=0,data=true}={}) {
  class Layer {constructor(props){Object.assign(this,props);}}
  const c={DATA:{api:'/v1',access:data?{views:['With rail bed','Without rail bed']}:null},ui:{lens},
    deck:{PathLayer:Layer,LineLayer:Layer,TripsLayer:Layer,ScatterplotLayer:Layer,TextLayer:Layer},
    ON_TOP:{depthCompare:'always'},GLOW:{blend:true},calm:{matches:calm},isDark:()=>dark,esc:s=>String(s),
    groundZ:()=>10,groundPath:(path,lift)=>path.map(q=>[q[0],q[1],10+lift]),map:null,flyTo:()=>{},
    performance:{now:()=>now},location:{hash:''},history:{replaceState:()=>{}},URLSearchParams,
    renderLegend:()=>{},render:()=>{},animate:()=>{},console};
  vm.createContext(c);vm.runInContext(source,c);
  if(data)vm.runInContext('access.data[0]=accessPrepare(B)',Object.assign(c,{B:bundle()}));
  return c;
}
const ev=(c,expr)=>vm.runInContext(expr,c);
const layer=(layers,id)=>layers.find(l=>l.id===id);
const at=(c,step)=>{ev(c,`access.step=${JSON.stringify(step)};access.built=null`);return c.accessLayers(c.performance.now());};

test('nothing is drawn outside the Access plans view or before the graph loads',()=>{
  assert.equal(context({lens:'observed'}).accessLayers(0).length,0);
  assert.equal(context({data:false}).accessLayers(0).length,0);
  assert.match(context({data:false}).accessPanel(),/^$/);
});
test('a scenario is the baseline with its differences applied',()=>{
  const c=context(),v=ev(c,'accessView()');
  const s=c.accessScenario(v,'exact|10|2|1','medical');
  assert.deepEqual([...s.provider],[0,2,2,0]);assert.deepEqual([...s.via],[-1,-1,0,-1]);assert.deepEqual([...s.needs],[0,0,1,0]);
  assert.deepEqual([...c.accessScenario(v,'baseline','parks').provider],[1,1,-1,-1]);
  assert.equal(c.accessScenario(v,'exact|99|0|1','medical'),null,'unknown combinations are not invented');
});
test('each step shows its scenario against the right comparison',()=>{
  const c=context(),scene=(step)=>{ev(c,`access.step=${JSON.stringify(step)}`);return c.accessScene();};
  assert.equal(scene(0).key,'baseline');assert.equal(scene(0).compare,null);
  assert.ok(scene(1).search&&scene(1).allSites,'the choices step shows every site and its reach');
  assert.deepEqual([scene(2).key,scene(2).compare],['exact_no_links|10|0|1','baseline']);
  assert.deepEqual([scene(3).key,scene(3).compare],['exact|10|2|1','exact_no_links|10|0|1'],'the links are measured against the same budget without them');
  assert.equal(scene(4).greedy,'greedy|10|1|4');
  assert.ok(scene(5).ghosts);
  ev(c,"access.N=10;access.K=0;access.alpha=1;access.method='exact'");
  assert.equal(scene('explore').key,'exact_no_links|10|0|1','no links means the land-use optimum');
  ev(c,'access.K=3');
  assert.equal(scene('explore').key,'baseline');assert.ok(c.accessScene().missing);
});
test('new edges grow out from their providers, then pulse; homes change colour as their edge arrives',()=>{
  const c=context({now:0});
  let layers=at(c,3);
  const reveal=layer(layers,'access-reveal'),homes=layer(layers,'access-homes'),C=ev(c,'accColours()');
  assert.ok(reveal&&!layer(layers,'access-pulse'),'revealing first');
  assert.equal(reveal.data.length,1,'against the same plan without links, only home 2 changes');
  assert.equal(layer(layers,'access-edges').data.length,3,'the unchanged edges stay drawn, faint');
  assert.deepEqual([...homes.getFillColor({i:2})],[...C.none],'before its edge arrives the home is still out of reach');
  const built=ev(c,'access.built'),e2=built.edges.find(e=>e.h===2);
  c.performance.now=()=>(e2.arrive+0.01)*1000;
  layers=c.accessLayers(c.performance.now());
  assert.deepEqual([...layer(layers,'access-homes').getFillColor({i:2})],[...C.link],'reached only across a new link: gold');
  assert.deepEqual([...layer(layers,'access-homes').getLineColor({i:2})],[...C.ring]);
  c.performance.now=()=>(built.reveal+1)*1000;
  layers=c.accessLayers(c.performance.now());
  assert.ok(layer(layers,'access-pulse')&&!layer(layers,'access-reveal'));
  assert.equal(layer(layers,'access-edges').data.length,4);
  assert.deepEqual([...layer(layers,'access-homes').getFillColor({i:1})],[...C.before],'already served without the links');
  assert.deepEqual([...layer(layers,'access-homes').getFillColor({i:0})],[...C.before]);
  // against today, the plan's site brings home 1 in: orange
  at(c,2);c.performance.now=()=>1e9;
  assert.deepEqual([...layer(c.accessLayers(1e9),'access-homes').getFillColor({i:1})],[...C.gain]);
});
test('with reduced motion the final picture is drawn at once, with no moving layer',()=>{
  const c=context({calm:true}),layers=at(c,3),C=ev(c,'accColours()');
  assert.ok(!layer(layers,'access-reveal')&&!layer(layers,'access-pulse'));
  assert.equal(layer(layers,'access-edges').data.length,4);
  assert.deepEqual([...layer(layers,'access-homes').getFillColor({i:2})],[...C.link]);
  assert.equal(c.accessAnimating(),false);
});
test('the tiered graph lifts services, sites and link hubs, and routes a walk through the link it crosses',()=>{
  const c=context(),layers=at(c,3),built=ev(c,'access.built');
  const place=layer(layers,'access-places').data[0],site=layer(layers,'access-sites').data[0],hub=layer(layers,'access-hubs').data[0];
  assert.equal(place.at[2],10+ev(c,'ACC_TIER.place'));assert.equal(site.at[2],10+ev(c,'ACC_TIER.site'));assert.equal(hub.at[2],10+ev(c,'ACC_TIER.link'));
  assert.deepEqual(Array.from(layer(layers,'access-stems').data,q=>q.base),[10,10,10],'stems stand on the ground under each node');
  const e=built.edges.find(x=>x.h===2);
  assert.ok(e.path.some(q=>Math.abs(q[0]-hub.at[0])<1e-12&&Math.abs(q[1]-hub.at[1])<1e-12&&Math.abs(q[2]-hub.at[2])<1e-9),'through the hub');
  assert.equal(e.path.at(-1)[2],11.2,'ends on the home, on the ground');
  assert.deepEqual(Array.from(layer(layers,'access-labels').data,d=>d.text),['Clinic','Crossing #9']);
  const links=layer(layers,'access-links'),C=ev(c,'accColours()');
  assert.deepEqual(links.getColor(ev(c,'accessView()').links[0]),C.link);assert.deepEqual(links.getColor(ev(c,'accessView()').links[1]),C.inert);
  ev(c,"access.layout='map'");
  const flat=at(c,3);
  assert.equal(layer(flat,'access-places').data[0].at[2],12,'on the ground in the map layout');
  assert.equal(layer(flat,'access-stems'),undefined);
});
test('two open crossings at one junction share a label; site steps draw no walks but keep the homes\' state',()=>{
  const c=context();
  ev(c,`B.scenarios['exact|10|2|1'].links=['crossing/9','crossing/0'];B.links[1].mid=${JSON.stringify(p(120,0))};access.data[0]=accessPrepare(B)`);
  const labels=Array.from(layer(at(c,3),'access-labels').data,d=>d.text);
  assert.deepEqual(labels,['Clinic','Crossings #9 and #0']);
  const layers=at(c,1),C=ev(c,'accColours()');
  assert.equal(layer(layers,'access-edges').data.length,0);
  assert.deepEqual([...layer(layers,'access-homes').getFillColor({i:0})],[...C.before],'served homes stay blue');
  assert.deepEqual(Array.from(layer(layers,'access-sites').getFillColor({i:1,kind:'railbed'})),[...ev(c,'ACC_SITE_COLOUR.railbed'),230]);
});
test('the methods step rings the sites greedy chose instead; the last step shows buildings that may free up',()=>{
  const c=context();
  let layers=at(c,4);
  assert.deepEqual(Array.from(layer(layers,'access-greedy-only').data,s=>s.i),[1]);
  layers=at(c,5);
  const ghosts=layer(layers,'access-ghosts');
  assert.equal(ghosts.data.length,1);assert.equal(ghosts.getRadius(ghosts.data[0]),3+60*0.035);
  layers=at(c,1);
  assert.equal(layer(layers,'access-search').data.length,5,'every home each site could serve');
});
test('the panel tells each step with numbers from the bundle',()=>{
  const c=context();
  ev(c,'access.step=0');let panel=c.accessPanel();
  assert.match(panel,/<h2>Access plans<\/h2>/);assert.match(panel,/With rail bed · within a 500 m walk/);
  assert.match(panel,/50% of the corridor's about 100 residents live within a 500 m walk of clinics or hospitals/);
  assert.match(panel,/The widest gap is parks: 30%/);
  assert.match(panel,/data-acc="view" data-value="1" aria-pressed="false">Without rail bed/);
  ev(c,'access.step=1');assert.match(c.accessPanel(),/2 candidate sites \(1 BBED parcel and 1 rail-bed piece\) allow 3 site–use choices\. Of the 2 reviewed walking-link groups, 1 change no walk within 500 m, so links give only 2 distinct reach patterns/);
  ev(c,'access.step=2');panel=c.accessPanel();
  assert.match(panel,/The proven optimum for 10 sites at α = 1: 1 clinic/);assert.match(panel,/50% → <b>70%<\/b>/);
  ev(c,'access.step=3');panel=c.accessPanel();
  assert.match(panel,/Opening Crossing #9 with 10 re-optimised sites: medical care 70% → 100%\. 1 homes reach their nearest clinic across a new link; 1 of them gain one only that way/);
  ev(c,'access.step=4');assert.match(c.accessPanel(),/NSGA-II is 0\.20% short of the optimum, greedy 0\.80%\. Over all 3 cases: NSGA-II median 0\.10% \(worst 0\.30%\), greedy median 0\.60% \(worst 1\.20%, 1 over 1%\)/);
  ev(c,'access.step=5');panel=c.accessPanel();
  assert.match(panel,/a stable building has a 4% chance of being empty, stalled or vacant within 6 years; every building in a state gets the same chance/);
  assert.match(panel,/Plans staged over years/);assert.match(panel,/within 0\.02 mm\) over 6 scenarios/);
  ev(c,"access.step='explore'");panel=c.accessPanel();
  for(const k of ['N','K','alpha','method','layout'])assert.match(panel,new RegExp(`data-acc="${k}"`));
});
test('controls change the scene; the hash keeps the step',()=>{
  const c=context();let hash='';
  c.history.replaceState=(a,b,h)=>{hash=h;};
  ev(c,'access.step=0');
  c.accessPanelClick({target:{closest:()=>({dataset:{acc:'next'}})}});
  assert.equal(ev(c,'access.step'),1);assert.match(hash,/step=2/);
  c.accessPanelClick({target:{closest:()=>({dataset:{acc:'service',value:'parks'}})}});
  assert.equal(ev(c,'access.service'),'parks');
  c.accessPanelClick({target:{closest:()=>({dataset:{acc:'step',value:'explore'}})}});
  assert.equal(ev(c,'access.step'),'explore');assert.match(hash,/step=explore/);
});
test('tooltips describe nodes without claiming more than the data says',()=>{
  const c=context();at(c,3);
  const v=ev(c,'accessView()');
  assert.match(c.accessTip(v.homes[2]),/About 30 residents \(estimated from floor area\).*brought within 500 m of a plan clinic \(candidate site\) by this plan across Crossing #9/s);
  assert.match(c.accessTip(v.places[0]),/Clinic A.*current operation is not verified/s);
  assert.match(c.accessTip(v.sites[0]),/Candidate site · BBED parcel.*400 m².*BBED: stalled or cancelled.*This plan: clinic/s);
  assert.match(c.accessTip(v.links[1]),/Changes no walk within 500 m/);
  assert.match(c.accessTip({accessKind:'ghost',state:'stable_built'}),/2% in 2 years, 3% in 4 years, 4% in 6 years/);
});
test('template wires the Access plans view without touching the others',()=>{
  assert.match(html,/data-lens="access" aria-selected="false" hidden>Access plans</);
  assert.match(html,/document\.querySelector\('\[data-lens="access"\]'\)\.hidden = !\(DATA\.access && DATA\.api\);/);
  assert.match(html,/else if \(ui\.lens === 'access'\) rgb = accessBuildingColour\(\);/);
  assert.match(html,/\.\.\.facilityLayers\(\), \.\.\.accessLayers\(now\)\]/);
  assert.match(html,/\|\| accessAnimating\(\)\)\) fxRaf = requestAnimationFrame\(frame\);/);
  assert.match(html,/\} else if \(ui\.lens === 'access'\) \{\r?\n    body = accessPanel\(\);/);
  assert.match(html,/if \(lens === 'access'\) \{ togglePanel\('p-legend', \$\('t-legend'\), true\); accessActivate\(\); animate\(\); \}/);
  assert.match(html,/if\(object\.accessKind\) \{\r?\n    tip\.innerHTML=accessTip\(object\);/);
  assert.match(html,/object\?\.accessKind \|\| object\?\.properties\?\.kind \|\| object\?\.properties\?\.facility \|\| object\?\.bridgeStructure\) return;/);
  assert.match(html,/\$\('p-legend'\)\.addEventListener\('click', accessPanelClick\);/);
  assert.match(html,/const accessCredit = accessSource\(\);/);
});
