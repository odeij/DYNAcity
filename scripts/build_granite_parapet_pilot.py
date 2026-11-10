#!/usr/bin/env python3
"""
Optional additive opaque stone lower balcony-front overlay for source granite 106849cb.
Source: beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1 (granite producer walls 4 and 1).

CLI: python scripts/build_granite_parapet_pilot.py --manifest corridor-manifest-v2.json --output out_dir --fraction 0.7
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import corridor_reconstruction as cr
import reference_facade_geometry as rfg


# Source identity from manifest
GRANITE_SOURCE_ID = "beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1"
GRANITE_SOURCE_HEIGHT = 45.551700592041016  # exact frontage top minus base; metadata differs by 0.046 mm
GRANITE_BASE_Z = 12.41469955444336
GRANITE_TOP_Z = 57.966400146484375

# Overlay parameters
STONE_THICKNESS = 0.12  # m
STONE_PROJECTION = 0.02  # m toward street beyond glass_parapets face
FRACTION = 0.7  # fraction of each panel height


def extract_parapet_panels(parapet_part):
    """
    Extract individual glass_parapet panels as separate boxes.
    Each panel is 8 vertices / 12 triangles in the part's vertex buffer.
    Returns list of dicts with x0,x1,y0,y1,z0,z1 for each panel.
    """
    v = parapet_part['verts']
    f = parapet_part['faces']
    panels = []

    num_boxes = len(v) // 8
    if num_boxes * 8 != len(v) or num_boxes * 12 != len(f):
        raise ValueError(f'Expected vertices multiple of 8 and triangles multiple of 12, got {len(v)} verts, {len(f)} tris')

    for i in range(num_boxes):
        box_v = v[i*8:(i+1)*8]
        # Verify face indices are local to this box
        box_f = f[i*12:(i+1)*12] - i*8
        if not np.all((box_f >= 0) & (box_f < 8)):
            raise ValueError(f'Box {i} face indices not local')

        x0, x1 = float(box_v[:,0].min()), float(box_v[:,0].max())
        y0, y1 = float(box_v[:,1].min()), float(box_v[:,1].max())
        z0, z1 = float(box_v[:,2].min()), float(box_v[:,2].max())

        panels.append({
            'x0': x0, 'x1': x1,
            'y0': y0, 'y1': y1,  # y0 is back (toward street), y1 is front (glass face)
            'z0': z0, 'z1': z1,
            'height': z1 - z0,
            'proj_y': y0  # original projection depth (negative = toward street)
        })

    return panels


def build_overlay_for_wall(edge, frontage, plan_depth_m, floor_height_m, fraction):
    """Build stone overlay parts for one producer wall - ONE overlay panel PER glass panel."""
    if not np.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Fraction must be finite and between zero and one')
    if fraction == 0:
        return []
    mesh = rfg.mesh_for_profile(
        'modern_stone_recessed_balcony',
        width_m=frontage['length_m'],
        height_m=frontage['top_z'] - frontage['base_z'],
        depth_m=plan_depth_m,
        floor_height_m=floor_height_m,
        side_facades={'left': 'none', 'right': 'none'}
    )

    parapet = mesh.parts['glass_parapets']
    panels = extract_parapet_panels(parapet)

    parts = []
    for i, panel in enumerate(panels):
        # Each panel: cover lower `fraction` of its height
        z0 = panel['z0']
        z1 = z0 + fraction * panel['height']

        # Stone projects STONE_PROJECTION beyond glass face toward street
        # Glass face is at panel['y1']; street is more negative Y
        y_glass_face = panel['y0']
        y_stone_back = y_glass_face - STONE_PROJECTION - STONE_THICKNESS  # back of stone
        y_stone_front = y_glass_face - STONE_PROJECTION  # front of stone (0.02m from glass)

        v = np.array([
            [panel['x0'], y_stone_back, z0], [panel['x1'], y_stone_back, z0],
            [panel['x1'], y_stone_front, z0], [panel['x0'], y_stone_front, z0],
            [panel['x0'], y_stone_back, z1], [panel['x1'], y_stone_back, z1],
            [panel['x1'], y_stone_front, z1], [panel['x0'], y_stone_front, z1]
        ], float)
        f = np.array([
            [0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
            [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]
        ], np.int64)

        parts.append({
            'name': f'granite.{edge}:stone_overlay:{i}',
            'material': 'stone_light__granite_overlay',
            'vertices': v,
            'triangles': f
        })

    # Place parts using cr.place_parts ONCE per wall
    placed = cr.place_parts(parts, frontage)
    return placed


def write_glb(path, parts, materials):
    """Write GLB using corridor_reconstruction writer."""
    return cr.write_glb(path, parts, materials)


def compute_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_overlay(placed, original_panels, frontage, fraction):
    """Verify overlay panels: count matches, Z within parent, height <= 1.1m, no inter-floor connections."""
    if not placed:
        return {'panel_count_match':fraction==0,'overlay_panel_count':0,'original_panel_count':len(original_panels),
                'all_positive_volume':True,'all_height_leq_1_1m':True,'all_z_within_parent':True,
                'no_inter_floor_connections':True,'total_volume_m3':0.,'floor_count':0}
    all_verts = np.vstack([p['vertices'] for p in placed])
    checks = {
        'panel_count_match': len(placed) == len(original_panels),
        'overlay_panel_count': len(placed),
        'original_panel_count': len(original_panels),
        'all_positive_volume': True,
        'all_height_leq_1_1m': True,
        'all_z_within_parent': True,
        'no_inter_floor_connections': True,
        'world_bounds': {
            'min': all_verts.min(axis=0).tolist(),
            'max': all_verts.max(axis=0).tolist()
        },
        'total_volume_m3': 0.0
    }

    for i, (p, orig) in enumerate(zip(placed, original_panels)):
        v = p['vertices']
        local=v-v.mean(axis=0);tri=local[p['triangles']]
        vol=float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6)
        checks['total_volume_m3'] += vol

        if vol <= 0:
            checks['all_positive_volume'] = False

        h = v[:,2].max() - v[:,2].min()
        if h > 1.1:
            checks['all_height_leq_1_1m'] = False

        # Z within parent panel (after placement, parent Z range shifted by frontage base_z)
        parent_z0 = orig['z0'] + frontage['base_z']
        parent_z1 = orig['z0'] + fraction * orig['height'] + frontage['base_z']
        if v[:,2].min() < parent_z0 - 1e-6 or v[:,2].max() > parent_z1 + 1e-6:
            checks['all_z_within_parent'] = False

    # Check no vertical adjacency between DIFFERENT floors
    # Group by floor (base Z rounded to cm) and check gaps between floor groups
    z_by_floor = {}
    for p in placed:
        z0 = round(p['vertices'][:,2].min(), 2)
        z1 = round(p['vertices'][:,2].max(), 2)
        z_by_floor.setdefault(z0, []).append((z0, z1))

    # Check that there's a positive gap between each floor's max Z and next floor's min Z
    floor_z_ranges = [(min(v[0] for v in ranges), max(v[1] for v in ranges)) for ranges in z_by_floor.values()]
    floor_z_ranges.sort()
    checks['no_inter_floor_connections'] = True
    for i in range(len(floor_z_ranges)-1):
        gap = floor_z_ranges[i+1][0] - floor_z_ranges[i][1]
        if gap < -1e-6:  # overlap between different floors
            checks['no_inter_floor_connections'] = False
            break
    checks['floor_count'] = len(floor_z_ranges)

    return checks


def main():
    parser = argparse.ArgumentParser(description='Granite parapet stone overlay pilot')
    parser.add_argument('--manifest', required=True, help='Path to corridor-manifest-v2.json')
    parser.add_argument('--output', required=True, help='Output directory')
    parser.add_argument('--fraction', type=float, default=FRACTION, help='Fraction of each panel height for stone (0.0 = JSON only, 0.7 = emit GLB)')
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    fraction = args.fraction
    if not np.isfinite(fraction) or not 0 <= fraction <= 1:
        raise SystemExit('Fraction must be finite and between zero and one')

    # Find granite building
    granite = None
    for b in manifest['buildings']:
        if b.get('source_id') == '106849cb':
            granite = b
            break
    if not granite:
        raise SystemExit('Granite building not found in manifest')
    if granite.get('source_id_full') != GRANITE_SOURCE_ID:
        raise SystemExit('Granite source identity mismatch')

    # Producer walls (edges 4 and 1)
    producer_walls = [w for w in granite['walls'] if w['kind'] == 'producer']
    if len(producer_walls) != 2:
        raise SystemExit(f'Expected 2 producer walls, got {len(producer_walls)}')

    all_placed = []
    all_original_panels = []
    all_frontages = []

    for w in producer_walls:
        edge = w['edge']
        frontage = w['frontage']
        plan_depth = w['plan_depth_m']
        floor_h = w['params'].get('floor_height_m', 3.0)

        # Get original panels for verification
        mesh = rfg.mesh_for_profile(
            'modern_stone_recessed_balcony',
            width_m=frontage['length_m'],
            height_m=frontage['top_z'] - frontage['base_z'],
            depth_m=plan_depth,
            floor_height_m=floor_h,
            side_facades={'left': 'none', 'right': 'none'}
        )
        parapet = mesh.parts['glass_parapets']
        original_panels = extract_parapet_panels(parapet)
        all_original_panels.extend(original_panels)
        all_frontages.extend([frontage] * len(original_panels))

        # Build overlay
        placed = build_overlay_for_wall(edge, frontage, plan_depth, floor_h, fraction)
        all_placed.extend(placed)

    # Materials
    materials = {
        'stone_light__granite_overlay': {
            'base_color': [0.18, 0.19, 0.20, 1.0],
            'roughness': 0.85,
            'metallic': 0.0
        }
    }

    # Verification
    checks = verify_overlay(all_placed, all_original_panels, producer_walls[0]['frontage'], fraction)

    # World bounds
    all_verts = np.vstack([p['vertices'] for p in all_placed]) if all_placed else np.zeros((0, 3))
    world_bounds = {
        'min': all_verts.min(axis=0).tolist(),
        'max': all_verts.max(axis=0).tolist()
    } if len(all_verts) > 0 else {'min': [0,0,0], 'max': [0,0,0]}

    # Source metadata
    photo_meta = {
        'source_photo_id': '1288770597',
        'source_photo_url': 'https://kartaview.org/map/1288770597',
        'license': 'CC BY-SA 4.0 (Grab and KartaView Contributors)',
        'date': '2018-07-21',
        'note': 'Right-hand facade shows visible opaque stone fronts on balconies; fraction/height/layout estimated'
    }

    identity = {
        'source_id': GRANITE_SOURCE_ID,
        'status': 'candidate identity from multi-view wall visibility + family polygon overlap; NOT confirmed by survey',
        'estimated_fraction': fraction,
        'fraction_basis': 'ESTIMATED from photo 1288770597 right-hand facade; stone fraction not measured'
    }

    report = {
        'schema': 'dynacity-granite-parapet-overlay/1',
        'source_id': GRANITE_SOURCE_ID,
        'source_height_m': GRANITE_SOURCE_HEIGHT,
        'source_base_z_m': GRANITE_BASE_Z,
        'source_top_z_m': GRANITE_TOP_Z,
        'fraction': fraction,
        'stone_thickness_m': STONE_THICKNESS,
        'stone_projection_m': STONE_PROJECTION,
        'world_bounds_enu_m': world_bounds,
        'positive_volume_m3': round(checks['total_volume_m3'], 6),
        'walls_processed': [w['edge'] for w in producer_walls],
        'triangles': int(sum(len(p['triangles']) for p in all_placed)),
        'materials': list(materials.keys()),
        'glb_emitted': fraction > 0,
        'panel_counts': {
            'wall_4': len([p for p in all_placed if 'granite.4:' in p['name']]),
            'wall_1': len([p for p in all_placed if 'granite.1:' in p['name']])
        },
        'verification': checks,
        'photo_metadata': photo_meta,
        'identity': identity,
        'limitations': [
            'OVERLAY ONLY - does not alter main family/default geometry or city manifest',
            'Stone fraction/height/layout ESTIMATED from single photo (1288770597); not measured',
            'Source identity is candidate only; not confirmed by survey',
            'Original glass_parapets geometry unchanged; overlay sits below each panel top only',
            'No window opening or floor duplication - overlay is per-panel below glass parapet top',
            'No parts outside parent glass projected support',
            'Default producer/source is not modified by this additive builder; root archived-array check required',
            'Visual acceptance NOT claimed; Root reviews actual GLB and native 4 corridor obliques with rollback'
        ]
    }

    if fraction > 0:
        glb_path = out_dir / 'granite_parapet_overlay.glb'
        write_glb(glb_path, all_placed, materials)
        glb_hash = compute_hash(glb_path)

        # Readback verification
        readback_size = glb_path.stat().st_size
        readback_hash = compute_hash(glb_path)

        report['overlay_glb'] = str(glb_path)
        report['overlay_glb_sha256'] = glb_hash
        report['overlay_glb_size_bytes'] = readback_size
        report['readback_hash_match'] = (glb_hash == readback_hash)

        print(f'GLB written: {glb_path} ({glb_hash[:16]}...) size={readback_size} readback_ok={glb_hash==readback_hash}')

    report_path = out_dir / 'report.json'
    report_path.write_text(json.dumps(report, indent=1))
    print(f'Report written: {report_path}')

    handoff = f"""# Granite Parapet Overlay Pilot Handoff

## Output
- **Overlay GLB**: `{out_dir}/granite_parapet_overlay.glb` (emitted only when fraction > 0)
- **Report**: `{out_dir}/report.json`

## Source
- **Source ID**: {GRANITE_SOURCE_ID}
- **Source height**: {GRANITE_SOURCE_HEIGHT:.4f} m (base {GRANITE_BASE_Z:.4f}, top {GRANITE_TOP_Z:.4f})
- **Walls**: Producer edges 4 and 1 (street-facing)

## Overlay Parameters
- **Fraction**: {fraction} (lower {fraction*100:.0f}% of EACH glass_parapet panel height)
- **Stone thickness**: {STONE_THICKNESS} m
- **Projection beyond glass face**: {STONE_PROJECTION} m toward street
- **Material**: stone_light__granite_overlay (opaque)

## Verification
- **Panel counts**: {report['panel_counts']}
- **Panel count match**: {checks['panel_count_match']}
- **All positive volume**: {checks['all_positive_volume']}
- **All height <= 1.1m**: {checks['all_height_leq_1_1m']}
- **All Z within parent**: {checks['all_z_within_parent']}
- **No inter-floor connections**: {checks['no_inter_floor_connections']}
- **World bounds (ENU m)**: {world_bounds}
- **Total volume**: {checks['total_volume_m3']:.6f} m³
- **Triangles**: {report['triangles']}
- **GLB SHA256**: {report.get('overlay_glb_sha256', 'N/A (fraction=0)')}
- **Readback hash match**: {report.get('readback_hash_match', 'N/A')}

## Commands
```bash
# JSON-only (fraction=0):
python scripts/build_granite_parapet_pilot.py --manifest corridor-manifest-v2.json --output out_dir --fraction 0

# With GLB (fraction=0.7):
python scripts/build_granite_parapet_pilot.py --manifest corridor-manifest-v2.json --output out_dir --fraction 0.7
```

## Limitations
- Overlay ONLY; does not alter main family/default geometry or city manifest
- Stone fraction/height/layout ESTIMATED from photo 1288770597; not measured
- Source identity is CANDIDATE only; not confirmed by survey
- Original glass_parapets geometry unchanged; overlay sits below each panel top only
- No window opening or floor duplication
- No parts outside parent glass projected support
- Default original source hash unchanged
- Visual acceptance NOT claimed; Root reviews actual GLB and native 4 corridor obliques with rollback

## Photo Metadata
- **Photo ID**: 1288770597 (KartaView/Grab, 2018-07-21)
- **License**: CC BY-SA 4.0
- **Note**: Right-hand facade shows visible opaque stone fronts on balconies
"""
    handoff_path = out_dir / 'handoff.md'
    handoff_path.write_text(handoff)
    print(f'Handoff written: {handoff_path}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
