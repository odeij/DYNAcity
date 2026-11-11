/* Public mapped context. Locations/lines are OSM; furniture forms and missing
 * dimensions are illustrative. These layers never supply simulation inputs. */
let osmContextCache=null,osmVisibleCache=null,osmModelIndex=null,osmModelSource=null;
function osmContextEntries() {
  const source=DATA.landscape?.features;
  if(osmContextCache?.source===source)return osmContextCache.entries;
  const entries=(source??[]).filter(f=>f.properties.context_group).flatMap(f=>
    f.geometry.type==='MultiPolygon'?f.geometry.coordinates.map(coordinates=>({...f,geometry:{type:'Polygon',coordinates}})):[f]);
  const roadTags=new Map(entries.filter(f=>f.properties.context_group==='roads').map(f=>[f.properties.id,f.properties.tags]));
  for(const r of DATA.traffic?.roads??[]) {
    const tags=roadTags.get(`way/${r.source_way}`);if(!tags)continue;
    r.osmTags=tags;r.sidewalk=tags.sidewalk;
  }
  for(const f of entries) {
    const g=f.geometry,points=g.type==='Point'?[g.coordinates]:g.type==='Polygon'?g.coordinates[0]:g.coordinates;
    f.contextBox=ringBox(points);f.contextAnchor=g.type==='Point'?g.coordinates:centroid(points);
  }
  osmContextCache={source,entries};osmVisibleCache=null;
  return entries;
}
function osmCoveredByModel(ring) {
  if(!beirutOverlay.active)return coveredByBbed(ring);
  if(osmModelSource!==beirutOverlay.polygons) {
    osmModelSource=beirutOverlay.polygons;osmModelIndex=new Map();
    for(const p of beirutOverlay.polygons??[]) {
      const r=p.rings[0],entry={ring:r,c:centroid(r)};
      for(const key of cellKeys(ringBox(r))) {if(!osmModelIndex.has(key))osmModelIndex.set(key,[]);osmModelIndex.get(key).push(entry);}
    }
  }
  const c=centroid(ring),probes=[c,...ring.map(p=>[p[0]+(c[0]-p[0])/3,p[1]+(c[1]-p[1])/3])];
  const candidates=new Set(cellKeys(ringBox(ring)).flatMap(k=>osmModelIndex.get(k)??[]));
  return [...candidates].some(f=>inRing(f.c,ring)||probes.some(p=>inRing(p,f.ring)));
}
// Every drawn context building, built once per source and building mode: one
// stable array, so the extruded layer is tessellated once rather than per pan.
let osmBuildingCache=null;
function osmBuildingSet() {
  const entries=osmContextEntries(),key=`${beirutOverlay.active}`;
  if(osmBuildingCache?.entries===entries&&osmBuildingCache.key===key&&osmBuildingCache.model===beirutOverlay.polygons)return osmBuildingCache.data;
  const data=entries.filter(f=>f.properties.kind==='osm_building'&&f.geometry.type==='Polygon'&&!osmCoveredByModel(f.geometry.coordinates[0]));
  osmBuildingCache={entries,key,model:beirutOverlay.polygons,data};
  return data;
}
function osmVisibleEntries(centre,zoom,bounds) {
  const entries=osmContextEntries(),pad=.001;
  const box=bounds?[bounds[0][0]-pad,bounds[0][1]-pad,bounds[1][0]+pad,bounds[1][1]+pad]:
    [centre[0]-.008,centre[1]-.006,centre[0]+.008,centre[1]+.006];
  const key=box.map(v=>v.toFixed(4)).join(':')+':'+Math.floor(zoom*2)+':'+beirutOverlay.active;
  if(osmVisibleCache?.key===key&&osmVisibleCache.model===beirutOverlay.polygons)return osmVisibleCache;
  const visible=entries.filter(f=>{const b=f.contextBox;return b[0]<=box[2]&&b[2]>=box[0]&&b[1]<=box[3]&&b[3]>=box[1];});
  const grouped={};for(const f of visible)(grouped[f.properties.context_group]??=[]).push(f);
  const furniture=[...(grouped.furniture??[]),...(grouped.boundaries??[]).filter(f=>f.geometry.type==='Point')];
  const segments=[],treads=[];
  for(const f of grouped.boundaries??[]) {
    if(f.geometry.type!=='LineString')continue;
    if(!['wall','retaining_wall','fence','hedge','guard_rail','city_wall'].includes(f.properties.kind))continue;
    const path=f.geometry.coordinates;
    for(let i=1;i<path.length;i++) {
      const a=path[i-1],b=path[i],sx=111320*Math.cos(a[1]*Math.PI/180),dx=(b[0]-a[0])*sx,dy=(b[1]-a[1])*111320,length=Math.hypot(dx,dy);
      if(length<.05)continue;
      const kind=f.properties.kind,height=f.properties.height_m??(kind==='hedge'?1.3:kind==='guard_rail'?.7:1.6);
      segments.push({feature:f,position:onGround([(a[0]+b[0])/2,(a[1]+b[1])/2],.06),scale:[length,kind==='hedge'?.55:.18,height],orientation:[0,Math.atan2(dy,dx)*180/Math.PI,0]});
    }
  }
  for(const f of grouped.paths??[])if(f.properties.kind==='steps'&&f.geometry.type==='LineString') {
    const path=f.geometry.coordinates,width=f.properties.width_m??1.8;
    const total=path.slice(1).reduce((s,p,i)=>s+metresBetween(path[i],p),0);
    const declared=Number(f.properties.tags?.step_count),spacing=declared>0&&declared<2000?total/declared:.38;
    if(spacing<.08)continue;
    let along=0,next=spacing/2;
    for(let i=1;i<path.length;i++) {
      const a=path[i-1],b=path[i],sx=111320*Math.cos(a[1]*Math.PI/180),dx=(b[0]-a[0])*sx,dy=(b[1]-a[1])*111320,length=Math.hypot(dx,dy);
      if(length<.05)continue;
      for(;next<along+length;next+=spacing) {
        const u=(next-along)/length,p=[a[0]+u*(b[0]-a[0]),a[1]+u*(b[1]-a[1])];
        treads.push({feature:f,path:[[p[0]-dy/length*width/2/sx,p[1]+dx/length*width/2/111320],[p[0]+dy/length*width/2/sx,p[1]-dx/length*width/2/111320]].map(q=>onGround(q,.11))});
      }
      along+=length;
    }
  }
  const places=[...(grouped.places??[]),...(grouped.buildings??[]).filter(f=>f.properties.tags?.amenity||f.properties.tags?.shop||f.properties.tags?.tourism)]
    .filter(f=>!['traffic_signals','crossing','stop_position'].includes(f.properties.kind));
  const labels=places.filter(f=>f.properties.tags?.name||f.properties.tags?.['name:en']||f.properties.tags?.brand)
    .sort((a,b)=>metresBetween(a.contextAnchor,centre)-metresBetween(b.contextAnchor,centre)).slice(0,45);
  return osmVisibleCache={key,model:beirutOverlay.polygons,grouped,furniture,segments,treads,places,labels};
}
function osmFurnitureMesh(kind) {
  const v=[],metal=[.24,.28,.29],wood=[.48,.35,.22];
  if(kind==='street_lamp') {
    box(v,[-.07,-.07,0],[.07,.07,5.6],metal);box(v,[-.04,-.05,5.5],[1.3,.05,5.62],metal);
    box(v,[.85,-.18,5.45],[1.5,.18,5.57],[.83,.81,.68]);
  } else if(kind==='bench') {
    box(v,[-.85,-.25,.42],[.85,.25,.52],wood);box(v,[-.85,.2,.52],[.85,.28,.95],wood);
    for(const x of [-.65,.65])box(v,[x-.05,-.2,0],[x+.05,.2,.44],metal);
  } else if(kind==='bicycle_parking') {
    for(const x of [-.65,0,.65]){box(v,[x-.035,-.35,0],[x+.035,-.28,.8],metal);box(v,[x-.035,.28,0],[x+.035,.35,.8],metal);box(v,[x-.035,-.35,.76],[x+.035,.35,.83],metal);}
  } else if(kind==='gate'||kind==='lift_gate') {
    box(v,[-.12,-.12,0],[.12,.12,1],metal);box(v,[0,-.05,.88],[2,.05,1],[.72,.69,.59]);
  } else if(kind==='shelter') {
    for(const x of [-1.5,1.5])for(const y of [-.7,.7])box(v,[x-.04,y-.04,0],[x+.04,y+.04,2.35],metal);
    box(v,[-1.65,-.85,2.35],[1.65,.85,2.48],[.59,.62,.61]);
  } else if(kind==='fountain') {
    box(v,[-1,-1,0],[1,1,.25],[.62,.63,.57]);box(v,[-.85,-.85,.25],[.85,.85,.28],[.32,.53,.61]);
  } else {
    const bin=kind==='waste_basket',water=kind==='drinking_water';
    box(v,bin?[-.22,-.22,0]:[-.09,-.09,0],bin?[.22,.22,.8]:[.09,.09,water?1:.85],bin?[.28,.36,.3]:metal);
  }
  return meshOf(v);
}
function osmFenceMesh() {
  const v=[],colour=[1,1,1];
  box(v,[-.5,-.5,.12],[.5,.5,.17],colour);box(v,[-.5,-.5,.84],[.5,.5,.89],colour);
  for(const x of [-.49,-.25,0,.25,.49])box(v,[x-.008,-.5,0],[x+.008,.5,1],colour);
  return meshOf(v);
}
function osmContextSummary() {
  const data=DATA.landscape;if(!data?.counts)return '';
  const names={trees:'Tree display instances',surfaces:'Mapped surface areas',paths:'Paths and stairs',boundaries:'Boundaries',furniture:'Street furniture',infrastructure:'Infrastructure records',places:'Places and transport stops',water:'Waterways',transport:'Rail alignments',roads:'Road tag records',buildings:'OSM building outlines and parts'};
  const rejected=Object.values(data.geometry_rejected??{}).reduce((a,b)=>a+b,0);
  return `<h3>Mapped city context</h3><div class="note">OpenStreetMap · retrieved ${esc(data.retrieved_on??'date unavailable')} · current Beirut map extent. Mapping coverage and dates vary.</div>
    <dl class="kv">${Object.entries(data.counts).map(([k,n])=>`<dt>${esc(names[k]??k)}</dt><dd>${n.toLocaleString()}</dd>`).join('')}</dl>
    <div class="note">${data.tree_nodes??0} individual tree nodes; ${data.tree_rows??0} tree rows with illustrative spacing. OSM buildings fill gaps outside the active source model.${data.context_buildings?` ${data.context_buildings.added_footprints.toLocaleString()} Microsoft satellite footprints added via Overture; OSM buildings without a mapped height use the median Beirut 001 height for a similar footprint area (an estimate). ${esc(data.context_buildings.attribution)}`:''} Labels thin out at wider zoom; hover objects for source tags.${rejected?` ${rejected} objects lack usable display geometry and remain in the source extract.`:''}</div>`;
}
function osmLabelSelection(candidates,project) {
  const boxes=[],out=[];
  for(const f of candidates) {
    const p=project(f.contextAnchor),width=Math.min(220,Math.max(40,f.properties.name.length*6.5+8));
    const box=[p.x-width/2,p.y-23,p.x+width/2,p.y-5];
    if(boxes.some(b=>b[0]<box[2]&&b[2]>box[0]&&b[1]<box[3]&&b[3]>box[1]))continue;
    boxes.push(box);out.push(f);
  }
  return out;
}
function osmGapBuildingLayers() {
  const out=[],buildings=osmBuildingSet();
  if(buildings.length) {
    const occlusion=cameraOcclusion();
    out.push(new deck.SolidPolygonLayer({id:'osm-gap-buildings',data:buildings,getPolygon:f=>(f.g??=groundedPolygon(f.geometry.coordinates)),
      extruded:true,getElevation:f=>f.properties.height_m??6,getFillColor:f=>cameraSafeColour([192,196,190],'osm',f.properties.id,occlusion),
      // Recolour only while something blocks the camera; per-pan recolouring of the whole set stalls panning.
      material:MATERIAL,pickable:true,...nightWindowProps(f=>nightInfo('unknown',`o${f.properties.id}`),[HAZE_EXT]),updateTriggers:{getFillColor:occlusion.osm.size?sceneCameraKey():''}}));
  }
  return out;
}
function osmContextLayers() {
  if(!DATA.landscape||ui.satellite)return [];
  const vp=!map&&deckgl?.getViewports()[0],zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  // Drawn at every zoom: one stable array, so zooming out does not drop the suburbs.
  const gap=osmGapBuildingLayers();
  if(!centre||zoom<14)return gap;
  const d=osmVisibleEntries(centre,zoom,map?.getBounds?.().toArray()),night=nightLevel(),out=[];
  const paths=(d.grouped.paths??[]).filter(f=>f.geometry.type==='LineString'&&f.properties.tags?.footway!=='crossing'&&f.properties.tags?.tunnel!=='yes'&&(!f.properties.tags?.bridge||f.properties.tags.bridge==='no'));
  const paved=f=>['asphalt','concrete','paved','paving_stones'].includes(f.properties.surface);
  if(paths.length)out.push(new deck.PathLayer({id:'osm-pedestrian-paths',data:paths,getPath:f=>groundPath(f.geometry.coordinates,.085),
    getWidth:f=>Math.min(12,f.properties.width_m??(f.properties.kind==='pedestrian'?4:2)),widthUnits:'meters',widthMinPixels:.7,
    getColor:f=>shade(f.properties.kind==='cycleway'?[144,162,151,230]:paved(f)?[202,194,176,245]:[190,178,150,235],night,.5),
    capRounded:true,jointRounded:true,pickable:true,updateTriggers:{getColor:night}}));
  const plazas=(d.grouped.paths??[]).filter(f=>f.geometry.type==='Polygon');
  if(plazas.length)out.push(new deck.SolidPolygonLayer({id:'osm-pedestrian-plazas',data:plazas,getPolygon:f=>f.geometry.coordinates.map(r=>r.map(p=>onGround(p,.07))),
    getFillColor:shade([202,194,176,225],night,.5),material:false,pickable:true,updateTriggers:{getFillColor:night}}));
  if(zoom>=17&&d.treads.length)out.push(new deck.PathLayer({id:'osm-stair-treads',data:d.treads,getPath:d=>d.path,getColor:shade([119,115,105,220],night,.5),
    getWidth:.08,widthUnits:'meters',widthMinPixels:.5,pickable:false,updateTriggers:{getColor:night}}));
  const rails=(d.grouped.transport??[]).filter(f=>f.geometry.type==='LineString'&&!['abandoned','razed','subway'].includes(f.properties.tags?.railway));
  if(rails.length)out.push(new deck.PathLayer({id:'osm-rail-alignments',data:rails,getPath:f=>groundPath(f.geometry.coordinates,.06),getWidth:1.2,widthUnits:'meters',
    getColor:shade([118,111,98,210],night,.5),pickable:true,updateTriggers:{getColor:night}}));
  const water=(d.grouped.water??[]).filter(f=>f.geometry.type==='LineString'&&f.properties.tags?.tunnel!=='yes');
  if(water.length)out.push(new deck.PathLayer({id:'osm-waterways',data:water,getPath:f=>groundPath(f.geometry.coordinates,.075),
    getWidth:f=>f.properties.width_m??(f.properties.tags?.waterway==='river'?8:2),widthUnits:'meters',getColor:shade([109,156,165,210],night,.5),pickable:true,updateTriggers:{getColor:night}}));
  out.push(...gap);
  if(zoom>=17.5&&d.segments.length)for(const kind of new Set(d.segments.map(s=>s.feature.properties.kind))) {
    const items=d.segments.filter(s=>s.feature.properties.kind===kind);
    if(items.length)out.push(new deck.SimpleMeshLayer({id:`osm-boundary-${kind}`,data:items,
      mesh:kind==='fence'||kind==='guard_rail'?mesh('osm-boundary-fence',osmFenceMesh):mesh('osm-boundary-box',blockMesh),
      getPosition:d=>d.position,getScale:d=>d.scale,getOrientation:d=>d.orientation,
      getColor:kind==='hedge'?[101,130,81]:[162,158,144],material:MATERIAL,pickable:true}));
  }
  if(zoom>=18)for(const kind of new Set(d.furniture.map(f=>f.properties.kind))) {
    const items=d.furniture.filter(f=>f.properties.kind===kind);
    out.push(new deck.SimpleMeshLayer({id:`osm-furniture-${kind}`,data:items,mesh:mesh(`osm-furniture-${kind}`,()=>osmFurnitureMesh(kind)),
      getPosition:f=>onGround(f.contextAnchor,.06),getOrientation:f=>[0,Number(f.properties.tags?.direction)||0,0],
      getScale:f=>kind==='street_lamp'&&f.properties.height_m?[1,1,f.properties.height_m/5.62]:[1,1,1],
      getColor:[255,255,255],material:MATERIAL,pickable:true}));
  }
  // Source references, not physical models: do not invent fixture dimensions.
  const infrastructure=d.grouped.infrastructure??[];
  const infrastructureLines=infrastructure.filter(f=>f.geometry.type==='LineString');
  if(zoom>=17&&infrastructureLines.length)out.push(new deck.PathLayer({id:'osm-infrastructure-lines',data:infrastructureLines,
    getPath:f=>groundPath(f.geometry.coordinates,.12),getWidth:1,widthUnits:'pixels',widthMinPixels:1,
    getColor:[155,112,69,220],pickable:true}));
  // Show mapped area boundaries, including holes, as references rather than
  // implying a measured 3D structure or filling the entire mapped area.
  const infrastructureAreas=infrastructure.filter(f=>f.geometry.type==='Polygon')
    .flatMap(f=>f.geometry.coordinates.map(path=>({feature:f,path})));
  if(zoom>=17&&infrastructureAreas.length)out.push(new deck.PathLayer({id:'osm-infrastructure-areas',data:infrastructureAreas,
    getPath:d=>groundPath(d.path,.12),getWidth:1,widthUnits:'pixels',widthMinPixels:1,
    getColor:[155,112,69,220],pickable:true}));
  const infrastructurePoints=infrastructure.filter(f=>f.geometry.type==='Point');
  if(zoom>=18&&infrastructurePoints.length)out.push(new deck.ScatterplotLayer({id:'osm-infrastructure-points',data:infrastructurePoints,
    getPosition:f=>onGround(f.geometry.coordinates,.2),getRadius:2,radiusUnits:'pixels',radiusMinPixels:2,radiusMaxPixels:3,
    getFillColor:[155,112,69,230],billboard:true,pickable:true}));
  return out;
}
// Names and place markers are map annotations: draw after buildings so a shop
// mapped inside a footprint is still discoverable, rather than buried by roofs.
function osmPlaceLayers() {
  if(!DATA.landscape||ui.satellite||(ui.lens&&ui.lens!=='observed'))return [];
  const vp=!map&&deckgl?.getViewports()[0],zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  if(!centre||zoom<18.5||ui.roadTool)return [];
  const d=osmVisibleEntries(centre,zoom,map?.getBounds?.().toArray()),night=nightLevel(),out=[];
  const labelKey=`${centre.map(v=>v.toFixed(6))}:${zoom.toFixed(3)}:${map?.getBearing?.()??0}:${map?.getPitch?.()??0}`;
  if(d.labelKey!==labelKey) {
    const mpp=78271.51696*Math.cos(centre[1]*Math.PI/180)/2**zoom;
    const project=p=>map?.project?map.project(p):{x:(p[0]-centre[0])*111320*Math.cos(centre[1]*Math.PI/180)/mpp,y:-(p[1]-centre[1])*111320/mpp};
    d.visibleLabels=osmLabelSelection(d.labels,project);d.labelKey=labelKey;
  }
  if(d.visibleLabels.length)out.push(new deck.ScatterplotLayer({id:'osm-mapped-places',data:d.visibleLabels,getPosition:f=>onGround(f.contextAnchor,.15),
    getRadius:1.2,radiusMinPixels:2,radiusMaxPixels:3,getFillColor:[51,102,111,225],billboard:true,stroked:false,pickable:true,parameters:ON_TOP}));
  if(d.visibleLabels.length)out.push(new deck.TextLayer({id:'osm-place-names',data:d.visibleLabels,getPosition:f=>onGround(f.contextAnchor,1.2),
    getText:f=>f.properties.name,getSize:12,sizeUnits:'pixels',sizeMinPixels:11,sizeMaxPixels:12,fontFamily:'Arial, sans-serif',fontWeight:500,characterSet:'auto',getColor:shade([62,79,77,235],night,.3),
    getPixelOffset:[0,-13],billboard:true,background:true,getBackgroundColor:[247,245,235,205],backgroundPadding:[3,1],pickable:true,
    parameters:ON_TOP,
    updateTriggers:{getColor:night}}));
  return out;
}
