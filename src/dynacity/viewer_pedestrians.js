// Pavement playback shares Unreal's prepared UNA paths. This is not a crossing solver.
// Walkers move at a fixed illustrative 1.3 m/s; that is not a planning control.
const mobilityLab = {people: true, flows: false, demand: 1, speed: 1.3, incoming: 1,
  paused: false, blocked: new Set(), time: 0, last: null};
let pedestrianPaths = null;
function preparePedestrianPaths() {
  if(pedestrianPaths)return pedestrianPaths;
  pedestrianPaths=(DATA.pedestrians?.segments??[]).map((s,id)=>{
    const distances=[0];
    for(let i=1;i<s.path.length;i++)distances.push(distances.at(-1)+metresBetween(s.path[i-1],s.path[i]));
    return {...s,id,distances,length:distances.at(-1),phase:0};
  }).filter(s=>s.length>0);
  return pedestrianPaths;
}
function pedestrianPosition(s,distance,side) {
  let i=1;while(i<s.distances.length-1&&s.distances[i]<distance)i++;
  const a=s.path[i-1],b=s.path[i],f=Math.max(0,Math.min(1,(distance-s.distances[i-1])/(s.distances[i]-s.distances[i-1]||1)));
  const lat=a[1]+(b[1]-a[1])*f,sx=111320*Math.cos(lat*Math.PI/180),dx=(b[0]-a[0])*sx,dy=(b[1]-a[1])*111320,n=Math.hypot(dx,dy)||1;
  const lon=a[0]+(b[0]-a[0])*f,offset=side*Math.max(.8,s.offset_m-walkingShift(s,side));
  // The browser's ground-road mesh is flat; source absolute heights must not float above it.
  const z=roadHeightAt(s.road_index,lon,lat)??0;
  return {position:[lon-dy/n*offset/sx,lat+dx/n*offset/111320,z+.15],yaw:Math.atan2(dy,dx)*180/Math.PI};
}
function pedestrianRows(bounds) {
  const rows=[];
  for(const s of preparePedestrianPaths()) {
    if(mobilityLab.blocked.has(s.id))continue;
    const a=s.path[0],b=s.path.at(-1);
    if(bounds&&(Math.max(a[0],b[0])<bounds[0]||Math.min(a[0],b[0])>bounds[2]||Math.max(a[1],b[1])<bounds[1]||Math.min(a[1],b[1])>bounds[3]))continue;
    // Stable fractional rounding preserves sparse segments without random flicker.
    const count=Math.floor(s.people*mobilityLab.demand+((s.id*0.61803398875)%1));
    for(let j=0;j<count&&rows.length<2400;j++) {
      const direction=j%2?1:-1,side=(j%4<2)?1:-1;
      const u=((j+.5)/count+direction*s.phase/s.length)%1;
      const pose=pedestrianPosition(s,((u+1)%1)*s.length,side);
      rows.push({id:`${s.id}:${j}`,s,position:pose.position,yaw:pose.yaw+(direction<0?180:0),paint:(s.id+j)%4});
    }
  }
  return rows;
}
function pedestrianMesh() {
  const v=[],skin=[.65,.43,.3],shirt=[.9,.91,.89],trousers=[.22,.27,.3];
  box(v,[-.14,-.19,.76],[.14,.19,1.36],shirt);
  box(v,[-.12,-.12,1.4],[.12,.12,1.65],skin);
  for(const y of [-.12,.12])box(v,[-.08,y-.055,.04],[.08,y+.055,.76],trousers);
  for(const y of [-.25,.25])box(v,[-.07,y-.055,.85],[.07,y+.055,1.3],skin);
  return meshOf(v);
}
function pedestrianLayers(now) {
  if(!ui.traffic||!DATA.pedestrians)return [];
  const lab=mobilityLab,dt=lab.last===null?0:Math.max(0,Math.min(.25,(now-lab.last)/1000));lab.last=now;
  if(!lab.paused&&!calm.matches) {
    lab.time+=dt;
    for(const s of preparePedestrianPaths())if(!lab.blocked.has(s.id))s.phase+=dt*lab.speed;
  }
  const out=[],paths=preparePedestrianPaths();
  if(lab.flows)out.push(new deck.PathLayer({id:'pedestrian-flow',data:paths,getPath:s=>s.path.map(p=>[p[0],p[1],(roadHeightAt(s.road_index,p[0],p[1])??0)+.22]),
    getWidth:s=>2+Math.sqrt(Math.max(0,s.ped_per_hour_pm-(DATA.pedestrians.baseline_removed_per_hour??0))*lab.demand)/3,
    widthUnits:'meters',widthMinPixels:2,getColor:s=>lab.blocked.has(s.id)?[192,72,65,220]:[22,155,141,185],
    pickable:false,updateTriggers:{getWidth:lab.demand,getColor:[...lab.blocked].join(',')}}));
  const bounds=trafficView();
  if(lab.people) {
    const rows=pedestrianRows(bounds),palette=[[232,190,113],[76,151,163],[197,100,87],[140,155,116]];
    out.push(new deck.SimpleMeshLayer({id:'pedestrian-walkers',data:rows,mesh:mesh('pedestrian-person',pedestrianMesh),
      getPosition:d=>d.position,getOrientation:d=>[0,d.yaw,0],getColor:d=>palette[d.paint],sizeScale:1,
      material:MATERIAL,pickable:false,updateTriggers:{getPosition:lab.time,getOrientation:lab.time}}));
  }
  // Transparent picking paths only while the pavement tool is active.
  if(ui.roadTool==='pavement')out.push(new deck.PathLayer({id:'pedestrian-edit',data:paths,
    getPath:s=>s.path.map(p=>[p[0],p[1],.3]),getWidth:10,widthUnits:'pixels',
    getColor:s=>lab.blocked.has(s.id)?[210,77,62,230]:[22,155,141,150],pickable:true,
    onClick:info=>{if(info.object){togglePavement(info.object.id);return true;}},
    updateTriggers:{getColor:[...lab.blocked].join(',')}}));
  return out;
}
function togglePavement(id) {
  const blocked=mobilityLab.blocked;
  if(blocked.has(id))blocked.delete(id);else blocked.add(id);
  renderMobilityLab();render();
}
function mobilityLabMetrics() {
  const paths=preparePedestrianPaths(),baseline=paths.reduce((n,s)=>n+s.people,0),blocked=paths.filter(s=>mobilityLab.blocked.has(s.id));
  const unavailable=blocked.reduce((n,s)=>n+s.people,0)*mobilityLab.demand;
  return {baseline,available:baseline*mobilityLab.demand-unavailable,unavailable,blocked:blocked.length};
}

// ---- Street interventions: the planner selects one road segment and edits it. ----
// Plans reach the same SUMO session through simulationRequest().planning.
const PLAN_LANE_WIDTH_M=3.2,PLAN_SPEEDS=[20,30,40,50,60];
const roadLengthM=r=>r.path.reduce((n,p,i)=>i?n+metresBetween(r.path[i-1],p):0,0);
const roadDirections=r=>r.oneway?1:2;
function roadLanesEach(r) {
  if(Number.isFinite(r.lanes)&&r.lanes>0)return {n:r.lanes,assumed:false};
  return {n:Math.max(1,Math.round(roadWidth(r)/PLAN_LANE_WIDTH_M/roadDirections(r))),assumed:true};
}
const canReallocateLane=r=>roadLanesEach(r).n>1&&pavementWidth(r)>0;
// One lane per direction becomes pavement; the total freed width is split across two pavements.
function freedWidthM(i) {
  const r=DATA.traffic?.roads[i],p=ui.planning?.get(i);
  return r&&p?.lanes_removed>0&&!ui.closedRoads.has(i)?p.lanes_removed*roadDirections(r)*PLAN_LANE_WIDTH_M:0;
}
// SUMO bars lane 0, the right-hand curb lane of each travel direction (traffic_planning.py).
// Sides are relative to the road path: +1 is left of its drawn direction (the sign offsetRoadPath
// and pedestrianPosition both use), -1 is right. A one-way road (oneway 1 / -1) has one curb lane
// side: right of the path for 1, left for -1. A two-way road keeps its centre line, so both sides widen.
function widenedSides(r) {
  return r.oneway>0?[-1]:r.oneway<0?[1]:[-1,1];
}
// Width gained on each widened side: the freed lanes on that side only.
const sideWidthM=i=>ui.planning?.get(i)?.lanes_removed>0&&!ui.closedRoads.has(i)?ui.planning.get(i).lanes_removed*PLAN_LANE_WIDTH_M:0;
// A pedestrian path may run against its road's drawn direction; the sign tells which way.
function pedestrianRoadSign(s) {
  if(s.roadSign!==undefined)return s.roadSign;
  const r=DATA.traffic?.roads[s.road_index];
  if(!r)return s.roadSign=1;
  const k=111320*Math.cos(s.path[0][1]*Math.PI/180);
  let dot=0;
  for(let i=1;i<s.path.length;i++) {
    const a=s.path[i-1],b=s.path[i],mx=(a[0]+b[0])/2,my=(a[1]+b[1])/2;
    let best=Infinity,dir=[0,0];
    for(let j=1;j<r.path.length;j++) {
      const c=r.path[j-1],d=r.path[j],cx=(c[0]+d[0])/2,cy=(c[1]+d[1])/2,dist=Math.hypot((cx-mx)*k,(cy-my)*111320);
      if(dist<best){best=dist;dir=[(d[0]-c[0])*k,(d[1]-c[1])*111320];}
    }
    dot+=(b[0]-a[0])*k*dir[0]+(b[1]-a[1])*111320*dir[1];
  }
  return s.roadSign=dot<0?-1:1;
}
// A widened pavement keeps its outer edge, so its centre moves inward by half the width gained.
// Walkers on the side that keeps its traffic lane are not moved.
function walkingShift(s,side) {
  if(!ui.planning?.size||s.road_index===null||s.road_index===undefined)return 0;
  const w=sideWidthM(s.road_index),r=DATA.traffic?.roads[s.road_index];
  return w&&r&&widenedSides(r).includes(side*pedestrianRoadSign(s))?w/2:0;
}
function planningChanged() {
  prepareCars();renderTraffic();render();requestTraffic();
}
// Map clicks keep the camera; list and search choices bring the section into view.
function selectRoad(i,focus=false) {
  ui.selectedRoad=i===null||!DATA.traffic?.roads[i]?null:i;
  renderMobilityLab();
  if(focus&&ui.selectedRoad!==null)focusRoads([ui.selectedRoad]);
  render();
  $('planner')?.querySelector('.plan-street')?.scrollIntoView?.({block:'nearest'});
}
function setPlan(i,patch) {
  if(!DATA.traffic?.roads[i])return;
  const next={...(ui.planning.get(i)??{}),...patch};
  if(next.speed_limit_kmh===undefined)delete next.speed_limit_kmh;
  if(!next.lanes_removed)next.lanes_removed=0;
  if(next.speed_limit_kmh===undefined&&next.lanes_removed===0)ui.planning.delete(i);else ui.planning.set(i,next);
  planningChanged();
}
function togglePedestrianise(i) {
  if(!DATA.traffic?.roads[i])return;
  if(ui.closedRoads.has(i))ui.closedRoads.delete(i);
  else {ui.closedRoads.add(i);ui.crashes.delete(i);ui.crashSites.delete(i);}
  planningChanged();
}
function restoreStreet(i) {
  ui.planning.delete(i);ui.closedRoads.delete(i);planningChanged();
}
function clearStreetChanges() {
  ui.planning.clear();ui.closedRoads.clear();planningChanged();
}
function modelledPeople(i) {
  return preparePedestrianPaths().filter(s=>s.road_index===i).reduce((n,s)=>n+s.people,0)*mobilityLab.demand;
}
// Walking space uses the exported segment length and the viewer's pavement widths; both are illustrative.
function streetMetrics(i) {
  const r=DATA.traffic.roads[i],length=roadLengthM(r),pave=pavementWidth(r),freed=freedWidthM(i);
  const before=length*2*pave,after=length*(2*pave+freed),walkers=modelledPeople(i),carriage=length*roadWidth(r);
  const density=a=>a>0?walkers/a*100:null,closed=ui.closedRoads.has(i);
  return {length,pavementM:pave,freedM:freed,areaBefore:before,areaAfter:after,walkers,
    densityBefore:density(before),densityAfter:density(after),
    carFreeArea:closed?before+carriage:null,densityCarFree:closed?density(before+carriage):null};
}
const planNum=(v,d=0)=>v===null?'n/a':v.toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});
function planResultsHtml(i) {
  const m=streetMetrics(i),closed=ui.closedRoads.has(i);
  const rows=[['Pavement walking space',`${planNum(m.areaBefore)} m²`,`${planNum(m.areaAfter)} m²`],
    ['Modelled walkers per 100 m²',planNum(m.densityBefore,1),planNum(m.densityAfter,1)]];
  if(closed)rows.push(['If the whole width is walkable',`${planNum(m.areaBefore)} m²`,`${planNum(m.carFreeArea)} m²`],
    ['Walkers per 100 m² on that width',planNum(m.densityBefore,1),planNum(m.densityCarFree,1)]);
  return `<table class="plan-table"><caption>Section length ${esc(planNum(m.length))} m</caption><thead><tr><th scope="col">Measure</th><th scope="col">Before</th><th scope="col">With plan</th></tr></thead><tbody>${
    rows.map(([a,b,c])=>`<tr><th scope="row">${esc(a)}</th><td>${esc(b)}</td><td>${esc(c)}</td></tr>`).join('')}</tbody></table>
    <div class="note">${m.walkers?`${esc(planNum(m.walkers))} modelled walkers use this section's pavements, the same before and after`:'No modelled walkers are assigned to this segment'}. Assumes ${PLAN_LANE_WIDTH_M} m lanes taken from the curb side of each travel direction, so a two-way section widens both pavements and a one-way section only its curb side (${esc(planNum(m.freedM,1))} m in total). The drawn carriageway is assumed to be lanes × ${PLAN_LANE_WIDTH_M} m, so a strip can sit slightly off a real lane${closed?' and a pedestrianised street walkable across its full width':''}. Pavement widths are illustrative viewer values. The model does not route pedestrians, add pedestrians, or estimate collision risk or wellbeing benefits.</div>`;
}
function planSearchHtml() {
  const q=(ui.planSearch??'').trim().toLowerCase();
  if(q.length<2||!DATA.traffic)return '';
  const seen=new Set(),hits=[];
  DATA.traffic.roads.forEach((r,i)=>{
    if(hits.length<8&&r.name&&r.name.toLowerCase().includes(q)&&!seen.has(r.name)){seen.add(r.name);hits.push([i,r]);}
  });
  return hits.length?hits.map(([i,r])=>`<button type="button" class="more" data-plan-select="${i}"><bdi>${esc(r.name)}</bdi> · section ${i}</button>`).join(''):'<div class="note">No named street matches.</div>';
}
function plannerHtml() {
  const roads=DATA.traffic.roads,sel=ui.selectedRoad,enabled=!!DATA.api;
  const changed=[...new Set([...ui.planning.keys(),...ui.closedRoads])].sort((a,b)=>a-b).slice(0,12);
  let body;
  if(sel!==null&&roads[sel]) {
    const r=roads[sel],plan=ui.planning.get(sel)??{},closed=ui.closedRoads.has(sel),lanes=roadLanesEach(r),can=canReallocateLane(r);
    const speed=plan.speed_limit_kmh,off=(enabled&&!closed)?'':' disabled';
    body=`<div class="note">Selected section</div><div class="plan-street"><bdi>${esc(r.name||'Unnamed street')}</bdi></div>
      <div class="note">${esc(r.cls.replace(/_/g,' '))} · ${r.oneway?'one-way · ':''}${lanes.n} lane${lanes.n===1?'':'s'}${r.oneway?'':' each way'}${lanes.assumed?' (estimated from width)':''} · section ${sel}</div>
      <div class="note">Changes apply to this one section only; a named street is made of several.</div>
      <div class="plan-actions">
        <button type="button" class="more" data-plan="pedestrianise" aria-pressed="${closed}"${enabled?'':' disabled'}>${closed?'Reopen this section to traffic':'Pedestrianise this section'}</button>
        <button type="button" class="more" data-plan="calm30" aria-pressed="${speed===30}"${off}>Calm this section to 30 km/h</button>
        <label class="plan-field">Other speed limit<select id="plan-speed"${off}><option value="">Unchanged</option>${PLAN_SPEEDS.map(v=>`<option value="${v}"${speed===v?' selected':''}>${v} km/h</option>`).join('')}</select></label>
        <button type="button" class="more" data-plan="lane" aria-pressed="${plan.lanes_removed>0}"${can?off:' disabled'}>${r.oneway?'Give the curb lane to the pavement':'Give one lane each way to pavements'}</button>
        ${can?'':'<div class="note">This section has no spare lane; pedestrianise it instead.</div>'}
        <button type="button" class="more" data-plan="restore">Restore this section</button></div>
      ${closed?'<div class="note">Closed to cars: speed and lane changes wait until it reopens. Pavements stay open.</div>':''}
      ${planResultsHtml(sel)}`;
  } else body=`<div class="note">${enabled?'Click a street section on the map to select it, or search by name. A named street is made of several sections.':'Street changes need the live server.'}</div>`;
  const list=changed.length?`<div class="plan-list" role="group" aria-label="Changed streets">${changed.map(i=>`<button type="button" class="more" data-plan-select="${i}" aria-pressed="${sel===i}"><bdi>${esc(roads[i]?.name||`Section ${i}`)}</bdi>${ui.closedRoads.has(i)?' · pedestrianised':''}${ui.planning.has(i)?' · planned':''}</button>`).join('')}
    <button type="button" class="more" data-plan="clear-all">Clear all street changes</button></div>`:'';
  return `<div class="lever"><div class="t">Street interventions</div>
    <label class="plan-field">Find a street<input id="plan-search" type="search" autocomplete="off" value="${esc(ui.planSearch??'')}"></label>
    <div id="plan-results" class="plan-list">${planSearchHtml()}</div>${body}${list}</div>`;
}
function renderPlanner() {
  const el=$('planner');if(!el||!DATA.traffic)return;
  const html=plannerHtml();
  if(el.dataset.content===html)return;
  const active=typeof document!=='undefined'?document.activeElement:null;
  const keep=active&&el.contains(active)?(active.id?`#${active.id}`:active.dataset.plan?`[data-plan="${active.dataset.plan}"]`:null):null;
  el.innerHTML=html;el.dataset.content=html;
  if(keep)el.querySelector(keep)?.focus?.();
}
// Widened pavement strips, planned-street outlines and the selected street, drawn above street detail.
function planningLayers() {
  if(!DATA.traffic||!ui.traffic||!ui.planning)return [];
  const roads=DATA.traffic.roads,out=[],dark=isDark(),night=nightLevel(),strips=[];
  for(const i of ui.planning.keys()) {
    if(!sideWidthM(i)||!roads[i])continue;
    // Each strip covers the barred curb lane: from the carriageway edge inward by the width gained
    // on that side, clamped to the half-carriageway so it never reaches the retained lane.
    const half=roadWidth(roads[i])/2,w=Math.min(sideWidthM(i),half);
    for(const side of widenedSides(roads[i]))strips.push({i,side,path:offsetRoadPath(roads[i].path,side*(half-w/2)),width:w});
  }
  if(strips.length)out.push(new deck.PathLayer({id:'plan-pavement-widening',data:strips,getPath:d=>d.path,getWidth:d=>d.width,
    widthUnits:'meters',getColor:shade(dark?[96,99,100,255]:[214,211,203,255],night,.6),parameters:SOFT,pickable:false,
    updateTriggers:{getColor:[dark,night]}}));
  const planned=[...ui.planning.keys()].filter(i=>roads[i]&&!ui.closedRoads.has(i));
  if(planned.length)out.push(new deck.PathLayer({id:'plan-streets',data:planned,getPath:i=>roads[i].path,getWidth:3,widthUnits:'pixels',
    getColor:[24,120,168,230],capRounded:true,parameters:ON_TOP,pickable:false}));
  if(ui.selectedRoad!==null&&roads[ui.selectedRoad])out.push(new deck.PathLayer({id:'plan-selected',data:[ui.selectedRoad],
    getPath:i=>roads[i].path,getWidth:6,widthUnits:'pixels',getColor:[255,190,30,255],capRounded:true,parameters:ON_TOP,pickable:false}));
  return out;
}
function renderMobilityLab() {
  const el=$('mobility-lab');if(!el)return;
  const lab=mobilityLab,m=mobilityLabMetrics();
  if(!el.dataset.ready) {
    el.innerHTML=`<div id="planner"></div>
      <details class="lever" id="lab-assumptions"><summary class="t">Optional assumptions and display</summary>
      <div class="note">Traffic uses SUMO. Walking uses the separate modelled PM demand. These multipliers are what-if assumptions, not planning interventions.</div>
      <div class="sev"><label for="lab-incoming">Incoming traffic multiplier <b id="lab-incoming-value"></b></label><input id="lab-incoming" type="range" min="0" max="2" step="0.25" value="1" ${DATA.api?'':'disabled'}></div>
      ${DATA.pedestrians?`<label class="road-row"><input id="lab-people" type="checkbox" checked>People on pavements</label>
      <label class="road-row"><input id="lab-flows" type="checkbox">Walking flow overlay</label>
      <div class="sev"><label for="lab-demand">Pedestrian demand multiplier <b id="lab-demand-value"></b></label><input id="lab-demand" type="range" min="0" max="3" step="0.25" value="1"></div>
      <button type="button" class="more" data-lab-pavement aria-pressed="false">Mark pavement unavailable (limitation test)</button>
      <div class="note">Click a green segment to remove its walkers; click again to restore them. This only removes modelled walkers and assigns no detours. Car-free roads keep their pavements open.</div>
      <div id="lab-results" aria-live="polite"></div>
      <details><summary>Pedestrian evidence</summary><div class="note">${esc(DATA.pedestrians.status)} · ${esc(DATA.pedestrians.period)}. ${esc(DATA.pedestrians.source)}. ${esc(DATA.pedestrians.limitations)} The traffic hour does not change this PM baseline. Walker figures represent modelled occupancy after removing the regression constant, with illustrative two-way motion and appearance at a fixed 1.3 m/s. Unavailable pavements remove walkers and report affected occupancy without assigning detours. Up to 2,400 people are drawn in the view.</div></details>`:'<div class="note">No pedestrian export loaded. Load the shared pedestrian export to see people here.</div>'}
      <details><summary>How traffic demand works</summary><div class="note">Changing incoming traffic restarts SUMO with the same initial fleet. The multiplier changes new edge entries and trips starting inside. Normal traffic and street-change comparison share this demand.</div></details></details>
      <div class="seg" style="margin-top:10px"><button type="button" data-lab-pause>Pause</button><button type="button" data-lab-reset>Reset experiment</button></div>
      <div class="note">Reset clears street changes, closures, crashes and these settings; weather scenarios stay as selected.</div>`;
    el.dataset.ready='true';
  }
  renderPlanner();
  $('lab-incoming-value').textContent=`${lab.incoming.toFixed(2)}×`;
  if(DATA.pedestrians) {
    $('lab-demand-value').textContent=`${lab.demand.toFixed(2)}×`;
    $('lab-results').innerHTML=`<div class="note"><b>${Math.round(m.available).toLocaleString()} modelled walkers on open pavements</b><br>Baseline ${Math.round(m.baseline).toLocaleString()} · ${m.blocked} segments unavailable · ${Math.round(m.unavailable)} modelled walkers affected. Citywide totals; no pedestrian–vehicle interaction.</div>`;
    el.querySelector('[data-lab-pavement]').setAttribute('aria-pressed',String(ui.roadTool==='pavement'));
  }
  el.querySelector('[data-lab-pause]').textContent=lab.paused?'Resume':'Pause';
}
function mobilityLabInput(target) {
  const lab=mobilityLab;
  if(target.id==='lab-incoming') {
    lab.incoming=Number(target.value);closeTrafficSession();cars=[];prepareCars();
  } else if(target.id==='lab-demand')lab.demand=Number(target.value);
  else if(target.id==='lab-people')lab.people=target.checked;
  else if(target.id==='lab-flows')lab.flows=target.checked;
  else if(target.id==='plan-speed') {
    if(ui.selectedRoad!==null)setPlan(ui.selectedRoad,{speed_limit_kmh:target.value===''?undefined:Number(target.value)});
    return;
  } else if(target.id==='plan-search') {
    ui.planSearch=target.value;
    const el=$('planner'),results=$('plan-results');
    if(el)el.dataset.content=plannerHtml();
    if(results)results.innerHTML=planSearchHtml();
    return;
  } else return;
  renderMobilityLab();render();
}
function mobilityLabClick(target) {
  const pick=target.closest('[data-plan-select]');
  if(pick) {selectRoad(Number(pick.dataset.planSelect),true);return true;}
  const act=target.closest('[data-plan]')?.dataset.plan,i=ui.selectedRoad;
  if(act) {
    const plan=ui.planning.get(i)??{};
    if(act==='clear-all')clearStreetChanges();
    else if(i!==null) {
      if(act==='pedestrianise')togglePedestrianise(i);
      else if(act==='calm30')setPlan(i,{speed_limit_kmh:plan.speed_limit_kmh===30?undefined:30});
      else if(act==='lane')setPlan(i,{lanes_removed:plan.lanes_removed>0?0:1});
      else if(act==='restore')restoreStreet(i);
    }
    return true;
  }
  if(target.closest('[data-lab-pause]')) {
    mobilityLab.paused=!mobilityLab.paused;carTime=null;mobilityLab.last=null;renderTraffic();renderMobilityLab();render();return true;
  }
  if(target.closest('[data-lab-pavement]')) {
    setRoadTool('pavement');renderMobilityLab();return true;
  }
  if(target.closest('[data-lab-reset]')) {
    Object.assign(mobilityLab,{people:true,flows:false,demand:1,speed:1.3,incoming:1,paused:false,time:0,last:null});
    mobilityLab.blocked.clear();for(const s of preparePedestrianPaths())s.phase=0;
    ui.roadTool=null;ui.closedRoads.clear();ui.crashes.clear();ui.crashSites.clear();
    ui.planning.clear();ui.selectedRoad=null;ui.planSearch='';
    const el=$('mobility-lab');if(el){el.dataset.ready='';el.innerHTML='';}
    closeTrafficSession();cars=[];prepareCars();requestTraffic();renderTraffic();render();return true;
  }
  return false;
}
