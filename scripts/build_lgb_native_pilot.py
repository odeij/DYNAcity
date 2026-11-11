"""Build reversible LGB-only native preview variants, never a citywide apply.
Heights are sensitivity hypotheses, not measured/fitted values. Original source untouched.

Optional ESTIMATED stepped-top diagnostic (defaults 0 = single-tier envelope, identical to the previous export):
  --glass-top-drop M     the rounded glass / mullions / floored facade / C frames end M metres (snapped to a floor line) below the cream
                         roof/parapet top; the whole height stays fixed
  --upper-setback-x M    the taller cream upper mass begins M metres from the rounded-corner end (snapped to a bay line; 0 = full width)
  --upper-setback-y M    the upper mass is recessed M metres behind the facade plane
  --terrace-parapet-h M  ESTIMATED height of the white terrace upstand (0..1, default 1; 0 = none, terrace cap stays)
  --frame-zone-frac F    ESTIMATED share of the frontage with C frames (existing bounds 0.4..0.7, default 0.55)
  --frontage-wall N     diagnostic: anchor procedural frontage to a source wall; source-wall identity remains uncertain
  --frontage-extension M diagnostic: extend that frontage toward the source annex, bounded by the footprint
  --annex-height M      diagnostic: independent lower annex height, preserving source plan and original collision
Default export remains unchanged. Lower-annex interface faces are retained; both volumes are independently closed.
These numbers are NOT observed or fitted. Observed only: in photo 1288771293 the glazed volume ends below a taller cream upper mass.
"""
import argparse,json,hashlib,math
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union, triangulate
from shapely.geometry.polygon import orient
from shapely import set_precision
try:
    from shapely import constrained_delaunay_triangles
except ImportError:
    constrained_delaunay_triangles=None
from dynacity import reference_facade_geometry as G, corridor_reconstruction as C
from dynacity.geometry_checks import coplanar_overlaps

# Closed plan-clipped parts: each has ONE z range (its own tier), so it is re-extruded from its own plan with that range (never flattened
# across tiers). 'core' parts are clipped to the source core plan, parapets to the source footprint.
CORE_CLIPPED=('body_core','roof_slab','terrace_slab','upper_core')
FOOTPRINT_CLIPPED=('roof_parapet','terrace_parapet')

def plan(part):
    v=np.asarray(part['verts']);f=np.asarray(part['faces'])
    faces=[]
    for t in v[f]:
        poly=Polygon(t[:,:2])
        if poly.area>1e-8:faces.append(poly)
    return unary_union(faces)

def polygons(geom):
    if geom.is_empty:return []
    return [geom] if geom.geom_type=='Polygon' else [g for g in geom.geoms if g.geom_type=='Polygon' and g.area>1e-6]

def prism_with_holes(poly,z0,z1):
    """Closed solid with actual inner walls; never fill a facade recess to hide a geometry error."""
    poly=orient(poly,sign=1.0)
    triangles=(list(constrained_delaunay_triangles(poly).geoms) if constrained_delaunay_triangles is not None else [t for t in triangulate(poly) if poly.covers(t)])
    if unary_union(triangles).symmetric_difference(poly).area>1e-7:
        raise ValueError('cap triangulation does not cover polygon with holes')
    xy=[];lookup={}
    def index(point):
        key=tuple(float(x) for x in point[:2])
        if key not in lookup:lookup[key]=len(xy);xy.append(key)
        return lookup[key]
    cap=[]
    for t in triangles:
        ring=list(orient(t,sign=1.0).exterior.coords)[:-1]
        cap.append([index(q) for q in ring])
    boundaries=[]
    for ring in [poly.exterior,*poly.interiors]:
        boundaries.append([index(q) for q in list(ring.coords)[:-1]])
    n=len(xy);verts=np.array([[x,y,z] for z in [z0,z1] for x,y in xy]);faces=[]
    for a,b,c in cap:faces.extend([(c,b,a),(a+n,b+n,c+n)])
    for ring in boundaries:
        for a,b in zip(ring,ring[1:]+ring[:1]):faces.extend([(a,b,b+n),(a,b+n,a+n)])
    return verts,np.asarray(faces,dtype=np.int64)

def clipped_prisms(part,region,extension=None):
    """Re-extrude ``part`` from its own plan clipped to ``region``, keeping its own z range."""
    v=np.asarray(part['verts'],float)
    levels=sorted({round(float(z),4) for z in v[:,2]})
    if len(levels)!=2:raise ValueError('part is not a single-tier extrusion; refusing to flatten it: %s'%levels)
    z0,z1=float(v[:,2].min()),float(v[:,2].max())
    verts,faces,base=[],[],0
    clipped = plan(part).intersection(region)
    if extension is not None:clipped = set_precision(clipped.union(extension),1e-4)
    for poly in polygons(clipped):
        if poly.interiors or extension is not None:
            pv,pf=prism_with_holes(poly,z0,z1);verts.append(pv);faces.append(pf+base);base+=len(pv);continue
        ring=np.array(poly.exterior.coords[:-1])
        if C.polygon_area(ring)<0:ring=ring[::-1]
        pv,pf=C.polygon_prism(ring,z0,z1);verts.append(pv);faces.append(pf+base);base+=len(pv)
    if not verts:return np.zeros((0,3)),np.zeros((0,3),np.int64)
    return np.concatenate(verts),np.concatenate(faces)

def closed_and_outward(v,f):
    """Every edge shared by exactly two triangles of opposite direction, and a positive signed volume (outward winding)."""
    key={};idx=[key.setdefault(tuple(np.round(p,6)),len(key)) for p in v];e={}
    for t in f:
        i=[idx[x] for x in t]
        for a,b in ((i[0],i[1]),(i[1],i[2]),(i[2],i[0])):e[(a,b)]=e.get((a,b),0)+1
    manifold=all(c==1 and (b,a) in e for (a,b),c in e.items())
    vol=float(sum(np.dot(v[t[0]],np.cross(v[t[1]],v[t[2]])) for t in f)/6)
    return bool(manifold),vol

def build(out,height,drop=0.0,setback_x=0.0,setback_y=0.0,terrace_rim=None,frame_zone=None,frontage_wall=None,frontage_extension=0.0,annex_height=None,corner_slab_projection_m=0.0):
    root=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
    source=json.loads((root/'source-building.json').read_text())
    raw=np.load(root/'source-batch.npz');vp=raw['vertices'];v=np.stack([vp[:,0],-vp[:,2],vp[:,1]],axis=1);rec=source['record'];f=raw['faces'][rec['first_triangle']:rec['first_triangle']+rec['triangle_count']]
    ring=C.building_ring(v,f)
    u=np.array(source['walls'][0]['b'][:2])-source['walls'][0]['a'][:2];u/=np.linalg.norm(u);inside=np.array([-u[1],u[0]])
    q=np.column_stack([ring@u,ring@inside]);lo=q.min(0);hi=q.max(0);origin=u*lo[0]+inside*lo[1]
    width,depth=hi-lo;base=rec['local_bounds_m'][0][2]
    footprint=Polygon(q-lo)
    annex=None
    if frontage_wall is None and (frontage_extension or annex_height is not None):raise ValueError('extension/annex height requires a source-wall diagnostic')
    if annex_height is not None and not 0 < annex_height < height:raise ValueError('annex height must be positive and below diagnostic tower height')
    if frontage_wall is not None:
        if not 0 <= frontage_wall < len(source['walls']):raise ValueError('frontage wall index outside source walls')
        wall=source['walls'][frontage_wall]
        origin=np.array(wall['a'][:2],float)
        edge=np.array(wall['b'][:2],float)-origin
        width=float(np.linalg.norm(edge))
        if width<=3:raise ValueError('diagnostic frontage too short for rounded profile')
        u=edge/width;inside=np.array([-u[1],u[0]])
        q=np.column_stack([(ring-origin)@u,(ring-origin)@inside])
        if not 0 <= frontage_extension <= max(0.,-float(q[:,0].min())):raise ValueError('frontage extension outside source footprint range')
        origin=origin-u*frontage_extension;width+=frontage_extension
        q=np.column_stack([(ring-origin)@u,(ring-origin)@inside])
        depth=float(q[:,1].max())
        if depth<=3:raise ValueError('chosen wall does not face a sufficiently deep source interior')
        footprint=set_precision(Polygon(q),1e-4)
        annex=footprint.difference(set_precision(box(0,0,width,depth),1e-4))
    diag={'glass_top_drop_m':float(drop),'upper_setback_x_m':float(setback_x),'upper_setback_y_m':float(setback_y)}
    if not math.isfinite(corner_slab_projection_m) or corner_slab_projection_m < 0.0 or corner_slab_projection_m > 0.6:
        raise ValueError('--corner-slab-projection must be finite and in [0, 0.6] m')
    if corner_slab_projection_m > 0:
        diag['corner_slab_projection_m']=float(corner_slab_projection_m)
    params=dict(diag) if drop>0 or corner_slab_projection_m>0 else {}
    if terrace_rim is not None:
        if drop<=0:raise ValueError('--terrace-parapet-h only applies together with --glass-top-drop > 0')
        params['terrace_parapet_h_m']=float(terrace_rim)
    if frame_zone is not None:params['frame_zone_frac']=float(frame_zone)
    params=params or None
    if drop<=0 and (setback_x or setback_y):raise ValueError('the setbacks only apply together with --glass-top-drop > 0')
    m=G.mesh_for_profile('lgb_cream_redbrown_bands',float(width),height,depth_m=float(depth),params=params)
    bad=[w for w in m.provenance['warnings'] if any(k in w for k in ('terrace_parapet_h_m','frame_zone_frac'))]
    if bad:raise ValueError('override outside its estimated bounds: %s'%bad)
    core=plan(m.parts['body_core']).intersection(footprint)
    if annex is not None and annex_height is None:core=set_precision(core.union(annex),1e-4)
    if core.geom_type!='Polygon' or (core.interiors and frontage_wall is None):
        raise ValueError('unsupported disconnected or holed core: %s, holes=%s'%(core.geom_type, [Polygon(x).area for x in getattr(core,'interiors',[])]))
    M=G.placement_matrix([*origin,base],u);parts=[];materials={};checks={'closed_parts':{},'plan_outside_footprint_m2':{},'clipped_plan_area_m2':{}}
    for name,p in m.parts.items():
        if name in ('rear_generic','side_left','side_right'):continue
        verts=np.asarray(p['verts'],float);tri=np.asarray(p['faces'])
        if name in CORE_CLIPPED or name in FOOTPRINT_CLIPPED:
            region=core if name in CORE_CLIPPED else footprint
            before=plan(p).area
            verts,tri=clipped_prisms(p,region,annex if name in CORE_CLIPPED and annex_height is None else (Polygon() if frontage_wall is not None and name in CORE_CLIPPED else None))
            if not len(tri):continue
            checks['clipped_plan_area_m2'][name]={'producer_plan':round(before,3),'after_clip':round(plan({'verts':verts,'faces':tri}).area,3)}
            ok,vol=closed_and_outward(verts,tri)
            checks['closed_parts'][name]={'closed_manifold':ok,'signed_volume_m3':round(vol,2),'outward':vol>0}
            if not ok or vol<=0:raise ValueError('clipped part %s is not a closed outward solid'%name)
            checks['plan_outside_footprint_m2'][name]=round(plan({'verts':verts,'faces':tri}).difference(footprint.buffer(1e-6)).area,6)
        role=p['meta']['material'];rgb=np.asarray(G.PALETTE[role][0],float)/255
        linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
        spec={'base_color':[*linear.tolist(),1.0],'roughness':.8,'metallic':0.0}
        if role=='glass':spec.update(base_color=[.025,.045,.065,1.0],roughness=.08,blend='translucent',opacity=.55)
        materials[role]=spec
        placed=np.column_stack([verts,np.ones(len(verts))])@M.T
        parts.append({'name':name,'vertices':placed[:,:3],'triangles':tri,'material':role})
    # --- verification of the new upper geometry (local plan frame, before placement) -------------------------------------------------
    if annex is not None and annex_height is not None:
        av,af,offset=[],[],0
        checks['annex_closed_components']=[]
        for poly in polygons(annex):
            v0,f0=prism_with_holes(poly,0.,annex_height)
            ok,vol=closed_and_outward(v0,f0)
            if not ok or vol<=0:raise ValueError('lower annex component is not a closed outward solid')
            checks['annex_closed_components'].append({'closed_manifold':ok,'signed_volume_m3':vol})
            av.append(v0);af.append(f0+offset);offset+=len(v0)
        if av:
            vv=np.concatenate(av);ff=np.concatenate(af)
            placed=np.column_stack([vv,np.ones(len(vv))])@M.T
            parts.append({'name':'estimated_lower_annex','vertices':placed[:,:3],'triangles':ff,'material':'generic_wall'})
        checks['annex_missing_plan_m2']=0.0
    stepped=m.provenance['stepped_top']
    tier_names=[x for x in ('body_core','terrace_slab','upper_core','roof_slab','terrace_parapet','roof_parapet') if x in {p['name'] for p in parts}]
    local={p['name']:p for p in parts}
    inv=np.linalg.inv(M)
    Vs,Fs,Ls,b0=[],[],[],0
    for n in tier_names:
        lv=(np.column_stack([local[n]['vertices'],np.ones(len(local[n]['vertices']))])@inv.T)[:,:3]
        Vs.append(lv);Fs.append(local[n]['triangles']+b0);Ls+=[n]*len(local[n]['triangles']);b0+=len(lv)
    same=[h for h in coplanar_overlaps(np.concatenate(Vs),np.concatenate(Fs),labels=Ls) if h['same_orientation']]
    allz=np.concatenate([np.asarray(m.parts[n]['verts'],float)[:,2] for n in m.parts])
    frame_zmax=float(max(np.asarray(m.parts[n]['verts'],float)[:,2].max() for n in ('frame_slabs','frame_returns','curved_corner_glass','corner_mullions')))
    checks.update({'same_orientation_overlaps_among_tier_parts':len(same),'whole_height_unchanged':bool(abs(allz.max()-height)<1e-6 and abs(float(np.asarray(m.parts['roof_parapet']['verts'],float)[:,2].max())-height)<1e-6),
                   'glazed_and_frame_top_z_m':round(frame_zmax,3),'glass_top_z_m':stepped['glass_top_z_m'],'frames_and_glass_below_glass_top':bool(frame_zmax<=stepped['glass_top_z_m']+1e-6) if stepped['enabled'] else None,
                   'highest_cap_z_m':round(float(np.asarray(m.parts['roof_slab']['verts'],float)[:,2].max()),3),'all_vertices_within_height':bool(allz.min()>=-1e-6 and allz.max()<=height+1e-6)})
    if stepped['enabled'] and (same or not checks['frames_and_glass_below_glass_top'] or not checks['whole_height_unchanged']):
        raise ValueError('stepped-top checks failed: %s'%{k:checks[k] for k in ('same_orientation_overlaps_among_tier_parts','frames_and_glass_below_glass_top','whole_height_unchanged')})
    out.mkdir(parents=True,exist_ok=True)
    tag='' if drop<=0 else '-drop%g-sx%g-sy%g'%(drop,setback_x,setback_y)
    if terrace_rim is not None:tag+='-rim%g'%terrace_rim
    if frame_zone is not None:tag+='-zone%g'%frame_zone
    if frontage_wall is not None:tag+='-frontwall%d'%frontage_wall
    if frontage_extension:tag+='-ext%g'%frontage_extension
    if annex_height is not None:tag+='-annex%g'%annex_height
    if corner_slab_projection_m > 0:tag+='-cornerproj%g'%corner_slab_projection_m
    glb=out/f'lgb-height-{height:g}{tag}.glb';export=C.write_glb(glb,parts,materials,mesh_name='LGB_estimated_pilot')
    report={'source_id':rec['source_id'],'source_height_m':rec['metadata']['height_m'],'candidate_height_m':height,'height_status':'sensitivity hypothesis, NOT measured or fitted','height_native_collision':'original source collision retained; visual candidate not collision acceptance','source_footprint_area_m2':footprint.area,'clipped_core_area_m2':core.area,'placement_matrix':M.tolist(),'width_m':width,'depth_m':depth,'source_unchanged':True,'source_geometry_sha256':hashlib.sha256((root/'source-batch.npz').read_bytes()).hexdigest(),'geometry':str(glb.resolve()),'sha256':hashlib.sha256(glb.read_bytes()).hexdigest(),'materials':materials,'export':export,'producer_provenance':m.provenance,'part_summary':m.part_summary(),
            'diagnostic_parameters':{**diag,'optional_estimated_overrides':{k:{'requested':v,'effective':m.provenance['parameters'][k]['value'],'bounds':[m.provenance['parameters'][k]['lo'],m.provenance['parameters'][k]['hi']],'basis':'ESTIMATED, not observed or fitted; default %s'%d} for k,v,d in (('terrace_parapet_h_m',terrace_rim,1.0),('frame_zone_frac',frame_zone,0.55)) if v is not None},'status':'ESTIMATED diagnostics, not observed or fitted; observed only that the glazed volume ends below a taller cream upper mass (photo 1288771293)','effective':stepped,'whole_height_fixed_m':height},
            'verification':checks,
            'limits':['Footprint registration inferred, not surveyed','C-frame projections and hidden elevations estimated','Core/caps/parapets clipped to the source plan, each keeping its own tier z range; projecting facade and front wall panels are not clipped and need native clearance review (the source west face is stepped by 1.74 m, so a recessed upper front can still overhang the registered footprint on its southern run)','Terrace and upper roof plan are hidden/estimated','No native visual/collision/persistence acceptance from this export']}
    if frontage_wall is not None:
        report['frontage_diagnostic']={'wall_index':frontage_wall,'status':'estimated diagnostic; source wall identity/placement NOT accepted',
            'origin_enu_m':[*origin,base],'tangent_enu':u.tolist(),'width_m':width,'depth_m':depth,
            'annex_plan_area_m2':annex.area,'annex_missing_from_core_m2':annex.difference(core if annex_height is None else core.union(annex)).area,'frontage_extension_m':frontage_extension,'annex_height_m':annex_height,
            'annex_height_status':('generic annex extended through existing core/cap tiers' if annex_height is None else 'independent closed lower annex contacting tower; interface faces retained; height ESTIMATED'),
            'default_unchanged':True,'diagnostic_plan_grid_m':1e-4,'native_acceptance_required':'four matched views, two obliques, source identity and registration limitations, exact rollback before any scoped acceptance'}
        if annex_height is None and annex.difference(core).area>1e-6:raise ValueError('source annex lost from diagnostic core')
    rp=glb.with_suffix('.json');rp.write_text(json.dumps(report,indent=2));return str(rp)

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter);a.add_argument('output',type=Path);a.add_argument('--height',type=float,required=True)
    a.add_argument('--glass-top-drop',type=float,default=0.0);a.add_argument('--upper-setback-x',type=float,default=0.0);a.add_argument('--upper-setback-y',type=float,default=0.0)
    a.add_argument('--terrace-parapet-h',type=float,default=None,help='ESTIMATED height of the white terrace upstand, 0..1 m (default 1.0)');a.add_argument('--frame-zone-frac',type=float,default=None,help='ESTIMATED share of the frontage with C frames, 0.4..0.7 (default 0.55)')
    a.add_argument('--frontage-wall',type=int,default=None,help='Diagnostic source-wall anchored frontage; preserves source annex in core/cap tiers, unaccepted placement')
    a.add_argument('--frontage-extension',type=float,default=0.,help='Estimated extension of chosen frontage toward source annex, in metres')
    a.add_argument('--annex-height',type=float,default=None,help='Estimated separate lower-annex visual height; retains source footprint, original collision untouched')
    a.add_argument('--corner-slab-projection',type=float,default=0.0,help='Optional corner floor-band projection beyond curved glass (0..0.6 m, default 0 disables); estimates only')
    args=a.parse_args();print(build(args.output,args.height,args.glass_top_drop,args.upper_setback_x,args.upper_setback_y,args.terrace_parapet_h,args.frame_zone_frac,args.frontage_wall,args.frontage_extension,args.annex_height,args.corner_slab_projection))
