/* Key buildings view: the public services people go to, from build-facilities
 * (OSM plus the MEHE school list). Shown only in the "Key buildings" lens. Map
 * annotations only: they never supply simulation inputs, and whether a place
 * still operates is not verified. */
const FACILITY_GROUPS={health:'Health',education:'Education',safety:'Safety and emergency',
  government:'Government services',welfare:'Welfare and community',public:'Public buildings'};
// Each kind appears from its own zoom, so the city view shows the main services first.
// Pixel sizes are clamped: under a pitched camera unclamped labels swell near the viewer.
const FACILITY_STYLE={
  hospital:{group:'health',label:'Hospital',colour:[196,52,52],radius:8,zoom:12,labelZoom:14,glyph:'H'},
  clinic:{group:'health',label:'Clinic or doctor',colour:[222,112,88],radius:4.5,zoom:15,labelZoom:17.5},
  pharmacy:{group:'health',label:'Pharmacy',colour:[236,156,138],radius:4,zoom:16,labelZoom:18},
  university:{group:'education',label:'University or college',colour:[88,70,158],radius:6.5,zoom:13,labelZoom:15.5},
  school:{group:'education',label:'School',colour:[47,98,173],radius:5.5,zoom:14,labelZoom:16.5},
  kindergarten:{group:'education',label:'Kindergarten',colour:[104,140,206],radius:4.5,zoom:15,labelZoom:17.5},
  police:{group:'safety',label:'Police station',colour:[22,44,86],radius:6.5,zoom:13,labelZoom:15.5},
  fire_station:{group:'safety',label:'Fire or civil defence station',colour:[214,98,24],radius:6.5,zoom:13,labelZoom:15.5},
  ambulance:{group:'safety',label:'Ambulance station',colour:[228,160,30],radius:5.5,zoom:14,labelZoom:16.5},
  townhall:{group:'government',label:'Municipality or town hall',colour:[118,84,38],radius:6.5,zoom:13,labelZoom:15.5},
  courthouse:{group:'government',label:'Courthouse',colour:[146,112,62],radius:5.5,zoom:14,labelZoom:16.5},
  post_office:{group:'government',label:'Post office',colour:[176,140,64],radius:4.5,zoom:15,labelZoom:17},
  government_office:{group:'government',label:'Government office',colour:[160,128,96],radius:4.5,zoom:15,labelZoom:17},
  social_facility:{group:'welfare',label:'Social or welfare service',colour:[30,128,88],radius:5,zoom:14,labelZoom:16.5},
  childcare:{group:'welfare',label:'Childcare',colour:[78,164,118],radius:4,zoom:16,labelZoom:17.5},
  community_centre:{group:'welfare',label:'Community centre',colour:[118,184,140],radius:4.5,zoom:15,labelZoom:17},
  library:{group:'public',label:'Library',colour:[84,106,118],radius:4.5,zoom:15,labelZoom:17},
  public_building:{group:'public',label:'Other public building',colour:[128,140,146],radius:4,zoom:16,labelZoom:17.5},
};
// Recorded buildings without a key service in this view.
const KEY_OTHER=[218,218,212];
const facilityHidden=new Set();
// Bumped when a kind is toggled, so building colours are recomputed.
let keyBuildingsVersion=0;
let facilityCache=null,facilityLabelCache=null,keyBuildingCache=null;
function facilityEntries() {
  const source=DATA.facilities?.features;
  if(facilityCache?.source===source)return facilityCache.entries;
  const entries=(source??[]).filter(f=>FACILITY_STYLE[f.properties.facility]);
  for(const f of entries)f.contextAnchor=f.geometry.coordinates;
  facilityCache={source,entries};
  return entries;
}
const facilityStyle=f=>FACILITY_STYLE[f.properties.facility];
// Services by map cell (`cellKeys`), so footprints test only the points near them.
let facilityIndexCache=null;
function facilityIndex() {
  const entries=facilityEntries();
  if(facilityIndexCache?.entries===entries)return facilityIndexCache.index;
  const index=new Map();
  for(const f of entries)for(const key of cellKeys([...f.geometry.coordinates,...f.geometry.coordinates])) {
    if(!index.has(key))index.set(key,[]);
    index.get(key).push(f);
  }
  facilityIndexCache={entries,index};
  return index;
}
// Kinds of the services whose point lies in the footprint, most prominent first.
function facilitiesInRing(ring) {
  const box=ringBox(ring),index=facilityIndex(),kinds=[];
  for(const key of cellKeys(box))for(const f of index.get(key)??[]) {
    const [x,y]=f.geometry.coordinates;
    if(x>=box[0]&&x<=box[2]&&y>=box[1]&&y<=box[3]&&inRing([x,y],ring))kinds.push(f.properties.facility);
  }
  return kinds.sort((a,b)=>FACILITY_STYLE[b].radius-FACILITY_STYLE[a].radius);
}
// Recorded building id -> kinds of the services inside it.
function keyBuildingKinds() {
  const entries=facilityEntries();
  if(keyBuildingCache?.entries===entries)return keyBuildingCache.kinds;
  const kinds=new Map();
  if(entries.length)for(const b of DATA.buildings) {
    const inside=b.rings.flatMap(facilitiesInRing);
    if(inside.length)kinds.set(b.id,inside.sort((x,y)=>FACILITY_STYLE[y].radius-FACILITY_STYLE[x].radius));
  }
  keyBuildingCache={entries,kinds};
  return kinds;
}
const shownKind=kinds=>kinds.find(k=>!facilityHidden.has(k));
function keyBuildingColour(b) {
  const kind=shownKind(keyBuildingKinds().get(b.id)??[]);
  return kind?FACILITY_STYLE[kind].colour:KEY_OTHER;
}
// Basemap buildings (vector tiles) that hold a service, for the same view; null for the rest.
const keyContextCache=new WeakMap();
function keyContextColour(feature,tile) {
  let kinds=keyContextCache.get(feature);
  if(!kinds) {
    const g=feature.geometry,polys=g.type==='Polygon'?[g.coordinates]:g.type==='MultiPolygon'?g.coordinates:[];
    const toLngLat=tileToLngLat(tile.bbox);
    kinds=polys.flatMap(poly=>facilitiesInRing(poly[0].map(toLngLat)));
    keyContextCache.set(feature,kinds);
  }
  const kind=shownKind(kinds);
  return kind?FACILITY_STYLE[kind].colour:null;
}
function facilityLayers() {
  if(!DATA.facilities||ui.satellite||ui.lens!=='key')return [];
  const vp=!map&&deckgl?.getViewports()[0],zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  if(!centre)return [];
  // Smaller kinds first, so a pharmacy dot never covers a hospital.
  const shown=facilityEntries().filter(f=>!facilityHidden.has(f.properties.facility)&&zoom>=facilityStyle(f).zoom)
    .sort((a,b)=>facilityStyle(a).radius-facilityStyle(b).radius);
  if(!shown.length)return [];
  const out=[new deck.ScatterplotLayer({id:'facility-markers',data:shown,getPosition:f=>[...f.geometry.coordinates,1],
    getRadius:f=>facilityStyle(f).radius,radiusUnits:'pixels',radiusMaxPixels:9,getFillColor:f=>[...facilityStyle(f).colour,235],
    stroked:true,getLineColor:[255,255,255,240],lineWidthUnits:'pixels',getLineWidth:1.5,billboard:true,pickable:true,parameters:ON_TOP})];
  const glyphs=shown.filter(f=>facilityStyle(f).glyph);
  if(glyphs.length)out.push(new deck.TextLayer({id:'facility-glyphs',data:glyphs,getPosition:f=>[...f.geometry.coordinates,1],
    getText:f=>facilityStyle(f).glyph,getSize:10,sizeUnits:'pixels',sizeMinPixels:9,sizeMaxPixels:10,fontFamily:'Arial, sans-serif',fontWeight:700,
    getColor:[255,255,255,255],billboard:true,pickable:false,parameters:ON_TOP}));
  const key=`${centre.map(v=>v.toFixed(6))}:${zoom.toFixed(3)}:${map?.getBearing?.()??0}:${map?.getPitch?.()??0}:${[...facilityHidden]}`;
  if(facilityLabelCache?.key!==key||facilityLabelCache.source!==shown.length) {
    const named=shown.filter(f=>f.properties.named&&zoom>=facilityStyle(f).labelZoom)
      .sort((a,b)=>facilityStyle(b).radius-facilityStyle(a).radius||metresBetween(a.contextAnchor,centre)-metresBetween(b.contextAnchor,centre)).slice(0,40);
    const mpp=78271.51696*Math.cos(centre[1]*Math.PI/180)/2**zoom;
    const project=p=>map?.project?map.project(p):{x:(p[0]-centre[0])*111320*Math.cos(centre[1]*Math.PI/180)/mpp,y:-(p[1]-centre[1])*111320/mpp};
    facilityLabelCache={key,source:shown.length,labels:osmLabelSelection(named,project)};
  }
  const labels=facilityLabelCache.labels;
  if(labels.length)out.push(new deck.TextLayer({id:'facility-names',data:labels,getPosition:f=>[...f.geometry.coordinates,1],
    getText:f=>f.properties.name,getSize:12,sizeUnits:'pixels',sizeMinPixels:11,sizeMaxPixels:12,fontFamily:'Arial, sans-serif',fontWeight:600,characterSet:'auto',
    getColor:f=>[...facilityStyle(f).colour.map(v=>Math.round(v*.7)),255],getPixelOffset:[0,-16],billboard:true,
    background:true,getBackgroundColor:[255,255,255,220],backgroundPadding:[3,1],pickable:true,parameters:ON_TOP}));
  return out;
}
function facilityTip(f) {
  const p=f.properties,tags=p.tags??{};
  const rows=[['operator','Operator'],['operator:type','Operator type'],['government','Government service'],
    ['social_facility','Service'],['social_facility:for','Serves'],['emergency','Emergency'],['beds','Beds'],
    ['healthcare:speciality','Speciality'],['isced:level','ISCED level'],['school:gender','Gender'],
    ['wheelchair','Wheelchair access'],['opening_hours','Tagged opening hours']];
  const official=p.official&&p.source!==p.official.list;
  return `<div class="h">${esc(p.name)}</div>${p.name_ar?`<div class="s" dir="auto">${esc(p.name_ar)}</div>`:''}
    <div class="s">${esc(FACILITY_STYLE[p.facility].label)} · ${esc(p.source)} · ${esc(p.id)}</div>
    ${rows.filter(([k])=>tags[k]!=null).map(([k,l])=>`<div class="s">${l}: ${esc(tags[k])}</div>`).join('')}
    ${official?`<div class="s">Also on the ${esc(p.official.list)} as ${esc(p.official.name)} (CERD ${esc(p.official.code)})</div>`:''}
    <div class="s">${p.as_of?`OSM as of ${esc(String(p.as_of).slice(0,10))}. `:''}Mapped or listed location; current operation is not verified.</div>`;
}
function keyBuildingsLegend() {
  if(!DATA.facilities)return '';
  const counts={};
  for(const f of facilityEntries())counts[f.properties.facility]=(counts[f.properties.facility]??0)+1;
  const row=([k,s])=>`<label class="legend-row toggle"><input type="checkbox" data-facility="${k}"${facilityHidden.has(k)?'':' checked'}><span class="sw dot" style="background:rgb(${s.colour.join(',')})"></span><span>${esc(s.label)}</span><span class="n">${counts[k].toLocaleString()}</span></label>`;
  const groups=Object.entries(FACILITY_GROUPS).map(([g,title])=>{
    const rows=Object.entries(FACILITY_STYLE).filter(([k,s])=>s.group===g&&counts[k]).map(row).join('');
    return rows?`<div class="legend-group">${esc(title)}</div>${rows}`:'';
  }).join('');
  const housed=keyBuildingKinds().size,m=DATA.facilities.official_schools,sources=DATA.facilities.sources??[];
  return `<h2>Key buildings</h2><div class="sub">Public services people go to; more appear as you zoom in</div>${groups||'<div class="note">None in this area.</div>'}
    <div class="note">Buildings whose outline holds one of these take its colour (${housed.toLocaleString()} of the recorded buildings here); other recorded buildings are grey. A point mapped beside its building leaves the building uncoloured, but its marker still shows.</div>
    <div class="note">${sources.map(s=>`${esc(s.name)}${s.as_of?` (${esc(String(s.as_of).slice(0,10))})`:''}, ${esc(s.licence)}`).join('; ')}.${m?` ${m.confirmed} mapped schools match a ministry-listed school by place and name; the other ${m.added} listed schools are shown at the listed point.`:''} ${esc(DATA.facilities.limits??'')}</div>`;
}
function toggleFacility(kind,on) {
  if(on)facilityHidden.delete(kind);else facilityHidden.add(kind);
  keyBuildingsVersion++;
}
