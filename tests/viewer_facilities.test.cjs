const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_facilities.js`,'utf8');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const centre=[35.5,33.9],sy=111320,sx=sy*Math.cos(centre[1]*Math.PI/180);
const p=(x,y)=>[centre[0]+x/sx,centre[1]+y/sy];
const square=(x,y,r)=>[p(x-r,y-r),p(x+r,y-r),p(x+r,y+r),p(x-r,y+r),p(x-r,y-r)];
function facility(id,kind,at,props={}) {
 return {type:'Feature',geometry:{type:'Point',coordinates:at},properties:{id,facility:kind,name:props.name??kind,named:!!props.name,source:'OpenStreetMap',...props}};
}
function scene(features,{zoom=19,lens='key',buildings=[]}={}) {
 class Layer {constructor(props){Object.assign(this,props);}}
 const c={DATA:{buildings,facilities:{features,sources:[{name:'OpenStreetMap contributors',licence:'ODbL',as_of:'2026-11-10T20:52:19Z'}],
   official_schools:{confirmed:1,added:2},limits:'Operation not verified.'}},ui:{satellite:false,lens},
 map:{getZoom:()=>zoom,getCenter:()=>({toArray:()=>centre}),project:q=>({x:(q[0]-centre[0])*sx,y:-(q[1]-centre[1])*sy})},
 deckgl:null,ON_TOP:{depthCompare:'always'},esc:s=>String(s),
 metresBetween:(a,b)=>Math.hypot((a[0]-b[0])*sx,(a[1]-b[1])*sy),
 ringBox:r=>[Math.min(...r.map(q=>q[0])),Math.min(...r.map(q=>q[1])),Math.max(...r.map(q=>q[0])),Math.max(...r.map(q=>q[1]))],
 inRing:([x,y],r)=>{let inside=false;for(let i=0,j=r.length-1;i<r.length;j=i++){const[a,b]=r[i],[c2,d]=r[j];if((b>y)!==(d>y)&&x<(c2-a)*(y-b)/(d-b)+a)inside=!inside;}return inside;},
 CELL:.001,cellKeys:([w,s,e,n])=>{const k=[];for(let i=Math.floor(w/.001);i<=Math.floor(e/.001);i++)for(let j=Math.floor(s/.001);j<=Math.floor(n/.001);j++)k.push(`${i}:${j}`);return k;},
 tileToLngLat:()=>q=>q,
 osmLabelSelection:(candidates)=>candidates,deck:{ScatterplotLayer:Layer,TextLayer:Layer}};
 vm.createContext(c);vm.runInContext(source,c);return c;
}
// Top-level consts and lets of the module are not context properties.
const ev=(c,expr)=>vm.runInContext(expr,c);
const ids=layer=>[...layer.data].map(f=>f.properties.id);
test('key services appear only in the Key buildings view, by kind as the camera zooms in',()=>{
 const features=[facility('node/1','hospital',p(0,0),{name:'Hôpital'}),facility('node/2','pharmacy',p(20,0)),
   facility('node/3','school',p(40,0),{name:'School'}),facility('node/4','police',p(60,0),{name:'Police'})];
 assert.equal(scene(features,{lens:'observed'}).facilityLayers().length,0,'the default view carries no service colours');
 const far=scene(features,{zoom:13}).facilityLayers();
 assert.deepEqual(ids(far.find(l=>l.id==='facility-markers')),['node/4','node/1'],'hospitals and police at city zoom');
 const near=scene(features).facilityLayers(),markers=near.find(l=>l.id==='facility-markers');
 assert.deepEqual(ids(markers),['node/2','node/3','node/4','node/1'],'smaller kinds first, so a hospital is never covered');
 assert.equal(near.find(l=>l.id==='facility-glyphs').data.length,1);
 assert.deepEqual(ids(near.find(l=>l.id==='facility-names')),['node/1','node/4','node/3'],'unnamed places get no label');
 assert.deepEqual(Array.from(markers.getPosition(features[0])),[...features[0].geometry.coordinates,1]);
});
test('recorded buildings take the colour of the most prominent service inside, others turn grey',()=>{
 const buildings=[{id:'a',rings:[square(0,0,10)]},{id:'b',rings:[square(50,0,10)]}];
 const c=scene([facility('node/1','pharmacy',p(1,1)),facility('node/2','hospital',p(-2,0)),facility('node/3','school',p(200,0))],{buildings});
 assert.deepEqual(Array.from(c.keyBuildingColour(buildings[0])),Array.from(ev(c,'FACILITY_STYLE.hospital.colour')));
 assert.deepEqual(Array.from(c.keyBuildingColour(buildings[1])),Array.from(ev(c,'KEY_OTHER')));
 const version=ev(c,'keyBuildingsVersion');
 c.toggleFacility('hospital',false);
 assert.deepEqual(Array.from(c.keyBuildingColour(buildings[0])),Array.from(ev(c,'FACILITY_STYLE.pharmacy.colour')),'a hidden kind gives way to the next');
 assert.ok(ev(c,'keyBuildingsVersion')>version,'toggling recolours the buildings');
 assert.match(c.keyBuildingsLegend(),/\(1 of the recorded buildings here\)/);
 const basemap={type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[square(200,0,10)]}};
 assert.deepEqual(Array.from(c.keyContextColour(basemap,{bbox:{}})),Array.from(ev(c,'FACILITY_STYLE.school.colour')),'basemap buildings holding a service are coloured too');
 assert.equal(c.keyContextColour({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[square(500,0,10)]}},{bbox:{}}),null);
});
test('legend groups the services, toggles hide a kind, and reports sources and the official-list merge',()=>{
 const c=scene([facility('node/1','hospital',p(0,0)),facility('mehe/9','school',p(5,5),{source:'MEHE list'}),
   facility('node/5','townhall',p(9,9)),facility('node/6','social_facility',p(9,0))]);
 const legend=c.keyBuildingsLegend();
 assert.match(legend,/<h2>Key buildings<\/h2>/);
 for(const group of ['Health','Education','Government services','Welfare and community'])assert.match(legend,new RegExp(`legend-group">${group}<`));
 assert.doesNotMatch(legend,/Safety and emergency/,'empty groups are left out');
 assert.match(legend,/data-facility="hospital" checked/);assert.match(legend,/OpenStreetMap contributors \(2026-11-10\), ODbL/);
 assert.match(legend,/1 mapped schools match a ministry-listed school by place and name; the other 2 listed schools/);
 c.toggleFacility('hospital',false);
 assert.deepEqual(ids(c.facilityLayers().find(l=>l.id==='facility-markers')),['node/6','mehe/9','node/5']);
 assert.match(c.keyBuildingsLegend(),/data-facility="hospital">/);
 assert.equal(scene([]).facilityLayers().length,0);
});
test('tooltip names the source and service and never claims the place operates',()=>{
 const c=scene([]);
 const tip=c.facilityTip(facility('way/2','school',p(0,0),{name:'School B',tags:{operator:'MEHE','phone':'x'},
   official:{list:'MEHE list',code:'10',name:'Public School B'},as_of:'2026-11-10T20:52:19Z'}));
 assert.match(tip,/School · OpenStreetMap · way\/2/);assert.match(tip,/Operator: MEHE/);assert.doesNotMatch(tip,/phone/);
 assert.match(tip,/Also on the MEHE list as Public School B \(CERD 10\)/);assert.match(tip,/current operation is not verified/);
 assert.match(c.facilityTip(facility('node/7','government_office',p(0,0),{tags:{government:'register_office'}})),/Government service: register_office/);
});
test('template adds the Key buildings view without touching the default one',()=>{
 assert.match(html,/data-lens="key" aria-selected="false" hidden>Key buildings</);
 assert.match(html,/document\.querySelector\('\[data-lens="key"\]'\)\.hidden = !DATA\.facilities;/);
 assert.match(html,/else if \(ui\.lens === 'key'\) rgb = keyBuildingColour\(b\);/);
 assert.match(html,/if \(ui\.lens === 'key'\) \{\r?\n    body = keyBuildingsLegend\(\);/);
 assert.doesNotMatch(html,/facilityLegend\(\)/);
 assert.match(html,/\.\.\.osmPlaceLayers\(\), \.\.\.facilityLayers\(\)/);
 assert.match(html,/object\.properties\?\.facility\) \{\r?\n    tip\.innerHTML=facilityTip\(object\)/);
 assert.match(html,/object\?\.properties\?\.facility \|\| object\?\.bridgeStructure\) return;/);
 // Walls and roofs of the BBED and Beirut 001 buildings, and the basemap buildings.
 assert.equal((html.match(/getFillColor: ?\[ui\.lens, ?keyBuildingsVersion/g)??[]).length,5);
 assert.match(html,/keyContextColour\(f, props\.tile\)/);
});
