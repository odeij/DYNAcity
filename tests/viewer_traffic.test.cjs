// Pure playback adapter tests, using authoritative TraCI-shaped frames.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
function fixture() {
 const context=vm.createContext({ui:{traffic:true,hour:8,closedRoads:new Set(),crashes:new Set()},
  DATA:{traffic:{roads:[]}},currentHazards:()=>[],prepareRoads:()=>{},CAR_SPEEDUP:1,
  clamp01:x=>Math.max(0,Math.min(1,x)),renderTraffic:()=>{},render:()=>{},esc:x=>x,
  fetch:async()=>{throw new Error('unexpected fetch')},console});
 const run=s=>vm.runInContext(s,context);
 run('let cars=[],carHour=null,carTime=null;');
 run(html.slice(html.indexOf('// Authoritative SUMO replay'),html.indexOf('const CAR_PAINT')));
 run(`trafficFrames=[{time_s:0,vehicles:[['a',35,33,90,10,'0:1',0]],metrics:{total:1}},
 {time_s:1,vehicles:[['a',35.001,33,90,10,'0:1',0]],metrics:{total:1}},
 {time_s:2,vehicles:[['a',35.002,33,90,10,'1:1',0]],metrics:{total:1}}];
 trafficClock=0; trafficFetch=true;`);
 return {run};
}
test('replay interpolates authoritative positions at 1× and preserves identity',()=>{
 const {run}=fixture();run('advanceCars(0); advanceCars(250)');
 assert.equal(run('trafficClock'),.25);
 assert.equal(run('cars[0].pose.position[0]'),35.00025);
 assert.equal(run('cars[0].pose.orientation[1]'),0);
 assert.equal(run('cars[0].key'),'a');
});
test('buffer exhaustion pauses instead of extrapolating or wrapping',()=>{
 const {run}=fixture();run('for(let t=0;t<=4000;t+=250) advanceCars(t)');
 assert.equal(run('trafficClock'),2);
 assert.equal(run('cars[0].pose.position[0]'),35.002);
});
test('closure change preserves fleet positions pending authoritative reroute',()=>{
 const {run}=fixture();run('advanceCars(0);ui.closedRoads.add(0);carHour=8;prepareCars()');
 assert.equal(run('cars.length'),1);
 assert.equal(run('cars[0].key'),'a');
});
test('arrival removes vehicle only on the recorded arrival frame',()=>{
 const {run}=fixture();run('trafficFrames[1].vehicles=[]; advanceCars(0);advanceCars(250)');
 assert.equal(run('cars.length'),1);
 run('advanceCars(500);advanceCars(750);advanceCars(1000)');
 assert.equal(run('cars.length'),0);
});
test('detail levels preserve full represented fleet and imported models',()=>{
 assert.match(html,/importedCarMesh\(kind\)/);
 assert.match(html,/for \(const c of cars\) carBuckets/);
 assert.equal(Number(html.match(/const CAR_SPEEDUP = ([0-9.]+)/)[1]),1);
 assert.match(html,/if \(zoom < 16.5/);
});
