const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_pedestrians.js`,'utf8');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const grab=re=>html.match(re)[0];
const plain=x=>JSON.parse(JSON.stringify(x));
function fixture(){
  class Layer {constructor(p){Object.assign(this,p);}}
  const road=(name,over={})=>({name,cls:'secondary',width_m:12,lanes:2,path:[[35.5,33.9],[35.501,33.9]],speed:Array(24).fill(40),...over});
  const calls=[];
  const c=vm.createContext({DATA:{api:true,traffic:{roads:[road('Main <b>St</b>'),road('Lane',{cls:'residential',width_m:6,lanes:1}),road('Ring',{cls:'motorway',width_m:16})]},
    pedestrians:{segments:[{path:[[35.5,33.9,40],[35.501,33.9,41]],
    people:8,offset_m:5,ped_per_hour_pm:200,road_index:0}],baseline_removed_per_hour:71}},
    ui:{traffic:true,roadTool:null,closedRoads:new Set(),crashes:new Set(),crashSites:new Map(),planning:new Map(),selectedRoad:null,planSearch:''},
    calm:{matches:false},roadHeightAt:()=>0,
    metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*92500,(a[1]-b[1])*111320),
    deck:{PathLayer:Layer,SimpleMeshLayer:Layer},mesh:()=>({}),MATERIAL:{},trafficView:()=>null,
    $:()=>null,render:()=>{},renderTraffic:()=>{},prepareCars:()=>calls.push('prepare'),requestTraffic:()=>calls.push('request'),
    esc:x=>String(x).replace(/[&<>"']/g,ch=>`&#${ch.charCodeAt(0)};`),isDark:()=>false,nightLevel:()=>0,
    closeTrafficSession:()=>{},focusRoads:i=>calls.push('focus:'+i.join()),SOFT:{},ON_TOP:{},offsetRoadPath:(path,o)=>path.map(p=>[p[0],p[1]+o/111320])});
  vm.runInContext([grab(/^const FAST_ROADS = .*$/m),grab(/^const ROAD_WIDTH = .*$/m),grab(/^const roadWidth = .*$/m),
    html.slice(html.indexOf('const pavementWidth = road'),html.indexOf('function mappedRoadColour')),grab(/^const shade = .*$/m),
    html.slice(html.indexOf('function planningRequest()'),html.indexOf('function simulationRequest()'))].join('\n'),c);
  vm.runInContext(source,c);
  return {run:s=>vm.runInContext(s,c),c,calls};
}
const click=(run,attr,value)=>run(`mobilityLabClick({closest:s=>s==='${attr}'?${value}:null})`);
const plan=(run,action)=>click(run,'[data-plan]',`{dataset:{plan:'${action}'}}`);
test('pavement walkers stay offset on both sides and use the browser road plane',()=>{
  const {run}=fixture();
  const rows=run('pedestrianRows(null)');
  assert.equal(rows.length,8);
  assert.ok(rows.some(p=>p.position[1]>33.9));assert.ok(rows.some(p=>p.position[1]<33.9));
  assert.ok(rows.every(p=>p.position[2]===.15));
  assert.ok(rows.every(p=>p.position.every(Number.isFinite)));
});
test('demand and unavailable pavements change actual walkers, metrics and reopening without losing segment identity',()=>{
  const {run}=fixture();
  assert.equal(run('pedestrianRows(null).length'),8);
  run('mobilityLab.demand=2');assert.equal(run('pedestrianRows(null).length'),16);
  run('togglePavement(0)');assert.equal(run('pedestrianRows(null).length'),0);
  assert.equal(run('mobilityLabMetrics().unavailable'),16);
  assert.equal(run('mobilityLabMetrics().available'),0);
  run('togglePavement(0)');assert.equal(run('pedestrianRows(null).length'),16);
  run('mobilityLab.demand=0');assert.equal(run('pedestrianRows(null).length'),0);
});
test('pause and reduced motion freeze walker positions at the fixed illustrative speed',()=>{
  const {run}=fixture();
  assert.equal(run('mobilityLab.speed'),1.3);
  run('pedestrianLayers(0);pedestrianLayers(200)');
  const first=run('pedestrianRows(null)[0].position[0]');
  run('mobilityLab.paused=true;pedestrianLayers(400)');
  assert.equal(run('pedestrianRows(null)[0].position[0]'),first);
  run('mobilityLab.paused=false;pedestrianLayers(600)');
  assert.notEqual(run('pedestrianRows(null)[0].position[0]'),first);
  assert.ok(Math.abs(run('preparePedestrianPaths()[0].phase')-.52)<1e-8);
  run('calm.matches=true;pedestrianLayers(800)');
  assert.ok(Math.abs(run('preparePedestrianPaths()[0].phase')-.52)<1e-8);
});
test('pavement tool picks walking paths and never sends an unmodelled vehicle interaction',()=>{
  const {run}=fixture();
  run("ui.roadTool='pavement'");
  const layers=run('pedestrianLayers(0)');
  const edit=layers.find(l=>l.id==='pedestrian-edit');
  edit.onClick({object:edit.data[0]});
  assert.equal(run('mobilityLab.blocked.has(0)'),true);
  assert.equal(run('pedestrianRows([36,34,37,35]).length'),0);
});
test('the planner leads with street interventions and offers no walking-speed control',()=>{
  const {run}=fixture();
  assert.match(run('plannerHtml()'),/Street interventions/);
  assert.match(run('plannerHtml()'),/Click a street section on the map/);
  assert.doesNotMatch(source+html,/lab-speed|Walking speed/);
});
test('road selection stays separate from road tools: only close and crash tools may close a road',()=>{
  const {run}=fixture();
  const guard=html.slice(html.indexOf('function toggleRoadEvent'),html.indexOf('function clearRoadEvents'));
  assert.match(guard,/roadTool!=='close'&&ui\.roadTool!=='crash'/);
  assert.match(html,/if \(ui\.roadTool\) toggleRoadEvent\(object\.i, coordinate\); else selectRoad\(object\.i\)/);
  run('selectRoad(0)');
  assert.equal(run('ui.roadTool'),null);
  assert.equal(run('ui.closedRoads.size'),0);
});
test('selecting a street, then calming, reallocating a lane and pedestrianising reach the SUMO request',()=>{
  const {run,calls}=fixture();
  assert.deepEqual(plain(run('planningRequest()')),[]);
  run('selectRoad(0)');
  assert.equal(run('ui.selectedRoad'),0);
  const panel=run('plannerHtml()');
  assert.match(panel,/Main &#60;b&#62;St&#60;\/b&#62;/);          // name is escaped, not injected
  assert.doesNotMatch(panel,/<b>St<\/b>/);
  plan(run,'calm30');plan(run,'lane');
  assert.deepEqual(plain(run('planningRequest()')),[{road:0,lanes_removed:1,speed_limit_kmh:30}]);
  run("mobilityLabInput({id:'plan-speed',value:'50'})");
  assert.deepEqual(plain(run('planningRequest()')),[{road:0,lanes_removed:1,speed_limit_kmh:50}]);
  run("mobilityLabInput({id:'plan-speed',value:''})");
  assert.deepEqual(plain(run('planningRequest()')),[{road:0,lanes_removed:1}]);
  assert.ok(calls.includes('prepare')&&calls.includes('request'));
  assert.match(html,/closed:\[\.\.\.ui\.closedRoads\], crashes:\[\.\.\.ui\.crashes\], planning:planningRequest\(\)/);
  plan(run,'pedestrianise');
  assert.equal(run('ui.closedRoads.has(0)'),true);
  assert.equal(run('freedWidthM(0)'),0);                       // a closed street has nothing to widen
  assert.match(run('plannerHtml()'),/Reopen this section to traffic/);
  plan(run,'pedestrianise');
  assert.equal(run('ui.closedRoads.has(0)'),false);
  assert.equal(run('freedWidthM(0)'),6.4);                      // plan survived the reopen
});
test('plans stay unique and capped, and empty plans disappear',()=>{
  const {run}=fixture();
  run('setPlan(0,{lanes_removed:1});setPlan(0,{lanes_removed:1});setPlan(1,{speed_limit_kmh:30})');
  assert.equal(run('planningRequest().length'),2);
  run('setPlan(0,{lanes_removed:0});setPlan(1,{speed_limit_kmh:undefined})');
  assert.equal(run('planningRequest().length'),0);
  run('for(let i=0;i<1200;i++)ui.planning.set(i,{lanes_removed:1})');
  assert.equal(run('planningRequest().length'),1000);
});
test('lane reallocation is offered only where a spare lane and a pavement exist',()=>{
  const {run}=fixture();
  assert.equal(run('canReallocateLane(DATA.traffic.roads[0])'),true);
  assert.equal(run('canReallocateLane(DATA.traffic.roads[1])'),false);   // single lane each way
  assert.equal(run('canReallocateLane(DATA.traffic.roads[2])'),false);   // motorway: no pavement
  run('selectRoad(1)');
  assert.match(run('plannerHtml()'),/no spare lane/);
  assert.match(run('plannerHtml()'),/data-plan="lane"[^>]* disabled/);
});
test('widening area, density and walker position follow stated geometry assumptions',()=>{
  const {run}=fixture();
  const length=run('roadLengthM(DATA.traffic.roads[0])');
  const before=run('streetMetrics(0)');
  assert.ok(Math.abs(before.areaBefore-length*2*2.4)<1e-6);
  assert.equal(before.areaAfter,before.areaBefore);
  assert.ok(Math.abs(before.densityBefore-8/before.areaBefore*100)<1e-9);
  const far=run('pedestrianRows(null)');
  run('setPlan(0,{lanes_removed:1})');
  const after=run('streetMetrics(0)');
  assert.equal(after.freedM,6.4);                                // 1 lane x 2 directions x 3.2 m
  assert.ok(Math.abs(after.areaAfter-length*(4.8+6.4))<1e-6);
  assert.ok(after.densityAfter<after.densityBefore);
  assert.equal(after.walkers,8);
  const near=run('pedestrianRows(null)');
  const lat=r=>Math.abs(r.position[1]-33.9)*111320;
  assert.ok(Math.abs(Math.max(...far.map(lat))-Math.max(...near.map(lat))-1.6)<.01);   // 6.4 / 4 m nearer the road
  assert.equal(near.length,8);                                    // widening never adds or removes walkers
  run('DATA.traffic.roads[0].oneway=true');
  assert.equal(run('freedWidthM(0)'),3.2);                        // one-way: one direction only
});
test('pedestrianising keeps walkers and reports the optional full-width assumption',()=>{
  const {run}=fixture();
  run('selectRoad(0);togglePedestrianise(0)');
  assert.equal(run('pedestrianRows(null).length'),8);
  const m=run('streetMetrics(0)');
  assert.ok(m.carFreeArea>m.areaBefore);
  assert.ok(m.densityCarFree<m.densityBefore);
  assert.match(run('plannerHtml()'),/walkable across its full width/);
  assert.match(run('plannerHtml()'),/does not route pedestrians, add pedestrians, or estimate collision risk/);
});
test('planning layers highlight the selected and planned streets and show widened pavements',()=>{
  const {run}=fixture();
  assert.equal(run('planningLayers().length'),0);
  run('selectRoad(0);setPlan(0,{lanes_removed:1,speed_limit_kmh:30})');
  const layers=run('planningLayers()');
  assert.deepEqual(plain(layers.map(l=>l.id)),['plan-pavement-widening','plan-streets','plan-selected']);
  assert.equal(layers[0].data.length,2);
  assert.ok(layers[0].data.every(d=>d.width===3.2));             // 6.4 m freed, half per pavement
  run('selectRoad(null);ui.planning.clear()');
  assert.equal(run('planningLayers().length'),0);
});
test('restore, clear-all and reset return streets to baseline and clear the selection',()=>{
  const {run}=fixture();
  run('selectRoad(0);setPlan(0,{lanes_removed:1,speed_limit_kmh:30});togglePedestrianise(1);setPlan(1,{speed_limit_kmh:20})');
  run('restoreStreet(0)');
  assert.equal(run('ui.planning.has(0)'),false);
  assert.equal(run('ui.closedRoads.has(1)'),true);
  run('clearStreetChanges()');
  assert.equal(run('ui.planning.size+ui.closedRoads.size'),0);
  run('setPlan(0,{lanes_removed:1});ui.crashes.add(2);selectRoad(0);mobilityLab.demand=2;ui.planSearch="x"');
  click(run,'[data-lab-reset]','{}');
  assert.equal(run('ui.planning.size+ui.crashes.size+ui.closedRoads.size'),0);
  assert.equal(run('ui.selectedRoad'),null);
  assert.equal(run('mobilityLab.demand'),1);
  assert.equal(run('ui.planSearch'),'');
});
test('street search finds named streets and stays out of the SUMO request',()=>{
  const {run,calls}=fixture();
  run("mobilityLabInput({id:'plan-search',value:'rin'})");
  assert.match(run('planSearchHtml()'),/data-plan-select="2"/);
  click(run,'[data-plan-select]','{dataset:{planSelect:"2"}}');
  assert.equal(run('ui.selectedRoad'),2);
  assert.ok(calls.includes('focus:2'));                          // list/search choices bring the section into view
  calls.length=0;run('selectRoad(0)');                           // an ordinary map click keeps the camera
  assert.ok(!calls.some(c=>c.startsWith('focus')));
  assert.equal(run('planningRequest().length'),0);
});

// Lateral position of a point relative to the east-running road at lat 33.9: + is left of travel (north).
const metresNorth=lat=>(lat-33.9)*111320;
test('the panel names the selected section and says actions affect one section only',()=>{
  const {run}=fixture();
  run('selectRoad(0)');
  const panel=run('plannerHtml()');
  assert.match(panel,/Selected section/);
  assert.match(panel,/this one section only; a named street is made of several/);
  assert.match(panel,/Pedestrianise this section/);
  assert.match(panel,/Calm this section to 30 km\/h/);
  assert.match(panel,/curb side of each travel direction/);
  assert.doesNotMatch(panel,/shared equally between the two pavements/);
});
for(const [oneway,expected,label] of [[1,[-1],'one-way +1 widens the right curb only'],[-1,[1],'one-way -1 widens the left curb only'],[0,[-1,1],'two-way widens both curbs']]) {
  test(`overlay geometry: ${label} and never covers the retained lane`,()=>{
    const {run}=fixture();
    run(`DATA.traffic.roads[0].oneway=${oneway};setPlan(0,{lanes_removed:1})`);
    const strips=run('planningLayers()').find(l=>l.id==='plan-pavement-widening').data;
    assert.deepEqual(plain(strips.map(d=>d.side).sort()),plain([...expected].sort()));
    const half=6;
    for(const d of strips) {
      const centre=metresNorth(d.path[0][1]);
      assert.equal(Math.sign(centre),d.side);                       // lies on the curb side it names
      assert.ok(Math.abs(centre)+d.width/2<=half+1e-6);             // outer edge = carriageway edge
      assert.ok(Math.abs(centre)-d.width/2>=-1e-6);                 // inner edge has not crossed the centre line
      assert.equal(d.width,3.2);                                    // exactly one lane
    }
    // one-way: the opposite half (the retained lane) carries no strip at all
    if(oneway)assert.ok(strips.every(d=>Math.sign(metresNorth(d.path[0][1]))===(oneway>0?-1:1)));
    assert.equal(run('freedWidthM(0)'),oneway?3.2:6.4);             // total area indicator is unchanged
  });
  test(`walker positions: ${label}; walkers on the other side stay put`,()=>{
    const {run}=fixture();
    const before=run('pedestrianRows(null)').map(r=>metresNorth(r.position[1]));
    run(`DATA.traffic.roads[0].oneway=${oneway};setPlan(0,{lanes_removed:1})`);
    const after=run('pedestrianRows(null)').map(r=>metresNorth(r.position[1]));
    assert.equal(after.length,8);
    after.forEach((y,k)=>{
      const side=Math.sign(before[k]);
      if(expected.includes(side))assert.ok(Math.abs(Math.abs(before[k])-Math.abs(y)-1.6)<.01);   // centre moves in by half of 3.2 m
      else assert.ok(Math.abs(before[k]-y)<1e-6);
    });
  });
}
test('a pedestrian path drawn against its road still widens the real curb side',()=>{
  const {run}=fixture();
  run('DATA.pedestrians.segments[0].path.reverse()');            // runs west while the road runs east
  const before=run('pedestrianRows(null)').map(r=>metresNorth(r.position[1]));
  run('DATA.traffic.roads[0].oneway=1;setPlan(0,{lanes_removed:1})');
  const after=run('pedestrianRows(null)').map(r=>metresNorth(r.position[1]));
  assert.equal(run('pedestrianRoadSign(preparePedestrianPaths()[0])'),-1);
  after.forEach((y,k)=>{
    if(before[k]<0)assert.ok(Math.abs(Math.abs(before[k])-Math.abs(y)-1.6)<.01);   // south walkers are on the road's right curb and move
    else assert.ok(Math.abs(before[k]-y)<1e-6);
  });
  assert.ok(before.some(y=>y<0)&&before.some(y=>y>0));
});
test('a pedestrianised one-way section shows no widening strip and moves no walker',()=>{
  const {run}=fixture();
  run('DATA.traffic.roads[0].oneway=1;setPlan(0,{lanes_removed:1})');
  run('togglePedestrianise(0)');
  assert.equal(run('planningLayers().some(l=>l.id==="plan-pavement-widening")'),false);
  assert.equal(run('walkingShift(preparePedestrianPaths()[0],-1)'),0);
});
test('the narrow-panel layout wraps long names and keeps tables and fields inside the panel',()=>{
  assert.match(html,/#mobility-lab :is\(\.more, \.plan-street, \.note, \.plan-field\) \{ min-width: 0; overflow-wrap: anywhere; \}/);
  assert.match(html,/\.plan-table \{ width: 100%; table-layout: fixed;/);
  assert.match(html,/\.panel \{ padding: 14px 16px; max-height: 100%; overflow: auto;/);
});
