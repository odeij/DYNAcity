const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
function scene(){
 const calls=[];
 class Light { constructor(props){Object.assign(this,props);} }
 const ctx={ui:{traffic:true,hour:8},mix:(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t),nightLevel:()=>0,isDark:()=>false,
  deck:{AmbientLight:Light,DirectionalLight:Light,LightingEffect:Light},deckgl:{setProps:p=>calls.push(p)},map:null,ground:{grid:null}};
 vm.runInNewContext(html.slice(html.indexOf('function daylightSettings('),html.indexOf('const MATERIAL =')),ctx);
 return {ctx,calls};
}
test('daylight moves east to west while all rays stay downward',()=>{
 const {ctx}=scene(),morning=ctx.daylightSettings(8),noon=ctx.daylightSettings(12),evening=ctx.daylightSettings(16);
 assert.ok(morning.direction[0]<0);assert.ok(evening.direction[0]>0);
 assert.ok(Math.abs(noon.direction[0])<1e-9);
 for(const s of [morning,noon,evening])assert.ok(s.direction[2]<0);
 assert.ok(noon.direction[2]<morning.direction[2]);
 assert.ok(morning.colour[2]<noon.colour[2]);
});
test('daytime hour changes refresh lighting once, animation frames reuse it',()=>{
 const {ctx,calls}=scene();ctx.applyNight();ctx.applyNight();assert.equal(calls.length,1);
 ctx.ui.hour=16;ctx.applyNight();assert.equal(calls.length,2);
 assert.ok(calls[0].effects[0].sun.direction[0]<0);assert.ok(calls[1].effects[0].sun.direction[0]>0);
});
test('night disables sun and static map uses neutral midday illumination',()=>{
 const {ctx}=scene();assert.equal(ctx.lighting(1).sun.intensity,0);
 ctx.ui.traffic=false;ctx.ui.hour=3;
 assert.ok(Math.abs(ctx.lighting().sun.direction[0])<1e-9);
});
test('sun shadows cast from buildings only onto the ground, and fade out at night',()=>{
 const {ctx}=scene();ctx.ui.shadows=true;ctx.nightLevel=()=>0;
 assert.ok(ctx.shadowsOn());assert.ok(ctx.lighting(0).sun._shadow);
 assert.equal(ctx.lighting(0).shadowColor[3],0.5);assert.equal(ctx.lighting(1).shadowColor[3],0);
 ctx.nightLevel=()=>1;assert.ok(!ctx.shadowsOn());
 ctx.nightLevel=()=>0;ctx.ui.shadows=false;assert.ok(!ctx.shadowsOn());
 // Terrain no longer switches shadows off: the receiver follows the ground instead.
 ctx.ui.shadows=true;ctx.ground.grid={};assert.ok(ctx.shadowsOn());ctx.ground.grid=null;
 // Roof caps sit on the building tops and must not cast a second shadow.
 for(const id of ['buildings-roofs','beirut-001-roofs'])assert.ok(!vm.runInContext('SHADOW_CASTERS',ctx).test(id),id);
 for(const id of ['buildings','beirut-001-buildings','osm-gap-buildings','bridge-deck-slabs','mapped-tree-canopies'])assert.ok(vm.runInContext('SHADOW_CASTERS',ctx).test(id),id);
 for(const id of ['shadow-ground','shadow-terrain']){assert.ok(!vm.runInContext('SHADOW_CASTERS',ctx).test(id),id);assert.ok(vm.runInContext('SHADOW_RECEIVERS',ctx).test(id),id);}
 // The depth-only copy of the terrain mesh neither casts nor receives.
 assert.ok(!vm.runInContext('SHADOW_CASTERS',ctx).test('shadow-terrain-depth'));assert.ok(!vm.runInContext('SHADOW_RECEIVERS',ctx).test('shadow-terrain-depth'));
 assert.ok(!vm.runInContext('SHADOW_RECEIVERS',ctx).test('buildings'));
});
// A synthetic 12 x 9 node grid, 1e-4 degrees a cell, height = 2 m per column + 1 m per row.
function terrainScene(bounds){
 const {ctx}=scene(),cols=12,rows=9,grid=new Int16Array(cols*rows);
 for(let r=0;r<rows;r++)for(let c=0;c<cols;c++)grid[r*cols+c]=(2*c+r)*100;
 ctx.DATA={terrain:{west:35.5,north:33.9,cell:1e-4,cols,rows,unit_m:.01},bounds};
 ctx.ground.grid=grid;
 class Layer{constructor(props){Object.assign(this,props);}}
 ctx.deck.SimpleMeshLayer=Layer;ctx.SOFT={depthWriteEnabled:false};
 return ctx;
}
test('with terrain the shadow receiver is the ground grid at its own heights, lifted clear of the roads',()=>{
 // Bounds wide enough that the padded box covers the whole grid.
 const ctx=terrainScene([[35.5,33.8992],[35.5011,33.9]]);
 const m=ctx.shadowTerrainMesh(),lift=vm.runInContext('SHADOW_MESH_LIFT_M',ctx);
 assert.equal(m.step,1);assert.equal(m.vertices,12*9);
 assert.deepEqual([...m.origin],[35.5,33.9,0]);
 const pos=m.mesh.attributes.POSITION.value,idx=m.mesh.indices.value;
 assert.equal(idx.length,11*8*6);assert.ok(Math.max(...idx)<m.vertices);
 // Node (column 5, row 3): offsets from the north-west node, height (2*5+3) m plus the lift.
 const k=(3*12+5)*3;
 assert.ok(Math.abs(pos[k]-5e-4)<1e-9);assert.ok(Math.abs(pos[k+1]+3e-4)<1e-9);assert.ok(Math.abs(pos[k+2]-(13+lift))<1e-5);
 // Every node sits on its own grid height (groundZ is exact on the nodes).
 for(let v=0;v<m.vertices;v++){const c=Math.round(pos[v*3]/1e-4),r=Math.round(-pos[v*3+1]/1e-4);assert.ok(Math.abs(pos[v*3+2]-lift-(2*c+r))<1e-4,String(v));}
 // Built once per grid.
 assert.equal(ctx.shadowTerrainMesh(),m);
});
test('the terrain receiver thins to its vertex budget and keeps the grid edge',()=>{
 const ctx=terrainScene([[35.5,33.8992],[35.5011,33.9]]);
 const m=ctx.shadowTerrainMesh(30);
 assert.equal(m.step,2);assert.equal(m.vertices,7*5);
 const pos=m.mesh.attributes.POSITION.value,last=(m.vertices-1)*3;
 assert.ok(Math.abs(pos[last]-11e-4)<1e-9);assert.ok(Math.abs(pos[last+1]+8e-4)<1e-9);
});
test('terrain shadow layers: a sunk depth-only copy, then the transparent receiver',()=>{
 const ctx=terrainScene([[35.5,33.8992],[35.5011,33.9]]);
 const [depth,receiver]=ctx.shadowTerrainLayers();
 assert.equal(depth.id,'shadow-terrain-depth');assert.equal(receiver.id,'shadow-terrain');
 for(const l of [depth,receiver]){assert.equal(l._instanced,false);assert.deepEqual([...l.getColor],[0,0,0,0]);assert.equal(l.material,false);assert.equal(l.pickable,false);}
 assert.ok(depth.getTranslation[2]<-1);assert.equal(depth.parameters,undefined);
 assert.equal(receiver.parameters.depthWriteEnabled,false);
});
