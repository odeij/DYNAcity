const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const surveyFixture=process.env.DYNACITY_TEST_FLOOD_CONTEXT?JSON.parse(fs.readFileSync(process.env.DYNACITY_TEST_FLOOD_CONTEXT,'utf8')):null;
test('typed flood requests open hydraulic controls without starting an LLM request',async()=>{
 const nodes=new Map(),drops=[];
 const context=vm.createContext({dropAt:kind=>drops.push(kind),
  $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id)}});
 vm.runInContext(html.slice(html.indexOf('function isFloodRequest('),html.indexOf('async function runAsk(')),context);
 for(const text of ['run flood','simulate flooding here','فيضان']){
  await vm.runInContext(`runScenario(${JSON.stringify(text)})`,context);
 }
 assert.deepEqual(drops,['flood','flood','flood']);
 assert.match(nodes.get('progress').textContent,/not running/);
});
function surveyRuntime(){
 const context=vm.createContext({surveyFixture,photoreal:()=>false,ON_TOP:{},deck:{
  PathLayer:class{constructor(props){this.props=props}},ScatterplotLayer:class{constructor(props){this.props=props}}}});
 vm.runInContext(html.slice(html.indexOf('let floodSurvey ='),html.indexOf('// Timestamped model outputs:')),context);
 vm.runInContext('floodSurvey.data=surveyFixture',context);
 return code=>vm.runInContext(code,context);
}
function nativeRuntime(fetch){
 const nodes=new Map(),timers=new Map();let nextTimer=0;
 const context=vm.createContext({fetch,AbortController,TypeError,photoreal:()=>false,ON_TOP:{},render:()=>{},
  $:id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',value:0});return nodes.get(id)},
  setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer},clearTimeout:id=>timers.delete(id),
  deck:{SolidPolygonLayer:class{constructor(props){this.props=props}}}});
 vm.runInContext(html.slice(html.indexOf('let floodRun='),html.indexOf('// Historical survey context')),context);
 return {run:code=>vm.runInContext(code,context),timers,nodes};
}
test('unresponsive flood service times out and releases the request timer',async()=>{
 const {run,timers}=nativeRuntime((url,options)=>new Promise((resolve,reject)=>{
  options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));
 }));
 const request=run("floodRunRequest('/v1/flood/solver/inputs')");
 assert.equal(timers.size,1);
 [...timers.values()][0]();
 await assert.rejects(request,/did not respond in time/);
 assert.equal(timers.size,0);
});
test('offline flood service shows a useful error without leaving a loading request',async()=>{
 const {run,timers}=nativeRuntime(async()=>{throw new TypeError('Failed to fetch')});
 await assert.rejects(run("floodRunRequest('/v1/flood/solver/inputs')"),/Cannot reach the local flood server/);
 assert.equal(timers.size,0);
});
test('computed mesh playback preserves dry, missing and wet cells without inventing elevations',()=>{
 const {run}=nativeRuntime();
 run("floodRun.geometry={polygons:['a','b','c']};floodRun.frame={depth_m:[0,null,.35]}");
 assert.equal(run('floodNativeLayers()[0].props.data.length'),2);
 assert.equal(run('floodNativeLayers()[0].props.data[0].depth'),null);
 assert.equal(run('floodNativeLayers()[0].props.data[1].depth'),.35);
 assert.equal(run('floodNativeLayers()[0].props.extruded'),false);
 assert.equal(run('floodNativeLayers()[0].props.getElevation'),undefined);
 run('floodRun.visible=false');assert.equal(run('floodNativeLayers().length'),0);
});
test('a late frame response cannot overwrite a newer timeline selection',async()=>{
 const requests=[];
 const {run}=nativeRuntime(()=>new Promise(resolve=>requests.push(resolve)));
 run("floodRun.job={job_id:'test'};floodRun.geometry={timestamps:['a','b'],volumes_m3:[0,1]}");
 const old=run('loadFloodNativeFrame(0)'),latest=run('loadFloodNativeFrame(1)');
 requests[1]({ok:true,json:async()=>({index:1,time_s:1,depth_m:[.2]})});await latest;
 requests[0]({ok:true,json:async()=>({index:0,time_s:0,depth_m:[0]})});await old;
 assert.equal(run('floodRun.frame.index'),1);
});
test('pausing during a frame request prevents playback restarting itself',async()=>{
 let finish;
 const {run,timers}=nativeRuntime(()=>new Promise(resolve=>{finish=resolve}));
 run("floodRun.job={job_id:'test'};floodRun.geometry={timestamps:['a','b','c'],volumes_m3:[0,1,2]};floodRun.frame={index:0}");
 await run('toggleFloodNative()');
 const step=timers.values().next().value;timers.clear();const pending=step();
 run('pauseFloodNative()');
 finish({ok:true,json:async()=>({index:1,time_s:1,depth_m:[.2]})});await pending;
 assert.equal(timers.size,0);assert.equal(run('floodRun.play'),null);
});
test('verified survey counts remain records, with unknowns and independent ponding preserved',{skip:!surveyFixture},()=>{
 const run=surveyRuntime();
 assert.equal(run('floodSurveyRows().streets.length'),679);assert.equal(run('floodSurveyRows().points.length'),224);
 run("floodSurvey.condition='unknown'");assert.equal(run('floodSurveyRows().streets.length'),11);
 assert.equal(run('floodSurveyRows().points.length'),224);
 run("floodSurvey.condition='';floodSurvey.neighborhood='Karm El-Zeytoun'");
 assert.equal(run('floodSurveyRows().streets.length'),146);assert.equal(run('floodSurveyRows().points.length'),9);
 run('floodSurvey.ponding=false');assert.equal(run('floodSurveyRows().points.length'),0);
});
test('survey map creates observation layers without manufactured heights or hydraulic assets',{skip:!surveyFixture},()=>{
 const run=surveyRuntime();assert.equal(run('floodSurveyLayers().length'),0);
 run('floodSurvey.visible=true');assert.equal(run('floodSurveyLayers().length'),2);
 assert.equal(run("floodSurveyLayers()[0].props.id"),'flood-survey-streets');
 assert.equal(run("floodSurveyLayers()[1].props.id"),'flood-survey-ponding');
 assert.equal(run("floodSurveyLayers()[1].props.getPosition(floodSurveyLayers()[1].props.data[0]).length"),2);
 assert.equal(run("floodSurveyLayers()[0].props.getElevation"),undefined);
 assert.equal(run("floodSurveyLayers()[0].props.data.every(d=>d.survey.hydraulic_asset===false)"),true);
 run("floodSurvey.condition='blocked';floodSurvey.ponding=false");
 assert.equal(run("floodSurveyLayers().length"),1);
 assert.equal(run("floodSurveyLayers()[0].props.data.every(d=>d.survey.condition==='blocked')"),true);
});
test('flood controls do not expose an invented demo',()=>{
 assert.ok(!html.includes('id="flood-map-demo"'));
 assert.ok(!html.includes("fetch('/v1/flood/pilot/demo')"));
 assert.ok(html.includes('id="flood-run-start" type="button" disabled'));
 assert.ok(html.includes('Run flood'));
});
test('flood selection opens documented outputs without calculating an invented drop',()=>{
 const panel={hidden:true};let toggled=false;
 const context=vm.createContext({$:()=>panel,togglePanel:()=>{toggled=true},
  toLngLat:()=>{throw Error('must not create a flood drop')}});
 vm.runInContext(html.slice(html.indexOf('function dropAt('),html.indexOf('function steerStart(')),context);
 vm.runInContext("dropAt('flood',0,0)",context);
 assert.equal(panel.hidden,false);assert.ok(toggled);
});
test('city flood layer preserves exact depths, nodata and output timestamps',()=>{
 const context=vm.createContext({photoreal:()=>false,deck:{SolidPolygonLayer:class{constructor(props){this.props=props}}}});
 const run=code=>vm.runInContext(code,context);
 run(html.slice(html.indexOf('let floodPlayback ='),html.indexOf('function pauseFloodPlayback()')));
 run(`floodPlayback.bundle={scenarios:[{frames:[{depth_m:[0,null,.35]},{depth_m:[.1,0,.2]}]}]};floodPlayback.geometry={polygons:['a','b','c']};floodPlayback.visible=true;`);
 assert.equal(run('floodPlaybackRows().length'),2);
 assert.equal(run('floodPlaybackRows()[0].depth'),null);
 assert.equal(run('floodPlaybackLayers()[0].props.getElevation(floodPlaybackRows()[1])'),.35);
 run('floodPlayback.frame=1');
 assert.equal(run('floodPlaybackRows()[0].depth'),.1);
 run('floodPlayback.visible=false');
 assert.equal(run('floodPlaybackLayers().length'),0);
});
test('playback pauses, replays and stops without extrapolating past the final output',()=>{
 let tick=null;const button={textContent:''};let renders=0;
 const context=vm.createContext({$:()=>button,setInterval:fn=>{tick=fn;return 1},clearInterval:()=>{tick=null},
  renderFloodPlayback:()=>{renders++}});
 const run=code=>vm.runInContext(code,context);
 run("let floodPlayback={bundle:{scenarios:[{frames:[{},{}]}]},frame:0,timer:null};");
 const pause=html.slice(html.indexOf('function pauseFloodPlayback()'),html.indexOf('function renderFloodPlayback()'));
 const play=html.slice(html.indexOf('function toggleFloodPlayback()'),html.indexOf('function wireFloodWorkspace()'));
 run(pause+play);
 run('toggleFloodPlayback()');assert.equal(button.textContent,'Ⅱ Pause');tick();
 assert.equal(run('floodPlayback.frame'),1);assert.equal(tick,null);assert.equal(button.textContent,'↻ Replay');
 run('toggleFloodPlayback()');assert.equal(run('floodPlayback.frame'),0);
 run('toggleFloodPlayback()');assert.equal(tick,null);assert.equal(button.textContent,'▶ Play');assert.ok(renders>=2);
});
