const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_osm_context.js`,'utf8');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const centre=[35.5,33.9],sy=111320,sx=sy*Math.cos(centre[1]*Math.PI/180);
const p=(x,y)=>[centre[0]+x/sx,centre[1]+y/sy];
const ring=[p(-10,-10),p(10,-10),p(10,10),p(-10,10),p(-10,-10)];
function feature(id,group,kind,geometry,tags={}) {return {type:'Feature',geometry,properties:{id,context_group:group,kind,name:tags.name??kind,tags}};}
function scene(features,extra={}) {
 class Layer {constructor(props){Object.assign(this,props);}}
 const c={DATA:{landscape:{features},traffic:{roads:[{source_way:7}]}},ui:{satellite:false},beirutOverlay:{active:false},
 map:{getZoom:()=>19,getCenter:()=>({toArray:()=>centre}),getBounds:()=>({toArray:()=>[p(-300,-300),p(300,300)]})},
 nightLevel:()=>0,shade:x=>x,mesh:()=>({}),blockMesh:()=>{},MATERIAL:{},ON_TOP:{depthCompare:'always',depthWriteEnabled:false},coveredByBbed:()=>false,
 cameraOcclusion:()=>({model:new Set(),bbed:new Set(),osm:new Set()}),cameraSafeColour:x=>x,sceneCameraKey:()=>'',
 metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*sx,(a[1]-b[1])*sy),
 ringBox:r=>[Math.min(...r.map(p=>p[0])),Math.min(...r.map(p=>p[1])),Math.max(...r.map(p=>p[0])),Math.max(...r.map(p=>p[1]))],
 centroid:r=>r.reduce((s,p)=>[s[0]+p[0]/r.length,s[1]+p[1]/r.length],[0,0]),esc:s=>String(s),
 deck:{PathLayer:Layer,SolidPolygonLayer:Layer,SimpleMeshLayer:Layer,ScatterplotLayer:Layer,TextLayer:Layer},...extra};
 vm.createContext(c);vm.runInContext(source,c);return c;
}
test('context uses mapped paths, tagged stairs and road attributes without changing carriageways',()=>{
 const c=scene([
 feature('way/1','paths','steps',{type:'LineString',coordinates:[p(0,0),p(0,12)]},{step_count:'12'}),
 feature('way/7','roads','road_attributes',{type:'LineString',coordinates:[p(0,0),p(0,12)]},{sidewalk:'no',surface:'concrete'})]);
 const layers=c.osmContextLayers(),paths=layers.find(l=>l.id==='osm-pedestrian-paths'),treads=layers.find(l=>l.id==='osm-stair-treads');
 assert.equal(paths.data.length,1);assert.equal(treads.data.length,12);
 assert.equal(c.DATA.traffic.roads[0].sidewalk,'no');assert.equal(c.DATA.traffic.roads[0].osmTags.surface,'concrete');
 assert.equal(c.DATA.traffic.roads[0].width_m,undefined,'display tags never overwrite simulation widths');
 for(const t of treads.data)for(const point of t.path)assert.equal(point[2],.11,'unknown stairs elevation is not invented');
 const again=c.osmContextLayers();assert.equal(again.find(l=>l.id==='osm-stair-treads').data,treads.data);
});
test('infrastructure references show only mapped points and source lines at close zoom',()=>{
 const point=feature('node/8','infrastructure','traffic_calming',{type:'Point',coordinates:p(2,3)},{traffic_calming:'bump'});
 const line=feature('way/9','infrastructure','power_line',{type:'LineString',coordinates:[p(0,0),p(4,4)]},{power:'line'});
 const c=scene([point,line]),layers=c.osmContextLayers();
 const dots=layers.find(l=>l.id==='osm-infrastructure-points'),paths=layers.find(l=>l.id==='osm-infrastructure-lines');
 assert.equal(dots.data[0],point);assert.equal(paths.data[0],line);
 assert.deepEqual(Array.from(dots.getPosition(point)),[...point.geometry.coordinates,.2]);
 c.map.getZoom=()=>16;assert.equal(c.osmContextLayers().some(l=>l.id.startsWith('osm-infrastructure-')),false);
});
test('mapped infrastructure areas preserve source outlines and holes',()=>{
 const hole=ring.map(q=>[centre[0]+(q[0]-centre[0])/2,centre[1]+(q[1]-centre[1])/2]);
 const area=feature('way/21','infrastructure','power_substation',{type:'Polygon',coordinates:[ring,hole]},{power:'substation'});
 const c=scene([area]),outline=c.osmContextLayers().find(l=>l.id==='osm-infrastructure-areas');
 assert.equal(outline.data.length,2);
 assert.equal(outline.data[0].feature,area);
 assert.deepEqual(Array.from(outline.getPath(outline.data[1]),p=>Array.from(p)),hole.map(p=>[...p,.12]));
 c.map.getZoom=()=>16;assert.equal(c.osmContextLayers().some(l=>l.id==='osm-infrastructure-areas'),false);
});
test('gap buildings respect source footprint priority and never fill covered areas',()=>{
 const a=feature('way/1','buildings','osm_building',{type:'Polygon',coordinates:[ring]});
 const c=scene([a],{coveredByBbed:()=>true});assert.equal(c.osmContextLayers().some(l=>l.id==='osm-gap-buildings'),false);
 const open=scene([a]);
 assert.equal(open.osmContextLayers().find(l=>l.id==='osm-gap-buildings').data.length,1);
});
test('gap buildings draw citywide at every zoom as one stable array, not per viewport',()=>{
 const far=[p(4000,4000),p(4020,4000),p(4020,4020),p(4000,4020),p(4000,4000)];
 const a=feature('way/1','buildings','osm_building',{type:'Polygon',coordinates:[ring]});
 const b=feature('overture/x','buildings','osm_building',{type:'Polygon',coordinates:[far]});
 const c=scene([a,b]);c.map.getZoom=()=>13;
 const first=c.osmContextLayers().find(l=>l.id==='osm-gap-buildings');
 assert.equal(first.data.length,2,'buildings outside the current view are still drawn');
 c.map.getBounds=()=>({toArray:()=>[p(-50,-50),p(50,50)]});
 assert.equal(c.osmContextLayers().find(l=>l.id==='osm-gap-buildings').data,first.data,'panning reuses the same array');
 c.map.getZoom=()=>11;assert.equal(c.osmContextLayers().find(l=>l.id==='osm-gap-buildings').data,first.data,'zooming out keeps them');
});
test('plazas preserve holes and furniture/labels appear only at useful zoom levels',()=>{
 const hole=ring.map(q=>[centre[0]+(q[0]-centre[0])/2,centre[1]+(q[1]-centre[1])/2]);
 const c=scene([
 feature('way/1','paths','pedestrian',{type:'Polygon',coordinates:[ring,hole]}),
 feature('node/2','furniture','bench',{type:'Point',coordinates:p(20,10)}),
 feature('node/3','places','cafe',{type:'Point',coordinates:p(10,20)},{name:'Cafe'}),
 feature('node/4','places','cafe',{type:'Point',coordinates:p(2000,2000)},{name:'Far cafe'})]);
 const layers=[...c.osmContextLayers(),...c.osmPlaceLayers()],plaza=layers.find(l=>l.id==='osm-pedestrian-plazas');
 assert.equal(plaza.getPolygon(plaza.data[0]).length,2);
 assert.equal(layers.find(l=>l.id==='osm-place-names').data.length,1);
 assert.equal(layers.find(l=>l.id==='osm-place-names').parameters.depthCompare,'always','places inside buildings remain readable');
 assert.equal(layers.find(l=>l.id==='osm-furniture-bench').data.length,1);
 c.map.getZoom=()=>15;assert.equal(c.osmContextLayers().some(l=>l.id==='osm-furniture-bench'),false);
 c.ui.satellite=true;assert.equal(c.osmContextLayers().length,0);
});
test('furniture meshes contain finite vertices and reasonable generic heights',()=>{
 const c=scene([]);
 vm.runInContext(html.slice(html.indexOf('function meshOf('),html.indexOf('// A square-section taper')),c);
 for(const kind of ['street_lamp','bench','bicycle_parking','gate','shelter','fountain','waste_basket','bollard']) {
  const shape=c.osmFurnitureMesh(kind),positions=Array.from(shape.positions.value);
  assert.ok(positions.length>0&&positions.every(Number.isFinite));
  assert.ok(Math.max(...positions.filter((_,i)=>i%3===2))<6);
 }
});
test('place labels thin overlapping names without hiding every anchor',()=>{
 const c=scene([]),a=feature('node/1','places','cafe',{type:'Point',coordinates:p(0,0)},{name:'First Cafe'}),
 b=feature('node/2','places','cafe',{type:'Point',coordinates:p(1,0)},{name:'Overlapping Cafe'}),
 d=feature('node/3','places','cafe',{type:'Point',coordinates:p(200,0)},{name:'Distant Cafe'});
 for(const f of [a,b,d])f.contextAnchor=f.geometry.coordinates;
 const labels=c.osmLabelSelection([a,b,d],q=>({x:(q[0]-centre[0])*sx,y:0}));
 assert.equal(labels.length,2);assert.equal(labels[0],a);assert.equal(labels[1],d);
});
test('zooming out hides business labels and markers immediately, then close zoom restores them',()=>{
 const c=scene([feature('node/1','places','cafe',{type:'Point',coordinates:p(0,0)},{name:'Cafe'})]);
 assert.equal(c.osmPlaceLayers().length,2);
 c.map.getZoom=()=>16;assert.equal(c.osmPlaceLayers().length,0);
 c.map.getZoom=()=>18.4;assert.equal(c.osmPlaceLayers().length,0);
 c.map.getZoom=()=>19;assert.equal(c.osmPlaceLayers().length,2);
 const label=c.osmPlaceLayers().find(l=>l.id==='osm-place-names');assert.equal(label.sizeUnits,'pixels');assert.equal(label.sizeMaxPixels,12);
});
