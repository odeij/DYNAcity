"""Build the iteration-2 corridor reconstruction GLB (all selected walls) from producer components + photo measurements.

python scripts/build_corridor_geometry_v2.py <corridor-manifest-v2.json> <reference-profiles.json> <wall-measurements.json> <out_dir> [albedo_scale_json]

Per wall: producer facade parts (street-facing only; the source envelope stays original), or a plain banded panel
(narrow walls), or a net/scaffold wrap (the tower's dark side). Palettes come from the photo-measured wall colour of
each building, with the producer's role ratios kept, converted sRGB -> linear; shared roles (glass, interior) stay fixed.
Everything is an estimate: candidate identities, bounded dimensions, appearance targets.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import corridor_reconstruction as cr  # noqa: E402
import geometry_checks as gc  # noqa: E402
import reference_facade_geometry as rfg  # noqa: E402

DROP = {'body_core', 'rear_generic', 'side_left', 'side_right', 'side_left_bands', 'side_right_bands', 'roof_slab', 'service_core'}
MAIN_ROLE = {'modern_stone_recessed_balcony': 'stone_light', 'cream_yellow_older_balcony': 'cream_wall', 'construction_tower_frame': 'concrete_pale'}
FIXED = {'glass', 'interior_dark', 'net_green', 'glass_window', 'glass_parapet', 'glass_storefront'}
GLASS_ROLE_BY_PART = {'glass_parapets': 'glass_parapet', 'storefront_glass': 'glass_storefront', 'glass': 'glass_window'}
# translucent PBR specs (linear base colour, opacity). Values are estimates chosen for dark recesses behind thin glass.
GLASS_SPECS = {'glass_window': {'base_color': [0.020, 0.030, 0.040, 1.0], 'roughness': 0.08, 'metallic': 0.0, 'blend': 'translucent', 'opacity': 0.55},
               'glass_parapet': {'base_color': [0.030, 0.050, 0.060, 1.0], 'roughness': 0.08, 'metallic': 0.0, 'blend': 'translucent', 'opacity': 0.30},
               'glass_storefront': {'base_color': [0.015, 0.020, 0.025, 1.0], 'roughness': 0.08, 'metallic': 0.0, 'blend': 'translucent', 'opacity': 0.70}}


def s2l(c):
    c = np.asarray(c, float) / 255.
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def palette_for(key, family, measured_srgb, scale):
    """role -> linear rgb. Producer role ratios (unlifted measured patches) keep the stone/fascia/podium hierarchy."""
    main = s2l(rfg.PALETTE[MAIN_ROLE[family]][0])
    target = s2l(measured_srgb) * scale
    out = {}
    for role, (rgb, lift, basis) in rfg.PALETTE.items():
        if role in FIXED:
            continue
        ratio = s2l(rgb) / np.maximum(main, 1e-4)
        out[role] = np.clip(target * ratio, 0.004, 0.9)
    return out


def box(x0, x1, y0, y1, z0, z1):
    v, f = rfg._box(x0, x1, y0, y1, z0, z1)
    return v.astype(float), f.astype(np.int64)


def part(name, material, boxes):
    vs, fs, base = [], [], 0
    for b in boxes:
        v, f = box(*b)
        vs.append(v); fs.append(f + base); base += len(v)
    return {'name': name, 'material': material, 'vertices': np.concatenate(vs), 'triangles': np.concatenate(fs)}


def plain_panel(W, H, depth, floor_h):
    """Narrow wall: solid skin with floor bands. Producer frame: street is -Y."""
    n = int(max(1, round(H / floor_h)))
    bands = [(0.0, W, -0.12, 0.0, k * H / n - 0.15, k * H / n + 0.15) for k in range(1, n)]
    return [part('plain_wall', 'stone_light', [(0.0, W, 0.0, 0.25, 0.0, H)]), part('plain_bands', 'fascia_dark', bands)]


def net_wrap(W, H, depth, offset=0.8, floor_h=3.1):
    """Dark netted scaffold plane across the whole face, offset in front of the wall (observed in all four photos)."""
    stand = [(x, x + 0.06, -offset - 0.06, -offset, 0.0, H) for x in np.arange(0.2, W - 0.1, 1.8)]
    ledgers = [(0.0, W, -offset - 0.06, -offset, z, z + 0.06) for z in np.arange(floor_h, H, floor_h)]
    return [part('net_plane', 'net_green', [(0.0, W, -offset - 0.02, -offset + 0.02, 0.0, H)]), part('scaffold', 'white_trim', stand + ledgers)]




# facade detail wins over structure when two same-orientation faces share a plane; the interior body core always loses
PART_PRIORITY = ['stone_podium', 'podium', 'podium_courses', 'piers', 'floor_bands', 'slab_edges', 'cantilever_fascia', 'parapet_balconies', 'loggia_floors', 'gallery_floors',
                 'closed_bays', 'shutters', 'glazed_top', 'glass', 'glass_parapets', 'storefront_glass', 'loggia_back_walls', 'gallery_back_walls', 'podium_cap', 'roof_railing',
                 'roof_parapet', 'side_left', 'side_right', 'rear_generic', 'roof_slab', 'body_core']


def resolve_block(placed, prefix):
    """Replace the block's parts by the same surfaces with coplanar same-orientation overlaps clipped away (nothing moved)."""
    sel = [p for p in placed if p['name'].startswith(prefix)]
    rest = [p for p in placed if not p['name'].startswith(prefix)]
    vs, fs, lab, mats_of, base = [], [], [], {}, 0
    for p in sel:
        v = np.asarray(p['vertices'], float); t = np.asarray(p['triangles'])
        vs.append(v); fs.append(t + base); lab += [p['name']] * len(t); mats_of[p['name']] = p['material']; base += len(v)
    V, T = np.concatenate(vs), np.concatenate(fs)
    short = lambda n: n.split(':', 1)[-1]
    pri = [PART_PRIORITY.index(short(n)) if short(n) in PART_PRIORITY else len(PART_PRIORITY) - 8 for n in lab]
    nv, nt, nl, st = gc.resolve_coplanar(V, T, pri, lab)
    st['passes'] = 1
    for _ in range(5):  # run to a fixed point: pieces created by one pass can still overlap each other
        remaining = [h for h in gc.coplanar_overlaps(nv, nt, labels=nl, min_area=1e-4) if h['same_orientation']]
        if not remaining:
            break
        pri = [PART_PRIORITY.index(short(n)) if short(n) in PART_PRIORITY else len(PART_PRIORITY) - 8 for n in nl]
        nv, nt, nl, st2 = gc.resolve_coplanar(nv, nt, pri, nl, min_area=1e-7)
        st['passes'] += 1
        for k in ('fully_covered_removed', 'clipped', 'pieces_added', 'area_removed_m2'):
            st[k] += st2[k]
        st['triangles_out'] = st2['triangles_out']
    out = []
    for name in dict.fromkeys(nl):
        tri = nt[[i for i, l in enumerate(nl) if l == name]]
        used = np.unique(tri)
        remap = {int(u): k for k, u in enumerate(used)}
        out.append({'name': name, 'material': mats_of[name], 'vertices': nv[used], 'triangles': np.vectorize(remap.get)(tri).reshape(-1, 3)})
    return rest + out, st


def overlap_report(placed, prefix, tol=0.003):
    """Coplanar-overlap check over the parts whose name starts with ``prefix`` (world coordinates). Same-orientation overlaps
    are visible z-fighting; opposite-orientation ones are hidden back-to-back contacts and are reported separately."""
    sel = [p for p in placed if p['name'].startswith(prefix)]
    if not sel:
        return None
    vs, fs, labels, base = [], [], [], 0
    for p in sel:
        v = np.asarray(p['vertices'], float)
        vs.append(v); t = np.asarray(p['triangles']); fs.append(t + base); labels += [p['name'].split(':', 1)[-1]] * len(t); base += len(v)
    hits = gc.coplanar_overlaps(np.concatenate(vs), np.concatenate(fs), tol=tol, labels=labels)
    same = [h for h in hits if h['same_orientation']]
    return {'prefix': prefix, 'tolerance_m': tol, 'triangles_checked': len(labels), 'same_orientation_overlaps': len(same),
            'same_orientation_area_m2': round(sum(h['area_m2'] for h in same), 3), 'opposite_orientation_contacts': len(hits) - len(same),
            'top_same_orientation_pairs': [r for r in gc.summarise(same)][:12],
            'largest_examples': [{'area_m2': h['area_m2'], 'labels': list(h['labels']), 'normal': h['normal'], 'plane_offset': h['plane_offset'],
                                  'a_bbox': [np.concatenate(vs)[np.concatenate(fs)[h['a']]].min(0).round(3).tolist(), np.concatenate(vs)[np.concatenate(fs)[h['a']]].max(0).round(3).tolist()],
                                  'b_bbox': [np.concatenate(vs)[np.concatenate(fs)[h['b']]].min(0).round(3).tolist(), np.concatenate(vs)[np.concatenate(fs)[h['b']]].max(0).round(3).tolist()]}
                                 for h in sorted(same, key=lambda h: -h['area_m2'])[:6]]}


def main(manifest_path, profile_path, meas_path, out_dir, scale_path=None):
    manifest = json.loads(Path(manifest_path).read_text()); profile = json.loads(Path(profile_path).read_text())
    meas = {(m['source_id'], m['edge']): m for m in json.loads(Path(meas_path).read_text())['walls']}
    scales = json.loads(Path(scale_path).read_text()) if scale_path else {}
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    placed, records, mats = [], [], {}
    for b in manifest['buildings']:
        key = b['key']; uid = b['source_id']
        measured = None
        for w in b['walls']:
            m = meas.get((uid, w['edge']))
            if m and m.get('appearance_srgb_8bit'):
                measured = m['appearance_srgb_8bit']; break
        fam = b['family']
        scale = float(scales.get(key, 1.0))
        pal = palette_for(key, fam, measured or rfg.PALETTE[MAIN_ROLE[fam]][0], scale)
        override = b.get('height_override')
        for w in b['walls']:
            fr = w['frontage']; W = fr['length_m']
            Hsrc = fr['top_z'] - fr['base_z']
            envelope = bool(override and override.get('raise_mode') == 'envelope')
            H = override['est'] if envelope else Hsrc  # frame mode: facade ends at the source roof; envelope mode: facade rises with the closed massing
            Hnet = override['est'] if override else Hsrc
            D = w['plan_depth_m']
            m = meas.get((uid, w['edge']), {})
            fh = (w.get('params') or {}).get('floor_height_m') or (m.get('floor_pitch_summary') or {}).get('median_strong_m')
            kind = w['kind']
            warnings, prov = [], {}
            if kind == 'producer':
                params = {k: v for k, v in (w.get('params') or {}).items() if k != 'floor_height_m'}
                mesh = rfg.mesh_for_profile(fam, W, H, depth_m=D, floor_height_m=fh if fh else None, params=params or None,
                                            side_facades={'left': 'none', 'right': 'none'}, profile=profile)
                parts = [{'name': n, 'material': p['meta']['material'], 'vertices': p['verts'].astype(float), 'triangles': p['faces'].astype(np.int64)}
                         for n, p in mesh.parts.items() if n not in DROP]
                prov = mesh.provenance; warnings = list(prov.get('warnings') or [])
                recess = max(float(p['vertices'][:, 1].max()) for p in parts)
            elif kind == 'plain':
                parts = plain_panel(W, H, D, fh or 3.1); recess = 0.25
            elif kind == 'netwrap':
                parts = net_wrap(W, Hnet, D, 0.8, fh or 3.1); recess = 0.0
            else:
                raise SystemExit('unknown wall kind ' + kind)
            for p in parts:
                if p['material'] == 'glass':
                    p['material'] = GLASS_ROLE_BY_PART.get(p['name'], 'glass_window')
            parts += cr.backing_parts(W, H, min(max(recess, 0.3), D), 'interior_dark')
            tag = f'{key}.{w["edge"]}'
            for p in parts:
                p['name'] = f"{tag}:{p['name']}"
                role = p['material']
                if role in FIXED:
                    continue
                p['material'] = f'{role}__{key}'
                mats[p['material']] = {'base_color': [round(float(x), 4) for x in pal[role]] + [1.0], 'roughness': 0.85, 'metallic': 0.0}
            cover = cr.wall_coverage([p for p in parts if not p['name'].endswith(('return_x0', 'return_xw'))], W, H)
            placed += cr.place_parts(parts, fr)
            records.append({'building': key, 'source_id': b['source_id_full'], 'edge': w['edge'], 'kind': kind, 'family': fam,
                            'evidence_level': 'multi_view_visible_wall + family polygon overlap; identity NOT confirmed',
                            'wall_m': [W, round(Hsrc, 2)], 'built_height_m': round(H, 2), 'net_wrap_height_m': round(Hnet, 2) if kind == 'netwrap' else None, 'height_override': override, 'plan_depth_m': D,
                            'floor_height_used_m': fh, 'floor_height_basis': 'photo autocorrelation of this wall' if fh else 'producer estimate (3.1 m, bounds 2.8-3.5)',
                            'appearance_target_srgb_balanced': measured, 'albedo_scale': scale,
                            'triangles': int(sum(len(p['triangles']) for p in parts)), 'street_wall_coverage': round(cover, 4),
                            'producer_parts': sorted({p['name'].split(':')[1] for p in parts}), 'producer_warnings': warnings,
                            'parameters': prov.get('parameters'), 'floors': prov.get('floors'), 'bays': prov.get('bays'),
                            'geometry_status': 'estimated from reference photos; not surveyed'})
        if override and override.get('raise_mode') == 'envelope':  # closed massing above the source roof; the facade edge(s) stay open
            fr0 = b['walls'][0]['frontage']
            z0, z1 = fr0['top_z'], fr0['base_z'] + override['est']
            ring_a = np.array(b['ring_local'])
            ev, ef = cr.envelope_prism(ring_a, z0, z1, skip_edges=[w['edge'] for w in b['walls']])
            placed.append({'name': f'{key}.raised:envelope', 'material': f'{MAIN_ROLE[fam]}__{key}', 'vertices': ev, 'triangles': ef})
            mats.setdefault(f'{MAIN_ROLE[fam]}__{key}', {'base_color': [round(float(x), 4) for x in pal[MAIN_ROLE[fam]]] + [1.0], 'roughness': 0.85, 'metallic': 0.0})
            records.append({'building': key, 'source_id': b['source_id_full'], 'kind': 'raised_envelope', 'family': fam, 'z_from_local_m': round(z0, 2), 'z_to_local_m': round(z1, 2),
                            'height_estimate': override, 'footprint_area_m2': round(abs(cr.polygon_area(ring_a)), 1), 'triangles': int(len(ef)),
                            'roof_closure': 'roof cap + side faces over the true footprint on every edge except the rebuilt facade edge(s)',
                            'collision': 'original collision ends at the source height; the raised massing is visual only (no collision added)',
                            'geometry_status': 'estimated massing from 3 photos; uncalibrated camera; not surveyed'})
        elif override:  # raised part: a full-perimeter open concrete frame over the TRUE footprint, from the source roof up
            fr0 = b['walls'][0]['frontage']
            z0, z1 = fr0['top_z'], fr0['base_z'] + override['est']
            frame = cr.raised_frame(np.array(b['ring_local']), z0, z1, floor_h=3.1, material='concrete_pale')
            for p in frame:
                p['name'] = f'{key}.raised:{p["name"]}'
                p['material'] = f'concrete_pale__{key}'
            placed += frame
            ring_a = np.array(b['ring_local'])
            records.append({'building': key, 'source_id': b['source_id_full'], 'kind': 'raised_frame', 'family': fam,
                            'z_from_local_m': round(z0, 2), 'z_to_local_m': round(z1, 2), 'height_estimate': override,
                            'footprint_area_m2': round(abs(cr.polygon_area(ring_a)), 1), 'footprint_vertices': len(ring_a),
                            'columns': int(len(frame[0]['vertices']) // 8), 'floor_plates': int(len(frame[1]['triangles']) // (2 * len(ring_a) + 2 * (len(ring_a) - 2))),
                            'triangles': int(sum(len(p['triangles']) for p in frame)),
                            'roof_closure': 'source roof at the source height stays as the base; the part above is an OPEN construction frame (perimeter columns + floor plates + top plate), not a closed envelope',
                            'collision': 'original collision ends at the source height; the raised frame is visual only (no collision added)',
                            'geometry_status': 'estimated massing, uncalibrated height; not surveyed'})
    for blk in manifest.get('estimated_blocks', []):  # photo-fitted additive blocks (no source triangles involved)
        key, fam = blk['key'], blk['family']
        a, bb = np.array(blk['a_local_m']), np.array(blk['b_local_m'])
        t = (bb - a) / np.linalg.norm(bb - a); n = np.array([t[1], -t[0]])  # exact orthonormal pair (the manifest copy is rounded)
        W, H, D, z0 = blk['width_m'], blk['height_m'], blk['plan_depth_used_m'], blk['ground_z_local_m']
        fr = {'start': a.tolist(), 'tangent': t.tolist(), 'normal': n.tolist(), 'length_m': W, 'base_z': z0, 'top_z': z0 + H}
        pal = palette_for(key, fam, blk['albedo_target_srgb_balanced'], blk['albedo_scale'])
        # The producer builds a CLOSED volume (body, sides, rear, roof) with its own end faces: use it whole. Adding a separate
        # envelope/backing on top put two same-plane faces at the end walls (z-fighting at the floor lines).
        mesh = rfg.mesh_for_profile(fam, W, H, depth_m=D, params={'ground_floor_m': blk['ground_floor_m']}, side_facades={'left': 'plain', 'right': 'plain'}, profile=profile)
        parts = [{'name': nme, 'material': p['meta']['material'], 'vertices': p['verts'].astype(float), 'triangles': p['faces'].astype(np.int64)}
                 for nme, p in mesh.parts.items()]
        for p in parts:
            if p['material'] == 'glass':
                p['material'] = GLASS_ROLE_BY_PART.get(p['name'], 'glass_window')
        cover = cr.wall_coverage(parts, W, H)
        for p in parts:
            p['name'] = f'{key}.est:{p["name"]}'
            if p['material'] not in FIXED:
                role = p['material']
                p['material'] = f'{role}__{key}'
                mats[p['material']] = {'base_color': [round(float(x), 4) for x in pal[role]] + [1.0], 'roughness': 0.85, 'metallic': 0.0}
        placed += cr.place_parts(parts, fr)
        records.append({'building': key, 'source_id': blk['candidate_source_id'], 'kind': 'estimated_block', 'family': fam, 'wall_m': [W, H], 'plan_depth_m': D,
                        'triangles': int(sum(len(p['triangles']) for p in parts)), 'street_wall_coverage': round(cover, 4),
                        'producer_parts': sorted(mesh.parts), 'height_p5_p95_m': blk['height_p5_p95_m'], 'identity': blk['identity'], 'collision': blk['collision'],
                        'source_geometry': blk['source_geometry'], 'footprint_world_local_m': np.array([a, bb, bb - n * D, a - n * D]).round(2).tolist(),
                        'closed_volume': 'producer body/sides/rear/roof (no separate envelope, backing or returns)', 'geometry_status': blk['geometry_status']})
    for mname in {p['material'] for p in placed}:  # fixed-role materials (glass specs, interior, net, producer palette roles)
        if mname in mats:
            continue
        if mname in GLASS_SPECS:
            mats[mname] = dict(GLASS_SPECS[mname])
        elif mname == 'interior_dark':
            mats[mname] = {'base_color': [0.012, 0.014, 0.016, 1.0], 'roughness': 0.9, 'metallic': 0.0}
        elif mname == 'net_green':
            k = float(scales.get('net_green', 1.0))
            mats[mname] = {'base_color': [round(0.012 * k, 4), round(0.026 * k, 4), round(0.02 * k, 4), 1.0], 'roughness': 0.95, 'metallic': 0.0}
        else:
            c = s2l(rfg.PALETTE[mname][0])
            mats[mname] = {'base_color': [round(float(x), 4) for x in c] + [1.0], 'roughness': 0.85, 'metallic': 0.0}
    resolve_stats = {}
    for blk in manifest.get('estimated_blocks', []):
        before = overlap_report(placed, blk['key'] + '.est:')
        placed, resolve_stats[blk['key']] = resolve_block(placed, blk['key'] + '.est:')
        resolve_stats[blk['key']]['overlap_before'] = {x: before[x] for x in ('same_orientation_overlaps', 'same_orientation_area_m2', 'opposite_orientation_contacts')}
    checks = {blk['key']: overlap_report(placed, blk['key'] + '.est:') for blk in manifest.get('estimated_blocks', [])}
    for bld in manifest['buildings']:  # report-only: accepted buildings are NOT modified by the resolver
        checks[bld['key'] + ' (report only, unchanged)'] = overlap_report(placed, bld['key'] + '.')
    for k, s in resolve_stats.items():
        print('resolver', k, s)
    for k, r in checks.items():
        print('overlap check', k, {x: r[x] for x in ('triangles_checked', 'same_orientation_overlaps', 'same_orientation_area_m2', 'opposite_orientation_contacts')})
        for row in r['top_same_orientation_pairs'][:8]:
            print('   ', row)
    mats = {k: v for k, v in mats.items() if k in {p['material'] for p in placed}}
    info = cr.write_glb(out / 'corridor-reconstruction-v2.glb', placed, mats)
    (out / 'corridor-materials-v2.json').write_text(json.dumps(mats, indent=1))
    (out / 'corridor-geometry-report-v2.json').write_text(json.dumps({'schema': 'dynacity-corridor-geometry/2', 'glb': info, 'walls': records,
        'camera_status': 'uncalibrated_estimate', 'coplanar_overlap_checks': checks, 'coplanar_resolution': resolve_stats, 'note': 'candidate identities and estimated geometry; not an exact reconstruction'}, indent=1, default=str))
    for r in [x for x in records if 'edge' in x or x.get('kind') == 'estimated_block']:
        print(r['building'], r.get('edge', '-'), r['kind'], r['wall_m'], r['triangles'], 'tris cov', r['street_wall_coverage'])
    print(info['triangles'], 'triangles,', len(mats), 'materials')


if __name__ == '__main__':
    main(*sys.argv[1:6])
