// Run with node --test tests/viewer_bridges.test.cjs.
require('./viewer_ground_helper.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function load(start, end, context) {
  vm.runInNewContext(html.slice(html.indexOf(start), html.indexOf(end)), context);
}
const LAT = 33.9, M = 1 / 111320;
const mPerLon = (lat) => 111320 * Math.cos((lat * Math.PI) / 180);
const metresBetween = (a, b) => Math.hypot((b[0] - a[0]) * mPerLon(a[1]), (b[1] - a[1]) * 111320);
// Ground road 0 → bridge 1 → bridge 2 → ground road 3 along a north-running line,
// plus a tunnel. Node 3 joins only bridges, so the deck stays up there.
const at = (metres) => [35.5, LAT + metres * M];
function roads() {
  return [
    {u: 1, v: 2, path: [at(-100), at(0)]},
    {u: 2, v: 3, lvl: 1, path: [at(0), at(200)]},
    {u: 3, v: 4, lvl: 1, path: [at(200), at(400)]},
    {u: 4, v: 5, path: [at(400), at(500)]},
    {u: 6, v: 7, lvl: -1, path: [at(600), at(700)]},
  ];
}
function scene() {
  const context = {DATA: {traffic: {roads: roads()}}, metresBetween};
  load('// ---- bridges', 'function roadMarkings(', context);
  vm.runInContext('elevateRoads()', context);  // the page runs it once the ground grid has loaded
  load('// Height of road `i`', 'function simulationSummary(', context);
  return context;
}

test('source bridge mesh is confined to model view and cannot cover traffic roads',()=>{
 class Layer {constructor(props){Object.assign(this,props);}}
 const encoded=Buffer.from(new Float32Array([0,0,0,1,0,0,0,1,0]).buffer).toString('base64');
 const context={ui:{traffic:false},beirutOverlay:{active:true,data:{bridge_mesh:{origin:[35.5,33.9,0],vertices:3,positions:encoded,normals:encoded}}},
  atob:s=>Buffer.from(s,'base64').toString('binary'),BRIDGE_MATERIAL:{},deck:{SimpleMeshLayer:Layer}};
 load('function beirutBridgeLayers()', 'async function wireBeirutOverlay()',context);
 const layer=context.beirutBridgeLayers()[0];
 assert.equal(layer.id,'beirut-001-bridge-structures');
 assert.equal(layer.mesh.positions.value.length,9);
 assert.equal(context.beirutBridgeLayers()[0].mesh,layer.mesh);
 context.ui.traffic=true;assert.equal(context.beirutBridgeLayers().length,0);
 context.ui.traffic=false;context.beirutOverlay.active=false;assert.equal(context.beirutBridgeLayers().length,0);
});

test('illustrative decks have thickness and piers leave lower road corridors clear',()=>{
 const context={roadWidth:r=>r.width_m??6.4,pavementWidth:()=>1};
 load('function bridgeStructureGeometry(', 'function landscapeLayers()',context);
 const bridge={lvl:1,width_m:6.4,cls:'primary',path:[...[[35.5,LAT,6],[35.5,LAT+100*M,6]]]};
 const crossing={width_m:6.4,path:[[35.5-30/mPerLon(LAT),LAT+48*M],[35.5+30/mPerLon(LAT),LAT+48*M]]};
 const geometry=context.bridgeStructureGeometry([bridge,crossing]);
 assert.equal(geometry.slabs.length,1);
 assert.equal(geometry.slabs[0].scale[2],.36);
 assert.equal(geometry.girders.length,1);
 assert.equal(geometry.barriers.length,2);
 assert.equal(geometry.piers.length,2);
 assert.equal(geometry.caps.length,2);
 for(const pier of geometry.piers) {
  const y=(pier.position[1]-LAT)*111320;
  assert.ok(Math.abs(y-48)>4.8);
  assert.ok(pier.scale[0]>=1.8 && pier.scale[2]>3);
 }
 assert.equal(context.bridgeStructureGeometry([{...bridge,lvl:0}]).piers.length,0);
});

test('concrete bridge profiles have tapered girders, solid barriers and round substantial piers',()=>{
 const context={};
 load('function meshOf(', 'const meshes =',context);
 load('function concreteSectionMesh(', 'function signalPoleMesh()',context);
 const pier=context.concretePierMesh().positions.value;
 assert.ok(pier.length>1000);
 const girder=context.concreteGirderMesh().positions.value;
 const top=[],bottom=[];
 for(let i=0;i<girder.length;i+=3)(girder[i+2]>.49?top:bottom).push(Math.abs(girder[i+1]));
 assert.ok(Math.max(...top)>Math.max(...bottom));
 assert.ok(context.concreteParapetMesh().positions.value.length>100);
});

test('bridges ramp up from the ground and stay level where only bridges meet', () => {
  const {DATA} = scene(), [ground, first, second, after, tunnel] = DATA.traffic.roads;
  const z = (road) => road.path.map((p) => p[2]);
  // Densified, so a straight two-point span can still ramp.
  assert.ok(first.path.length > 20);
  assert.equal(z(first)[0], 0);
  assert.equal(z(first).at(-1), 6);
  assert.ok(Math.abs(z(first)[5] - 6 * metresBetween(first.path[0], first.path[5]) / 40) < 1e-9);
  assert.deepEqual(new Set(z(second).slice(0, 20)), new Set([6]));
  assert.equal(z(second).at(-1), 0);
  // Ground roads and tunnels keep their flat paths.
  assert.equal(ground.path[0].length, 2);
  assert.equal(tunnel.path.length, 2);
  assert.equal(after.path[1].length, 2);
});

test('cars ride on bridges, keep their height through junctions and vanish in tunnels', () => {
  const context = scene();
  const car = (i, metres) => ({i, pose: {position: [...at(metres), .04]}});
  const onDeck = car(1, 150);
  context.carPose(onDeck);
  assert.equal(onDeck.pose.position[2], 6.04);
  // Inside a junction SUMO reports road 0; far from road 0 the height is kept.
  onDeck.i = 0;
  onDeck.pose.position[1] = at(210)[1];
  context.carPose(onDeck);
  assert.equal(onDeck.pose.position[2], 6.04);
  const street = car(3, 450);
  context.carPose(street);
  assert.equal(street.pose.position[2], .04);
  assert.equal(context.inTunnel(car(4, 650)), true);
  assert.equal(context.inTunnel(street), false);
});

test('vehicles follow the continuous ramp surface between sampled road vertices',()=>{
 const context=scene();
 context.DATA.traffic.roads[1].path=[[...at(0),0],[...at(40),6],[...at(200),6]];
 for(const distance of [1,3,5,9,15,21,33,39]) {
  const c={i:1,pose:{position:[...at(distance),.04]}};
  context.carPose(c);
  assert.ok(Math.abs(c.pose.position[2]-(.04+6*distance/40))<1e-7);
 }
 assert.equal(context.roadHeightAt(1,35.51,LAT+20*M),null);
});

test('short bridge pieces meet adjoining road heights exactly without vertical steps',()=>{
 const data=[{u:1,v:2,path:[at(-20),at(0)]},
  {u:2,v:3,lvl:1,deck:6,path:[at(0),at(10)]},
  {u:3,v:4,lvl:1,deck:17,path:[at(10),at(50)]},
  {u:4,v:5,path:[at(50),at(150)]}];
 const context={DATA:{traffic:{roads:data}},metresBetween};
 load('// ---- bridges','function roadMarkings(',context);
 vm.runInContext('elevateRoads()',context);
 assert.equal(data[1].path[0][2],0);
 assert.ok(Math.abs(data[1].path.at(-1)[2]-6)<1e-8);
 assert.ok(Math.abs(data[1].path.at(-1)[2]-data[2].path[0][2])<1e-8);
 assert.ok(Math.abs(data[2].path.at(-1)[2])<1e-8);
});

test('steep concrete sections have the same horizontal endpoints as their asphalt',()=>{
 const context={roadWidth:()=>6.4,pavementWidth:()=>1};
 load('function bridgeStructureGeometry(','function landscapeLayers()',context);
 const road={lvl:1,cls:'primary',path:[[...at(0),3],[...at(10),9]]};
 const slab=context.bridgeStructureGeometry([road]).slabs[0],m=slab.transform;
 assert.ok(Math.abs(m[1]-10)<1e-7);
 for(const [end,z]of[[-.5,3],[.5,9]]) {
  const top=slab.position[2]+m[2]*end+m[10]*.5;
  assert.ok(Math.abs(top-(z-.05))<1e-8,'slab top stays below the ramp surface');
 }
});
