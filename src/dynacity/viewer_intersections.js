// Paint follows SUMO incoming-lane geometry and controlled movements. Its
// dimensions and physical presence are illustrative, not a street survey.
let intersectionDetailCache = null;
function intersectionRoadProjection(point, road) {
  const origin=road.path[0], sx=111320*Math.cos(origin[1]*Math.PI/180), sy=111320;
  let best=null, along=0;
  for(let i=1;i<road.path.length;i++) {
    const a=road.path[i-1], b=road.path[i], dx=(b[0]-a[0])*sx,dy=(b[1]-a[1])*sy,len=Math.hypot(dx,dy);
    if(len<.001)continue;
    const px=(point[0]-a[0])*sx,py=(point[1]-a[1])*sy,t=Math.max(0,Math.min(1,(px*dx+py*dy)/(len*len)));
    const distance=Math.hypot(px-t*dx,py-t*dy);
    if(!best||distance<best.distance)best={distance,along:along+t*len,z:(a[2]??0)+t*((b[2]??0)-(a[2]??0))};
    along+=len;
  }
  return best&&{...best,total:along};
}
function intersectionApproachMarkings(signals, roads) {
  const approaches=new Map(),marks=[];
  for(const s of signals) {
    if(s.length<11||![s[2],s[3],s[6],s[9]].every(Number.isFinite))continue;
    const r=roads[s[9]];
    if(!r||r.lvl<0||!r.path?.length)continue;
    const directions=String(s[10]).toLowerCase().replace(/[^slr]/g,'');
    if(!directions)continue;
    // A lane can have several controlled links. Merge their movements into
    // one shared stem instead of piling arrows on the same lane.
    const key=`${s[9]}:${s[2].toFixed(7)}:${s[3].toFixed(7)}:${Math.round(s[6])}`;
    if(!approaches.has(key))approaches.set(key,{s,r,directions:new Set()});
    for(const d of directions)approaches.get(key).directions.add(d);
  }
  for(const {s,r,directions} of [...approaches.values()].slice(0,300)) {
    const angle=s[6]*Math.PI/180,fx=Math.cos(angle),fy=Math.sin(angle);
    const sx=111320*Math.cos(s[3]*Math.PI/180),sy=111320;
    // x points to the driver's right, y along the incoming travel direction.
    const ll=(x,y)=>[s[2]+(fy*x+fx*(y-8))/sx,s[3]+(-fx*x+fy*(y-8))/sy];
    const shapes=[[[0,-2],[0,.1]]];
    if(directions.has('s'))shapes.push([[0,0],[0,2]], [[-.48,1.4],[0,2],[.48,1.4]]);
    if(directions.has('l'))shapes.push([[0,0],[-.32,.7],[-1.05,1.1]], [[-.53,1.18],[-1.05,1.1],[-.95,.54]]);
    if(directions.has('r'))shapes.push([[0,0],[.32,.7],[1.05,1.1]], [[.53,1.18],[1.05,1.1],[.95,.54]]);
    const paths=shapes.map(shape=>shape.map(([x,y])=>{
      const p=ll(x,y),projection=intersectionRoadProjection(p,r);
      // Reject a complete arrow if it would cross the sidewalk or extend
      // beyond this approach. Curved and short lanes may have no safe room.
      if(!projection||projection.distance>roadWidth(r)/2-.18||projection.along<.3||projection.total-projection.along<.3)return null;
      return [...p,projection.z+.065];
    }));
    if(paths.some(path=>path.some(p=>!p)))continue;
    for(const path of paths)marks.push({path,road:s[9],directions:[...directions].sort().join(''),illustrative:true});
  }
  return marks;
}
function intersectionDetailLayers() {
  if(!ui.traffic||ui.trafficStyle!=='street'||!DATA.traffic)return [];
  const vp=!map&&deckgl?.getViewports()[0], zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  if(zoom<18||!centre)return [];
  const signals=trafficFrames[0]?.signals??null;
  // State changes do not invalidate paint. Geometry only rebuilds after a
  // new lane layout appears or the camera travels a meaningful distance.
  // Replay samples are immutable arrays. Scan a sample's lane metadata once,
  // rather than rebuilding a city-wide signature on every animation frame.
  const signature=intersectionDetailCache?.signals===signals?intersectionDetailCache.signature:
    (signals??[]).map(s=>`${s[0]}:${s[1]}:${s[2]}:${s[3]}:${s[6]}:${s[9]}:${s[10]}`).join('|');
  if(!intersectionDetailCache||intersectionDetailCache.signature!==signature||intersectionDetailCache.roads!==DATA.traffic.roads||metresBetween(centre,intersectionDetailCache.centre)>60) {
    const nearby=(signals??[]).filter(s=>metresBetween([s[2],s[3]],centre)<450);
    intersectionDetailCache={signals,signature,roads:DATA.traffic.roads,centre:centre.slice(),data:intersectionApproachMarkings(nearby,DATA.traffic.roads)};
  } else intersectionDetailCache.signals=signals;
  if(!intersectionDetailCache.data.length)return [];
  const night=nightLevel();
  return [new deck.PathLayer({id:'intersection-direction-paint',data:intersectionDetailCache.data,
    getPath:d=>d.path,getColor:shade([238,235,218,230],night,.5),getWidth:.18,widthUnits:'meters',
    widthMinPixels:.5,jointRounded:true,capRounded:false,pickable:false,parameters:SOFT,updateTriggers:{getColor:night}})];
}
