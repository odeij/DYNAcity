// Pure playback adapter tests, using authoritative TraCI-shaped frames.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
test('road overlay follows simulated carriageway width, with a legacy fallback',()=>{
 const run=s=>vm.runInNewContext(`${html.match(/^const ROAD_WIDTH = .*$/m)[0]}\n${html.match(/^const roadWidth = .*$/m)[0]}\n${s}`);
 assert.equal(run("roadWidth({cls:'residential',width_m:6.4})"),6.4);
 assert.equal(run("roadWidth({cls:'primary',width_m:19.2})"),19.2);
 assert.equal(run("roadWidth({cls:'primary_link'})"),10);
 assert.equal(run("roadWidth({cls:'residential',width_m:0})"),5);
});
function fixture(overrides = {}) {
 const context=vm.createContext({ui:{traffic:true,hour:8,closedRoads:new Set(),crashes:new Set()},
  DATA:{traffic:{roads:[]}},currentHazards:()=>[],prepareRoads:()=>{},CAR_SPEEDUP:1,
  TRAFFIC_INITIAL_RATE:.25,TRAFFIC_MIN_RATE:.05,TRAFFIC_RATE_HEADROOM:.75,
  clamp01:x=>Math.max(0,Math.min(1,x)),renderTraffic:()=>{},render:()=>{},esc:x=>x,
  fetch:async()=>{throw new Error('unexpected fetch')},performance:{now:()=>0},console,...overrides});
 const run=s=>vm.runInContext(s,context);
 run('let cars=[],carHour=null,carTime=null;');
 run(html.slice(html.indexOf('// Authoritative SUMO replay'),html.indexOf('const CAR_PAINT')));
 run(html.slice(html.indexOf('function mobilityPilotPanel()'),html.indexOf('function setTraffic(on)')));
 run(`trafficFrames=[{time_s:0,vehicles:[['a',35,33,90,10,'0:1',0]],metrics:{total:1}},
 {time_s:1,vehicles:[['a',35.001,33,90,10,'0:1',0]],metrics:{total:1}},
 {time_s:2,vehicles:[['a',35.002,33,90,10,'1:1',0]],metrics:{total:1}}];
 trafficClock=0; trafficFetch=true;`);
 return {run};
}
const frame = (time, vehicles = [['a',35 + time * .001,33,90,10,'0:1',0]]) =>
 ({time_s:time,vehicles,metrics:{total:1,active:vehicles.length}});
const response = body => ({ok:true,json:async()=>body});
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const settle = () => new Promise(resolve=>setImmediate(resolve));

test('mobility comparison shows paired baseline results and distinguishes unfinished trips',()=>{
 const {run}=fixture({esc:x=>String(x).replaceAll('<','&lt;')});
 run(`DATA.traffic.roads=[{name:'<Main>'}];ui.closedRoads.add(0);
 trafficClock=25;trafficMetrics={total:10,active:5,pending:1,arrived:4,stopped:2,
 baseline:{active:3,pending:1,arrived:6,stopped:1},detour:[[0,3]],stopped_roads:[[0,2]],
 mean_completed_delay_s:4.5,paired_arrivals:4,overdue:2,no_detour:1};`);
 const rendered=run('mobilityComparison()');
 assert.match(rendered,/&lt;Main>/);
 assert.doesNotMatch(rendered,/<Main>/);
 assert.match(rendered,/Completed<\/th><td>6<\/td><td>4/);
 assert.match(rendered,/4.5 seconds on average \(4 trips finished in both runs\)/);
 assert.match(rendered,/2 trips finished in normal traffic but are still travelling/);
 assert.match(rendered,/data-mobility-road="0"/);
 assert.match(rendered,/3 diverted · 2 stopped/);
 assert.match(rendered,/finite fleet/);
 run('trafficMetrics.mean_completed_delay_s=null');
 assert.match(run('mobilityComparison()'),/waiting for trips to finish in both runs/);
});

test('mobility comparison does not invent results before startup and flags mixed effects',()=>{
 const {run}=fixture({currentHazards:()=>[{kind:'earthquake'}]});
 assert.match(run('mobilityComparison()'),/Comparison appears when traffic simulation is available/);
 assert.match(run('mobilityComparison()'),/also includes active crashes or disaster effects/);
 assert.doesNotMatch(run('mobilityComparison()'),/<table/);
});

test('typed replay retains vehicle class and sends the selected mix to SUMO',()=>{
 const {run}=fixture();
 run(`ui.mixed=true;trafficFrames[0].vehicles[0].push('bus');trafficFrames[1].vehicles[0].push('bus');advanceCars(0)`);
 assert.equal(run('cars[0].type'),'bus');
 assert.equal(run('simulationRequest().mixed'),true);
 assert.equal(run('cars.length'),1);
});

test('pilot distinguishes mapped infrastructure, missing observations and transferred historical mix',()=>{
 const {run}=fixture();
 run(`DATA.traffic.mobility={title:'Pilot',features:[{kind:'bus_stop'},{kind:'parking'}],sources:[],
  fleet:{label:'2015 historical mix',scope:'Transferred historical scenario'},gaps:['Motorcycle counts missing'],
  surveyed_signal_programs:0,observed_stop_events:0}`);
 const panel=run('mobilityPilotPanel()');
 assert.match(panel,/Occupancy unknown/);
 assert.match(panel,/0 surveyed signal programs/);
 assert.match(panel,/Other timings and control locations are assumed/);
 assert.match(panel,/Motorcycle counts missing/);
 assert.match(panel,/Changing the mix starts a new run/);
});

test('signal colour follows the displayed sample, never the future buffer head',()=>{
 class Layer {constructor(props){Object.assign(this,props);}}
 const {run}=fixture({deck:{ScatterplotLayer:Layer,TextLayer:Layer,SolidPolygonLayer:Layer}});
 run(html.slice(html.indexOf('function mobilityLayers()'),html.indexOf('function mobilityPilotPanel()')));
 run(`DATA.traffic.mobility={features:[]};ui.showSignals=true;ui.mobilityMap=true;
 trafficFrames[0].signals=[['n',0,35,33,'r',false]];trafficFrames[1].signals=[['n',0,35,33,'G',false]]`);
 assert.equal(run('JSON.stringify(mobilityLayers()[0].getFillColor(mobilityLayers()[0].data[0]))'),'[238,62,65]');
 run('trafficFrames.shift()');
 assert.equal(run('JSON.stringify(mobilityLayers()[0].getFillColor(mobilityLayers()[0].data[0]))'),'[39,196,106]');
 run('ui.showSignals=false');assert.equal(run('mobilityLayers().length'),0);
});

test('utility geometry uses the authoritative type dimensions at both detail levels',()=>{
 const {run}=fixture();
 run(html.slice(html.indexOf('function carDetail('),html.indexOf('function carLayers(')));
 run(html.slice(html.indexOf('function vehicleScale('),html.indexOf('function setMixedTraffic(')));
 run(`trafficNetwork={vehicle_types:{bus:{class:'bus',length_m:12,width_m:2.5,height_m:3.4}}};
  c={type:'bus',pose:{position:[35,33]}}`);
 assert.equal(run('carDetail(c,10,null)'),'bus');
 assert.equal(run('JSON.stringify(vehicleScale(c,"bus"))'),'[12,2.5,3.4]');
 run('c.pose.orientation=[0,0,0];c.pose.position=[35,33,.04]');
 assert.ok(Math.abs(run('(35-vehicleMeshPosition(c)[0])*111320*Math.cos(33*Math.PI/180)')-6)<1e-7);
 assert.equal(run('vehicleMeshPosition(c)[1]'),33);
 assert.equal(run('c.pose.position[0]'),35);
});
test('replay interpolates authoritative positions at the adaptive rate and preserves identity',()=>{
 const {run}=fixture();run('advanceCars(0); advanceCars(250)');
 assert.equal(run('trafficClock'),.0625);
 assert.equal(run('cars[0].pose.position[0]'),35.0000625);
 assert.equal(run('cars[0].pose.orientation[1]'),0);
 assert.equal(run('cars[0].key'),'a');
});
test('slow playback approaches the buffer edge without extrapolating or wrapping',()=>{
 const {run}=fixture();run('for(let t=0;t<=10000;t+=250) advanceCars(t)');
 assert.ok(run('trafficClock') > 1.8);
 assert.ok(run('trafficClock') < 2);
 assert.ok(run('cars[0].pose.position[0]') < 35.002);
});
test('playback starts below real time so a slow SUMO response does not immediately drain the buffer',()=>{
 const {run}=fixture();run('for(let t=0;t<=4000;t+=250) advanceCars(t)');
 assert.equal(run('trafficClock'),1);
 assert.equal(run('trafficEffectiveRate'),.25);
 assert.equal(run('cars[0].pose.position[0]'),35.001);
});
test('closure change preserves fleet positions pending authoritative reroute',()=>{
 const {run}=fixture();run('advanceCars(0);ui.closedRoads.add(0);carHour=8;prepareCars()');
 assert.equal(run('cars.length'),1);
 assert.equal(run('cars[0].key'),'a');
});
test('arrival removes vehicle only on the recorded arrival frame',()=>{
 const {run}=fixture();run('trafficPlaybackRate=1;trafficFetch=false;trafficFrames[1].vehicles=[]; advanceCars(0);advanceCars(250)');
 assert.equal(run('cars.length'),1);
 run('advanceCars(500);advanceCars(750);advanceCars(1000)');
 assert.equal(run('cars.length'),0);
});
test('an exhausted buffer holds its last SUMO pose and resumes from there after refill',async()=>{
 const {run}=fixture({fetch:async()=>response({frames:[frame(3),frame(4)]})});
 run('for(let t=0;t<=120000;t+=250) advanceCars(t)');
 assert.equal(run('trafficClock'),2);
 assert.equal(run('trafficEffectiveRate'),0);
 assert.equal(run('cars[0].pose.position[0]'),35.002);
 run('trafficSession="live";trafficFetch=false');
 await run('fetchTrafficFrames()');
 assert.equal(run('trafficClock'),2);
 run('advanceCars(120250)');
 assert.ok(run('trafficClock')>2 && run('trafficClock')<=2.25);
 assert.equal(run('cars[0].key'),'a');
 assert.ok(run('cars[0].pose.position[0]')>35.002);
});
test('rate learning uses delivered SUMO time rather than requested time',async()=>{
 let now=0, batch=[frame(2),frame(2.5)];
 const {run}=fixture({performance:{now:()=>now},fetch:async()=>{now+=4000;return response({frames:batch});}});
 run('trafficSession="live";trafficFetch=false');
 await run('fetchTrafficFrames()');
 assert.equal(run('trafficPlaybackRate'),.25*.35+(.5/4*.75)*.65);
 batch=[frame(3)];
 await run('fetchTrafficFrames()');
 assert.ok(run('trafficPlaybackRate')<.15);
 assert.equal(run('trafficFrames.length'),5); // overlapping frame at 2 is ignored
});
test('duplicate, empty and invalid batches preserve the last buffer and report an error',async()=>{
 for(const frames of [[],[frame(2)],[frame(4),frame(3)],[{...frame(3),time_s:NaN}]]) {
  const {run}=fixture({fetch:async()=>response({frames})});
  run('trafficSession="live";trafficFetch=false');
  await run('fetchTrafficFrames()');
  assert.equal(run('trafficFrames.length'),3);
  assert.equal(run('trafficClock'),0);
  assert.equal(run('trafficPlaybackRate'),.25);
  assert.match(run('trafficError'),/SUMO frame batch/);
  assert.equal(run('trafficFetch'),false);
 }
});
test('initial empty response is recoverable by toggling traffic and starts at the first SUMO timestamp',async()=>{
 let frames=[];
 const {run}=fixture({fetch:async()=>response({session:'new',frames})});
 run('closeTrafficSession();carHour=8');
 await run('fetchTrafficFrames()');
 assert.match(run('trafficError'),/Invalid SUMO/);
 assert.equal(run('trafficClock'),null);
 run('ui.traffic=false;prepareCars()');
 frames=[frame(100),frame(101)];
 run('ui.traffic=true;prepareCars()');
 await settle();
 assert.equal(run('trafficError'),null);
 assert.equal(run('trafficClock'),100);
 run('advanceCars(999999)');
 assert.equal(run('trafficClock'),100); // no startup wall-time catch-up
});
test('hour changes discard stale startup responses and delete the abandoned session',async()=>{
 const pending=deferred(), requests=[];
 const {run}=fixture({fetch:(url,options)=>{
  requests.push({url,options});
  if(options.method==='DELETE')return Promise.resolve({});
  return requests.filter(r=>r.options.method==='POST').length===1 ? pending.promise : Promise.resolve(response({session:'current',frames:[frame(80),frame(81)]}));
 }});
 run('closeTrafficSession();carHour=8');
 const old=run('fetchTrafficFrames()');
 run('ui.hour=9;prepareCars()');
 await settle();
 pending.resolve(response({session:'abandoned',frames:[frame(0),frame(1)]}));
 await old;
 assert.equal(run('trafficSession'),'current');
 assert.equal(run('trafficClock'),80);
 assert.equal(run('trafficPlaybackRate'),.25);
 assert.ok(requests.some(r=>r.url.endsWith('/abandoned') && r.options.method==='DELETE'));
});
test('a stale failed request cannot clear the fetch lock for the new hour',async()=>{
 const old=deferred(), current=deferred();let requests=0;
 const {run}=fixture({fetch:()=>++requests===1?old.promise:current.promise});
 run('closeTrafficSession();carHour=8');
 const first=run('fetchTrafficFrames()');
 run('ui.hour=9;prepareCars()');
 old.resolve({ok:false,json:async()=>({detail:'old error'})});
 await first;
 assert.equal(run('trafficError'),null);
 assert.equal(run('trafficFetch'),true);
 await run('fetchTrafficFrames()');
 assert.equal(requests,2);
 current.resolve(response({session:'current',frames:[frame(80),frame(81)]}));
 await settle();
 assert.equal(run('trafficFetch'),false);
});
test('road and hazard edits during a fetch keep the fleet and reach SUMO in the next request',async()=>{
 const pending=deferred(), requests=[];let hazards=[];
 const {run}=fixture({currentHazards:()=>hazards,fetch:(url,options)=>{
  requests.push(JSON.parse(options.body));
  return requests.length===1 ? pending.promise : Promise.resolve(response({frames:[frame(5),frame(6)]}));
 }});
 run('advanceCars(0);carHour=8;trafficSession="live";trafficFetch=false');
 const first=run('fetchTrafficFrames()');
 hazards=[{kind:'storm',severity:1}];
 run('ui.closedRoads.add(3);ui.crashes.add(4);prepareCars()');
 assert.equal(run('cars[0].key'),'a');
 assert.equal(run('cars[0].pose.position[0]'),35);
 assert.equal(requests.length,1);
 pending.resolve(response({frames:[frame(3),frame(4)]}));
 await first;
 run('advanceCars(250)');
 await settle();
 assert.equal(requests.length,2);
 assert.deepEqual(requests[1],{hour:8,seconds:2,continuous:true,mixed:false,closed:[3],crashes:[4],hazards});
 assert.equal(run('cars[0].key'),'a');
});
test('rate learning remains bounded for near-instant and very slow deliveries',async()=>{
 for(const elapsed of [0,1000000]) {
  let now=0, time=2;
  const {run}=fixture({performance:{now:()=>now},fetch:async()=>{now+=elapsed;return response({frames:[frame(++time)]});}});
  run('trafficSession="live";trafficFetch=false');
  for(let i=0;i<20;i++)await run('fetchTrafficFrames()');
  assert.ok(run('trafficPlaybackRate')>=.05 && run('trafficPlaybackRate')<=1);
  assert.ok(elapsed===0 ? run('trafficPlaybackRate')>.99 : run('trafficPlaybackRate')<.051);
 }
});
test('departures and replacements appear only at their authoritative frame, with shortest-turn interpolation',()=>{
 const {run}=fixture();
 run(`trafficFrames[0].vehicles[0][3]=350;
 trafficFrames[1].vehicles[0][3]=10;
 trafficFrames[2].vehicles=[['b',36,34,90,10,'2:1',0]];
 trafficPlaybackRate=1;advanceCars(0);advanceCars(250);advanceCars(500)`);
 assert.equal(run('cars[0].pose.orientation[1]'),-270);
 run('for(let t=750;t<=60000;t+=250) advanceCars(t)');
 assert.equal(run('cars.length'),1);
 assert.equal(run('cars[0].key'),'b');
 assert.equal(run('cars[0].pose.position[0]'),36);
 assert.equal(run('cars[0].i'),2);
});
test('traffic redraw updates the learned rate without rebuilding the hour slider or removing tools',()=>{
 const fields={};let headerWrites=0;
 fields['p-traffic']={hidden:false,set innerHTML(value){
  headerWrites++;
  for(const id of ['hour','hour-label','traffic-body','traffic-playback-rate','traffic-controls','mobility-pilot'])fields[id]={value:'',textContent:'',innerHTML:'',dataset:{},setAttribute(){}};
 }};
 const {run}=fixture({$:id=>fields[id],hourLabel:h=>`${h}:00`,roadImpact:()=>null,
  FLOW:[[1,2,3],[4,5,6],[7,8,9]],simulationSummary:()=>'',stationRows:()=>''});
 run(html.slice(html.indexOf('function renderTraffic()'),html.indexOf('// ---- meshes')));
 run('DATA.api=true;renderTraffic()');
 const slider=fields.hour;
 assert.match(fields['traffic-playback-rate'].textContent,/0.25×/);
 run('trafficPlaybackRate=.6;ui.hour=9;ui.roadTool="crash";ui.crashes.add(0);renderTraffic()');
 assert.equal(headerWrites,1);
 assert.equal(fields.hour,slider);
 assert.equal(slider.value,9);
 assert.match(fields['traffic-playback-rate'].textContent,/0.60×/);
 assert.match(fields['traffic-controls'].innerHTML,/data-road-tool="close"/);
 assert.match(fields['traffic-controls'].innerHTML,/data-road-tool="crash" aria-pressed="true"/);
 assert.match(fields['traffic-controls'].innerHTML,/data-road-clear/);
});
test('detail levels preserve full represented fleet and imported models',()=>{
 assert.match(html,/importedCarMesh\(kind,/);
 assert.match(html,/for \(const c of cars\) \{/);
 assert.equal(Number(html.match(/const CAR_SPEEDUP = ([0-9.]+)/)[1]),1);
 assert.equal(Number(html.match(/const TRAFFIC_INITIAL_RATE = ([0-9.]+)/)[1]),.25);
 assert.match(html,/if \(zoom < 16.5/);
});

function roadScene() {
 const context=vm.createContext({Math,metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*111320,(a[1]-b[1])*111320),
   roadWidth:r=>r.width_m,ui:{},renderTraffic(){},render(){}});
 vm.runInContext(html.slice(html.indexOf('function offsetRoadPath('),html.indexOf('let streetDetailsCache')),context);
 return s=>vm.runInContext(s,context);
}
test('street markings respect lane widths, junction margins and one-way roads',()=>{
 const run=roadScene();
 run(`r={path:[[0,0],[.001,0]],width_m:12.8,oneway:0};marks=roadMarkings(r)`);
 assert.equal(run("marks.filter(m=>m.kind==='centre').length"),1);
 assert.equal(run("marks.filter(m=>m.kind==='edge').length"),2);
 assert.ok(run("marks.some(m=>m.kind==='lane')"));
 assert.ok(run('marks.every(m=>m.path.every(p=>Math.abs(p[1]*111320)<=6.4))'));
 assert.ok(run('marks.every(m=>m.path.every(p=>p[0]*111320>10 && p[0]*111320<102))'));
 run('r.oneway=-1;marks=roadMarkings(r)');
 assert.equal(run("marks.filter(m=>m.kind==='centre').length"),0);
 run('r.path=[[0,0],[.00005,0]]');
 assert.equal(run('roadMarkings(r).length'),0);
 // Degenerate vertices and sharp bends must not produce infinite offsets.
 assert.ok(run('offsetRoadPath([[0,0],[0,0],[.001,0],[0,0]],3).flat().every(Number.isFinite)'));
});

test('vehicle paint changes body panels while preserving glass, wheels and geometry',()=>{
 const models=JSON.parse(fs.readFileSync(`${__dirname}/../src/dynacity/assets/cars/meshes.json`,'utf8'));
 const context=vm.createContext({IMPORTED_CARS:models,atob,Uint8Array,Uint16Array,Float32Array,Math});
 vm.runInContext(html.slice(html.indexOf('const CAR_PAINT ='),html.indexOf('let carBuckets')),context);
 vm.runInContext('const meshes={};'+html.slice(html.indexOf('function importedCarMesh('),html.indexOf('// Shared low-poly sedan')),context);
 for(const kind of ['sedan','suv']) {
   const a=vm.runInContext(`importedCarMesh('${kind}',0)`,context),b=vm.runInContext(`importedCarMesh('${kind}',2)`,context);
   const mask=Buffer.from(models[kind].paint,'base64');let changed=0;
   for(let i=0;i<mask.length;i++)for(let c=0;c<3;c++) {
     const x=a.attributes.colors.value[i*3+c],y=b.attributes.colors.value[i*3+c];
     if(mask[i])changed+=x!==y;else assert.equal(x,y);
   }
   assert.ok(changed>0,`${kind} has visible paint variation`);
   assert.deepEqual(a.attributes.positions.value,b.attributes.positions.value);
   assert.deepEqual(a.indices.value,b.indices.value);
 }
});

test('appearance switch keeps the same simulation and vehicle identities',()=>{
 const {run}=fixture();
 run(html.slice(html.indexOf('function setTrafficStyle('),html.indexOf('function roadLayers(')));
 run("const originalCars=cars;trafficSession='same-session';setTrafficStyle('street');setTrafficStyle('colours')");
 assert.equal(run('cars===originalCars'),true);
 assert.equal(run('trafficSession'),'same-session');
 assert.equal(run('ui.trafficStyle'),'colours');
});

test('brake lights follow recorded slowing or stopped vehicles and speed interpolates',()=>{
 const {run}=fixture();
 run('trafficPlaybackRate=1;trafficFrames[1].vehicles[0][4]=0;advanceCars(0);advanceCars(250);advanceCars(500)');
 assert.equal(run('cars[0].speed'),5);
 assert.equal(run('cars[0].braking'),true);
 run('trafficFrames[1].vehicles[0][4]=12;advanceCars(500)');
 assert.equal(run('cars[0].braking'),false);
});

test('animation reuses pose arrays between samples and updates them when the frame changes',()=>{
 const {run}=fixture();
 run('trafficPlaybackRate=1;advanceCars(0);const pose=cars[0].pose,position=pose.position;advanceCars(250)');
 assert.equal(run('cars[0].pose===pose && cars[0].pose.position===position'),true);
 assert.equal(run('position[0]'),35.00025);
 run('advanceCars(500);advanceCars(750);advanceCars(1000);advanceCars(1250)');
 assert.equal(run('cars[0].pose===pose && cars[0].pose.position===position'),true);
 assert.equal(run('position[0]'),35.00125);
});

test('close-up drawing culls only offscreen cars and preserves the full simulated fleet',()=>{
 const scene={dataset:{}};
 const {run}=fixture({map:{getZoom:()=>19,getCenter:()=>({toArray:()=>[35,33]}),
   getBounds:()=>({toArray:()=>[[34.999,32.999],[35.002,33.002]]})},
   calm:{matches:false},simpleTraffic:false,trafficDebug:true,$:()=>scene,
   metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*90000,(a[1]-b[1])*111320),
   hash01:()=>.4,photoreal:()=>false,SOFT:{},mesh:()=>({}),importedCarMesh:()=>({}),
   carMesh(){},carShadowMesh(){},carBrakeMesh(){},
   deck:{SimpleMeshLayer:class {constructor(props){this.props=props;}}}});
 run(html.slice(html.indexOf('const CAR_PAINT ='),html.indexOf('function setTraffic(on)')));
 run(`advanceCars=()=>{};cars=[['near',35,33],['edge',35.0025,33],['far',36,33]].map(([key,x,y])=>
   ({key,pose:{position:[x,y,.04],orientation:[0,0,0]}}));layers=carLayers(1000);`);
 assert.equal(run('cars.length'),3);
 assert.equal(run("layers.filter(l=>l.props.id.startsWith('cars-')).reduce((n,l)=>n+l.props.data.length,0)"),2);
 assert.equal(scene.dataset.trafficCars,3);
 assert.equal(scene.dataset.trafficVisibleCars,2);
 assert.equal(run("layers.filter(l=>l.props.id.startsWith('cars-')).flatMap(l=>l.props.data).some(c=>c.key==='far')"),false);
});
