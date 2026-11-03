/* Close-view street edges. Dimensions are illustrative, anchored to mapped roads.
 * No median, crossing or accessibility feature is inferred from road class. */
let streetEdgeCache = null;
function streetEdgeGeometry(roads, centre, radius = 550) {
  const sx=111320*Math.cos(centre[1]*Math.PI/180),sy=111320;
  const eligible=new Set(['primary','secondary','tertiary','unclassified','residential','living_street']);
  const curbs=[],walks=[],seen=new Set(),junctionWidth=new Map();
  for(const r of roads)if((r.lvl??0)===0)for(const node of [r.u,r.v])if(node!=null)junctionWidth.set(node,Math.max(junctionWidth.get(node)??0,roadWidth(r)));
  const metric=p=>[(p[0]-centre[0])*sx,(p[1]-centre[1])*sy];
  const ll=(x,y,z)=>[centre[0]+x/sx,centre[1]+y/sy,z];
  for(let roadIndex=0;roadIndex<roads.length;roadIndex++) {
    const r=roads[roadIndex];
    if(!eligible.has(r.cls)||(r.lvl??0)!==0||r.sidewalk==='no'||r.sidewalk==='none'||r.path.length<2)continue;
    const points=r.path.map(metric),lengths=[0];
    for(let i=1;i<points.length;i++)lengths.push(lengths[i-1]+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));
    // Stop before junctions; a continuous extrusion through a junction blocks traffic.
    const startTrim=Math.max(5,(junctionWidth.get(r.u)??roadWidth(r))/2+2),endTrim=Math.max(5,(junctionWidth.get(r.v)??roadWidth(r))/2+2),total=lengths.at(-1);
    if(total<startTrim+endTrim+3)continue;
    const width=roadWidth(r),walkWidth=Math.min(2.4,pavementWidth(r));
    if(walkWidth<.5)continue;
    const first=r.path[0],last=r.path.at(-1),key=[`${first[0]},${first[1]}`,`${last[0]},${last[1]}`].sort().join('|');
    if(seen.has(key))continue;seen.add(key);
    for(let i=1;i<points.length;i++) {
      const a=points[i-1],b=points[i],distance=lengths[i]-lengths[i-1];
      if(distance<.05||lengths[i]<=startTrim||lengths[i-1]>=total-endTrim)continue;
      const lo=Math.max(0,(startTrim-lengths[i-1])/distance),hi=Math.min(1,(total-endTrim-lengths[i-1])/distance);
      const p=[a[0]+(b[0]-a[0])*lo,a[1]+(b[1]-a[1])*lo],q=[a[0]+(b[0]-a[0])*hi,a[1]+(b[1]-a[1])*hi];
      // Segment bounding boxes keep long ways crossing the view without loading all nearby roads.
      if(Math.min(p[0],q[0])>radius||Math.max(p[0],q[0])<-radius||Math.min(p[1],q[1])>radius||Math.max(p[1],q[1])<-radius)continue;
      const nx=-(b[1]-a[1])/distance,ny=(b[0]-a[0])/distance;
      const z=Math.max(0,r.path[i-1][2]??0,r.path[i][2]??0);
      if(z>.3)continue;
      for(const side of [-1,1]) {
        if(r.sidewalk==='left'&&side<0||r.sidewalk==='right'&&side>0)continue;
        const polygon=(inner,outer)=>[[p[0]+nx*inner*side,p[1]+ny*inner*side],[q[0]+nx*inner*side,q[1]+ny*inner*side],[q[0]+nx*outer*side,q[1]+ny*outer*side],[p[0]+nx*outer*side,p[1]+ny*outer*side]].map(v=>ll(v[0],v[1],z+.015));
        const source={roadIndex,illustrative:true};
        curbs.push({...source,polygon:polygon(width/2+.04,width/2+.22)});
        walks.push({...source,polygon:polygon(width/2+.22,width/2+walkWidth)});
      }
      if(curbs.length>=5000)return {curbs,walks};
    }
  }
  return {curbs,walks};
}
function streetEdgeDetailLayers() {
  if(!ui.traffic||ui.trafficStyle!=='street'||!DATA.traffic||ui.satellite)return [];
  const vp=!map&&deckgl?.getViewports()[0],zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  if(zoom<18||!centre)return [];
  if(!streetEdgeCache||streetEdgeCache.roads!==DATA.traffic.roads||metresBetween(centre,streetEdgeCache.centre)>100)
    streetEdgeCache={roads:DATA.traffic.roads,centre:centre.slice(),...streetEdgeGeometry(DATA.traffic.roads,centre)};
  const night=nightLevel(),dark=isDark();
  const common={extruded:true,wireframe:false,getPolygon:d=>d.polygon,material:{ambient:.65,diffuse:.5,shininess:1},pickable:false};
  return [new deck.SolidPolygonLayer({...common,id:'street-sidewalk-slabs',data:streetEdgeCache.walks,getElevation:.105,
    getFillColor:shade(dark?[100,101,98,255]:[198,194,183,255],night,.45),updateTriggers:{getFillColor:[night,dark]}}),
    new deck.SolidPolygonLayer({...common,id:'street-raised-curbs',data:streetEdgeCache.curbs,getElevation:.16,
    getFillColor:shade(dark?[132,133,127,255]:[220,216,203,255],night,.4),updateTriggers:{getFillColor:[night,dark]}})];
}

