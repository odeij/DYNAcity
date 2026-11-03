"""Only the real parking cover meshes are matched; scene parents, other cover kinds and city layers are not."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest

sys.modules.setdefault('unreal', types.ModuleType('unreal'))
spec = importlib.util.spec_from_file_location('style_city_under_test', Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/style_city.py')
style_city = importlib.util.module_from_spec(spec)
spec.loader.exec_module(style_city)


@pytest.mark.parametrize('label,expected', [
    ('StreetCover_port_parking_9f1bda80', True),
    ('StreetCover_ras_beyrouth_parking_9f1bda80', True),
    ('StreetCover_minet_el_hosn_parking_9f1bda80', True),
    ('StreetCover_port_parking_9f1bda80_scene_0', False),   # scene parent without a mesh
    ('StreetCover_port_parking_scene_0', False),
    ('StreetCover_port_pedestrian_9f1bda80', False),        # the pale strips are a different cover kind
    ('StreetCover_port_park_9f1bda80', False),
    ('StreetCover_TerrainExtension', False),
    ('roads_port_material_1', False),
    ('source-ground', False),
])
def test_parking_cover_label_matching(label, expected):
    assert style_city.is_parking_cover_label(label) is expected
