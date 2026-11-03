"""Finished-asset provenance and physical left-driver presentation contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/library_asset_core.py'
spec=importlib.util.spec_from_file_location('library_asset_core',path)
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)

def test_texture_source_supports_webp_extension_and_rejects_missing_source():
    assert core.texture_source_index({'source': 2}) == 2
    assert core.texture_source_index({'extensions': {'EXT_texture_webp': {'source': 0}}}) == 0
    with pytest.raises(ValueError,match='supported image source'):core.texture_source_index({})


def profile():
    return {'handedness':'left','mirror_cabin':False,'steering_ground_cm':[38,-34,80],
            'eye_chassis_cm':[-10,-34,35]}


def test_left_wheel_and_driver_seat_must_align_without_mirroring():
    core.validate_vehicle(profile())
    for changes in ({'handedness':'right'},{'mirror_cabin':True},
                    {'steering_ground_cm':[38,0,80]},{'steering_ground_cm':[38,34,80]},
                    {'eye_chassis_cm':[-10,34,35]},{'eye_chassis_cm':[-10,-65,35]}):
        with pytest.raises(ValueError,match='left-hand-drive'):core.validate_vehicle({**profile(),**changes})


@pytest.mark.parametrize('value',[[0,float('nan'),1],[0,float('inf'),1],[0,True,1],[0,1]])
def test_invalid_camera_coordinates_rejected(value):
    with pytest.raises(ValueError,match='Finite'):core.validate_vehicle({**profile(),'eye_chassis_cm':value})


def prepared(tmp_path,license='CC0 1.0'):
    source=tmp_path/'library.glb';source.write_bytes(b'original source content')
    fbx=tmp_path/'asset.fbx';fbx.write_bytes(b'converted source content')
    data={'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
          'fbx':str(fbx),'fbx_sha256':hashlib.sha256(fbx.read_bytes()).hexdigest(),'vehicle':profile()}
    (tmp_path/'prepared.json').write_text(json.dumps(data))
    (tmp_path/'source.json').write_text(json.dumps({'license':license}))
    return source,fbx


def test_licensed_unchanged_source_is_accepted(tmp_path):
    prepared(tmp_path,'CC BY 4.0')
    assert core.read_prepared(tmp_path)['provenance']['license']=='CC BY 4.0'


def test_missing_approved_license_and_changed_content_are_rejected(tmp_path):
    source,fbx=prepared(tmp_path,'Unknown')
    with pytest.raises(ValueError,match='licence'):core.read_prepared(tmp_path)
    prepared(tmp_path);fbx.write_bytes(b'changed geometry')
    with pytest.raises(ValueError,match='content changed: fbx'):core.read_prepared(tmp_path)
    prepared(tmp_path);source.write_bytes(b'changed source')
    with pytest.raises(ValueError,match='content changed: source'):core.read_prepared(tmp_path)


def test_truncated_fbx_material_name_matches_only_unique_original():
    name = 'Printed artwork and information panels (original abstract graphics)'
    material = {'name': name, 'pbrMetallicRoughness': {'metallicFactor': 0}}
    truncated = name[:63]
    assert core.material_definition(truncated, [material]) is material
    with pytest.raises(ValueError, match='Ambiguous'):
        core.material_definition(truncated, [material, {'name': truncated + ' different'}])
    with pytest.raises(ValueError, match='No source'):
        core.material_definition('Printed artwork', [material])
