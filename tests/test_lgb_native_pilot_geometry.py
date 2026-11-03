"""Export topology checks independent of private Beirut imagery/data."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
from shapely.geometry import Polygon

spec = importlib.util.spec_from_file_location('lgb_native_builder', Path(__file__).parents[1] / 'scripts/build_lgb_native_pilot.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

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
