require('./viewer_ground_helper.cjs');
﻿const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_street_edges.js`,'utf8');
const centre=[35.5,33.9],sy=111320,sx=sy*Math.cos(33.9*Math.PI/180);
const ll=(x,y,z=0)=>[35.5+x/sx,33.9+y/sy,z];
function run(roads) {
 const ctx={roadWidth:()=>6.4,pavementWidth:()=>1.6};vm.createContext(ctx);vm.runInContext(source,ctx);
 return ctx.streetEdgeGeometry(roads,centre);
}
const road=(extra={})=>({cls:'residential',path:[ll(0,0),ll(0,80)],...extra});
test('raised edges remain outside asphalt and stop before junction endpoints',()=>{
 const {curbs,walks}=run([road()]);assert.equal(curbs.length,2);assert.equal(walks.length,2);
 for(const d of [...curbs,...walks])for(const p of d.polygon) {
  assert.ok(Math.abs((p[0]-centre[0])*sx)>=3.23);
  assert.ok((p[1]-centre[1])*sy>5 && (p[1]-centre[1])*sy<75);
 }
 assert.equal(curbs[0].illustrative,true);
});
test('no edges are invented on bridges, tunnels, fast roads, absent sidewalks or short junction links',()=>{
 for(const r of [road({lvl:1}),road({lvl:-1}),road({cls:'motorway'}),road({sidewalk:'no'}),road({path:[ll(0,0),ll(0,9)]})])assert.equal(run([r]).curbs.length,0);
 assert.equal(run([road(),road({path:[ll(0,80),ll(0,0)]})]).curbs.length,2);
});
test('long ways intersecting the visible region are retained, far ways are excluded',()=>{
 assert.equal(run([road({path:[ll(0,-900),ll(0,900)]})]).curbs.length,2);
 assert.equal(run([road({path:[ll(2000,0),ll(2000,80)]})]).curbs.length,0);
});
test('street detail only renders at close range and in street view',()=>{
 class Layer {constructor(p){Object.assign(this,p);}}
 const ctx={DATA:{traffic:{roads:[road()]}},ui:{traffic:true,trafficStyle:'street'},map:{getZoom:()=>19,getCenter:()=>({toArray:()=>centre})},roadWidth:()=>6.4,pavementWidth:()=>1.6,metresBetween:()=>0,nightLevel:()=>0,isDark:()=>false,shade:x=>x,deck:{SolidPolygonLayer:Layer}};
 vm.createContext(ctx);vm.runInContext(source,ctx);assert.equal(ctx.streetEdgeDetailLayers().length,2);
 ctx.map.getZoom=()=>17;assert.equal(ctx.streetEdgeDetailLayers().length,0);
 ctx.map.getZoom=()=>19;ctx.ui.trafficStyle='colours';assert.equal(ctx.streetEdgeDetailLayers().length,0);
});
