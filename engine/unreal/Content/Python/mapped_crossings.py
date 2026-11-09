"""Rebuild only mapped painted crossings; preserve other street layers."""
import json
import math
from collections import defaultdict
from pathlib import Path
import re
import shutil


def is_painted(point):
    tags = point.get('tags', {})
    style = tags.get('crossing', '')
    markings = tags.get('crossing:markings', '')
    if point.get('kind') != 'crossing' or markings == 'no' or style in {'no', 'unmarked', 'informal'}:
        return False
    return markings not in {'', 'no'} or style in {'zebra', 'marked', 'uncontrolled', 'traffic_signals'}


def apply(output):
    import unreal
    import city_world, explore_city
    world = city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before updating crossings')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    root = city_world._root() / 'Saved/DynaCity'
    source = Path(json.loads((root/'street-layer.json').read_text())['manifest'])
    data = json.loads(source.read_text())
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text())['origin_utm_m']:
        raise ValueError('Crossing and city origins differ')
    points = [p for p in data['points'] if is_painted(p)]
    if not points or len({p['id'] for p in points}) != len(points):
        raise ValueError('Missing or duplicate mapped crossing IDs')
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs
        if 'crossing_marking' in str(d.label) or str(d.label).startswith('roads_')])
    actors = [a for a in city_world._actors() if 'street_kind:crossing_marking' in {str(t) for t in a.tags}]
    bylabel = {a.get_actor_label(): a for a in actors}
    if len(bylabel) != len(actors):
        raise ValueError('Duplicate crossing groups need review')
    props = json.loads((root/'street-prop-import.json').read_text())['crossing_marking']
    mesh = unreal.load_asset(props['asset'])
    if not mesh: raise RuntimeError('Missing crossing mesh')
    width = (props['bounds_cm']['max'][1]-props['bounds_cm']['min'][1])/100
    backups = []
    for a in actors:
        c = a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        package_name = a.get_outermost().get_path_name()
        if package_name.startswith('/Game/'):
            package_file = city_world._root()/'Content'/(package_name[6:]+'.uasset')
            if package_file.is_file():
                target = output/'package-backup'/(package_name[6:]+'.uasset')
                target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists(): shutil.copy2(package_file,target)
        backups.append({'actor': a.get_path_name(), 'label': a.get_actor_label(),
            'transforms': [str(c.get_instance_transform(i, world_space=True)) for i in range(c.get_instance_count())]})
    (output/'before.json').write_text(json.dumps(backups, indent=2))
    index_path = root/'street-instances.json'
    if not (output/'street-instances.before.json').exists():
        (output/'street-instances.before.json').write_bytes(index_path.read_bytes())
    _, east, north, _ = explore_city._basis()
    rows = []; plans = defaultdict(list)
    # Resolve every transform before mutating any existing group.
    for p in points:
        w = explore_city._world(p['position_local_m'])
        heading = math.radians(p.get('road_heading_degrees') or p.get('heading_degrees') or 0)
        direction = east*math.cos(heading)+north*math.sin(heading)
        hit = unreal.SystemLibrary.line_trace_single(world, w+unreal.Vector(0,0,1000),
            w-unreal.Vector(0,0,1500), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [], unreal.DrawDebugTrace.NONE, True)
        normal = unreal.Vector(0,0,1); surface = 'source terrain + existing road lift (unverified fallback)'
        if hit:
            h = hit.to_dict(); actor = h.get('hit_actor'); label = actor.get_actor_label() if actor else ''
            if label.startswith('roads_') and h['impact_normal'].z > .7:
                w = h['impact_point']; normal = h['impact_normal']; surface = label
            else: w += unreal.Vector(0,0,12)
        else: w += unreal.Vector(0,0,12)
        w += normal*3
        rotation = unreal.MathLibrary.make_rot_from_xz(direction, normal)
        scale = unreal.Vector(1,max(.4,min(4,(p.get('road_width_m') or 6)/width)),1)
        transform = unreal.Transform(location=w, rotation=rotation, scale=scale)
        plans[p.get('production_area','Unassigned')].append((p, transform, surface))
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    cls = unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    touched = []; groups = []
    for area, items in sorted(plans.items()):
        label = 'Street_'+re.sub('[^A-Za-z0-9]+','_',area)+'_crossing_marking'
        actor = bylabel.get(label)
        if actor is None:
            actor = subsystem.spawn_actor_from_class(cls,unreal.Vector())
            actor.set_actor_label(label); actor.tags = ['DYNACITY_STREET_CONTEXT','street_kind:crossing_marking']
            actor.set_folder_path('DynaCity/Mapped streets/'+area)
            actor.set_editor_property('is_spatially_loaded',True)
        actor.modify(); actor.set_editor_property('layer_kind','crossing_marking')
        actor.set_editor_property('production_area',area); actor.set_editor_property('source_manifest',str(source))
        actor.set_street_mesh(mesh); actor.clear_street_instances(); actor.set_street_collision(False)
        actor.set_street_cull_distance(25000,70000)
        actor.set_actor_hidden_in_game(False); actor.set_is_temporarily_hidden_in_editor(False)
        component = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        component.modify(); component.set_visibility(True); component.set_hidden_in_game(False)
        for p, transform, surface in items:
            instance = actor.add_street_instance(transform,p.get('instance_id',p['id']))
            if instance < 0: raise RuntimeError('Crossing placement failed')
            rows.append({'actor':actor.get_path_name(),'instance':instance,'source_id':p['id'],
                'instance_id':p.get('instance_id',p['id']),'kind':'crossing','surface':surface,
                'world_cm':[transform.translation.x,transform.translation.y,transform.translation.z]})
        if actor.get_street_instance_count() != len(items): raise RuntimeError('Crossing count mismatch')
        touched.append(actor)
        groups.append({'actor':actor.get_path_name(),'kind':'crossing_marking','area':area,'instances':len(items)})
    if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched}),True):
        raise RuntimeError('Crossing actor package save failed')
    index = json.loads(index_path.read_text())
    index['groups'] = [g for g in index['groups'] if g['kind'] != 'crossing_marking']+groups
    index['instances'] = [r for r in index['instances'] if r['kind'] != 'crossing']+rows
    index_path.write_text(json.dumps(index,indent=2))
    report = {'source_manifest':str(source),'mapped_features':sum(p['kind']=='crossing' for p in data['points']),
        'painted_instances':len(rows),'groups':groups,'instances':rows,
        'road_surface_verified':sum(r['surface'].startswith('roads_') for r in rows),
        'limits':['OSM coverage is incomplete; historical mapping, not a present-day survey.',
                  'Zebra pattern is illustrative unless explicitly mapped.',
                  'Fallback heights need review where no loaded road collision was found.'],
        'saved':True}
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


# ---- Clearance fit: keep every painted stripe set between the existing kerbs (scoped, per instance) ----
# The marking's across-road width is road_width_m (from the source road), so on narrow roads its outer edges lie on the kerb face
# (clearance to the kerb is 0-3 cm) and slanted kerb faces / corner wedges then cut the outer stripe. The fit uses the real collision:
# at several positions along the 3 m footprint it walks across the road from the centre until the surface stops being the road asphalt
# (another actor's material, no hit, or a vertical step), intersects those free intervals, keeps a margin and then either only slides
# the footprint (width unchanged) or narrows it to the room that exists. The whole 6-stripe mesh is retained (only scale Y / position change).
CLEARANCE_MARGIN_M = 0.12
MIN_FIT_WIDTH_M = 1.8
MESH_LENGTH_M, MESH_WIDTH_M = 3.0, 5.48
ALONG_SAMPLES_M = tuple(-1.5 + 0.25 * i for i in range(13))
ACROSS_STEP_M = 0.04
STEP_JUMP_M = 0.05
SEARCH_EXTRA_M = 1.0
LIFT_CM = 3.0
FIT_TOLERANCE_M = 0.03   # about one trace step: smaller offsets or width differences are measurement noise


def common_interval(rows):
    """Intersection of the per-row free intervals (lo <= 0 <= hi, metres along the footprint's local +Y); None if any row is blocked."""
    if not rows or any(r is None for r in rows):
        return None
    lo, hi = max(r[0] for r in rows), min(r[1] for r in rows)
    return (lo, hi) if hi > lo else None


def fit_to_clearance(lo, hi, width, margin=CLEARANCE_MARGIN_M, min_width=MIN_FIT_WIDTH_M):
    """Fit a footprint of `width` (centred on 0) into the free interval [lo, hi].
    status: unchanged (already inside the margin), shifted (slid, same width), narrowed (room < width: width = room, centred), unfit."""
    usable_lo, usable_hi = lo + margin, hi - margin
    room = usable_hi - usable_lo
    if room < min_width:
        return {'status': 'unfit', 'offset_m': 0.0, 'width_m': width, 'room_m': room}
    if room >= width - FIT_TOLERANCE_M:
        offset = min(max(0.0, usable_lo + width / 2), usable_hi - width / 2) if room >= width else 0.0
        if abs(offset) <= FIT_TOLERANCE_M:   # within the 4 cm trace resolution: already fitted, do not nudge again
            return {'status': 'unchanged', 'offset_m': 0.0, 'width_m': width, 'room_m': room}
        return {'status': 'shifted', 'offset_m': offset, 'width_m': width, 'room_m': room}
    return {'status': 'narrowed', 'offset_m': (usable_lo + usable_hi) / 2, 'width_m': room, 'room_m': room}


def _unit_transform(location, rotation):
    import unreal
    return unreal.Transform(location=location, rotation=rotation, scale=unreal.Vector(1, 1, 1))


def _ground(world, x, y, z):
    import unreal
    hit = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x, y, z+100), unreal.Vector(x, y, z-150),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [], unreal.DrawDebugTrace.NONE, True)
    if not hit:
        return None
    h = hit.to_dict(); actor = h.get('hit_actor')
    n = h['impact_normal']
    return {'label': actor.get_actor_label() if actor else '', 'z': h['impact_point'].z, 'nz': n.z, 'normal': (n.x, n.y, n.z)}


def _is_road(hit):
    return bool(hit) and hit['label'].startswith('roads_') and hit['label'].endswith('_material_1') and hit['nz'] > .7


def trace_clearance(world, location, rotation, half_width_m, ground_z):
    """Per along-sample free interval (m, local +Y, from the centre) on road asphalt. Edge = last good sample (conservative)."""
    import unreal
    t = _unit_transform(location, rotation)
    extent = half_width_m + SEARCH_EXTRA_M
    rows = []
    for lx in ALONG_SAMPLES_M:
        edges = []
        for sign in (-1, 1):
            last_good, prev_z, stop = 0.0, None, 'open'
            for k in range(0, int(extent / ACROSS_STEP_M) + 1):
                ly = sign * k * ACROSS_STEP_M
                w = unreal.MathLibrary.transform_location(t, unreal.Vector(lx*100, ly*100, 0))
                hit = _ground(world, w.x, w.y, ground_z)
                if not _is_road(hit) or (prev_z is not None and abs(hit['z']-prev_z) > STEP_JUMP_M*100):
                    # a real kerb or edge persists; a single sample is a seam between road triangles (the ground shows through): skip it
                    nxt = unreal.MathLibrary.transform_location(t, unreal.Vector(lx*100, (ly + sign*ACROSS_STEP_M)*100, 0))
                    again = _ground(world, nxt.x, nxt.y, ground_z)
                    if _is_road(again) and prev_z is not None and abs(again['z']-prev_z) <= STEP_JUMP_M*100:
                        continue
                    stop = 'no_hit' if hit is None else (hit['label'] if not _is_road(hit) else 'step')
                    break
                prev_z, last_good = hit['z'], abs(ly)
            edges.append((last_good, stop))
        rows.append({'along_m': lx, 'lo': -edges[0][0], 'hi': edges[1][0], 'stop_lo': edges[0][1], 'stop_hi': edges[1][1]})
    return rows


def footprint_check(world, location, rotation, scale_y):
    """Marking plane vs ground at the footprint edges/centre of three rows, and how many of those samples are not road asphalt."""
    import unreal
    t = _unit_transform(location, rotation)
    n = unreal.MathLibrary.get_up_vector(rotation)
    half = MESH_WIDTH_M*scale_y/2
    gaps, kerb = [], 0
    for lx in (-MESH_LENGTH_M/2, 0.0, MESH_LENGTH_M/2):
        for ly in (-half, -half/2, 0.0, half/2, half):
            w = unreal.MathLibrary.transform_location(t, unreal.Vector(lx*100, ly*100, 0))
            hit = _ground(world, w.x, w.y, location.z)
            if not _is_road(hit):
                kerb += 1
                continue
            plane = location.z - (n.x*(w.x-location.x)+n.y*(w.y-location.y))/n.z
            gaps.append((plane-hit['z'])*10)   # mm
    return {'plane_above_ground_mm': [min(gaps), max(gaps)] if gaps else None, 'edge_samples_not_road': kerb}


def refit(ids, output, dry_run=False, force_ids=()):
    """Fit only the listed crossing instances to the real road clearance. Everything else is read back and compared, never written."""
    import unreal
    import city_world
    world = city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before updating crossings')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    root = city_world._root() / 'Saved/DynaCity'
    rows = [r for r in json.loads((root/'street-instances.json').read_text())['instances'] if r['kind'] == 'crossing']
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs
        if 'crossing_marking' in str(d.label) or str(d.label).startswith('roads_')])
    by_path = {a.get_path_name(): a for a in city_world._actors() if 'street_kind:crossing_marking' in {str(t) for t in a.tags}}

    def read(row):
        c = by_path[row['actor']].get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        t = c.get_instance_transform(row['instance'], world_space=True)
        r = t.rotation.rotator()
        return c, t, [t.translation.x, t.translation.y, t.translation.z, r.pitch, r.yaw, r.roll, t.scale3d.x, t.scale3d.y, t.scale3d.z]

    before = {r['instance_id']: read(r)[2] for r in rows}
    if len(before) != len(rows) or any(i not in before for i in ids):
        raise ValueError('Unknown or duplicate crossing IDs')
    before_path = output / 'before-all-159.json'
    if not before_path.exists():
        before_path.write_text(json.dumps(before, indent=1))
    plans, touched = {}, {}
    for row in [r for r in rows if r['instance_id'] in ids]:
        c, t, vals = read(row)
        loc, rot = t.translation, t.rotation.rotator()
        width = MESH_WIDTH_M*t.scale3d.y
        ground = _ground(world, loc.x, loc.y, loc.z)
        clear = trace_clearance(world, loc, rot, width/2, ground['z'] if ground else loc.z)
        interval = common_interval([(r['lo'], r['hi']) for r in clear])
        plan = {'instance_id': row['instance_id'], 'actor': row['actor'], 'index': row['instance'], 'before': vals, 'width_before_m': width,
                'clearance_rows': clear, 'common_interval_m': interval, 'before_footprint': footprint_check(world, loc, rot, t.scale3d.y)}
        plan['fit'] = ({'status': 'blocked_centre', 'offset_m': 0.0, 'width_m': width} if interval is None
                       else fit_to_clearance(interval[0], interval[1], width))
        plans[row['instance_id']] = plan
        if plan['fit']['status'] in ('shifted', 'narrowed', 'unchanged') and not dry_run and (
                plan['fit']['status'] != 'unchanged' or row['instance_id'] in force_ids):
            right = unreal.MathLibrary.get_right_vector(rot)
            off = plan['fit']['offset_m']*100
            x, y = loc.x + right.x*off, loc.y + right.y*off
            hit = _ground(world, x, y, loc.z)
            if not _is_road(hit):
                plan['fit'] = {'status': 'skipped_new_centre_not_road', 'offset_m': 0.0, 'width_m': width}
                continue
            # Follow the road surface along the heading too (the old placement kept the forward axis horizontal, so on a road that
            # slopes along its heading the two ends sank into / floated over the asphalt).
            nx, ny, nz = hit['normal']
            f = unreal.MathLibrary.get_forward_vector(rot)
            d = f.x*nx + f.y*ny + f.z*nz
            ax, ay, az = f.x - nx*d, f.y - ny*d, f.z - nz*d
            norm = (ax*ax + ay*ay + az*az) ** .5
            normal = unreal.Vector(nx, ny, nz)
            new_rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(ax/norm, ay/norm, az/norm), normal)
            new_loc = unreal.Vector(x, y, hit['z'] + LIFT_CM*normal.z)
            scale = unreal.Vector(t.scale3d.x, plan['fit']['width_m']/MESH_WIDTH_M, t.scale3d.z)
            if not c.update_instance_transform(row['instance'], unreal.Transform(location=new_loc, rotation=new_rot, scale=scale), True, True, True):
                raise RuntimeError('update_instance_transform failed for '+row['instance_id'])
            touched[row['actor']] = by_path[row['actor']]
            _, t2, vals2 = read(row)
            r2 = t2.rotation.rotator()
            plan['after'] = vals2
            plan['after_clearance_rows'] = trace_clearance(world, t2.translation, r2, plan['fit']['width_m']/2, hit['z'])
            plan['after_footprint'] = footprint_check(world, t2.translation, r2, t2.scale3d.y)
    after = {r['instance_id']: read(r)[2] for r in rows}
    changed = sorted(i for i in after if after[i] != before[i])
    report = {'dry_run': dry_run, 'targets': list(ids), 'plans': plans, 'changed_ids': changed,
              'unchanged_other_ids': len(after) - len(changed), 'total_instances': len(after)}
    if touched and not dry_run:
        for a in touched.values():
            a.modify()
        names = {a.get_outermost().get_path_name() for a in touched.values()}
        for name in names:
            if name.startswith('/Game/'):
                f = Path(unreal.Paths.project_content_dir())/(name[6:]+'.uasset')
                if f.is_file():
                    target = output/'package-backup'/(name[6:]+'.uasset'); target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(f, target)
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched.values()}), True):
            raise RuntimeError('Crossing actor package save failed')
        report['saved_packages'] = sorted(names)
    (output/('refit-plan.json' if dry_run else 'refit-applied.json')).write_text(json.dumps(report, indent=1))
    return report


def restore_refit(output):
    """Put every instance back to output/before-all-159.json (only instances that differ are written)."""
    import unreal
    import city_world
    output = Path(output)
    saved = json.loads((output/'before-all-159.json').read_text())
    root = city_world._root() / 'Saved/DynaCity'
    rows = {r['instance_id']: r for r in json.loads((root/'street-instances.json').read_text())['instances'] if r['kind'] == 'crossing'}
    by_path = {a.get_path_name(): a for a in city_world._actors() if 'street_kind:crossing_marking' in {str(t) for t in a.tags}}
    restored, touched = [], {}
    for id_, vals in saved.items():
        row = rows[id_]
        c = by_path[row['actor']].get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        t = c.get_instance_transform(row['instance'], world_space=True)
        r = t.rotation.rotator()
        now = [t.translation.x, t.translation.y, t.translation.z, r.pitch, r.yaw, r.roll, t.scale3d.x, t.scale3d.y, t.scale3d.z]
        if now == vals:
            continue
        target = unreal.Transform(location=unreal.Vector(*vals[:3]), rotation=unreal.Rotator(roll=vals[5], pitch=vals[3], yaw=vals[4]), scale=unreal.Vector(*vals[6:9]))
        if not c.update_instance_transform(row['instance'], target, True, True, True):
            raise RuntimeError('restore failed for '+id_)
        restored.append(id_); touched[row['actor']] = by_path[row['actor']]
    for a in touched.values():
        a.modify()
    if touched and not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched.values()}), True):
        raise RuntimeError('Crossing actor package save failed')
    return {'restored': restored}


# ---- Bulk preflight: a per-ID decision from native traces; reads only, never writes ----
PREFLIGHT_VERSION = 'crossing-preflight/1'
MAX_OFFSET_M = 0.5            # a slide larger than this is not "conservative"
MIN_WIDTH_RATIO = 0.75        # never narrow below 75% of the current width
MAX_WIDTH_M = 12.0            # a wider current footprint is not a normal road crossing
MAX_GROUND_RANGE_M = 0.08     # ground height range around the fitted plane over the proposed footprint
MIN_NORMAL_Z = 0.97           # road surface steeper than about 14 degrees is not touched
PLANE_GAP_MM = (-10.0, 80.0)  # marking plane above ground over the (proposed) footprint: never more than 1 cm below, never floating above 8 cm


def classify_preflight(facts):
    """facts: fallback, centre_road, multilevel, normal_z, interval (lo, hi) | None, width_before_m, fit (fit_to_clearance dict), ground_range_m.
    Returns (category, reason). Fails closed: anything unmeasured blocks."""
    if facts.get('fallback'):
        return 'excluded_unverified_fallback', 'placed on source terrain fallback; needs separate support'
    if not facts.get('centre_road'):
        return 'blocked_no_road_at_centre', 'no road asphalt under the centre'
    if facts.get('multilevel'):
        return 'blocked_multilevel', 'more than one road or structure surface in the vertical stack'
    if facts.get('normal_z') is None or facts['normal_z'] < MIN_NORMAL_Z:
        return 'blocked_steep', 'road normal z %s < %s' % (facts.get('normal_z'), MIN_NORMAL_Z)
    if facts.get('interval') is None:
        return 'blocked_centre_not_clear', 'a trace row has no free road at the centre'
    if facts['width_before_m'] > MAX_WIDTH_M:
        return 'blocked_unreasonable_width', 'current width %.2f m > %.1f m' % (facts['width_before_m'], MAX_WIDTH_M)
    if facts.get('ground_range_m') is None or facts['ground_range_m'] > MAX_GROUND_RANGE_M:
        return 'blocked_ground_discontinuity', 'ground range %s m > %s m' % (facts.get('ground_range_m'), MAX_GROUND_RANGE_M)
    gap = facts.get('plane_gap_mm')
    if gap is None or gap[0] < PLANE_GAP_MM[0] or gap[1] > PLANE_GAP_MM[1]:
        return 'blocked_plane_fit', 'plane gap %s mm outside %s' % (gap, PLANE_GAP_MM)
    fit = facts['fit']
    if fit['status'] == 'unchanged':
        return 'unchanged_safe', 'already clears both kerbs by the margin'
    if fit['status'] == 'unfit':
        return 'blocked_no_inset_room', 'room %.2f m < %.1f m' % (fit['room_m'], MIN_FIT_WIDTH_M)
    if abs(fit['offset_m']) > MAX_OFFSET_M:
        return 'blocked_large_offset', 'offset %.2f m > %.1f m' % (fit['offset_m'], MAX_OFFSET_M)
    if fit['width_m'] / facts['width_before_m'] < MIN_WIDTH_RATIO:
        return 'blocked_excessive_narrowing', 'width ratio %.2f < %.2f' % (fit['width_m'] / facts['width_before_m'], MIN_WIDTH_RATIO)
    return 'adjustable', fit['status']


def _vertical_stack(world, x, y, z):
    """Distinct road / structure surfaces within the vertical stack at (x, y)."""
    import unreal
    hits = unreal.SystemLibrary.line_trace_multi(world, unreal.Vector(x, y, z + 1500), unreal.Vector(x, y, z - 1500),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [], unreal.DrawDebugTrace.NONE, True)
    stack = []
    for hit in hits or []:
        h = hit.to_dict()
        actor = h.get('hit_actor')
        label = actor.get_actor_label() if actor else ''
        if label.startswith(('roads_', 'beirut001_transport')) or 'bridge' in label.lower() or 'tunnel' in label.lower():
            stack.append({'label': label, 'z': h['impact_point'].z})
    stack.sort(key=lambda r: r['z'])
    return stack


def _proposed(world, loc, rot, fit, ground_z):
    """What refit would write for this fit (no write): new transform values, ground variation and plane gaps of the proposed footprint."""
    import unreal
    right = unreal.MathLibrary.get_right_vector(rot)
    off = fit['offset_m'] * 100
    x, y = loc.x + right.x * off, loc.y + right.y * off
    hit = _ground(world, x, y, ground_z)
    if not _is_road(hit):
        return None
    nx, ny, nz = hit['normal']
    f = unreal.MathLibrary.get_forward_vector(rot)
    d = f.x * nx + f.y * ny + f.z * nz
    ax, ay, az = f.x - nx * d, f.y - ny * d, f.z - nz * d
    norm = (ax * ax + ay * ay + az * az) ** .5
    new_rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(ax / norm, ay / norm, az / norm), unreal.Vector(nx, ny, nz))
    new_loc = unreal.Vector(x, y, hit['z'] + LIFT_CM * nz)
    new_scale_y = fit['width_m'] / MESH_WIDTH_M
    check = footprint_check(world, new_loc, new_rot, new_scale_y)
    t = _unit_transform(new_loc, new_rot)
    up = unreal.MathLibrary.get_up_vector(new_rot)
    dev, off_road = [], 0
    half = fit['width_m'] / 2
    for lx in ALONG_SAMPLES_M[::2]:
        for k in range(-4, 5):
            ly = half * k / 4
            w = unreal.MathLibrary.transform_location(t, unreal.Vector(lx * 100, ly * 100, 0))
            g = _ground(world, w.x, w.y, new_loc.z)
            if not _is_road(g):
                off_road += 1
                continue
            plane = new_loc.z - (up.x * (w.x - new_loc.x) + up.y * (w.y - new_loc.y)) / up.z
            dev.append(g['z'] - plane)
    return {'location': [new_loc.x, new_loc.y, new_loc.z], 'rotation': [new_rot.pitch, new_rot.yaw, new_rot.roll], 'scale_y': new_scale_y,
            'footprint_check': check, 'ground_range_m': (max(dev) - min(dev)) / 100 if dev else None, 'samples_off_road': off_road}


def preflight(only_ids=None):
    """Read-only: decide every painted crossing. Compares all transforms before and after to prove nothing was written."""
    import hashlib
    import unreal
    import city_world
    world = city_world._current()
    root = city_world._root() / 'Saved/DynaCity'
    rows = [r for r in json.loads((root/'street-instances.json').read_text())['instances'] if r['kind'] == 'crossing']
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs
        if 'crossing_marking' in str(d.label) or str(d.label).startswith('roads_')])
    by_path = {a.get_path_name(): a for a in city_world._actors() if 'street_kind:crossing_marking' in {str(t) for t in a.tags}}

    def read(row):
        c = by_path[row['actor']].get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        t = c.get_instance_transform(row['instance'], world_space=True)
        r = t.rotation.rotator()
        return t, r, [t.translation.x, t.translation.y, t.translation.z, r.pitch, r.yaw, r.roll, t.scale3d.x, t.scale3d.y, t.scale3d.z]

    snapshot0 = {r['instance_id']: read(r)[2] for r in rows}
    decisions = {}
    for row in rows:
        if only_ids and row['instance_id'] not in only_ids:
            continue
        id_ = row['instance_id']
        t, rot, vals = read(row)
        loc = t.translation
        width = MESH_WIDTH_M * t.scale3d.y
        fallback = not str(row.get('surface', '')).startswith('roads_')
        ground = _ground(world, loc.x, loc.y, loc.z)
        facts = {'fallback': fallback, 'centre_road': _is_road(ground), 'multilevel': False, 'normal_z': ground['nz'] if ground else None,
                 'interval': None, 'width_before_m': width, 'fit': None, 'ground_range_m': None, 'plane_gap_mm': None}
        evidence = {'centre_ground': ground['label'] if ground else None, 'surface_source': row.get('surface')}
        stack = _vertical_stack(world, loc.x, loc.y, loc.z) if ground else []
        evidence['vertical_stack'] = stack
        facts['multilevel'] = len(stack) > 1 and (stack[-1]['z'] - stack[0]['z']) > 250
        proposed = None
        if facts['centre_road'] and not facts['multilevel'] and not fallback:
            clear = trace_clearance(world, loc, rot, width / 2, ground['z'])
            facts['interval'] = common_interval([(c['lo'], c['hi']) for c in clear])
            evidence['clearance_rows_min_max_m'] = {'lo': [min(c['lo'] for c in clear), max(c['lo'] for c in clear)],
                                                    'hi': [min(c['hi'] for c in clear), max(c['hi'] for c in clear)]}
            evidence['stop_labels'] = sorted({c['stop_lo'] for c in clear} | {c['stop_hi'] for c in clear})
            current = footprint_check(world, loc, rot, t.scale3d.y)
            evidence['current_footprint_check'] = current
            if facts['interval'] is not None:
                facts['fit'] = fit_to_clearance(facts['interval'][0], facts['interval'][1], width)
                if facts['fit']['status'] in ('shifted', 'narrowed'):
                    proposed = _proposed(world, loc, rot, facts['fit'], ground['z'])
                    facts['ground_range_m'] = proposed['ground_range_m'] if proposed else None
                    facts['plane_gap_mm'] = proposed['footprint_check']['plane_above_ground_mm'] if proposed else None
                elif current.get('plane_above_ground_mm'):
                    facts['ground_range_m'] = (current['plane_above_ground_mm'][1] - current['plane_above_ground_mm'][0]) / 1000
                    facts['plane_gap_mm'] = current['plane_above_ground_mm']
        category, reason = classify_preflight(facts)
        entry = {'id': id_, 'area': row['actor'].split('.')[-1], 'actor': row['actor'], 'instance': row['instance'], 'category': category, 'reason': reason,
                 'flags': {'unverified_fallback': fallback, 'centre_not_on_asphalt': not facts['centre_road'], 'multilevel': facts['multilevel']},
                 'current_transform': vals, 'width_before_m': width, 'interval_m': facts['interval'], 'fit': facts['fit'], 'evidence': evidence}
        if proposed and category == 'adjustable':
            entry['proposed'] = {'location': proposed['location'], 'rotation': proposed['rotation'], 'scale_y': proposed['scale_y'],
                'offset_m': facts['fit']['offset_m'], 'width_after_m': facts['fit']['width_m'], 'width_delta_m': facts['fit']['width_m'] - width,
                'max_move_cm': ((proposed['location'][0] - vals[0]) ** 2 + (proposed['location'][1] - vals[1]) ** 2) ** .5,
                'z_delta_cm': proposed['location'][2] - vals[2],
                'rotation_delta_deg': [proposed['rotation'][i] - vals[3 + i] for i in range(3)],
                'plane_above_ground_mm': proposed['footprint_check']['plane_above_ground_mm'],
                'edge_samples_not_road': proposed['footprint_check']['edge_samples_not_road'], 'ground_range_m': proposed['ground_range_m']}
            entry['plan_hash'] = hashlib.sha256(json.dumps({'id': id_, 'v': PREFLIGHT_VERSION, 'cur': [round(x, 3) for x in vals],
                'new': [round(x, 3) for x in proposed['location'] + proposed['rotation'] + [proposed['scale_y']]]}, sort_keys=True).encode()).hexdigest()[:16]
        decisions[id_] = entry
    snapshot1 = {r['instance_id']: read(r)[2] for r in rows}
    return {'version': PREFLIGHT_VERSION, 'thresholds': {'margin_m': CLEARANCE_MARGIN_M, 'min_fit_width_m': MIN_FIT_WIDTH_M, 'max_offset_m': MAX_OFFSET_M,
            'min_width_ratio': MIN_WIDTH_RATIO, 'max_width_m': MAX_WIDTH_M, 'max_ground_range_m': MAX_GROUND_RANGE_M, 'min_normal_z': MIN_NORMAL_Z, 'plane_gap_mm': list(PLANE_GAP_MM), 'fit_tolerance_m': FIT_TOLERANCE_M,
            'along_samples_m': list(ALONG_SAMPLES_M), 'across_step_m': ACROSS_STEP_M},
            'baseline_transforms': snapshot0, 'nothing_written': snapshot0 == snapshot1, 'decisions': decisions}


# ---- Batch apply of an accepted, frozen preflight plan (exact IDs and transforms only; never recomputes a fit) ----
APPLY_TOL = {'location_cm': 0.05, 'rotation_deg': 0.01, 'scale': 1e-4}      # float32 serialization tolerance for the planned transforms
REVALIDATE_TOL = {'location_cm': 0.1, 'rotation_deg': 0.05, 'scale': 1e-3}  # native re-run of the plan vs the frozen plan
APPLY_MIN_MARGIN_M = CLEARANCE_MARGIN_M - 0.04                               # one trace step below the planning margin


def baseline_hash(transforms):
    import hashlib
    return hashlib.sha256(json.dumps({k: [round(x, 4) for x in transforms[k]] for k in sorted(transforms)}).encode()).hexdigest()


def planned_vector(plan, current):
    """Transform vector [x, y, z, pitch, yaw, roll, sx, sy, sz] of a frozen plan; scale X/Z stay as they are now."""
    return list(plan['proposed_location']) + list(plan['proposed_rotation']) + [current[6], plan['scale_y'], current[8]]


def vectors_close(a, b, tol):
    """True when two transform vectors agree within the explicit tolerances (angles compared on the circle)."""
    def ang(x, y):
        return abs((x - y + 180.0) % 360.0 - 180.0)
    return (all(abs(a[i] - b[i]) <= tol['location_cm'] for i in range(3)) and all(ang(a[3 + i], b[3 + i]) <= tol['rotation_deg'] for i in range(3))
            and all(abs(a[6 + i] - b[6 + i]) <= tol['scale'] for i in range(3)))


def reconcile(before, after, allowed_ids, expected, tol=APPLY_TOL):
    """Compare two full snapshots: IDs outside allowed_ids must be exactly equal; allowed IDs must equal their expected vector within tol."""
    outside_changed = sorted(k for k in before if k not in allowed_ids and before[k] != after.get(k))
    inside_wrong = sorted(k for k in allowed_ids if not vectors_close(after[k], expected[k], tol))
    return {'ids_before': len(before), 'ids_after': len(after), 'same_ids_and_order': list(before) == list(after),
            'outside_allowlist_changed': outside_changed, 'allowlist_not_at_expected': inside_wrong,
            'allowlist_changed_count': sum(1 for k in allowed_ids if before[k] != after[k]), 'ok': not outside_changed and not inside_wrong and list(before) == list(after)}


def apply_plan(allowlist_path, frozen_preflight_sha256, expected_baseline_hash, output):
    """Apply exactly the frozen accepted plans. Fails closed on any difference found before mutation; passing IDs only if some fail."""
    import hashlib
    import unreal
    import city_world
    allow_doc = json.loads(Path(allowlist_path).read_text())
    allow = allow_doc['allowlist']
    if allow_doc['meta']['preflight_sha256'] != frozen_preflight_sha256 or allow_doc['meta']['baseline_hash'] != expected_baseline_hash:
        raise RuntimeError('Frozen allowlist does not match the authorized preflight/baseline')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    world = city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before updating crossings')
    root = city_world._root() / 'Saved/DynaCity'
    rows = [r for r in json.loads((root/'street-instances.json').read_text())['instances'] if r['kind'] == 'crossing']
    row_by_id = {r['instance_id']: r for r in rows}
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if 'crossing_marking' in str(d.label) or str(d.label).startswith('roads_')])
    by_path = {a.get_path_name(): a for a in city_world._actors() if 'street_kind:crossing_marking' in {str(t) for t in a.tags}}

    def read(row):
        c = by_path[row['actor']].get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        t = c.get_instance_transform(row['instance'], world_space=True)
        r = t.rotation.rotator()
        return c, [t.translation.x, t.translation.y, t.translation.z, r.pitch, r.yaw, r.roll, t.scale3d.x, t.scale3d.y, t.scale3d.z]

    before = {r['instance_id']: read(r)[1] for r in rows}
    manifest = {'version': 'bulk-crossing-apply/1', 'frozen_preflight_sha256': frozen_preflight_sha256, 'allowlist_count': len(allow),
                'expected_baseline_hash': expected_baseline_hash, 'tolerances': {'apply': APPLY_TOL, 'revalidate': REVALIDATE_TOL, 'min_margin_m': APPLY_MIN_MARGIN_M}}
    manifest['baseline_hash_now'] = baseline_hash(before)
    manifest['baseline_matches'] = manifest['baseline_hash_now'] == expected_baseline_hash
    (output / 'before-all-159.json').write_text(json.dumps(before, indent=1))
    if not manifest['baseline_matches'] or list(before) != [r['instance_id'] for r in rows] or any(i not in before for i in allow):
        manifest['status'] = 'aborted_baseline_changed'
        (output / 'apply-manifest.json').write_text(json.dumps(manifest, indent=1))
        return manifest
    # 1. current allowlist transforms equal the frozen ones
    drift = [i for i, p in allow.items() if not vectors_close(before[i], p['current'], REVALIDATE_TOL)]
    manifest['allowlist_current_drift'] = drift
    # 2. native revalidation of every plan with the same thresholds
    fresh = preflight(set(allow))
    rv = {}
    for i, p in allow.items():
        d = fresh['decisions'][i]
        reasons = []
        if d['category'] != 'adjustable':
            reasons.append('category_now_' + d['category'])
        else:
            if not vectors_close(planned_vector(p, before[i]), list(d['proposed']['location']) + list(d['proposed']['rotation']) + [before[i][6], d['proposed']['scale_y'], before[i][8]], REVALIDATE_TOL):
                reasons.append('plan_changed_on_recompute')
        if i in drift:
            reasons.append('current_transform_drifted')
        rv[i] = {'revalidated': not reasons, 'reasons': reasons, 'category_now': d['category'], 'plan_hash_frozen': p['plan_hash'], 'plan_hash_now': d.get('plan_hash')}
    passing = [i for i in allow if rv[i]['revalidated']]
    skipped = {i: rv[i]['reasons'] for i in allow if not rv[i]['revalidated']}
    manifest['revalidation'] = rv
    manifest['will_apply'] = len(passing)
    manifest['skipped_before_mutation'] = skipped
    # 3. package backup of every affected actor first
    packages = {}
    for i in passing:
        a = by_path[row_by_id[i]['actor']]
        packages[a.get_outermost().get_path_name()] = a
    for name in packages:
        if name.startswith('/Game/'):
            src = Path(unreal.Paths.project_content_dir()) / (name[6:] + '.uasset')
            if src.is_file():
                dst = output / 'package-backup' / (name[6:] + '.uasset'); dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copy2(src, dst)
    manifest['backed_up_packages'] = sorted(packages)
    # 4. apply the frozen transforms
    expected = {i: list(before[i]) for i in before}
    for i in passing:
        expected[i] = planned_vector(allow[i], before[i])
        c, _ = read(row_by_id[i])
        v = expected[i]
        target = unreal.Transform(location=unreal.Vector(*v[:3]), rotation=unreal.Rotator(roll=v[5], pitch=v[3], yaw=v[4]), scale=unreal.Vector(*v[6:9]))
        if not c.update_instance_transform(row_by_id[i]['instance'], target, True, True, True):
            raise RuntimeError('update_instance_transform failed for ' + i)
    after = {r['instance_id']: read(r)[1] for r in rows}
    # 5. per-ID native checks at the new transform; revert any that fail
    checks, reverted = {}, []
    for i in passing:
        row = row_by_id[i]
        c, v = read(row)
        loc = unreal.Vector(*v[:3]); rot = unreal.Rotator(roll=v[5], pitch=v[3], yaw=v[4])
        half = MESH_WIDTH_M * v[7] / 2
        ground = _ground(world, loc.x, loc.y, loc.z)
        clear = trace_clearance(world, loc, rot, half, ground['z']) if ground else None
        fc = footprint_check(world, loc, rot, v[7])
        margins = [min(-c_['lo'] for c_ in clear) - half, min(c_['hi'] for c_ in clear) - half] if clear else None
        problems = []
        if not vectors_close(v, expected[i], APPLY_TOL):
            problems.append('readback_not_at_plan')
        if margins is None or min(margins) < APPLY_MIN_MARGIN_M:
            problems.append('kerb_margin_%s' % margins)
        if fc['edge_samples_not_road']:
            problems.append('edge_samples_not_road_%d' % fc['edge_samples_not_road'])
        gap = fc['plane_above_ground_mm']
        if not gap or gap[0] < PLANE_GAP_MM[0] or gap[1] > PLANE_GAP_MM[1]:
            problems.append('plane_gap_%s' % gap)
        checks[i] = {'kerb_margin_lo_hi_m': margins, 'plane_above_ground_mm': gap, 'edge_samples_not_road': fc['edge_samples_not_road'], 'width_m': 2 * half,
                     'expected_vector': expected[i], 'measured_vector': v, 'before_vector': before[i], 'plan_hash': allow[i]['plan_hash'], 'problems': problems, 'passed': not problems}
        if problems:
            b = before[i]
            c.update_instance_transform(row['instance'], unreal.Transform(location=unreal.Vector(*b[:3]), rotation=unreal.Rotator(roll=b[5], pitch=b[3], yaw=b[4]), scale=unreal.Vector(*b[6:9])), True, True, True)
            expected[i] = list(b)
            reverted.append(i)
    after = {r['instance_id']: read(r)[1] for r in rows}
    applied = [i for i in passing if i not in reverted]
    manifest.update(checks=checks, reverted_after_failed_check={i: checks[i]['problems'] for i in reverted}, applied_ids=applied, applied_count=len(applied))
    rec = reconcile(before, after, set(applied), expected)
    manifest['reconcile_after_apply'] = rec
    # 6. scoped save of only the affected actor packages
    touched = {}
    for i in applied:
        a = by_path[row_by_id[i]['actor']]
        a.modify()
        touched[a.get_outermost().get_path_name()] = a.get_outermost()
    if touched and rec['ok']:
        manifest['saved_ok'] = bool(unreal.EditorLoadingAndSavingUtils.save_packages(list(touched.values()), True))
        manifest['saved_packages'] = sorted(touched)
    else:
        manifest['saved_ok'] = False
        manifest['saved_packages'] = []
    (output / 'after-all-159.json').write_text(json.dumps(after, indent=1))
    manifest['status'] = 'applied' if rec['ok'] and manifest['saved_ok'] else 'not_saved_see_reconcile'
    (output / 'apply-manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest
