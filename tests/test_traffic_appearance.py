import importlib.util
from collections import Counter
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('traffic_appearance', Path(__file__).parents[1] / 'engine/unreal/Content/Python/traffic_appearance.py')
appearance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appearance)


def test_weighted_colours_match_target_and_are_stable():
    palette = [{'name': name, 'weight': weight} for name, weight in [('white', 29), ('black', 23), ('gray', 22), ('silver', 7), ('blue', 6), ('red', 3), ('green', 3), ('gold', 3), ('beige', 2), ('other', 2)]]
    ids = [str(i) for i in range(20000)]
    picked = {i: appearance.choose(i, palette, 'paint-v1')['name'] for i in ids}
    assert picked == {i: appearance.choose(i, palette, 'paint-v1')['name'] for i in reversed(ids)}
    counts = Counter(picked.values())
    for entry in palette:
        assert abs(counts[entry['name']] / len(ids) * 100 - entry['weight']) < 1


def test_only_body_paint_types_change_and_model_choice_preserves_trajectory():
    data = {'traffic_model_choices': {'car': [{'type_id': 'bmw', 'weight': 1}]}, 'traffic_type_groups': {'bmw': 0},
            'traffic_paint_palette': [{'weight': 1, 'rgb_linear': [0.7, 0.7, 0.7]}], 'traffic_paint_types': ['car']}
    appearance.validate(data)
    car = {'id': 'a', 'type_id': 'car', 'position': [1, 2, 3], 'heading_deg': 42}
    assert appearance.apply(car.copy(), data) == {**car, 'type_id': 'bmw', 'paint_rgb': [0.7, 0.7, 0.7]}
    bus = {**car, 'type_id': 'bus'}
    assert appearance.apply(bus.copy(), data) == bus


@pytest.mark.parametrize('weight', [0, -1, True, float('inf'), float('nan')])
def test_rejects_invalid_weights(weight):
    with pytest.raises(ValueError):
        appearance.choose('id', [{'weight': weight}], 'paint')


def test_rejects_missing_group_and_invalid_rgb():
    with pytest.raises(ValueError, match='native traffic group'):
        appearance.validate({'traffic_model_choices': {'car': [{'type_id': 'absent', 'weight': 1}]}})
    with pytest.raises(ValueError, match='colour channels'):
        appearance.validate({'traffic_paint_palette': [{'weight': 1, 'rgb_linear': [float('nan'), 0, 0]}]})
