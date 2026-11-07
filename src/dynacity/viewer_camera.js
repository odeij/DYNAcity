/* Camera-dependent detail and near-camera occlusion; source geometry is intact. */
let viewGeometryKey='',viewGeometryTime=-Infinity,viewRefreshFrame=0;
let occlusionCache=null,occlusionModelSource=null,occlusionModelIndex=new Map();
const EMPTY_CAMERA_OCCLUSION={model:new Set(),bbed:new Set(),osm:new Set()};
function currentSceneCamera() {
  if(map)return {centre:map.getCenter().toArray(),zoom:map.getZoom(),pitch:map.getPitch?.()??0,bearing:map.getBearing?.()??0,
    height:map.getCanvas?.().clientHeight||600};
  const vp=deckgl?.getViewports?.()[0];
  return vp?{centre:[vp.longitude,vp.latitude],zoom:vp.zoom,pitch:vp.pitch??0,bearing:vp.bearing??0,height:vp.height||600}:null;
}
function sceneCameraKey() {
  const c=currentSceneCamera();
  return c?`${c.centre.map(v=>v.toFixed(5))}:${c.zoom.toFixed(2)}:${c.pitch.toFixed(1)}:${c.bearing.toFixed(1)}:${c.height}`:'';
}
function refreshCameraGeometry(now,force=false) {
  const key=sceneCameraKey();
  if(key===viewGeometryKey||(!force&&now-viewGeometryTime<90))return;
  viewGeometryKey=key;viewGeometryTime=now;baseLayers=layers();
}
function cameraChanged() {
  if(viewRefreshFrame)return;
  viewRefreshFrame=requestAnimationFrame(()=>{viewRefreshFrame=0;drawFx();});
}
function cameraMoveFinished() {
  const camera=currentSceneCamera();
  if(camera&&typeof history!=='undefined'&&typeof location!=='undefined') {
    const params=new URLSearchParams(location.hash.slice(1));
    params.set('lon',camera.centre[0].toFixed(7));params.set('lat',camera.centre[1].toFixed(7));
    params.set('zoom',camera.zoom.toFixed(2));params.set('pitch',camera.pitch.toFixed(1));params.set('bearing',camera.bearing.toFixed(1));
    history.replaceState(null,'',`#${params}`);
  }
  refreshCameraGeometry(performance.now(),true);cameraChanged();
}
function estimatedCameraEye(camera) {
  // MapLibre's default vertical field of view gives camera distance = 1.5 ×
  // canvas height. Work in local metres, matching the flat map used here.
  // MapLibre/deck use a 512-pixel world at zoom 0 (not 256-pixel slippy tiles).
  const [lon,lat]=camera.centre,mpp=78271.51696*Math.cos(lat*Math.PI/180)/2**camera.zoom;
  const distance=1.5*camera.height*mpp,pitch=camera.pitch*Math.PI/180,bearing=camera.bearing*Math.PI/180;
  const horizontal=distance*Math.sin(pitch);
  return [lon-Math.sin(bearing)*horizontal/(111320*Math.cos(lat*Math.PI/180)),
    lat-Math.cos(bearing)*horizontal/111320,distance*Math.cos(pitch)];
}
function buildingBlocksCamera(rings,height,eye,target) {
  const a=eye,b=target,dx=b[0]-a[0],dy=b[1]-a[1],cuts=[0,.9];
  for(const ring of rings)for(let i=0;i<ring.length;i++) {
    const p=ring[i],q=ring[(i+1)%ring.length],ex=q[0]-p[0],ey=q[1]-p[1],den=dx*ey-dy*ex;
    if(Math.abs(den)<1e-18)continue;
    const px=p[0]-a[0],py=p[1]-a[1],t=(px*ey-py*ex)/den,u=(px*dy-py*dx)/den;
    if(t>0&&t<.9&&u>=0&&u<=1)cuts.push(t);
  }
  cuts.sort((x,y)=>x-y);
  for(let i=1;i<cuts.length;i++) {
    const lo=cuts[i-1],hi=cuts[i],t=(lo+hi)/2,p=[a[0]+dx*t,a[1]+dy*t];
    if(height>eye[2]*(1-hi)-.5&&inRing(p,rings[0])&&!rings.slice(1).some(r=>inRing(p,r)))return true;
  }
  return false;
}
function cameraOcclusion() {
  const camera=currentSceneCamera();
  if(!camera||camera.zoom<18)return EMPTY_CAMERA_OCCLUSION;
  const source=beirutOverlay.active?beirutOverlay.polygons:null,key=sceneCameraKey();
  if(occlusionCache?.key===key&&occlusionCache.source===source)return occlusionCache;
  const eye=estimatedCameraEye(camera),target=camera.centre;
  // The eye is a height above the ground under the view centre; a building's top is its height above its own
  // ground. On relief, bring both to one level: the building's ground relative to the centre's.
  const centreGround=groundZ(target[0],target[1]),rise=rings=>ground.grid?Math.min(...rings[0].map(p=>groundZ(p[0],p[1])))-centreGround:0;
  const rayBox=[Math.min(eye[0],target[0]),Math.min(eye[1],target[1]),Math.max(eye[0],target[0]),Math.max(eye[1],target[1])];
  const crosses=box=>box[0]<=rayBox[2]&&box[2]>=rayBox[0]&&box[1]<=rayBox[3]&&box[3]>=rayBox[1];
  const result={key,source,eye,model:new Set(),bbed:new Set(),osm:new Set()};
  if(source) {
    if(occlusionModelSource!==source) {
      occlusionModelSource=source;occlusionModelIndex=new Map();
      for(const p of source)for(const cell of cellKeys(ringBox(p.rings[0]))) {
        if(!occlusionModelIndex.has(cell))occlusionModelIndex.set(cell,[]);
        occlusionModelIndex.get(cell).push(p);
      }
    }
    const candidates=new Set(cellKeys(rayBox).flatMap(k=>occlusionModelIndex.get(k)??[]));
    for(const p of candidates)
      if(buildingBlocksCamera(p.rings,beirutOverlay.data.buildings[p.beirutModel].height_m+rise(p.rings),eye,target))result.model.add(p.beirutModel);
  } else {
    for(const b of DATA.buildings??[])if(b.rings.some(r=>crosses(ringBox(r))&&buildingBlocksCamera([r],elevation(b)+rise([r]),eye,target)))result.bbed.add(b.id);
  }
  for(const f of osmContextEntries())if(f.properties.kind==='osm_building'&&crosses(f.contextBox)&&buildingBlocksCamera(f.geometry.coordinates,(f.properties.height_m??6)+rise(f.geometry.coordinates),eye,target))result.osm.add(f.properties.id);
  return occlusionCache=result;
}
function cameraSafeColour(rgb,group,id,state=cameraOcclusion()) {
  const blocked=state[group].has(id);
  return [...rgb.slice(0,3),blocked?Math.min(rgb[3]??255,35):(rgb[3]??255)];
}
