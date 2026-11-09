/* Night windows: warm lit window rows drawn on building WALLS by a deck.gl
   LayerExtension (shader injection into SolidPolygonLayer's side shader).
   Storeys are ~3 m and bays ~3.6 m, fitted to each wall so rows end cleanly at corners.
   Which windows glow is a deterministic hash of the building id: decorative, not occupancy.
   Hooks used (checked in deck.gl@9.4.0 dist): vs:#decl, vs:#main-end, fs:#decl, fs:DECKGL_FILTER_COLOR. */
const NIGHT_DARK_STATES=new Set(['vacant_or_evicted','demolished','empty_or_parking','stalled_or_cancelled']);
// FNV-1a of the building id, as a unit float: the same building always gets the same windows.
function nightSeed(id) {
  let h=2166136261;
  for(const ch of String(id)){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)>>>0;}
  return (h>>>8)/16777216;
}
// [seed, lit-capable]: recorded vacant, demolished, empty-lot and stalled buildings never get windows.
function nightInfo(state,id) {return [nightSeed(id),NIGHT_DARK_STATES.has(state)?0:1];}
const NIGHT_WINDOW_UNIFORMS='layout(std140) uniform nightWinUniforms {\n  float level;\n} nightWin;\n';
const NIGHT_WINDOW_MODULE={
  name:'nightWin',vs:NIGHT_WINDOW_UNIFORMS,fs:NIGHT_WINDOW_UNIFORMS,uniformTypes:{level:'f32'},
  getUniforms:(o)=>({level:o?.level??0}),
  inject:{
    'vs:#decl':`
in vec2 nightInfo;
out vec2 vNwA;
flat out vec4 vNwB;
`,
    // Wall varyings: along-wall metres and height above the base (interpolated); wall length, building
    // height, seed and lit flag (flat). Walls only: roofs keep lit flag 0.
    'vs:#main-end':`
vNwA = vec2(0.0);
vNwB = vec4(0.0);
#ifdef IS_SIDE_VERTEX
{
  vec3 nwD = (nextVertexPositions - vertexPositions) + (nextVertexPositions64Low - vertexPositions64Low);
  vec2 nwM = vec2(nwD.x * 111320.0 * cos(radians(vertexPositions.y)), nwD.y * 110574.0);
  float nwLen = length(nwM);
  vNwA = vec2(positions.x * nwLen, elevations * positions.y);
  vNwB = vec4(nwLen, elevations, nightInfo.x, nightInfo.y);
}
#endif
`,
    'fs:#decl':`
in vec2 vNwA;
flat in vec4 vNwB;
float nwHash(vec2 p) {
  p = fract(p * vec2(443.897, 441.423));
  p += dot(p, p.yx + 19.19);
  return fract((p.x + p.y) * p.x);
}
`,
    'fs:DECKGL_FILTER_COLOR':`
if (nightWin.level > 0.0 && vNwB.w > 0.5 && vNwB.x > 1.8 && vNwB.y > 2.5) {
  float nwLen = vNwB.x;
  float nwH = vNwB.y;
  float nwNx = max(1.0, floor(nwLen / 3.6 + 0.5));
  float nwNy = max(1.0, floor(nwH / 3.0 + 0.5));
  vec2 nwCuv = vNwA / vec2(nwLen / nwNx, nwH / nwNy);
  vec2 nwId = min(floor(nwCuv), vec2(nwNx - 1.0, nwNy - 1.0));
  vec2 nwF = nwCuv - nwId;
  float nwSeed = vNwB.z;
  vec2 nwCell = vec2(nwLen / nwNx, nwH / nwNy);
  float nwWx = clamp(1.7 / nwCell.x, 0.25, 0.7);
  float nwWy = clamp(1.5 / nwCell.y, 0.25, 0.6);
  vec2 nwLo = vec2(0.5 - nwWx * 0.5, 0.28);
  vec2 nwHi = nwLo + vec2(nwWx, nwWy);
  vec2 nwE = max(fwidth(nwCuv), vec2(0.03));
  vec2 nwQ = smoothstep(nwLo - nwE * 0.5, nwLo + nwE * 0.5, nwF) * (1.0 - smoothstep(nwHi - nwE * 0.5, nwHi + nwE * 0.5, nwF));
  float nwMask = nwQ.x * nwQ.y;
  vec2 nwG = vec2(0.12, 0.10);
  vec2 nwQh = smoothstep(nwLo - nwG - nwE, nwLo, nwF) * (1.0 - smoothstep(nwHi, nwHi + nwG + nwE, nwF));
  float nwHalo = nwQh.x * nwQh.y * (1.0 - nwMask);
  // Fraction of lit windows grows through dusk, so the city fills with light gradually.
  float nwLf = mix(0.12, 0.55, nwHash(vec2(nwSeed * 91.7, 4.2)));
  float nwOn = nwLf * smoothstep(0.0, 0.6, nightWin.level);
  float nwCellHash = nwHash(nwId + vec2(nwSeed * 57.3 + 11.0, nwSeed * 29.1 + 3.0));
  float nwLit = step(nwCellHash, nwOn);
  // Windows smaller than ~2-5 px blend to their average coverage, so distant walls do not shimmer.
  float nwPx = 1.0 / max(max(fwidth(nwCuv).x, fwidth(nwCuv).y), 1e-4);
  float nwFade = smoothstep(2.0, 5.0, nwPx);
  float nwLitAmt = mix(nwWx * nwWy * nwOn, nwMask * nwLit, nwFade);
  float nwGlass = mix(nwWx * nwWy * (1.0 - nwOn), nwMask * (1.0 - nwLit), nwFade);
  vec3 nwTint = mix(vec3(1.0, 0.62, 0.26), vec3(1.0, 0.84, 0.58), nwHash(nwId + vec2(7.7, nwSeed * 13.0)));
  float nwBright = 0.7 + 0.5 * nwHash(nwId + vec2(3.1, nwSeed * 5.0));
  color.rgb *= 1.0 - 0.2 * nwGlass * nightWin.level;
  color.rgb = mix(color.rgb, nwTint * nwBright, clamp(nwLitAmt * 0.95, 0.0, 1.0));
  color.rgb += nwTint * nwHalo * nwOn * nwFade * 0.16;
}
`,
  },
};
const NightWindows=typeof deck!=='undefined'&&deck.LayerExtension?class NightWindows extends deck.LayerExtension {
  getShaders() {return {modules:[NIGHT_WINDOW_MODULE]};}
  initializeState() {
    this.getAttributeManager()?.add({nightInfo:{size:2,type:'float32',stepMode:'dynamic',accessor:'getNightInfo'}});
  }
  draw() {this.setShaderModuleProps({nightWin:{level:this.props.nightLevel||0}});}
}:null;
if(NightWindows){
  NightWindows.extensionName='NightWindows';
  NightWindows.defaultProps={getNightInfo:{type:'accessor',value:[0,0]},nightLevel:{type:'number',value:0,min:0,max:1}};
}
const NIGHT_WINDOWS=NightWindows?new NightWindows():null;
// Spread into a SolidPolygonLayer: `info(d)` returns nightInfo(state,id) for that object.
// `extensions` are the layer's own (e.g. the distance haze); windows go first so the
// haze also fades distant lit windows.
function nightWindowProps(info, extensions = []) {
  return NIGHT_WINDOWS?{extensions:[NIGHT_WINDOWS,...extensions],getNightInfo:info,nightLevel:appliedNight}:{extensions};
}
