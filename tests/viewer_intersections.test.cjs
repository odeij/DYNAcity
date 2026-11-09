require('./viewer_ground_helper.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_intersections.js`,'utf8');
function setup(){
 const sy=111320,sx=sy*Math.cos(33.9*Math.PI/180);
 const road={path:[[35.5,33.9,2],[35.5+40/sx,33.9,6]],lvl:1};
 const signal=(direction='s',link=0)=>['node',link,35.5+30/sx,33.9,'G',false,0,0,0,0,direction];
 const context=vm.createContext({roadWidth:()=>7,DATA:{traffic:{roads:[road]}},ui:{traffic:true,trafficStyle:'street'},
  map:{getZoom:()=>19,getCenter:()=>({toArray:()=>[35.5,33.9]})},trafficFrames:[{signals:[signal()]}],
  metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*sx,(a[1]-b[1])*sy),nightLevel:()=>0,shade:x=>x,SOFT:{},
  deck:{PathLayer:class{constructor(props){Object.assign(this,props);}}}});
 vm.runInContext(source,context);
 return {context,road,signal,sx};
}
test('direction paint merges links on a shared lane and follows ramp heights',()=>{
 const {context,road,signal,sx}=setup();
 const one=context.intersectionApproachMarkings([signal('slr')],[road]);
 const merged=context.intersectionApproachMarkings([signal('l'),signal('sr',1)],[road]);
 assert.equal(one.length,7);assert.equal(merged.length,7);
 for(const mark of merged)for(const p of mark.path){
  const along=(p[0]-35.5)*sx;
  assert.ok(Math.abs(p[2]-(2+along/40*4+.065))<1e-7);
  assert.ok(Math.abs((p[1]-33.9)*111320)<3.5);
 }
 const xs=merged.flatMap(m=>m.path.map(p=>(p[1]-33.9)*111320));
 assert.ok(Math.min(...xs)<-1);assert.ok(Math.max(...xs)>1);
});
test('short, tunnel, invalid and off-road approaches never paint an arrow',()=>{
 const {context,road,signal,sx}=setup();
 assert.equal(context.intersectionApproachMarkings([signal('t')],[road]).length,0);
 assert.equal(context.intersectionApproachMarkings([signal()],[{...road,lvl:-1}]).length,0);
 const off=signal();off[3]+=.001;
 assert.equal(context.intersectionApproachMarkings([off],[road]).length,0);
 const short=signal();short[2]=35.5+6/sx;
 assert.equal(context.intersectionApproachMarkings([short],[road]).length,0);
 assert.equal(context.intersectionApproachMarkings([[0,1,NaN]],[road]).length,0);
});
test('paint is close-view only and caches across signal state changes',()=>{
 const {context}=setup();
 const first=context.intersectionDetailLayers()[0];assert.ok(first.data.length);
 context.trafficFrames[0].signals[0][4]='r';
 assert.equal(context.intersectionDetailLayers()[0].data,first.data);
 context.map.getZoom=()=>17;assert.equal(context.intersectionDetailLayers().length,0);
 context.map.getZoom=()=>19;context.ui.trafficStyle='colours';assert.equal(context.intersectionDetailLayers().length,0);
});
test('repeated animation frames do not scan lane metadata; new layout and camera update paint',()=>{
 const {context,signal}=setup();let headingReads=0;
 const s=new Proxy(signal(),{get(target,key){if(key==='6')headingReads++;return target[key];}});
 context.trafficFrames[0].signals=[s];
 const first=context.intersectionDetailLayers()[0].data,firstReads=headingReads;
 for(let i=0;i<60;i++)assert.equal(context.intersectionDetailLayers()[0].data,first);
 assert.equal(headingReads,firstReads);
 const next=s.slice();next[4]='r';context.trafficFrames[0].signals=[next];
 assert.equal(context.intersectionDetailLayers()[0].data,first);
 // A new array with changed controlled movements must rebuild the geometry.
 const changed=next.slice();changed[10]='sl';context.trafficFrames[0].signals=[changed];
 const updated=context.intersectionDetailLayers()[0].data;
 assert.notEqual(updated,first);assert.equal(updated.length,5);
 context.map.getCenter=()=>({toArray:()=>[35.52,33.9]});
 assert.equal(context.intersectionDetailLayers().length,0);
});
