"""Agent 2: trace existing loaded road/ground heights under mapped traffic island vertices. Read-only."""
import json
from collections import Counter
from pathlib import Path
import unreal


def apply(filename, output):
    import city_world, explore_city
    world = city_world._current()
    data = json.loads(Path(filename).read_text(encoding='utf8'))
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('Origin mismatch')
    city_world.load_editor_geometry()
    out = {}
    for isl in data['islands']:
        seen = {}
        for tri in isl['triangles_local_xy_m']:
            for x, y in tri:
                key = (round(x, 4), round(y, 4))
                if key in seen:
                    continue
                w = explore_city._world([x, y, 0.0])
                hit = unreal.SystemLibrary.line_trace_single(world, w + unreal.Vector(0, 0, 500), w - unreal.Vector(0, 0, 500),
                    unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [], unreal.DrawDebugTrace.NONE, True)
                z = None
                label = ''
                if hit is not None:
                    info = hit.to_dict()
                    act = info.get('hit_actor')
                    label = act.get_actor_label() if act else ''
                    if info['impact_normal'].z >= .7:
                        z = info['impact_point'].z
                seen[key] = {'xy': list(key), 'world_cm': [w.x, w.y], 'z_cm': z, 'hit': label}
        out[isl['source_id']] = list(seen.values())
    Path(output).write_text(json.dumps(out), encoding='utf8')
    _, east, north, _ = explore_city._basis()
    return {'islands': len(out), 'vertices': sum(len(v) for v in out.values()),
            'traced': sum(1 for v in out.values() for r in v if r['z_cm'] is not None),
            'basis_east': [east.x, east.y, east.z], 'basis_north': [north.x, north.y, north.z]}
