/* Access plans view: the planning graph behind the corridor access results (build-access-view).
 * Homes stay on the ground; links, candidate sites and mapped services float on tiers above them, and
 * each home is joined to the nearest provider that serves it within the threshold (500 m along the
 * walking network), through the link its walk crosses, if any. Guided steps go from today's access to
 * the proven best plan, what the links add, how close each search method gets, and what comes next.
 * The graph is fetched from a loopback-only endpoint (its homes come from Beirut 001) and never cached.
 * Every number on the page is read from the bundle, whose distances were checked against the scorer. */
const ACC_USES={
  education:{label:'Schools',one:'school',many:'schools',colour:[104,86,196]},
  medical:{label:'Medical care',one:'clinic',many:'clinics or hospitals',colour:[214,64,92]},
  shopping:{label:'Daily shops',one:'shop',many:'daily shops',colour:[214,140,30]},
  entertainment:{label:'Leisure',one:'leisure place',many:'leisure places',colour:[28,146,172]},
  parks:{label:'Parks',one:'park',many:'parks',colour:[52,146,80]},
};
const ACC_TIER={place:150,site:95,link:48};
// Candidate sites by kind, apart in lightness as well as hue.
const ACC_SITE_COLOUR={bbed:[150,92,164],parking:[72,86,108],railbed:[196,160,104],station:[46,120,176],other:[128,136,144]};
const ACC_SITE_KIND={bbed:['BBED parcel','BBED parcels'],parking:['parking lot','parking lots'],railbed:['rail-bed piece','rail-bed pieces'],
  station:['station land','station land'],other:['other site','other sites']};
const ACC_STEPS=[
  {id:'today',title:'Today'},{id:'choices',title:'The choices'},{id:'plan',title:'The best plan'},
  {id:'links',title:'What the links add'},{id:'methods',title:'How close each method gets'},{id:'next',title:"What's next"},
];
const ACC_REVEAL={spread:1.9,travel:0.9,pulse:4.6};
const access={view:0,data:[],loading:[],error:null,step:0,service:'medical',N:10,K:2,alpha:1,method:'exact',
  layout:'tiered',search:false,sceneId:'',sceneAt:0,built:null,focused:false};

// ---- data ------------------------------------------------------------------------------------
function accessView() { return access.data[access.view] ?? null; }
function accessPrepare(b) {
  const H=b.homes.residents.length,homes=[];
  for(let i=0;i<H;i++)homes.push({accessKind:'home',i,ll:[b.homes.lonlat[2*i],b.homes.lonlat[2*i+1]],r:b.homes.residents[i]});
  const places=b.places.map((p,i)=>({accessKind:'place',i,ll:p.lonlat,use:b.uses[p.use],name:p.name,source:p.source}));
  const P=places.length;
  const sites=b.sites.map((s,i)=>({accessKind:'site',i,p:P+i,ll:s.lonlat,...s,allowedUses:s.allowed.map(u=>b.uses[u])}));
  const groupOf=b.links.map(l=>l.group);
  const links=b.links.map((l,i)=>({accessKind:'link',i,...l,groupName:b.groups[l.group].name,inert:b.groups[l.group].inert}));
  return {b,H,P,homes,places,sites,links,groupOf,cache:new Map(),z:null};
}
// Ground heights, read once the terrain grid is in (lens use starts after boot, when it always is).
function accessGround(v) {
  if(v.z)return v.z;
  const g=(ll)=>(typeof groundZ==='function'?groundZ(ll[0],ll[1]):0);
  v.z={home:v.homes.map(h=>g(h.ll)),place:v.places.map(p=>g(p.ll)),site:v.sites.map(s=>g(s.ll)),link:v.links.map(l=>g(l.mid))};
  v.homeAt=v.homes.map(h=>[h.ll[0],h.ll[1],v.z.home[h.i]+1.2]);
  for(const l of v.links)l.onGround??=(typeof groundPath==='function'?groundPath(l.path,0.8,10):l.path.map(p=>[p[0],p[1],0.8]));
  return v.z;
}
const accLinkName=(name)=>name==='railbed-walkway'?'Rail-bed walkway':String(name).replace(/^crossing\/(\d+)$/,'Crossing #$1');
// Each home's nearest provider, the link its walk crosses (or -1), and whether it needs a link, for a
// scenario: the baseline with the scenario's differences applied.
function accessScenario(v,key,use) {
  const id=`${key}|${use}`;
  if(v.cache.has(id))return v.cache.get(id);
  const s=key==='baseline'?null:v.b.scenarios[key];
  if(key!=='baseline'&&!s)return null;
  const provider=Int32Array.from(v.b.baseline.provider[use]),via=Int32Array.from(v.b.baseline.via[use]),needs=new Uint8Array(v.H);
  if(s){const e=s.edges[use];for(let k=0;k<e.length;k+=4){provider[e[k]]=e[k+1];via[e[k]]=e[k+2];needs[e[k]]=e[k+3];}}
  const out={provider,via,needs,s,residents:s?s.residents_within:v.b.baseline.residents_within};
  v.cache.set(id,out);
  return out;
}
const accKey=(method,N,K,alpha)=>`${method}|${N}|${K}|${alpha}`;
function accessExploreKey(v) {
  if(access.K===0)return accKey(access.method==='exact'?'exact_no_links':access.method,access.N,0,access.alpha);
  return accKey(access.method,access.N,access.K,access.alpha);
}

// ---- scenes ----------------------------------------------------------------------------------
function accessScene() {
  const v=accessView();
  if(!v)return null;
  const st=v.b.story,step=access.step==='explore'?'explore':ACC_STEPS[access.step]?.id;
  const scene={step,use:access.service,key:'baseline',compare:null,search:false,allSites:false,ghosts:false,greedy:null,
    layout:access.layout};
  if(step==='choices'){scene.search=true;scene.allSites=true;scene.noEdges=true;}
  else if(step==='plan'&&st.plan){scene.key=st.plan;scene.compare='baseline';}
  else if(step==='links'&&st.links){scene.key=st.links;scene.compare=st.plan??'baseline';}
  else if(step==='methods'&&st.methods?.exact){scene.key=st.methods.exact;scene.compare='baseline';scene.greedy=st.methods.greedy;}
  else if(step==='next'){scene.ghosts=true;scene.allSites=true;scene.noEdges=true;}
  else if(step==='explore'){
    const key=accessExploreKey(v);
    scene.key=v.b.scenarios[key]?key:'baseline';scene.compare='baseline';scene.search=access.search;scene.missing=scene.key!==key;
  }
  scene.id=[access.view,step,scene.key,scene.compare,scene.use,scene.layout,scene.search].join('|');
  return scene;
}
// Where a provider, link hub or home sits in the current layout.
function accessPoint(v,kind,i,layout) {
  const z=accessGround(v),tiered=layout==='tiered';
  if(kind==='home')return v.homeAt[i];
  if(kind==='link')return [...v.links[i].mid,z.link[i]+(tiered?ACC_TIER.link:1.5)];
  if(i<v.P)return [...v.places[i].ll,z.place[i]+(tiered?ACC_TIER.place:2)];
  return [...v.sites[i-v.P].ll,z.site[i-v.P]+(tiered?ACC_TIER.site:2)];
}
function accMetres(a,b) {
  const sy=111320,sx=sy*Math.cos(a[1]*Math.PI/180);
  return Math.hypot((a[0]-b[0])*sx,(a[1]-b[1])*sy);
}
// A falling curve from a lifted node to a lower one (tiered), or a low arc between ground points (map).
function accCurve(a,b,tiered,n=12) {
  const out=[];
  if(tiered) {
    const c1=[a[0]+(b[0]-a[0])*.35,a[1]+(b[1]-a[1])*.35,a[2]],c2=[b[0],b[1],b[2]+(a[2]-b[2])*.45];
    for(let k=0;k<=n;k++){const t=k/n,u=1-t;
      out.push([0,1,2].map(j=>u*u*u*a[j]+3*u*u*t*c1[j]+3*u*t*t*c2[j]+t*t*t*b[j]));}
  } else {
    const h=Math.min(80,Math.max(6,accMetres(a,b)*.22));
    for(let k=0;k<=n;k++){const t=k/n;out.push([a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t+4*t*(1-t)*h]);}
  }
  return out;
}
// Edges, home states and counts for a scene; rebuilt only when the scene changes.
function accessBuild(v,scene) {
  if(access.built?.id===scene.id)return access.built;
  const cur=accessScenario(v,scene.key,scene.use),cmp=scene.compare?accessScenario(v,scene.compare,scene.use):null;
  const tiered=scene.layout==='tiered',thr=v.b.threshold_m,status=new Uint8Array(v.H),serves=new Map(),hubs=new Map();
  const edges=[];let reveal=0;
  for(let h=0;h<v.H;h++) {
    const p=cur.provider[h];
    if(p<0)continue;
    const was=cmp?cmp.provider[h]>=0:true,l=cur.via[h];
    const fresh=!cmp||!was||cmp.provider[h]!==p||cmp.via[h]!==l;
    status[h]=!was?(cur.needs[h]?3:2):1;
    serves.set(p,(serves.get(p)??0)+1);
    // steps about sites, not walks, keep the homes' state but draw no edges
    if(scene.noEdges)continue;
    if(l>=0)hubs.set(l,(hubs.get(l)??0)+1);
    const a=accessPoint(v,'provider',p,scene.layout),b=accessPoint(v,'home',h,scene.layout);
    let path,metres;
    if(l>=0){const m=accessPoint(v,'link',l,scene.layout);path=[...accCurve(a,m,tiered,10),...accCurve(m,b,tiered,10).slice(1)];metres=accMetres(a,m)+accMetres(m,b);}
    else{path=accCurve(a,b,tiered,14);metres=accMetres(a,b);}
    const delay=0.12+ACC_REVEAL.spread*Math.min(metres/thr,1.25)/1.25;
    const ts=path.map((_,k)=>delay+ACC_REVEAL.travel*k/(path.length-1));
    reveal=Math.max(reveal,ts[ts.length-1]);
    edges.push({h,p,l,fresh,gain:status[h]>=2,path,ts,arrive:ts[ts.length-1]});
  }
  const chosen=new Map((cur.s?.uses??[]).map(([s,u])=>[s,v.b.uses[u]]));
  let greedyOnly=new Set();
  if(scene.greedy&&v.b.scenarios[scene.greedy]) {
    const g=new Set(v.b.scenarios[scene.greedy].uses.map(([s])=>s));
    greedyOnly=new Set([...g].filter(s=>!chosen.has(s)));
  }
  // Layer data is built once per scene: deck.gl re-reads a layer whose data array changes.
  const z=accessGround(v),open=new Set(cur.s?.links??[]);
  const places=v.places.filter(p=>p.use===scene.use).map(p=>({...p,at:accessPoint(v,'provider',p.i,scene.layout),base:z.place[p.i]}));
  const sites=v.sites.filter(s=>chosen.has(s.i)||greedyOnly.has(s.i)||scene.allSites||scene.search)
    .map(s=>({...s,at:accessPoint(v,'provider',s.p,scene.layout),base:z.site[s.i]}));
  const hubList=[...hubs.keys()].map(l=>({...v.links[l],at:accessPoint(v,'link',l,scene.layout),base:z.link[l]}));
  const labels=[];
  // with many plan sites the names turn to noise; the tooltip still names each
  if(chosen.size<=12)for(const [s,use] of chosen)labels.push({text:ACC_USES[use].one.replace(/^./,(c)=>c.toUpperCase()),at:accessPoint(v,'provider',v.P+s,scene.layout)});
  const named=new Set(),linkLabels=[];
  for(const l of v.links)if(open.has(l.groupName)&&!named.has(l.groupName)) {
    // the walkway is named at its busiest piece, a crossing at its middle
    const busiest=[...hubs.entries()].filter(([i])=>v.links[i].groupName===l.groupName).sort((a,b)=>b[1]-a[1])[0]?.[0];
    named.add(l.groupName);
    const at=accessPoint(v,'link',busiest??l.i,scene.layout),near=linkLabels.find(x=>accMetres(x.at,at)<400);
    // two crossings at one junction share a label instead of printing over each other
    if(near){near.names.push(accLinkName(l.groupName));near.text=`Crossings ${near.names.map(n=>n.replace('Crossing ','')).join(' and ')}`;}
    else linkLabels.push({text:accLinkName(l.groupName),names:[accLinkName(l.groupName)],at,below:true});
  }
  labels.push(...linkLabels);
  let search=null;
  if(scene.search) {
    search=[];
    for(const s of v.sites){const a=accessPoint(v,'provider',s.p,scene.layout);for(const h of v.b.search_space[s.i])search.push([a,v.homeAt[h]]);}
  }
  const f=v.b.forecast,ghosts=scene.ghosts&&f?.buildings?.length?f.buildings.map((g,i)=>({accessKind:'ghost',i,...g,
    at:[g.lonlat[0],g.lonlat[1],(typeof groundZ==='function'?groundZ(g.lonlat[0],g.lonlat[1]):0)+2]})):null;
  access.built={id:scene.id,scene,cur,cmp,edges,fresh:edges.filter(e=>e.fresh),unchanged:edges.filter(e=>!e.fresh),
    status,serves,hubs,chosen,greedyOnly,open,places,sites,greedySites:sites.filter(s=>greedyOnly.has(s.i)),hubList,
    lifted:tiered?[...places,...sites,...hubList]:[],labels,search,ghosts,reveal:reveal+0.25,tiered};
  access.sceneAt=performance.now();
  return access.built;
}

// ---- drawing ---------------------------------------------------------------------------------
// Palettes are built once per theme, so constant colours keep their identity between frames.
const ACC_PALETTES={
  light:{none:[170,176,182,170],before:[52,104,164,235],gain:[232,112,26,255],link:[214,160,20,255],stem:[60,70,80,80],
    search:[70,80,92,50],candidate:[140,148,156,140],inert:[176,182,188,120],ring:[255,255,255,240],clear:[0,0,0,0],
    ghost:[124,84,184,230],text:[32,39,45,255],textBg:[255,255,255,225]},
  dark:{none:[112,122,132,170],before:[120,168,222,235],gain:[255,146,64,255],link:[255,214,110,255],stem:[200,210,220,90],
    search:[180,190,200,50],candidate:[150,160,170,130],inert:[110,118,126,110],ring:[255,255,255,235],clear:[0,0,0,0],
    ghost:[190,150,240,230],text:[236,240,244,255],textBg:[30,36,42,215]},
};
function accColours() { return ACC_PALETTES[typeof isDark==='function'&&isDark()?'dark':'light']; }
const accAlpha=(rgb,a)=>[rgb[0],rgb[1],rgb[2],a];
const accMix=(a,b,t)=>[0,1,2].map(i=>Math.round(a[i]+(b[i]-a[i])*t));
let accDrawParams=null;
function accParams() {
  // Graph layers draw over the buildings: a graph hidden behind a tower says nothing.
  if(!accDrawParams) {
    const over=typeof ON_TOP!=='undefined'?ON_TOP:{depthCompare:'always'};
    accDrawParams={over,glow:typeof GLOW!=='undefined'?{...GLOW,depthCompare:'always'}:over};
  }
  return accDrawParams;
}
function accessAnimating() { return ui.lens==='access'&&!!accessView()&&!(typeof calm!=='undefined'&&calm.matches); }
function accessLayers(now) {
  const v=accessView();
  if(ui.lens!=='access'||!v)return [];
  const scene=accessScene(),built=accessBuild(v,scene),C=accColours(),{over,glow}=accParams();
  const still=typeof calm!=='undefined'&&calm.matches;
  const t=still?Infinity:(now-access.sceneAt)/1000,revealing=t<built.reveal,id=scene.id;
  const useRgb=ACC_USES[scene.use].colour,edgeRgb=(e)=>e.l>=0?accMix(useRgb,C.link,.55):useRgb;
  const out=[];
  if(built.lifted.length)out.push(new deck.PathLayer({id:'access-stems',data:built.lifted,getPath:(q)=>[[q.at[0],q.at[1],q.base],q.at],
    getColor:C.stem,getWidth:1,widthUnits:'pixels',parameters:over,pickable:false,updateTriggers:{getPath:id}}));
  if(built.search)out.push(new deck.LineLayer({id:'access-search',data:built.search,getSourcePosition:(d)=>d[0],getTargetPosition:(d)=>d[1],
    getColor:C.search,getWidth:1,widthUnits:'pixels',parameters:over,pickable:false,updateTriggers:{getSourcePosition:id,getTargetPosition:id}}));
  // links on the ground: open ones bright, closed candidates thin, no-effect ones faint
  out.push(new deck.PathLayer({id:'access-links',data:v.links,getPath:(l)=>l.onGround,
    getColor:(l)=>built.open.has(l.groupName)?C.link:l.inert?C.inert:C.candidate,getWidth:(l)=>built.open.has(l.groupName)?5:l.inert?1.5:2.5,
    widthUnits:'pixels',capRounded:true,jointRounded:true,parameters:over,pickable:true,updateTriggers:{getColor:[id,C],getWidth:id}}));
  // edges: unchanged ones faint; new ones grow outward from their provider, then a pulse runs along them
  out.push(new deck.PathLayer({id:'access-edges',data:revealing?built.unchanged:built.edges,getPath:(e)=>e.path,
    getColor:(e)=>accAlpha(edgeRgb(e),scene.compare?(e.fresh?170:46):120),getWidth:(e)=>e.fresh&&scene.compare?1.7:1.1,widthUnits:'pixels',
    parameters:over,pickable:false,updateTriggers:{getColor:[id,C],getWidth:id}}));
  if(!still&&built.fresh.length) {
    if(revealing)out.push(new deck.TripsLayer({id:'access-reveal',data:built.fresh,getPath:(e)=>e.path,getTimestamps:(e)=>e.ts,
      getColor:(e)=>accAlpha(edgeRgb(e),230),currentTime:t,trailLength:1e4,fadeTrail:false,getWidth:2,widthUnits:'pixels',
      capRounded:true,jointRounded:true,parameters:over,updateTriggers:{getPath:id,getTimestamps:id,getColor:[id,C]}}));
    else {
      const period=Math.max(ACC_REVEAL.pulse,built.reveal);
      out.push(new deck.TripsLayer({id:'access-pulse',data:built.fresh,getPath:(e)=>e.path,getTimestamps:(e)=>e.ts,
        getColor:(e)=>accAlpha(accMix(edgeRgb(e),[255,255,255],.35),255),currentTime:(t-built.reveal)%period,trailLength:.42,fadeTrail:true,
        getWidth:2.4,widthUnits:'pixels',capRounded:true,jointRounded:true,parameters:glow,updateTriggers:{getPath:id,getTimestamps:id,getColor:[id,C]}}));
    }
  }
  // homes: grey out of reach, blue already served, orange brought in by the plan (gold only across a new
  // link); a home changes colour when its edge arrives
  const tick=revealing?Math.floor(t*12):-1;
  const pending=(h)=>revealing&&built.status[h]>=2&&(built.arriveAt??=accArrivals(built))[h]>t;
  out.push(new deck.ScatterplotLayer({id:'access-homes',data:v.homes,getPosition:(h)=>v.homeAt[h.i],
    getRadius:(h)=>1.6+Math.sqrt(h.r)*.55,radiusUnits:'meters',radiusMinPixels:2.2,radiusMaxPixels:8,
    getFillColor:(h)=>{const s=built.status[h.i];return pending(h.i)?C.none:s===0?C.none:s===1?C.before:s===2?C.gain:C.link;},
    stroked:true,getLineColor:(h)=>built.status[h.i]===3&&!pending(h.i)?C.ring:C.clear,lineWidthUnits:'pixels',getLineWidth:1.2,
    billboard:true,pickable:true,parameters:over,updateTriggers:{getFillColor:[id,tick,C],getLineColor:[id,tick,C]}}));
  // providers: the mapped places of this service, plan sites in their use's colour, other candidates small
  out.push(new deck.ScatterplotLayer({id:'access-places',data:built.places,getPosition:(p)=>p.at,getRadius:5.5,radiusUnits:'pixels',
    getFillColor:accAlpha(accMix(useRgb,[255,255,255],.15),245),stroked:true,getLineColor:C.ring,lineWidthUnits:'pixels',getLineWidth:1.5,
    billboard:true,pickable:true,parameters:over,updateTriggers:{getPosition:id,getFillColor:id}}));
  out.push(new deck.ScatterplotLayer({id:'access-sites',data:built.sites,getPosition:(s)=>s.at,
    getRadius:(s)=>built.chosen.has(s.i)?8:scene.allSites?5:3.5,radiusUnits:'pixels',
    getFillColor:(s)=>built.chosen.has(s.i)?accAlpha(ACC_USES[built.chosen.get(s.i)].colour,250):scene.allSites?accAlpha(ACC_SITE_COLOUR[s.kind]??ACC_SITE_COLOUR.other,230):C.candidate,
    stroked:true,getLineColor:(s)=>built.chosen.has(s.i)?C.ring:C.clear,lineWidthUnits:'pixels',getLineWidth:2,billboard:true,pickable:true,
    parameters:over,updateTriggers:{getPosition:id,getRadius:id,getFillColor:[id,C],getLineColor:[id,C]}}));
  if(built.greedySites.length)out.push(new deck.ScatterplotLayer({id:'access-greedy-only',data:built.greedySites,getPosition:(s)=>s.at,
    getRadius:11,radiusUnits:'pixels',filled:false,stroked:true,getLineColor:C.gain,lineWidthUnits:'pixels',getLineWidth:2.5,
    billboard:true,pickable:true,parameters:over,updateTriggers:{getPosition:id,getLineColor:C}}));
  if(built.hubList.length)out.push(new deck.ScatterplotLayer({id:'access-hubs',data:built.hubList,getPosition:(l)=>l.at,
    getRadius:(l)=>3+Math.min(5,Math.sqrt(built.hubs.get(l.i))*.6),radiusUnits:'pixels',getFillColor:C.link,stroked:true,getLineColor:C.ring,
    lineWidthUnits:'pixels',getLineWidth:1.5,billboard:true,pickable:true,parameters:over,updateTriggers:{getPosition:id,getRadius:id,getFillColor:C}}));
  if(built.ghosts) {
    const f=v.b.forecast,last=f.years.length-1;
    out.push(new deck.ScatterplotLayer({id:'access-ghosts',data:built.ghosts,getPosition:(g)=>g.at,
      getRadius:(g)=>3+60*(f.chance[g.state]?.[last]??0),radiusUnits:'pixels',radiusMaxPixels:12,filled:false,stroked:true,
      getLineColor:C.ghost,lineWidthUnits:'pixels',getLineWidth:2,billboard:true,pickable:true,parameters:over,updateTriggers:{getLineColor:C}}));
  }
  if(built.labels.length)out.push(new deck.TextLayer({id:'access-labels',data:built.labels,getPosition:(d)=>d.at,getText:(d)=>d.text,getSize:12,
    sizeUnits:'pixels',fontFamily:'Inter, Arial, sans-serif',fontWeight:600,characterSet:'auto',getColor:C.text,getPixelOffset:(d)=>d.below?[0,18]:[0,-16],
    billboard:true,background:true,getBackgroundColor:C.textBg,backgroundPadding:[4,2],parameters:over,pickable:false,
    updateTriggers:{getPosition:id,getText:id,getPixelOffset:id,getColor:C,getBackgroundColor:C}}));
  return out;
}
// When each newly served home's edge reaches it, for the reveal.
function accArrivals(built) {
  const at=new Float32Array(built.status.length);
  for(const e of built.fresh)at[e.h]=e.arrive;
  return at;
}

// ---- the camera --------------------------------------------------------------------------------
function accessFocus(v) {
  let w=Infinity,s=Infinity,e=-Infinity,n=-Infinity;
  for(const h of v.homes){w=Math.min(w,h.ll[0]);e=Math.max(e,h.ll[0]);s=Math.min(s,h.ll[1]);n=Math.max(n,h.ll[1]);}
  if(!isFinite(w))return;
  const still=typeof calm!=='undefined'&&calm.matches;
  if(map?.cameraForBounds) {
    const cam=map.cameraForBounds([[w,s],[e,n]],{padding:{top:90,bottom:90,left:60,right:420},bearing:-24});
    if(cam)map.flyTo({...cam,pitch:56,duration:still?0:1600,essential:true});
  } else if(typeof flyTo==='function') {
    const span=Math.max(e-w,n-s);flyTo({longitude:(w+e)/2,latitude:(s+n)/2,zoom:Math.log2(360/span)-0.4,pitch:56,bearing:-24});
  }
}

// ---- the panel --------------------------------------------------------------------------------
const accPct=(x)=>x==null?'–':`${Math.round(x*100)}%`;
const accPts=(a,b)=>{const d=Math.round((b-a)*100);return `${d>=0?'+':'−'}${Math.abs(d)} point${Math.abs(d)===1?'':'s'}`;};
const accList=(items)=>items.length<2?items.join(''):`${items.slice(0,-1).join(', ')} and ${items[items.length-1]}`;
function accUsesText(v,key) {
  const s=v.b.scenarios[key];
  if(!s)return '';
  const count={};
  for(const [,u] of s.uses)count[v.b.uses[u]]=(count[v.b.uses[u]]??0)+1;
  return accList(Object.entries(count).sort((a,b)=>b[1]-a[1]).map(([u,n])=>`${n} ${n===1?ACC_USES[u].one:ACC_USES[u].many.replace(' or hospitals','')}`));
}
function accStepText(v,scene) {
  const b=v.b,thr=b.threshold_m,use=scene.use,U=ACC_USES[use],base=b.baseline.residents_within,sm=b.summary;
  const residents=Math.round(b.homes.total_residents/100)*100;
  if(scene.step==='today') {
    const worst=b.uses.reduce((a,u)=>base[u]<base[a]?u:a,b.uses[0]);
    return `${accPct(base[use])} of the corridor's about ${residents.toLocaleString()} residents live within a ${thr} m walk of ${U.many}. Each line joins a home to its nearest mapped ${U.one}. ${worst===use?`${ACC_USES[worst].label} is the widest gap.`:`The widest gap is ${ACC_USES[worst].label.toLowerCase()}: ${accPct(base[worst])}.`}`;
  }
  if(scene.step==='choices') {
    const kinds={};for(const s of b.sites)kinds[s.kind]=(kinds[s.kind]??0)+1;
    const k=accList(Object.entries(kinds).sort((x,y)=>y[1]-x[1]).map(([kind,n])=>`${n} ${ACC_SITE_KIND[kind]?.[n===1?0:1]??kind}`));
    return `${sm.sites} candidate sites (${k}) allow ${sm.site_use_choices} site–use choices. Of the ${sm.link_groups} reviewed walking-link groups, ${sm.inert_groups.length} change no walk within ${thr} m, so links give only ${sm.patterns} distinct reach patterns. The faint lines show every home each site could serve.`;
  }
  const s=b.scenarios[scene.key],cmp=scene.compare==='baseline'?base:b.scenarios[scene.compare]?.residents_within;
  if(scene.step==='plan'&&s)
    return `The proven optimum for ${s.budget} sites at α = ${s.alpha}: ${accUsesText(v,scene.key)}. Bright lines are the walks the plan creates; orange homes come within ${thr} m.`;
  if(scene.step==='links'&&s) {
    const via=access.built?.edges.filter(e=>e.l>=0).length??0,needs=access.built?[...access.built.status].filter(x=>x===3).length:0;
    return `Opening ${accList(s.links.map(accLinkName))} with ${s.budget} re-optimised sites: ${U.label.toLowerCase()} ${accPct(cmp?.[use])} → ${accPct(s.residents_within[use])}. ${via.toLocaleString()} homes reach their nearest ${U.one} across a new link${needs?`; ${needs.toLocaleString()} of them gain one only that way (gold)`:''}. ${sm.inert_groups.length} reviewed crossings change nothing within ${thr} m.`;
  }
  if(scene.step==='methods'&&s) {
    const g=b.scenarios[b.story.methods.greedy],n2=b.scenarios[b.story.methods.nsga2];
    return `For ${s.budget} sites, ${s.link_budget} link${s.link_budget===1?'':'s'} and α = ${s.alpha}, the case greedy misses most: NSGA-II is ${n2?.gap_pct?.toFixed(2)}% short of the optimum, greedy ${g?.gap_pct?.toFixed(2)}%. Over all ${sm.greedy.cases} cases: NSGA-II median ${sm.nsga2.median.toFixed(2)}% (worst ${sm.nsga2.worst.toFixed(2)}%), greedy median ${sm.greedy.median.toFixed(2)}% (worst ${sm.greedy.worst.toFixed(2)}%, ${sm.greedy.over_1pct} over 1%). Filled: the exact plan's sites; orange rings: sites greedy chose instead.`;
  }
  if(scene.step==='next') {
    const f=b.forecast;
    const free=f?`With the current building forecast a stable building has a ${accPct(f.chance.stable_built?.[f.years.length-1])} chance of being empty, stalled or vacant within ${f.years[f.years.length-1]} years; every building in a state gets the same chance, since the model has no building covariates. Rings mark the ${f.buildings.length} recorded buildings that could join the candidates.`
      :'Sites free up as buildings change state; a building forecast would say which may join the candidates.';
    return `${free}`;
  }
  if(scene.step==='explore') {
    if(scene.missing)return 'No search result for this combination; showing today.';
    const opt=s?.optimal?'proven optimal':s?.gap_pct!=null?`${s.gap_pct.toFixed(2)}% short of the exact optimum`:'';
    return s?`${accUsesText(v,scene.key)}${s.links.length?`, with ${accList(s.links.map(accLinkName))}`:''}. Objective ${s.objective}${opt?`, ${opt}`:''}.`:'';
  }
  return '';
}
function accStats(v,scene) {
  const b=v.b,now=scene.key==='baseline'?b.baseline.residents_within:b.scenarios[scene.key]?.residents_within;
  const before=scene.compare==='baseline'?b.baseline.residents_within:scene.compare?b.scenarios[scene.compare]?.residents_within:null;
  if(!now)return '';
  return `<div class="acc-stats">${b.uses.map((u)=>{
    const a=before?.[u],c=now[u],rgb=ACC_USES[u].colour.join(',');
    return `<div class="acc-stat${u===scene.use?' on':''}"><span class="l">${esc(ACC_USES[u].label)}</span>
      <span class="acc-bar" style="--acc-c:rgb(${rgb})">${a!=null?`<i class="b0" style="width:${(a*100).toFixed(1)}%"></i>`:''}<i class="b1" style="width:${(c*100).toFixed(1)}%"></i></span>
      <span class="v">${a!=null&&Math.abs(c-a)>=0.0005?`${accPct(a)} → <b>${accPct(c)}</b>`:`<b>${accPct(c)}</b>`}</span></div>`;}).join('')}</div>`;
}
function accSeg(name,items,current) {
  return `<div class="seg" role="group">${items.map(([value,label])=>`<button type="button" data-acc="${name}" data-value="${esc(String(value))}" aria-pressed="${String(value)===String(current)}">${esc(label)}</button>`).join('')}</div>`;
}
function accessPanel() {
  if(!DATA.access)return '';
  const views=DATA.access.views??[],v=accessView();
  let html=`<h2>Access plans</h2><div class="sub">${esc(views[access.view]??'')} · within a ${v?.b.threshold_m??500} m walk · after Zheng et al. (2023)</div>`;
  if(views.length>1)html+=`<div class="acc-row"><span class="k">Sites</span>${accSeg('view',views.map((l,i)=>[i,l]),access.view)}</div>`;
  if(access.error)return html+`<div class="note">Could not load the access plans: ${esc(access.error)}</div>`;
  if(!v)return html+'<div class="acc-loading" role="status">Loading the planning graph…</div>';
  const scene=accessScene();
  accessBuild(v,scene);
  html+=`<div class="acc-row"><span class="k">Service</span>${accSeg('service',v.b.uses.map(u=>[u,ACC_USES[u].label]),access.service)}</div>`;
  html+=`<div class="acc-steps" role="group" aria-label="Steps">${ACC_STEPS.map((s,i)=>`<button type="button" data-acc="step" data-value="${i}" title="${esc(s.title)}" ${access.step===i?'aria-current="step"':''}>${i+1}</button>`).join('')}<button type="button" data-acc="step" data-value="explore" ${access.step==='explore'?'aria-current="step"':''}>Explore</button></div>`;
  const title=access.step==='explore'?'Explore the results':ACC_STEPS[access.step].title;
  const kicker=access.step==='explore'?'Any budget, weighting and method':`Step ${access.step+1} of ${ACC_STEPS.length}`;
  html+=`<div class="acc-card"><div class="acc-kicker">${kicker}</div><h3>${esc(title)}</h3><p class="acc-text">${esc(accStepText(v,scene))}</p>${scene.step==='choices'||scene.step==='next'?'':accStats(v,scene)}`;
  if(scene.step==='next')html+=`<ul class="acc-next"><li><b>Plans staged over years.</b> Does a policy that adapts as sites free up beat re-solving each year? First measure the room: perfect foresight against re-solving.</li><li><b>Robustness.</b> Re-score the finalists under uncertain residents, missing services and a 400–600 m threshold.</li><li><b>Traffic.</b> Run plans whose links touch roads in SUMO.</li></ul>`;
  html+='</div>';
  if(access.step!=='explore')html+=`<div class="acc-nav"><button type="button" data-acc="prev" ${access.step===0?'disabled':''}>Back</button><button type="button" class="primary" data-acc="next">${access.step===ACC_STEPS.length-1?'Explore':'Next'}</button></div>`;
  else {
    const Ns=[...new Set(Object.values(v.b.scenarios).map(s=>s.budget))].sort((a,b)=>a-b);
    const Ks=[...new Set(Object.values(v.b.scenarios).map(s=>s.link_budget))].sort((a,b)=>a-b);
    const As=[...new Set(Object.values(v.b.scenarios).map(s=>s.alpha))].sort((a,b)=>a-b);
    html+=`<div class="acc-row"><span class="k">Sites</span>${accSeg('N',Ns.map(n=>[n,n]),access.N)}</div>
      <div class="acc-row"><span class="k">Links</span>${accSeg('K',Ks.map(k=>[k,k]),access.K)}</div>
      <div class="acc-row"><span class="k">α</span>${accSeg('alpha',As.map(a=>[a,a]),access.alpha)}</div>
      <div class="acc-row"><span class="k">Method</span>${accSeg('method',[['exact','Exact'],['nsga2','NSGA-II'],['greedy','Greedy']],access.method)}</div>
      <label class="legend-row toggle"><input type="checkbox" data-acc="search" ${access.search?'checked':''}><span></span><span>Every site's possible reach</span><span></span></label>`;
  }
  html+=`<div class="acc-row"><span class="k">Layout</span>${accSeg('layout',[['tiered','Graph above the city'],['map','On the ground']],access.layout)}</div>
    <div class="acc-nav"><button type="button" data-acc="replay">Replay</button><button type="button" data-acc="fit">Fit view</button></div>`;
  html+=accLegend(v,scene);
  const check=v.b.check;
  html+=`<div class="note">Estimates for comparing plans, not measured outcomes: residents from floor area (Beirut 001), services as mapped (operation not verified), walks along OSM ways, links as reviewed. Parks on the rail bed are optimistic${views.length>1?'; compare the other site set':''}. Every home's walk was checked against the scorer (within ${(check.max_abs_diff_m*1000).toFixed(2)} mm) over ${check.scenarios} scenarios.</div>`;
  return html;
}
function accLegend(v,scene) {
  const C=accColours(),sw=(rgb,ring)=>`<span class="sw dot" style="background:rgb(${rgb.slice(0,3).join(',')})${ring?';box-shadow:0 0 0 1.5px rgb(255,255,255),0 0 0 2.5px rgba(0,0,0,.25)':''}"></span>`;
  const U=ACC_USES[scene.use],rows=[[C.none,'Home out of reach'],[C.before,scene.compare?'Home already within reach':'Home within reach']];
  if(scene.compare)rows.push([C.gain,'Home the plan brings within reach'],[C.link,'…only across a new link',true]);
  const providers=[[accMix(U.colour,[255,255,255],.15),`Mapped ${U.one}`]];
  if(scene.key!=='baseline')providers.push([U.colour,'Plan site (colour of its use)',true]);
  if(scene.allSites){
    const kinds=[...new Set(v.b.sites.map(s=>s.kind))];
    for(const k of kinds)providers.push([ACC_SITE_COLOUR[k]??ACC_SITE_COLOUR.other,`Candidate ${ACC_SITE_KIND[k]?.[0]??k}`]);
  } else if(scene.search)providers.push([C.candidate,'Candidate site']);
  if(scene.ghosts&&v.b.forecast)providers.push([C.ghost,'Recorded building that could free up']);
  const line=(rgb,label,w)=>`<div class="legend-row"><span class="sw" style="height:${w}px;border-radius:2px;background:rgb(${rgb.slice(0,3).join(',')})"></span><span>${esc(label)}</span><span></span></div>`;
  return `<div class="acc-legend"><div class="legend-group">Homes</div>${rows.map(([c,l,r])=>`<div class="legend-row">${sw(c,r)}<span>${esc(l)}</span><span></span></div>`).join('')}
    <div class="legend-group">${access.layout==='tiered'?'Nodes above the city':'Nodes'}</div>${providers.map(([c,l,r])=>`<div class="legend-row">${sw(c,r)}<span>${esc(l)}</span><span></span></div>`).join('')}
    <div class="legend-group">Walking links</div>${line(C.link,'Opened in this plan',5)}${line(C.candidate,'Reviewed, closed',3)}${line(C.inert,'Changes no walk within 500 m',2)}</div>`;
}
function accessStatusLabel() { return 'Planning estimates · walks within 500 m'; }

// ---- interaction -------------------------------------------------------------------------------
function accessSetHash() {
  const p=new URLSearchParams(location.hash.slice(1));
  p.set('step',access.step==='explore'?'explore':String(access.step+1));
  history.replaceState(null,'',`#${p}`);
}
function accessGo(step) {
  access.step=step==='explore'?'explore':Math.max(0,Math.min(ACC_STEPS.length-1,step));
  accessSetHash();renderLegend();render();animate();
}
function accessPanelClick(e) {
  const el=e.target.closest('[data-acc]');
  if(!el||ui.lens!=='access')return;
  const kind=el.dataset.acc,value=el.dataset.value;
  if(kind==='next'){accessGo(access.step===ACC_STEPS.length-1?'explore':access.step+1);return;}
  if(kind==='prev'){accessGo(access.step-1);return;}
  if(kind==='step'){accessGo(value==='explore'?'explore':Number(value));return;}
  if(kind==='replay'){access.built=null;}
  else if(kind==='fit'){const v=accessView();if(v)accessFocus(v);return;}
  else if(kind==='search')access.search=el.checked;
  else if(kind==='view'){access.view=Number(value);access.built=null;accessActivate();return;}
  else if(kind==='service')access.service=value;
  else if(kind==='layout')access.layout=value;
  else if(kind==='N'||kind==='K')access[kind]=Number(value);
  else if(kind==='alpha')access.alpha=Number(value);
  else if(kind==='method')access.method=value;
  renderLegend();render();animate();
}
async function accessLoad(i) {
  if(access.data[i])return access.data[i];
  access.loading[i]??=fetch(`/v1/access/view/${i}`,{cache:'no-store'}).then((r)=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
    .then((b)=>{if(b.schema!=='dynacity-access-view/1')throw new Error('not an access view');access.data[i]=accessPrepare(b);return access.data[i];});
  return access.loading[i];
}
function accessActivate() {
  if(!DATA.access||!DATA.api)return;
  const p=new URLSearchParams(location.hash.slice(1)).get('step');
  if(p&&!access.focused)access.step=p==='explore'?'explore':Math.max(0,Math.min(ACC_STEPS.length-1,(Number(p)||1)-1));
  accessLoad(access.view).then((v)=>{
    access.error=null;
    if(v.b.uses.length&&!v.b.uses.includes(access.service))access.service=v.b.uses[0];
    if(!access.focused){access.focused=true;accessFocus(v);}
    renderLegend();render();animate();
  }).catch((error)=>{access.error=error.message;access.loading[access.view]=null;renderLegend();});
}
function accessTip(o) {
  const v=accessView();
  if(!v)return '';
  const b=v.b,thr=b.threshold_m,built=access.built,use=built?.scene.use??access.service,U=ACC_USES[use];
  if(o.accessKind==='home') {
    const s=built?.status[o.i]??0,p=built?.cur.provider[o.i]??-1,l=built?.cur.via[o.i]??-1;
    const who=p<0?'':p<v.P?(v.places[p].name||`a mapped ${U.one}`):`a plan ${U.one} (candidate site)`;
    const state=s===0?`no ${U.one} within ${thr} m`:s===1?`within ${thr} m of ${who}`:`brought within ${thr} m of ${who} by this plan`;
    return `<div class="h">Home</div><div class="s">About ${Math.round(o.r)} residents (estimated from floor area)</div><div class="s">${esc(U.label)}: ${esc(state)}${l>=0?` across ${esc(accLinkName(v.links[l].groupName))}`:''}.</div>`;
  }
  if(o.accessKind==='place') {
    const n=built?.serves.get(o.i)??0;
    return `<div class="h">${esc(o.name||`Mapped ${ACC_USES[o.use].one}`)}</div><div class="s">${esc(ACC_USES[o.use].label)} · ${esc(o.source)}</div><div class="s">Nearest within ${thr} m for ${n.toLocaleString()} homes here.</div><div class="s">Mapped location; current operation is not verified.</div>`;
  }
  if(o.accessKind==='site') {
    const kind=ACC_SITE_KIND[o.kind]?.[0]??o.kind,planned=built?.chosen.get(o.i),n=built?.serves.get(o.p)??0;
    const size=o.area_m2!=null?`${o.area_m2.toLocaleString()} m²`:o.length_m!=null?`${o.length_m} m of line`:'size not recorded';
    return `<div class="h">Candidate site · ${esc(kind)}</div><div class="s">${esc(size)}${o.state?` · BBED: ${esc(String(o.state).replace(/_/g,' '))}`:''}</div>
      <div class="s">${planned?`This plan: ${esc(ACC_USES[planned].one)}, nearest for ${n.toLocaleString()} homes`:'No new use in this plan'}${built?.greedyOnly.has(o.i)?' · greedy chose it instead':''}</div>
      <div class="s">Allowed: ${esc(o.allowedUses.map(u=>ACC_USES[u]?.one??u).join(', ')||'none')} · could serve ${b.search_space[o.i].length.toLocaleString()} homes within ${thr} m</div>`;
  }
  if(o.accessKind==='link') {
    const open=(built?.cur.s?.links??[]).includes(o.groupName),n=built?.hubs.get(o.i)??0;
    return `<div class="h">${esc(accLinkName(o.groupName))}${o.name?` · ${esc(o.name)}`:''}</div><div class="s">${o.kind==='railbed_walkway'?'Rail-bed walkway piece':'Crossing'} · ${o.length_m} m${o.saved_m?` · saves at least ${Math.round(o.saved_m)} m of walking`:''}</div>
      <div class="s">${open?`Open in this plan${n?`; ${n.toLocaleString()} homes' nearest ${esc(U.one)} is reached across it`:''}`:o.inert?`Changes no walk within ${thr} m`:'Reviewed candidate, closed in this plan'}</div>`;
  }
  if(o.accessKind==='ghost') {
    const f=b.forecast,c=f.chance[o.state]??[];
    return `<div class="h">Recorded building · ${esc(String(o.state).replace(/_/g,' '))}</div><div class="s">Chance of being empty, stalled or vacant: ${f.years.map((y,k)=>`${accPct(c[k])} in ${y} years`).join(', ')}.</div><div class="s">${esc(f.note)}</div>`;
  }
  return '';
}
function accessSource() {
  const v=accessView();
  if(!DATA.access||!v)return null;
  const r=v.b.record??{};
  return ['Corridor access plans',`Planning graph: homes, candidate sites, services and walking links (${v.b.label})`,
    `build-access-view${r.commit?` at ${String(r.commit).slice(0,7)}`:''}: Beirut 001 homes (residents estimated), BBED and OpenStreetMap sites and services, reviewed walking links; exact optimum and search results.`];
}
const accessBuildingColour=()=>(typeof isDark==='function'&&isDark()?[92,102,112]:[226,228,230]);
