import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine' / 'unreal' / 'Content' / 'Python'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import annotate_location_index_traffic as annotate  # noqa: E402
import traffic_local  # noqa: E402

VIEW = [35.5, 33.9, 35.51, 33.91]


def _index(**extra):
    import location_core
    x, y = location_core.lonlat_to_utm(35.505, 33.905)
    path = [[0.0, 0.0, 0.0], [200.0, 0.0, 0.0]]
    return {'origin_utm_m': [x, y, 0.0], 'roads': [{'id': 0, 'path': path, 'bounds': [0, 0, 200, 0], **extra}]}


def test_ambient_cars_use_observed_speed_above_old_cap():
    client = traffic_local.LocalTrafficClient(_index(speed_kmh=[90.0] * 24), VIEW, count=20)
    client.batch(VIEW)
    speeds = [a['speed'] * 3.6 for a in client.agents]
    assert speeds and min(speeds) >= 80 and max(speeds) > 50


def test_roads_without_observed_speed_keep_fallback_and_water_roads_are_skipped():
    client = traffic_local.LocalTrafficClient(_index(), VIEW, count=5)
    client.batch(VIEW)
    assert all(7 <= a['speed'] <= 13 for a in client.agents)
    assert not traffic_local.LocalTrafficClient(_index(over_water=True), VIEW).roads


def test_over_water_flags_sea_cells():
    ground = np.array([[0.0, 0.0], [5.0, 5.0]])
    assert annotate.over_water([[5, 15], [15, 15]], ground, 0.0, 20.0, 10.0)
    assert not annotate.over_water([[5, 5], [15, 5]], ground, 0.0, 20.0, 10.0)
