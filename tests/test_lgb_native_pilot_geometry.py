"""Export topology checks independent of private Beirut imagery/data.
Also checks optional corner_slab_projection geometry: positive ring per band,
glass cut bands, mullions split at band intervals, watertight components.
"""
import importlib.util
import tempfile
from pathlib import Path
import math
import numpy as np
import pytest
from dynacity import reference_facade_geometry as G
from shapely.geometry import Polygon

spec = importlib.util.spec_from_file_location('lgb_native_builder', Path(__file__).parents[1] / 'scripts/build_lgb_native_pilot.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def _component_z_intervals(mesh, part_name):
    """Return list of (zmin, zmax) per connected component in a part."""
    v = np.asarray(mesh.parts[part_name]['verts'], float)
    f = np.asarray(mesh.parts[part_name]['faces'], int)
    from collections import defaultdict
    adj = defaultdict(set)
    for i, tri in enumerate(f):
        for a, b in [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]:
            e = (a, b) if a < b else (b, a)
            adj[e].add(i)
    seen = set()
    comps = []
    for i in range(len(f)):
        if i in seen:
            continue
        stack = [i]
        comp = []
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            comp.append(j)
            tri = f[j]
            for a, b in [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]:
                e = (a, b) if a < b else (b, a)
                for nb in adj[e]:
                    if nb not in seen:
                        stack.append(nb)
        if comp:
            zz = v[f[comp]].reshape(-1, 3)[:, 2]
            comps.append((float(zz.min()), float(zz.max())))
    return comps


def _band_intervals(mesh):
    """Extract band interior z-intervals from corner_floor_edges components."""
    cfe = np.asarray(mesh.parts['corner_floor_edges']['verts'], float)
    f = np.asarray(mesh.parts['corner_floor_edges']['faces'], int)
    # each floor band is a separate prism; get z-range per connected component
    intervals = []
    for zmin, zmax in _component_z_intervals(mesh, 'corner_floor_edges'):
        if zmax - zmin > 1e-4:
            intervals.append((zmin, zmax))
    return sorted(intervals)


def _watertight(mesh, part_name):
    """Check part is closed: every edge shared by exactly 2 faces with opposite winding."""
    v = np.asarray(mesh.parts[part_name]['verts'], float)
    f = np.asarray(mesh.parts[part_name]['faces'], int)
    edge_count = {}
    for tri in f:
        for a, b in [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]:
            edge_count[(a, b)] = edge_count.get((a, b), 0) + 1
    # each directed edge must appear exactly once and its reverse also once
    for (a, b), c in edge_count.items():
        if c != 1 or (b, a) not in edge_count:
            return False
    # positive total volume (prisms may be separate components but each prism should have positive vol)
    vol = sum(np.dot(v[t[0]], np.cross(v[t[1]], v[t[2]])) for t in f) / 6
    if vol <= 1e-10:
        return False
    return True


@pytest.mark.parametrize('proj', [0.0])
def test_default_unchanged_when_no_projection(proj):
    """Default proj=0 must produce identical corner_floor_edges ring from r-gt to r-st (no outer projection ring)."""
    m = G.mesh_for_profile('lgb_cream_redbrown_bands', 40.0, 45.0, depth_m=20.0, params={
        'glass_top_drop_m': 3.1, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8,
        'terrace_parapet_h_m': 0.2, 'frame_zone_frac': 0.4, 'corner_slab_projection_m': proj})
    r = 3.0
    gt = 0.08
    st = 0.35
    cfe = np.asarray(m.parts['corner_floor_edges']['verts'], float)
    radii = np.linalg.norm(cfe[:, :2] - np.array([r, r]), axis=1)
    assert radii.min() >= r - st - 1e-3
    assert radii.max() <= r - gt + 1e-3


def test_invalid_projection_rejected_at_builder_entry():
    """builder.build must raise ValueError for invalid projection before any I/O."""
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            builder.build(Path(tmp), 45.0, corner_slab_projection_m=-1)
        with pytest.raises(ValueError):
            builder.build(Path(tmp), 45.0, corner_slab_projection_m=0.7)
        with pytest.raises(ValueError):
            builder.build(Path(tmp), 45.0, corner_slab_projection_m=float('nan'))
        with pytest.raises(ValueError):
            builder.build(Path(tmp), 45.0, corner_slab_projection_m=float('inf'))


def _make_mesh(proj):
    """Return (mesh, band_intervals) for given projection."""
    m = G.mesh_for_profile('lgb_cream_redbrown_bands', 40.0, 45.0, depth_m=20.0, params={
        'glass_top_drop_m': 3.1, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8,
        'terrace_parapet_h_m': 0.2, 'frame_zone_frac': 0.4, 'corner_slab_projection_m': proj})
    return m, _band_intervals(m)


def test_projection_watertight_positive_volume():
    """proj=0.3 produces watertight closed components with positive volume each."""
    m, _ = _make_mesh(0.3)
    for name in ('corner_floor_edges', 'curved_corner_glass', 'corner_mullions'):
        assert _watertight(m, name), f'{name} not watertight/positive'


def test_glass_and_band_zones_do_not_overlap():
    """Glass and mullion components must not have z-intervals intersecting any band interior."""
    m_proj, bands = _make_mesh(0.3)
    m_base, _ = _make_mesh(0.0)  # baseline for original z-range
    # baseline glass max z
    base_glass_zmax = max(z for _, z in _component_z_intervals(m_base, 'curved_corner_glass'))
    base_mull_zmax = max(z for _, z in _component_z_intervals(m_base, 'corner_mullions'))
    base_mull_zmin = min(z for z, _ in _component_z_intervals(m_base, 'corner_mullions'))

    # check projected glass components
    for zmin, zmax in _component_z_intervals(m_proj, 'curved_corner_glass'):
        assert zmin >= -1e-6, f'glass below 0: {zmin}'
        assert zmax <= base_glass_zmax + 1e-6, f'glass exceeds baseline max: {zmax} > {base_glass_zmax}'
        for bzmin, bzmax in bands:
            # no positive overlap with band interior
            assert not (zmin < bzmax - 1e-6 and zmax > bzmin + 1e-6), \
                f'glass interval [{zmin:.3f},{zmax:.3f}] intersects band [{bzmin:.3f},{bzmax:.3f}]'

    # check projected mullion components
    for zmin, zmax in _component_z_intervals(m_proj, 'corner_mullions'):
        assert zmin >= base_mull_zmin - 1e-6, f'mullion below baseline min: {zmin}'
        assert zmax <= base_mull_zmax + 1e-6, f'mullion exceeds baseline max: {zmax}'
        for bzmin, bzmax in bands:
            assert not (zmin < bzmax - 1e-6 and zmax > bzmin + 1e-6), \
                f'mullion interval [{zmin:.3f},{zmax:.3f}] intersects band [{bzmin:.3f},{bzmax:.3f}]'


def test_projection_bands_at_correct_radius():
    """Projecting bands outer radius ~ r + proj (0.3m), inner ~ r - st (0.35m); center at (r,r)."""
    m, _ = _make_mesh(0.3)
    r = 3.0
    st = 0.35
    proj = 0.3
    cfe = np.asarray(m.parts['corner_floor_edges']['verts'], float)
    radii = np.linalg.norm(cfe[:, :2] - np.array([r, r]), axis=1)
    # outer radius approx r+proj, inner approx r-st
    assert abs(radii.max() - (r + proj)) < 1e-2, f'outer radius {radii.max():.4f} != {r+proj}'
    assert abs(radii.min() - (r - st)) < 1e-2, f'inner radius {radii.min():.4f} != {r-st}'


def test_glb_bytes_differ_between_proj0_and_proj03():
    """Deterministic in-memory GLB bytes must differ when projection > 0."""
    m0 = G.mesh_for_profile('lgb_cream_redbrown_bands', 40.0, 45.0, depth_m=20.0, params={
        'glass_top_drop_m': 3.1, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8,
        'terrace_parapet_h_m': 0.2, 'frame_zone_frac': 0.4, 'corner_slab_projection_m': 0.0})
    m1 = G.mesh_for_profile('lgb_cream_redbrown_bands', 40.0, 45.0, depth_m=20.0, params={
        'glass_top_drop_m': 3.1, 'upper_setback_x_m': 6.0, 'upper_setback_y_m': 1.8,
        'terrace_parapet_h_m': 0.2, 'frame_zone_frac': 0.4, 'corner_slab_projection_m': 0.3})
    b0 = m0.to_glb()
    b1 = m1.to_glb()
    assert b0 != b1, 'GLB bytes identical despite projection'
    # check projection extends bounds_min (negative X/Y direction from center at r,r)
    cfe0 = np.asarray(m0.parts['corner_floor_edges']['verts'], float)
    cfe1 = np.asarray(m1.parts['corner_floor_edges']['verts'], float)
    assert cfe1[:, 0].min() < cfe0[:, 0].min() - 0.01, 'X bounds_min should decrease with projection'
    assert cfe1[:, 1].min() < cfe0[:, 1].min() - 0.01, 'Y bounds_min should decrease with projection'


@pytest.mark.parametrize('outer,hole', [
    ([(0,0),(4,0),(4,4),(0,4)], [(1,1),(2,1),(2,2),(1,2)]),
    ([(0,0),(6,0),(6,2),(4,2),(4,5),(0,5)], [(1,1),(2,1),(2,3),(1,3)]),
])
def test_inner_recess_stays_open_in_caps_but_solid_is_closed(outer, hole):
    poly = Polygon(outer, holes=[hole])
    vertices, faces = builder.prism_with_holes(poly, 2., 5.)
    closed, volume = builder.closed_and_outward(vertices, faces)
    assert closed
    assert volume == pytest.approx(poly.area * 3., abs=1e-6)
    caps = [Polygon(vertices[t,:2]) for t in faces if np.ptp(vertices[t,2]) < 1e-9]
    assert caps
    for cap in caps:
        assert cap.intersection(Polygon(hole)).area < 1e-8
