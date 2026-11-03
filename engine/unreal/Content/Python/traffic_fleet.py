"""Install the reviewed everyday traffic assets without changing the player car."""
import json
from pathlib import Path

import unreal


def install_real(folder):
    """Install two reviewed real models and stable weighted body-paint metadata."""
    import city_world, library_assets
    from traffic_appearance import validate
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing traffic vehicles.')
    renderer = unreal.get_default_object(unreal.load_class(None, '/Script/DynaCityDrive.DynaMobilityWorld'))
    if renderer.get_editor_property('vehicles').get_editor_property('num_custom_data_floats') != 4:
        raise RuntimeError('Install the native per-instance traffic paint module first.')
    root = Path(folder)
    assets = {}
    for key, asset_key in [('bmw-e36-m3', 'bmw_e36_m3_real'), ('toyota-corolla', 'toyota_corolla_ke80_real')]:
        assets[key] = library_assets.import_prepared(root / 'prepared' / key, asset_key)
        mesh = unreal.load_asset(assets[key]['mesh'])
        editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        reduction = unreal.StaticMeshReductionOptions(
            auto_compute_lod_screen_size=False,
            reduction_settings=[unreal.StaticMeshReductionSettings(percent_triangles=fraction, screen_size=size)
                                for fraction, size in [(1.0, 1.0), (0.4, 0.25), (0.15, 0.08)]])
        if editor.set_lods(mesh, reduction) != 3:
            raise RuntimeError('Three traffic detail levels were not generated: ' + key)
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
        assets[key]['lod_count'] = editor.get_lod_count(mesh)
    path = city_world._root() / 'Saved/DynaCity/library-assets-active.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    previous = data.get('traffic_vehicle_assets', {})
    van = previous['delivery_van_innerscene']
    bus = previous['city_bus_innerscene']
    palette = json.loads((Path(__file__).parent / 'traffic-paint-palette.json').read_text(encoding='utf-8'))
    data.update(
        traffic_default_mesh=assets['bmw-e36-m3']['mesh'],
        traffic_fleet_meshes=[assets['toyota-corolla']['mesh'], van['mesh'], bus['mesh']],
        traffic_type_groups={'car': 0, 'car:bmw': 0, 'car:toyota': 1, 'taxi': 1, 'van': 2, 'light_goods': 2, 'bus': 3},
        traffic_model_choices={'car': [{'type_id': 'car:bmw', 'weight': 1}, {'type_id': 'car:toyota', 'weight': 1}]},
        traffic_model_source='Two-model illustrative presentation mix, equal weights; not measured Beirut make/model shares.',
        traffic_paint_palette=palette['palette'], traffic_paint_types=['car', 'taxi'],
        traffic_paint_source={key: value for key, value in palette.items() if key != 'palette'},
        traffic_vehicle_assets={**assets, 'delivery_van_innerscene': van, 'city_bus_innerscene': bus},
    )
    validate(data)
    backup = root / 'library-assets-before-real-fleet.json'
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    report = {'assets': assets, 'active_manifest': str(path), 'visually_verified': False,
              'paint_scope': palette['scope'], 'model_scope': data['traffic_model_source']}
    (root / 'import-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def install(folder):
    import city_world, library_assets
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing traffic vehicles.')
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaMobilityWorld')
    renderer = unreal.get_default_object(cls)
    if not all(hasattr(renderer, name) for name in ('configure_traffic_fleet', 'configure_traffic_types')):
        raise RuntimeError('Install the traffic fleet native module before importing vehicles.')
    keys = ('sedan_innerscene', 'delivery_van_innerscene', 'city_taxi_unique_innerscene', 'city_bus_innerscene')
    assets = {key: library_assets.import_prepared(Path(folder) / 'prepared' / key, key) for key in keys}
    saved = city_world._root() / 'Saved/DynaCity'
    path = saved / 'library-assets-active.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    backup = saved / 'library-assets-traffic.previous.json'
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    data.update(
        traffic_default_mesh=assets[keys[0]]['mesh'],
        traffic_fleet_meshes=[assets[key]['mesh'] for key in keys[1:]],
        traffic_type_groups={'car': 0, 'van': 1, 'light_goods': 1, 'taxi': 2, 'bus': 3},
        traffic_mixed=True,
        traffic_fleet_source='Historical 2015 Beirut aggregate mix as an illustrative scenario; not current measured traffic.',
        traffic_unrepresented_types=['minibus', 'truck1', 'truck2'],
        traffic_vehicle_assets=assets,
    )
    # Keep the existing display radius and limit; this changes presentation only.
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    report = {'assets': assets, 'active_manifest': str(path),
              'unrepresented_types': data['traffic_unrepresented_types'],
              'requires_backend_mobility_inputs': True, 'visually_verified': False}
    (saved / 'traffic-fleet-installed.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
