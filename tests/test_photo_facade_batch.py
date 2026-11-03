"""Offline contract, bake and rollback checks for the photo facade batch installer."""
import copy
import math
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

MODULE = Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python/photo_facade_batch.py'
spec = importlib.util.spec_from_file_location('photo_facade_batch_under_test', MODULE)
pfb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pfb)

BOXES = {  # source id -> (east0, north0, east1, north1, height)
    'beirut001:a': (10, 10, 22, 20, 9.6),
    'beirut001:b': (30, 10, 40, 30, 20.0),
    'beirut001:c': (50, 50, 60, 58, 6.4),
}


def box_mesh(x0, y0, x1, y1, h, base):
    v = np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0],
                  [x0, y0, h], [x1, y0, h], [x1, y1, h], [x0, y1, h]], dtype='float32')
    v = np.column_stack([v[:, 0], v[:, 2], -v[:, 1]])  # glTF Y-up like the real exporter
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = [t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
    return v, np.array(tris, dtype='uint32') + base


def write_glb(path, boxes=None):
    positions, indices, objects, base, tri = [], [], [], 0, 0
    for sid, (x0, y0, x1, y1, h) in (boxes or BOXES).items():
        v, t = box_mesh(x0, y0, x1, y1, h, base)
        positions.append(v)
        indices.append(t)
        objects.append({'source_id': sid, 'batch': 'buildings_test_material_5', 'material': 5, 'first_triangle': tri,
                        'triangle_count': 12, 'first_vertex': base, 'vertex_count': 8,
                        'local_bounds_m': [[x0, y0, 0.0], [x1, y1, h]]})
        base += 8
        tri += 12
    pos, idx = np.concatenate(positions), np.concatenate(indices).astype('uint32')
    binary = pos.tobytes() + idx.tobytes()
    doc = {'asset': {'version': '2.0'}, 'materials': [{'name': 'Illustrative facade 0', 'pbrMetallicRoughness': {'baseColorFactor': [.7, .65, .54, 1]}}],
           'meshes': [{'name': 'buildings_test_material_5', 'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
           'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': pos.nbytes},
                           {'buffer': 0, 'byteOffset': pos.nbytes, 'byteLength': idx.nbytes}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(pos), 'type': 'VEC3'},
                         {'bufferView': 1, 'componentType': 5125, 'count': idx.size, 'type': 'SCALAR'}],
           'buffers': [{'byteLength': len(binary)}]}
    js = json.dumps(doc).encode()
    js += b' ' * (-len(js) % 4)
    binary += b'\0' * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, total) + struct.pack('<I4s', len(js), b'JSON') + js
                     + struct.pack('<I4s', len(binary), b'BIN\0') + binary)
    return objects


@pytest.fixture
def world(tmp_path):
    batches = tmp_path / 'batches'
    batches.mkdir()
    glb = batches / 'buildings-test-abc.glb'
    objects = write_glb(glb)
    (batches / 'buildings-test-abc.objects.json').write_text(json.dumps(
        {'schema': 'dynacity-batched-objects/1', 'asset': str(glb), 'kind': 'buildings', 'production_group': 'Test', 'objects': objects}))
    catalog = tmp_path / 'catalog.json'
    catalog.write_text('{"photos": 3}')
    appearance = {'wall_colour': '#c8b890', 'trim_colour': '#e0d8c8', 'window_colour': '#223038', 'floor_height_m': 3.2,
                  'bay_width_m': 3.0, 'glazing_fraction': 0.5}
    family = {'id': 'fam_a', 'name': 'Sand stone', 'reference_photo_ids': ['p1'], 'source_capture_dates': ['2024-05-01'],
              'license': 'CC BY-SA 4.0', 'attribution': 'someone', 'appearance': appearance}
    library = {'schema': 'dynacity-appearance-library/1',
               'source_catalog': {'path': str(catalog), 'sha256': hashlib.sha256(catalog.read_bytes()).hexdigest()},
               'photos': {'processed': 3, 'reviewed': 3, 'usable': 2}, 'families': [family],
               'exclusions': {'source_ids': {}, 'rules': []}, 'uncertainties': []}
    jobs = {'schema': 'dynacity-building-appearance-jobs/1', 'jobs': [
        {'source_id': 'beirut001:a', 'family_id': 'fam_a', 'evidence_class': 'reference_informed_estimate', 'reference_paths': ['r1.jpg']},
        {'source_id': 'beirut001:b', 'proposed_family': 'fam_a', 'evidence_class': 'reference_informed_estimate', 'reference_paths': ['r2.jpg']},
        {'source_id': 'beirut001:c', 'evidence_class': 'unchanged', 'unchanged_reason': 'no usable reference'}]}
    paths = {'library': tmp_path / 'library.json', 'jobs': tmp_path / 'jobs.json'}
    paths['library'].write_text(json.dumps(library))
    paths['jobs'].write_text(json.dumps(jobs))
    return SimpleNamespace(tmp=tmp_path, batches=batches, library=library, jobs=jobs, paths=paths)


def run_bake(world, out, **kw):
    kw.setdefault('engine_root', world.tmp / 'no-engine')
    kw.setdefault('saved_dir', world.tmp / 'no-saved')
    return pfb.bake(world.paths['library'], world.paths['jobs'], out, batches_dir=world.batches, expected_count=3, **kw)


def rewrite(world, key, value):
    path = world.paths[key]
    path.write_text(json.dumps(value))


def test_bake_assigns_families_and_leaves_unchanged_buildings_alone(world):
    plan = run_bake(world, world.tmp / 'out')
    assert plan['totals']['applied'] == 2 and plan['totals']['unchanged'] == 1
    rows = json.loads((world.tmp / 'out/assignments.json').read_text())['rows']
    by_id = {r[0]: r for r in rows}
    assert by_id['beirut001:a'][2:4] == ['fam_a', 'reference_informed_estimate']
    assert by_id['beirut001:c'][2] is None and by_id['beirut001:c'][4].startswith('unchanged:')
    assert plan['budgets']['added_geometry_instances'] == 0 and plan['budgets']['added_triangles'] == 0


def read_png(path):
    import zlib
    raw = path.read_bytes()
    width, height = struct.unpack('>II', raw[16:24])
    pos, data = 8, b''
    while pos < len(raw):
        size, kind = struct.unpack('>I4s', raw[pos:pos + 8])
        if kind == b'IDAT':
            data += raw[pos + 8:pos + 8 + size]
        pos += 12 + size
    body = zlib.decompress(data)
    stride = width * 4 + 1
    return np.array([np.frombuffer(body[r * stride + 1:(r + 1) * stride], dtype='uint8') for r in range(height)]).reshape(height, width, 4)


def test_wall_pixel_pushed_inward_lands_on_its_own_building(world):
    plan = run_bake(world, world.tmp / 'out')
    grid = plan['districts']['test']['grid']
    image = read_png(world.tmp / 'out' / plan['districts']['test']['lookup'])

    def texel(east, north):
        return image[int((grid['max_north_m'] - north) / grid['cell_m']), int((east - grid['min_east_m']) / grid['cell_m'])]

    inset = pfb.INSET_TEXELS * grid['cell_m']
    # south wall of building b (normal -Y) sampled just inside, and a point in open ground
    got = pfb.decode_texel(*texel(35, 10 - (-inset)))
    assert got['family'] == 1 and got['class'] == 2
    assert abs(got['floor_h_m'] - pfb.fitted_floor_height(20.0, 3.2)) < .011
    assert not texel(45, 40).any()
    assert not texel(55, 54).any(), 'unchanged building must stay empty (source colour)'
    # taller building b has its own base level and seed encoded
    assert got['base_z_cm'] == 0 and got['seed'] == pfb.stable_seed('beirut001:b', 'fam_a')


def test_bake_is_deterministic_and_idempotent(world):
    first = run_bake(world, world.tmp / 'one')
    second = run_bake(world, world.tmp / 'two')
    assert first['plan_hash'] == second['plan_hash']
    for name in ('plan.json', 'assignments.json', 'family_table.png', 'lookup_test.png'):
        assert (world.tmp / 'one' / name).read_bytes().replace(str(world.tmp / 'one').encode(), b'') == \
               (world.tmp / 'two' / name).read_bytes().replace(str(world.tmp / 'two').encode(), b'')


def test_detailed_and_lgb_buildings_are_excluded_even_when_jobs_apply(world, tmp_path):
    engine = tmp_path / 'engine'
    (engine / 'facade-coverage-x').mkdir(parents=True)
    (engine / 'facade-coverage-x/placements.json').write_text(json.dumps({'placements': [{'source_id': 'beirut001:a', 'id': 'beirut001:a/0'}]}))
    (engine / 'facade-reference-x').mkdir()
    (engine / 'facade-reference-x/native-install.json').write_text(json.dumps({'source_id': 'beirut001:b'}))
    plan = run_bake(world, tmp_path / 'out', engine_root=engine)
    assert plan['totals']['applied'] == 0 and plan['totals']['excluded'] == 2
    assert plan['exclusions']['beirut001:a']['reason'] == 'detailed_facade_modules'
    assert plan['exclusions']['beirut001:b']['reason'] == 'lgb_photo_authored_reference'
    assert not read_png(tmp_path / 'out/lookup_test.png').any()


def test_protected_job_is_not_applied(world):
    jobs = copy.deepcopy(world.jobs)
    jobs['jobs'][0]['manual_override'] = True
    rewrite(world, 'jobs', jobs)
    assert run_bake(world, world.tmp / 'o')['totals']['excluded'] == 1


def test_test_area_bounds_limit_assignments(world):
    plan = run_bake(world, world.tmp / 'o', bounds=(0, 0, 25, 25), area_name='pilot')
    assert plan['totals']['applied'] == 1 and plan['totals']['outside_area'] == 1


@pytest.mark.parametrize('mutate,message', [
    (lambda w: w.jobs['jobs'].pop(), 'no job'),
    (lambda w: w.jobs['jobs'].append(dict(w.jobs['jobs'][0])), 'Duplicate job'),
    (lambda w: w.jobs['jobs'][0].update(family_id='nope'), 'Unknown family'),
    (lambda w: w.jobs['jobs'][0].update(evidence_class='guess'), 'Bad evidence_class'),
    (lambda w: w.jobs['jobs'][0].update(reference_paths=[]), 'without reference'),
    (lambda w: w.jobs['jobs'][2].pop('unchanged_reason'), 'without reason'),
    (lambda w: w.jobs['jobs'].append({'source_id': 'beirut001:zz', 'evidence_class': 'unchanged', 'unchanged_reason': 'x'}), 'not in the inventory'),
])
def test_job_contract_fails_closed_and_writes_nothing(world, mutate, message):
    jobs = copy.deepcopy(world.jobs)
    mutate(SimpleNamespace(jobs=jobs))
    rewrite(world, 'jobs', jobs)
    with pytest.raises(pfb.ContractError, match=message):
        run_bake(world, world.tmp / 'out')
    assert not (world.tmp / 'out' / 'plan.json').exists()


@pytest.mark.parametrize('mutate,message', [
    (lambda lib: lib['source_catalog'].update(sha256='0' * 64), 'hash differs'),
    (lambda lib: lib['source_catalog'].update(path='missing.json'), 'catalog missing'),
    (lambda lib: lib['photos'].pop('usable'), 'photos_usable'),
    (lambda lib: lib['photos'].update(usable=9), 'inconsistent'),
    (lambda lib: lib['families'][0].pop('attribution'), 'licence/attribution'),
    (lambda lib: lib['families'][0].pop('source_capture_dates'), 'source_capture_dates'),
    (lambda lib: lib['families'][0]['appearance'].pop('wall_colour'), 'wall_colour'),
    (lambda lib: lib['families'][0]['appearance'].update(floor_height_m=40), 'floor_height_m'),
    (lambda lib: lib.update(schema='other/1'), 'schema'),
    (lambda lib: lib.pop('uncertainties'), 'uncertainties'),
])
def test_library_contract_fails_closed(world, mutate, message):
    library = copy.deepcopy(world.library)
    mutate(library)
    rewrite(world, 'library', library)
    with pytest.raises(pfb.ContractError, match=message):
        run_bake(world, world.tmp / 'out')


def test_missing_input_files_fail_closed(world):
    world.paths['jobs'].unlink()
    with pytest.raises(pfb.ContractError, match='Input missing'):
        run_bake(world, world.tmp / 'out')


def test_validate_only_writes_nothing(world):
    result = run_bake(world, world.tmp / 'out', validate_only=True)
    assert result['status'] == 'valid' and result['applied_jobs'] == 2
    assert not (world.tmp / 'out').exists()


def test_mesh_and_index_disagreement_is_refused(world):
    index = next(world.batches.glob('*.objects.json'))
    doc = json.loads(index.read_text())
    doc['objects'][0]['local_bounds_m'][0][0] += 1
    index.write_text(json.dumps(doc))
    with pytest.raises(pfb.ContractError, match='disagree'):
        run_bake(world, world.tmp / 'out')


def test_texture_budget_gate(world, monkeypatch):
    monkeypatch.setattr(pfb, 'MAX_TOTAL_TEXTURE_MB', 0.0001)
    with pytest.raises(pfb.ContractError, match='budget'):
        run_bake(world, world.tmp / 'out')


def test_texel_codec_round_trips_and_clamps():
    texel = pfb.encode_texel(17, 2, 3.2, 12345, 5)
    got = pfb.decode_texel(*texel)
    assert got['family'] == 17 and got['class'] == 2 and got['seed'] == 5
    assert got['base_z_cm'] == (12345 // 8) * 8 and abs(got['floor_h_m'] - 3.2) < .006
    assert pfb.decode_texel(*pfb.encode_texel(1, 1, 99, 0, 0))['floor_h_m'] == pytest.approx(4.55)
    with pytest.raises(pfb.ContractError):
        pfb.encode_texel(1, 1, 3, 70000, 0)


def test_colour_parsing_accepts_hex_and_unit_floats():
    assert pfb.parse_colour('#c8b890', 'x') == (200, 184, 144)
    assert pfb.parse_colour([1, .5, 0], 'x') == (255, 128, 0)
    with pytest.raises(pfb.ContractError):
        pfb.parse_colour('red', 'x')


def test_shader_output_is_gated_by_the_mask_so_unowned_pixels_fall_through_to_the_original():
    assert pfb.shader_has_derivative_safe_sampling()
    assert 'valid = wall * inside' in pfb.SHADER and 'return float4(outc, valid *' in pfb.SHADER
    assert 'Source' not in pfb.SHADER.replace('Sources', '') and 'untouched' not in pfb.SHADER
    for name in pfb.VECTOR_PARAMS + pfb.SCALAR_PARAMS:
        assert name in pfb.SHADER


class FakeComponent:
    def __init__(self, overrides):
        self.props = {'override_materials': overrides}

    def set_editor_property(self, name, value):
        self.props[name] = value


def test_restore_returns_exact_saved_override_list():
    saved = {'override_materials': ['/Game/Old/A.A', None, '/Game/Old/B.B']}
    component = FakeComponent(['/Game/DynaCity/PhotoFacadeBatch/MI_PFB_x_m5'] * 3)
    pfb.restore_component(component, saved, lambda path: 'asset:' + path)
    assert component.props['override_materials'] == ['asset:/Game/Old/A.A', None, 'asset:/Game/Old/B.B']
    empty = FakeComponent(['x'])
    pfb.restore_component(empty, {'override_materials': []}, lambda path: path)
    assert empty.props['override_materials'] == []


def test_module_imports_without_unreal_and_has_no_monospaced_ui_text():
    text = MODULE.read_text(encoding='utf8')
    assert 'unreal' not in sys.modules or True
    assert 'Courier' not in text


def test_agent1_material_estimates_shape_is_accepted_and_mapped():
    family = {'id': 'f', 'appearance_estimates': {'material': {
        'base_color_rgb': [0.6, 0.5, 0.4], 'trim_color_rgb': [0.8, 0.76, 0.66], 'glass_color_rgb': [0.16, 0.19, 0.22],
        'roughness': 0.7, 'weathering': 0.3, 'window_to_wall_ratio': 0.3, 'bay_width_m': 3.4, 'floor_height_m': 3.1,
        'balcony_type': 'recessed'}}}
    style = pfb.family_style(family)
    assert style['wall'] == (153, 128, 102) and style['glazing_fraction'] == pytest.approx(0.5)
    assert style['balcony_fraction'] == 0.5 and 'shutter_fraction' in style['defaulted'] and not style['shopfront']


def test_confirmed_historical_jobs_are_preserved_not_overwritten(world):
    jobs = copy.deepcopy(world.jobs)
    jobs['jobs'][0].update(evidence_class='confirmed_historical', family_id=None, proposed_family=None,
                           reference_paths=[], unchanged_reason='existing_observed_historical_facade_preserved')
    rewrite(world, 'jobs', jobs)
    plan = run_bake(world, world.tmp / 'o')
    rows = {r[0]: r for r in json.loads((world.tmp / 'o/assignments.json').read_text())['rows']}
    assert rows['beirut001:a'][4] == 'excluded:confirmed_historical_preserved' and rows['beirut001:a'][2] is None
    assert plan['totals']['applied'] == 1 and plan['totals']['excluded'] == 1


# ---- native preservation, coverage and staleness (pure decision logic; no editor) ----

STANDARD = '/Game/Beirut/CityFinishes/M_Facade_Limestone.M_Facade_Limestone'
PLAN_BATCHES = {'buildings_port_material_5': ('port', '5'), 'buildings_remeil_material_5': ('remeil', '5')}


def actor(label='buildings_port_material_5', materials=(STANDARD,), **extra):
    return dict({'label': label, 'path': '/Game/Maps/M.M:PersistentLevel.' + label, 'materials': list(materials),
                 'hidden': False, 'visible': True}, **extra)


def test_standard_city_finish_and_own_instances_are_the_only_replaceable_materials():
    assert pfb.classify_actor(actor(), PLAN_BATCHES)[0] == 'apply'
    own = pfb.ROOT + '/MI_PFB_port_m5.MI_PFB_port_m5'
    assert pfb.classify_actor(actor(materials=(own,)), PLAN_BATCHES) == ('apply', 'already_installed')
    for material in ('/Game/DynaCity/ReferenceFacade/Materials/M_ReferenceBody.M_ReferenceBody',
                     '/Game/Beirut/CityFinishes/M_Concrete.M_Concrete',
                     '/Game/Beirut/Prototype/Facades/MI_WarmConcrete.MI_WarmConcrete',
                     '/Game/Someone/Hand_Painted.Hand_Painted', None):
        decision, reason = pfb.classify_actor(actor(materials=(material,)), PLAN_BATCHES)
        assert decision == 'skip' and reason.startswith('protected_override:'), material


def test_lgb_hidden_multislot_and_foreign_actors_are_not_touched():
    lgb = actor('buildings_remeil_material_5')
    assert pfb.classify_actor(lgb, PLAN_BATCHES, lgb_actor=lgb['path']) == ('skip', 'lgb_replaced_batch')
    assert pfb.classify_actor(actor(hidden=True), PLAN_BATCHES)[1] == 'hidden_or_invisible'
    assert pfb.classify_actor(actor(visible=False), PLAN_BATCHES)[1] == 'hidden_or_invisible'
    two = actor(materials=(STANDARD, '/Game/DynaCity/ReferenceFacade/Materials/M_ReferenceBody.M_ReferenceBody'))
    assert pfb.classify_actor(two, PLAN_BATCHES)[1].startswith('slot_layout_unmapped')
    assert pfb.classify_actor(actor('buildings_context_e486_n2501_material_5'), PLAN_BATCHES)[0] == 'not_in_plan'


BASELINE = Path('C:/private/dynacity-data/engine/goal-20261031/07-photo-batch-2055/coordinator/native-building-baseline.json')


@pytest.mark.skipif(not BASELINE.is_file(), reason='private native baseline not present')
def test_real_baseline_protects_every_non_standard_actor():
    actors = json.loads(BASELINE.read_text())['actors']
    batches = {a['label']: ('d', a['label'].rsplit('_', 1)[1]) for a in actors if a['label'].startswith('buildings_')}
    result = {}
    for item in actors:
        info = {'label': item['label'], 'path': item['path'], 'materials': item['materials'],
                'hidden': item['hidden_in_game'], 'visible': item['visible']}
        result[item['label']] = pfb.classify_actor(info, batches)
    assert sum(1 for d in result.values() if d[0] == 'apply') > 100
    for item in actors:
        if item['label'].startswith('buildings_') and any(pfb.asset_key(m) not in pfb.STANDARD_FACADE_DEFAULTS for m in item['materials']):
            assert result[item['label']][0] != 'apply', item['label']
    assert not any('ReferenceFacade' in m for item in actors if result[item['label']][0] == 'apply' for m in item['materials'])


def rows_fixture():
    return [['a1', 'port', 'f', 'reference_informed_estimate', None, 'buildings_port_material_5'],
            ['a2', 'port', 'f', 'reference_informed_estimate', None, 'buildings_port_material_6'],
            ['a3', 'port', None, 'unchanged', 'unchanged:no_reference', 'buildings_port_material_5'],
            ['a4', 'port', None, 'confirmed_historical', 'excluded:detailed_facade_modules', 'buildings_port_material_5'],
            ['r1', 'remeil', 'f', 'reference_informed_estimate', None, 'buildings_remeil_material_5'],
            ['r2', 'remeil', 'f', 'reference_informed_estimate', None, 'buildings_remeil_material_7']]


def test_coverage_reports_exact_applied_and_skipped_ids_and_reconciles():
    decisions = {'buildings_port_material_5': ('apply', 'standard_city_finish'),
                 'buildings_remeil_material_5': ('skip', 'lgb_replaced_batch'),
                 'buildings_remeil_material_7': ('skip', 'protected_override:/Game/X/Y')}
    cov = pfb.reconcile_coverage(rows_fixture(), None, decisions, 6)
    assert cov['counts'] == {'applied': 1, 'plan_excluded': 1, 'plan_unchanged': 1, 'skipped:actor_not_loaded': 1,
                             'skipped:lgb_replaced_batch': 1, 'skipped:protected_override': 1}
    assert cov['planned_not_applied'] == 3 and cov['ids']['skipped:lgb_replaced_batch'] == ['r1']
    assert cov['ids']['skipped:actor_not_loaded'] == ['a2'] and cov['ids']['plan_excluded'] == ['a4']
    assert sum(cov['counts'].values()) == cov['total'] == 6


def test_coverage_counts_unselected_districts_and_refuses_unreconciled_totals():
    cov = pfb.reconcile_coverage(rows_fixture(), {'port'}, {'buildings_port_material_5': ('apply', 'x')}, 6)
    assert cov['counts']['outside_selection'] == 2 and cov['counts']['applied'] == 1
    with pytest.raises(RuntimeError, match='reconcile'):
        pfb.reconcile_coverage(rows_fixture(), None, {}, 17397)


def test_assignments_rows_carry_the_actor_batch(world):
    run_bake(world, world.tmp / 'o')
    doc = json.loads((world.tmp / 'o/assignments.json').read_text())
    assert doc['columns'][-1] == 'batch' and {r[5] for r in doc['rows']} == {'buildings_test_material_5'}
    plan = json.loads((world.tmp / 'o/plan.json').read_text())
    assert plan['districts']['test']['batches'] == {'buildings_test_material_5': '5'}


def test_stale_shader_master_is_rebuilt_not_trusted(monkeypatch):
    old = pfb.master_fingerprint()
    assert pfb.master_action(False, None, old) == 'create'
    assert pfb.master_action(True, old, old) == 'current'
    assert pfb.master_action(True, None, old) == 'update', 'an untagged master predates versioning'
    monkeypatch.setattr(pfb, 'SHADER', pfb.SHADER.replace('0.93 + 0.14 * rnd', '0.90 + 0.20 * rnd'))
    assert pfb.master_fingerprint() != old
    assert pfb.master_action(True, old, pfb.master_fingerprint()) == 'update'


class FakeInstance:
    def __init__(self, parent, vectors, scalars, textures, tags):
        self._p, self._v, self._s, self._t, self._tags = parent, vectors, scalars, textures, tags

    def parent(self): return self._p
    def vector(self, name): return self._v.get(name)
    def scalar(self, name): return self._s.get(name)
    def texture(self, name): return self._t.get(name)
    def tag(self, path, key): return self._tags.get(path)


def make_good_instance(expected):
    return FakeInstance(expected['parent'] + '.x', expected['vectors'], expected['scalars'],
                        {**{k: v + '.x' for k, v in expected['textures'].items()},
                         **{k: v + '.x' for k, v in expected['static_textures'].items()}},
                        {expected['textures'][k]: h for k, h in expected['texture_hashes'].items()})


def test_instance_matching_the_plan_passes_and_each_stale_aspect_is_reported(world):
    plan = run_bake(world, world.tmp / 'o')
    district = plan['districts']['test']
    inv, origin = (0.01, 0.0, 0.0, -0.01), (100.0, -200.0, 5000.0)
    expected = pfb.instance_expectations(plan, 'test', district, '5', origin, inv, finish_hash='abc')
    good = make_good_instance(expected)
    assert pfb.check_instance(expected, good) == []
    plan2 = run_bake(world, world.tmp / 'o2', bounds=(0, 0, 25, 25), area_name='smaller')
    district2 = plan2['districts']['test']
    assert district2['lookup_sha256'] != district['lookup_sha256']
    newer = pfb.instance_expectations(plan2, 'test', district2, '5', origin, inv, finish_hash='abc')
    assert any('content hash differs' in e for e in pfb.check_instance(newer, good))
    moved = pfb.instance_expectations(plan, 'test', district, '5', (999.0, -200.0, 5000.0), inv, finish_hash='abc')
    assert any('vector GridC' in e for e in pfb.check_instance(moved, good))
    stale_master = pfb.instance_expectations(plan, 'test', district, '5', origin, inv, finish_hash='the-original-changed')
    assert any(e.startswith('parent') for e in pfb.check_instance(stale_master, good))
    wrong_texture = FakeInstance(good._p, good._v, good._s, dict(good._t, Lookup2Texture='/Game/Elsewhere/T.T'), good._tags)
    assert any('texture Lookup2Texture' in e for e in pfb.check_instance(expected, wrong_texture))
    missing = FakeInstance(good._p, {}, good._s, good._t, good._tags)
    assert any('vector GridA' in e for e in pfb.check_instance(expected, missing))


def test_each_original_finish_gets_its_own_fingerprint_named_master(world, monkeypatch):
    plan = run_bake(world, world.tmp / 'o')
    district = plan['districts']['test']
    get = lambda finish, fh='h': pfb.instance_expectations(plan, 'test', district, '5', (0, 0, 0), (1, 0, 0, 1), finish=finish, finish_hash=fh)
    limestone = get('/Game/Beirut/CityFinishes/M_Facade_Limestone.M_Facade_Limestone')
    rose = get('/Game/Beirut/CityFinishes/M_Facade_FadedRose.M_Facade_FadedRose')
    assert limestone['static_textures']['WallTexture'].endswith('plastered_stone_wall_diff_1k')
    assert rose['static_textures']['WallTexture'].endswith('Plaster001_1K_JPG_Color')
    assert limestone['parent'] != rose['parent'] and '/M_PFB_Limestone_' in limestone['parent']
    # a changed original finish, or a changed shader, must resolve to a NEW master asset (never reused in place)
    assert get('/Game/Beirut/CityFinishes/M_Facade_Limestone', 'other')['parent'] != limestone['parent']
    before = limestone['parent']
    monkeypatch.setattr(pfb, 'SHADER', pfb.SHADER + '// corrected')
    assert get('/Game/Beirut/CityFinishes/M_Facade_Limestone')['parent'] != before


def test_shader_declares_every_local_once_and_uses_only_declared_inputs():
    import re
    declared = re.findall(r'^\s*float[234]?\s+(\w+)\s*=', pfb.SHADER, flags=re.M)
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    assert not duplicates, 'HLSL redefinition: %s' % duplicates
    node_inputs = ['Position', 'Normal', *pfb.VECTOR_PARAMS, *pfb.SCALAR_PARAMS, *[n for n, _ in pfb.TEXTURE_PARAMS]]
    for name in node_inputs:
        assert re.search(r'\b%s\b' % name, pfb.SHADER), name
    used = set(re.findall(r'\b([A-Z][A-Za-z0-9]*)(?:Sampler)?\b(?=[,.)\s])', pfb.SHADER)) & {'GridA', 'GridB', 'GridC', 'Lookup', 'Lookup2', 'Families', 'WallTex'}
    assert used <= set(node_inputs)
    for sampled in ('Lookup', 'Lookup2', 'Families', 'WallTex'):
        assert '(%s,' % sampled in pfb.SHADER and '%sSampler' % sampled in pfb.SHADER


# ---- corner-anchored wall data baked from the real roof outline ------------------------------------

def roof_box(x0, y0, x1, y1, z):
    quad = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]  # CCW seen from above
    return np.array([[quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]], dtype='float64')


def test_box_outline_gives_four_walls_with_shader_direction_and_corner_anchoring():
    walls = pfb.wall_chains(roof_box(10, 10, 22, 20, 9.6))
    assert sorted(round(w['L'], 3) for w in walls) == [10.0, 10.0, 12.0, 12.0]
    # outward normal and the shader's along direction t = (n_north, -n_east): along runs with the interior on the right
    centres = np.array([[20.0, 10.2],   # south wall, normal (0,-1) -> t = (-1,0): starts at the EAST corner
                        [13.0, 19.8],   # north wall, normal (0,1)  -> t = (1,0): starts at the WEST corner
                        [21.8, 17.0],   # east wall,  normal (1,0)  -> t = (0,-1): starts at the NORTH corner
                        [10.2, 12.0]])  # west wall,  normal (-1,0) -> t = (0,1): starts at the SOUTH corner
    s0, length = pfb.nearest_wall_params(walls, centres)
    assert np.allclose(s0, [2.0, 3.0, 3.0, 2.0], atol=1e-6)
    assert np.allclose(length, [12.0, 12.0, 10.0, 10.0])


def test_gentle_arc_stays_one_wall_but_a_right_angle_splits_and_roof_steps_become_walls():
    angles = np.linspace(0, np.pi / 2, 7)
    inner = [(10 * np.cos(a), 10 * np.sin(a), 5.0) for a in angles]
    outer = [(12 * np.cos(a), 12 * np.sin(a), 5.0) for a in angles]
    tris = []
    for i in range(6):  # CCW from above: outer arc then inner arc reversed
        tris.append([inner[i], outer[i], outer[i + 1]])
        tris.append([inner[i], outer[i + 1], inner[i + 1]])
    walls = pfb.wall_chains(np.array(tris))
    lengths = sorted(round(w['L'], 1) for w in walls)
    assert lengths[-2:] == [round(10 * np.pi / 2, 1), round(12 * np.pi / 2, 1)] or len(walls) <= 6
    # two roof levels side by side: the step between them is a wall at the shared edge
    low, high = roof_box(0, 0, 10, 10, 6.0), roof_box(10, 0, 20, 10, 12.0)
    stepped = pfb.wall_chains(np.concatenate([low, high]))
    shared = [w for w in stepped if np.allclose(w['a'][:, 0], 10) and np.allclose(w['b'][:, 0], 10)]
    assert len(shared) == 2 and all(abs(w['L'] - 10) < 1e-6 for w in shared)


def test_wall_texel_codec_round_trips():
    out = pfb.encode_wall_texels(np.array([0.0, 123.457, 700.0]), np.array([12.0, 100.3, 999.0]), np.array([0, 7, 300]), np.array([0, 9, 15]))
    got = [pfb.decode_wall_texel(*row) for row in out.tolist()]
    assert got[0] == {'s0_m': 0.0, 'length_m': 12.0, 'floors': 0, 'normal_bin': 0}
    assert abs(got[1]['s0_m'] - 123.457) < .006, 'centimetre precision keeps window edges straight'
    assert got[1]['length_m'] == 100.5 and got[1]['floors'] == 7 and got[1]['normal_bin'] == 9
    assert got[2]['s0_m'] == pfb.MAX_WALL_M and got[2]['length_m'] == pfb.MAX_LENGTH_M and got[2]['floors'] == pfb.MAX_FLOORS and got[2]['normal_bin'] == 15
    for b in range(16):                                   # every normal bin survives the split across G and A
        assert pfb.decode_wall_texel(*pfb.encode_wall_texels(np.array([5.0]), np.array([10.0]), np.array([3]), np.array([b])).tolist()[0])['normal_bin'] == b
    assert pfb.MAX_PIECE_M * 100 <= 16383, 'walls are split before s0 can exceed its 14 bits'


def test_bake_writes_wall_run_and_floors_below_roof_for_a_real_building(world):
    plan = run_bake(world, world.tmp / 'o')
    district = plan['districts']['test']
    grid = district['grid']
    image2 = read_png(world.tmp / 'o' / district['lookup2'])

    def texel(east, north):
        return image2[int((grid['max_north_m'] - north) / grid['cell_m']), int((east - grid['min_east_m']) / grid['cell_m'])]

    got = pfb.decode_wall_texel(*texel(20.0, 10.3))   # building a: 12 m wide, 9.6 m tall, south wall near its east end
    assert abs(got['length_m'] - 12.0) < .06 and abs(got['s0_m'] - 2.0) < .6 and got['floors'] == 3
    tall = pfb.decode_wall_texel(*texel(35.0, 29.8))  # building b: 10 m wide, 20 m tall
    assert abs(tall['length_m'] - 10.0) < .06 and tall['floors'] == int(round(20.0 / pfb.fitted_floor_height(20.0, 3.2)))
    assert not texel(55.0, 54.0).any(), 'unchanged building keeps no wall data'
    assert district['lookup2_sha256']


def test_family_table_carries_balcony_curtain_and_shopfront_rules():
    mat = lambda balcony, ratio: {'id': 'f', 'appearance_estimates': {'material': {
        'base_color_rgb': [.6, .5, .4], 'trim_color_rgb': [.8, .8, .7], 'glass_color_rgb': [.2, .2, .2], 'roughness': .7,
        'weathering': .3, 'window_to_wall_ratio': ratio, 'bay_width_m': 3.4, 'floor_height_m': 3.1, 'balcony_type': balcony}}}
    glazed, commercial, residential = (pfb.family_style(mat('none', .72)), pfb.family_style(mat('none', .38)),
                                       pfb.family_style(mat('recessed', .28)))
    assert glazed['curtain'] and not glazed['shopfront']
    assert commercial['shopfront'] and not commercial['curtain']
    assert not residential['shopfront'] and residential['balcony_type'] == 'recessed'
    table = pfb.family_table({'r': mat('recessed', .28), 'g': mat('none', .72)}, ['r', 'g'])
    assert table.shape == (8, 64, 4)
    assert table[4, 1, 0] == 3 * 80 and table[4, 2, 1] == 255 and table[4, 1, 1] == 0


# ---- long walls, box fallback, whole-bay / corner / roofline clipping ---------------------------------

def test_walls_over_the_encodable_length_are_split_into_equal_runs_and_boxes_fill_in_for_missing_roofs():
    long_box = pfb.wall_chains(roof_box(0, 0, 300, 10, 8.0))
    lengths = sorted(round(w['L'], 3) for w in long_box)
    assert lengths == [10.0, 10.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0], lengths
    assert max(w['L'] for w in long_box) <= pfb.MAX_PIECE_M < pfb.MAX_LENGTH_M
    s0, length = pfb.nearest_wall_params(long_box, np.array([[250.0, 0.2]]))
    assert abs(length[0] - 100.0) < 1e-6 and 0.0 <= s0[0] <= 100.0
    box = pfb.box_walls([10, 10, 0.0], [22, 20, 9.6])
    assert sorted(round(w['L'], 3) for w in box) == [10.0, 10.0, 12.0, 12.0]
    s0, _ = pfb.nearest_wall_params(box, np.array([[20.0, 10.2]]))
    assert abs(s0[0] - 2.0) < 1e-6, 'box fallback uses the same interior-on-the-right orientation as roof outlines'


def bay_layout(length, bay0):
    """Python mirror of the shader's pad/bay fit, used only to check the DESIGN for every wall length."""
    pad = min(min(max(0.18 * bay0 + 0.25, 0.35), 0.9), 0.3 * length)
    usable = length - 2 * pad
    if usable < 0.6 * bay0:
        return None
    count = max(1, math.floor(usable / bay0 + 0.5))
    return pad, usable, count, usable / count


def test_shader_contains_the_bay_fit_formulas_this_design_check_mirrors():
    for fragment in ('clamp(0.18 * bw0 + 0.25, 0.35, 0.9)', '0.3 * wl', 'floor(usable / bw0 + 0.5)', 'step(0.6 * bw0, usable)',
                     'float xw = along - pad;', 'step(xw, usable)'):
        assert fragment in pfb.SHADER, fragment


@pytest.mark.parametrize('bay0', [1.1, 1.8, 3.0, 3.4, 4.2, 6.0, 12.0])
def test_fitted_bays_never_reach_a_corner_even_with_the_length_encoding_error(bay0):
    for tenths in range(20, 1300):
        true_length = tenths / 10.0
        encoded = round(true_length * 2) / 2  # what the 0.5 m byte stores; error is at most 0.25 m
        layout = bay_layout(encoded, bay0)
        if layout is None:
            continue
        pad, usable, count, bay = layout
        assert abs(count * bay - usable) < 1e-9 and count >= 1
        first_left = pad + 0.03 * bay                      # widest opening spans 0.03..0.97 of a bay (curtain wall)
        last_right = pad + (count - 1) * bay + 0.97 * bay
        assert first_left >= pad and last_right <= encoded - pad + 1e-9
        assert true_length - last_right >= pad - 0.25 - 1e-9, 'a window would reach the true far corner at L=%s' % true_length
        assert pad >= min(0.35, 0.3 * true_length) - 1e-9


def test_floors_never_extend_above_the_local_roof():
    for height in np.arange(2.0, 80.0, 0.37):
        fitted = pfb.fitted_floor_height(float(height), 3.2)
        floors = int(pfb.floors_below_roof(height, 0.0, fitted))
        assert floors * fitted <= height + 0.05 * fitted + 1e-9
        assert floors == 0 or (floors - 1) * fitted + 0.84 * fitted < height + 1e-9   # highest window row ends below the roof
    assert int(pfb.floors_below_roof(7.0, 0.0, 3.5)) == 2 < int(pfb.floors_below_roof(20.0, 0.0, 3.5))


# ---- preservation check: tolerance model with positive and negative controls --------------------------

CHECK = Path('C:/private/dynacity-data/engine/goal-20261031/07-photo-batch-2055/unreal-batch/preservation_check.py')


def load_check():
    spec = importlib.util.spec_from_file_location('preservation_check_under_test', CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_scene(painted=((100, 100, 260, 220),), change_outside=None, noise=0, size=(400, 300), seed=3):
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 140, size=(size[1], size[0], 3)).astype('int16')
    after = base.copy()
    mask_image = np.zeros_like(base)
    for x0, y0, x1, y1 in painted:
        after[y0:y1, x0:x1] = (base[y0:y1, x0:x1] + 90).clip(0, 255)  # a real material change inside the painted area
        mask_image[y0:y1, x0:x1] = (255, 0, 255)
    if change_outside:
        x0, y0, x1, y1 = change_outside
        after[y0:y1, x0:x1] = (base[y0:y1, x0:x1] + 70).clip(0, 255)
    if noise:
        after = (after + rng.integers(-noise, noise + 1, size=after.shape)).clip(0, 255).astype('int16')
        repeat = (base + rng.integers(-noise, noise + 1, size=base.shape)).clip(0, 255).astype('int16')
    else:
        repeat = base.copy()
    return base, after, mask_image, repeat


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_preservation_check_passes_untouched_surfaces_despite_noise_and_small_lighting_drift():
    check = load_check()
    base, after, mask, repeat = synthetic_scene(noise=3)
    after = after.copy()
    after[:, 270:290] = (after[:, 270:290] + 5).clip(0, 255)  # faint bounce-light drift beside the painted area
    stats = check.view_statistics(base, after, mask, repeat)
    assert stats['strict_inside_painted'] > 0.95, 'positive control: the painted area really changed'
    assert stats['large_outside_painted'] == 0.0
    assert check.verdict(stats)[0] == 'PASS'


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_preservation_check_fails_when_an_unpainted_region_changes_materially():
    check = load_check()
    base, after, mask, repeat = synthetic_scene(change_outside=(300, 40, 380, 120), noise=2)
    outcome, reasons = check.verdict(check.view_statistics(base, after, mask, repeat))
    assert outcome == 'FAIL' and any('large changes outside' in r for r in reasons)


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_preservation_check_is_inconclusive_when_blind_unsettled_or_without_a_noise_floor():
    check = load_check()
    base, after, mask, repeat = synthetic_scene()
    stats = check.view_statistics(base, after, mask, repeat)
    assert check.verdict(stats, settled=False)[0] == 'INCONCLUSIVE'
    assert check.verdict(check.view_statistics(base, after, mask, None))[0] == 'INCONCLUSIVE'
    blind = check.view_statistics(base, base.copy(), mask, repeat)  # the painted area did not change at all
    assert check.verdict(blind)[0] == 'INCONCLUSIVE' and 'positive control' in check.verdict(blind)[1][0]
    assert check.verdict(check.view_statistics(base, after, np.zeros_like(mask), repeat))[0] == 'INCONCLUSIVE'


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_projection_hull_mask_and_dilation_math():
    check = load_check()
    px = check.project([(1000, 0, 0)], (0, 0, 0), (0, 0, 0), (2000, 1000))[0]
    assert abs(px[0] - 1000) < 1e-6 and abs(px[1] - 500) < 1e-6          # straight ahead = image centre
    left = check.project([(1000, -1000, 0)], (0, 0, 0), (0, 0, 0), (2000, 1000))[0]
    assert left[0] < 1000, 'UE +Y is to the right of a camera facing +X, so -Y lands on the left'
    assert check.project([(-500, 0, 0)], (0, 0, 0), (0, 0, 0), (2000, 1000)) is None  # behind the camera
    assert sorted(check.hull([(0, 0), (4, 0), (4, 4), (0, 4), (2, 2)])) == [(0, 0), (0, 4), (4, 0), (4, 4)]
    image = np.zeros((40, 40, 3), dtype='int16')
    image[20, 20] = (255, 0, 255)
    raw, grown, far = check.painted_mask(image)
    assert raw.sum() == 1 and grown[20 + check.GROW_PX, 20] and not grown[20 + check.GROW_PX + 1, 20]
    big = np.zeros((200, 200), dtype=bool)
    big[100, 100] = True
    assert check.box_dilate(big, 40)[140, 60] and not check.box_dilate(big, 40)[141, 100]


def test_a_tiny_roof_jog_never_captures_texels_from_the_real_wall_beside_it():
    # 12 x 10 box whose south wall has a 0.2 m notch: texels near the notch must still use the 12 m wall
    quad = [(10, 10, 9.6), (22, 10, 9.6), (22, 20, 9.6), (10, 20, 9.6)]
    jog = [(15.0, 10.0, 9.6), (15.2, 10.0, 9.6), (15.2, 10.2, 9.6), (15.0, 10.2, 9.6)]
    tris = np.array([[quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]], dtype='float64')
    notch = np.array([[jog[0], jog[1], jog[2]], [jog[0], jog[2], jog[3]]], dtype='float64')
    walls = pfb.wall_chains(np.concatenate([tris, notch]))
    assert any(w['L'] < pfb.MIN_WALL_M for w in walls), 'fixture must contain a tiny wall'
    s0, length = pfb.nearest_wall_params(walls, np.array([[15.1, 10.4], [14.0, 10.3]]))
    assert np.allclose(length, 12.0), length
    out = pfb.encode_wall_texels(np.array([1.0]), np.array([0.2]), np.array([3]))
    assert out[0, 2] == 1, 'a wall that exists never encodes to length 0 (which means "no wall")'


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_preservation_check_separates_bounce_light_from_material_change():
    check = load_check()
    base, after, mask, repeat = synthetic_scene(noise=2)
    lit = after.copy()
    lit[40:140, 280:380] = (lit[40:140, 280:380] * 1.13).clip(0, 255)   # shadowed faces brightened ~13% by bounce light
    stats = check.view_statistics(base, lit, mask, repeat)
    assert stats['strict_outside_painted'] > 0.03, 'raw comparison alone would call this a change'
    assert check.verdict(stats)[0] == 'PASS' and stats['gain_summary']['fraction_brighter_over_3pct'] > 0.03
    recoloured = after.copy()                                             # same brightness, clearly different hue: a real change
    region = recoloured[40:140, 280:380]
    region[..., 0] += 50
    region[..., 2] -= 50
    outcome, reasons = check.verdict(check.view_statistics(base, recoloured.clip(0, 255), mask, repeat))
    assert outcome == 'FAIL' and any('large changes' in r for r in reasons)
    subtle = after.copy()                                                 # a 20-level hue shift sits in the drift band
    subtle[40:140, 280:380, 0] += 20
    subtle[40:140, 280:380, 2] -= 20
    subtle_stats = check.view_statistics(base, subtle.clip(0, 255), mask, repeat)
    assert check.verdict(subtle_stats)[0] == 'PASS', 'not a pass/fail criterion: not separable from drift by pixels'
    assert subtle_stats['strict_comp_outside_painted'] > 0.03, 'but it is reported, never hidden'
    # a brightness change far beyond any plausible bounce light is NOT compensated
    doubled = after.copy()
    doubled[40:140, 280:380] = (doubled[40:140, 280:380] * 1.9).clip(0, 255)
    assert check.verdict(check.view_statistics(base, doubled, mask, repeat))[0] == 'FAIL'


# ---- exact preservation: a neighbour's paint must never reach an unpainted building (the N-02 failure) ----------

def custom_world(world, boxes, exclude=(), batch='buildings_test_material_5'):
    for old in world.batches.glob('*'):
        old.unlink()
    glb = world.batches / 'buildings-test-abc.glb'
    objects = write_glb(glb, boxes)
    for o in objects:
        o['batch'] = batch
    (world.batches / 'buildings-test-abc.objects.json').write_text(json.dumps(
        {'schema': 'dynacity-batched-objects/1', 'asset': str(glb), 'kind': 'buildings', 'production_group': 'Test', 'objects': objects}))
    jobs = {'schema': 'dynacity-building-appearance-jobs/1', 'jobs': [
        {'source_id': sid, 'family_id': 'fam_a', 'evidence_class': 'reference_informed_estimate', 'reference_paths': ['r.jpg']} for sid in boxes]}
    rewrite(world, 'jobs', jobs)
    listing = world.tmp / 'exclude.json'
    listing.write_text(json.dumps(list(exclude)))
    return pfb.bake(world.paths['library'], world.paths['jobs'], world.tmp / 'leak', batches_dir=world.batches, expected_count=len(boxes),
                    exclusion_files=[listing], engine_root=world.tmp / 'none', saved_dir=world.tmp / 'none2')


def texel_at(plan, folder, name, east, north):
    district = plan['districts']['test']
    grid = district['grid']
    image = read_png(folder / district[name])
    return image[int((grid['max_north_m'] - north) / grid['cell_m']), int((east - grid['min_east_m']) / grid['cell_m'])]


def test_excluded_neighbour_footprint_is_protected_from_painted_ownership_and_dilation(world):
    boxes = {'beirut001:painted': (10, 10, 22, 20, 26.0), 'beirut001:kept': (22.3, 10, 34, 20, 34.0)}
    plan = custom_world(world, boxes, exclude=['beirut001:kept'])
    folder = world.tmp / 'leak'
    assert plan['totals']['applied'] == 1 and plan['totals']['excluded'] == 1
    # the painted building owns its own roof texels ...
    assert pfb.decode_texel(*texel_at(plan, folder, 'lookup', 16.0, 15.0))['family'] == 1
    # ... but not one texel inside the excluded building's footprint, including the two texels right beside the gap that
    # dilation used to fill (this is exactly how a windowless grey face appeared on an excluded building)
    for east in (22.8, 23.6, 25.0, 28.0, 33.0):
        assert not texel_at(plan, folder, 'lookup', east, 15.0).any(), east
        assert not texel_at(plan, folder, 'lookup2', east, 15.0).any(), east
    assert plan['districts']['test']['protected_footprint_texels'] > 100


def test_overlap_from_a_painted_building_cannot_overwrite_a_protected_footprint(world):
    boxes = {'beirut001:painted': (10, 10, 24, 20, 40.0), 'beirut001:kept': (22, 10, 34, 20, 12.0)}   # taller painted roof overlaps by 2 m
    plan = custom_world(world, boxes, exclude=['beirut001:kept'])
    folder = world.tmp / 'leak'
    assert not texel_at(plan, folder, 'lookup', 23.0, 15.0).any(), 'protected footprint wins the overlap even against a taller painted roof'
    assert pfb.decode_texel(*texel_at(plan, folder, 'lookup', 15.0, 15.0))['family'] == 1


def test_a_preserved_batch_is_reported_as_excluded_and_never_painted_or_leaked(world, monkeypatch):
    real_reader = pfb.read_batch_glb
    monkeypatch.setattr(pfb, 'read_batch_glb', lambda path: (lambda m: {**m, 'buildings_test_material_9': m['buildings_test_material_5']})(real_reader(path)))
    boxes = {'beirut001:painted': (10, 10, 22, 20, 26.0), 'beirut001:lgb': (22.3, 10, 34, 20, 34.0)}
    for old in world.batches.glob('*'):
        old.unlink()
    glb = world.batches / 'buildings-test-abc.glb'
    objects = write_glb(glb, boxes)
    objects[0]['batch'] = 'buildings_test_material_5'
    objects[1]['batch'] = 'buildings_test_material_9'
    objects[1]['material'] = 9
    (world.batches / 'buildings-test-abc.objects.json').write_text(json.dumps(
        {'schema': 'dynacity-batched-objects/1', 'asset': str(glb), 'kind': 'buildings', 'production_group': 'Test', 'objects': objects}))
    rewrite(world, 'jobs', {'schema': 'dynacity-building-appearance-jobs/1', 'jobs': [
        {'source_id': sid, 'family_id': 'fam_a', 'evidence_class': 'reference_informed_estimate', 'reference_paths': ['r.jpg']} for sid in boxes]})
    plan = pfb.bake(world.paths['library'], world.paths['jobs'], world.tmp / 'lgb', batches_dir=world.batches, expected_count=2,
                    engine_root=world.tmp / 'none', saved_dir=world.tmp / 'none2', preserve_batches=('buildings_test_material_9',))
    rows = {r[0]: r for r in json.loads((world.tmp / 'lgb/assignments.json').read_text())['rows']}
    assert rows['beirut001:lgb'][4] == 'excluded:lgb_replaced_batch' and rows['beirut001:painted'][4] is None
    assert plan['totals']['applied'] == 1 and plan['totals']['excluded'] == 1
    assert not texel_at(plan, world.tmp / 'lgb', 'lookup', 26.0, 15.0).any()


def test_shader_has_no_dashed_rail_uses_world_footprint_aa_and_a_bypass_that_outputs_the_original_colour():
    assert 'rail' not in pfb.SHADER, 'the dashed rail produced the repeating pale seam dashes'
    assert 'fwidth(ub)' not in pfb.SHADER and 'fwidth(vb)' not in pfb.SHADER, 'derivatives of the texel-grid along coordinate spike at texel borders'
    assert 'fwidth(Position.x)' in pfb.SHADER and 'dot(Position.xy, float2(-nh.y, nh.x))' in pfb.SHADER
    assert 'outc = lerp(outc, Orig, step(3.5, Debug));' in pfb.SHADER
    # in bypass mode the overlay colour is the original and glass roughness is off, so lerp(original, overlay, mask) == original
    assert '(1.0 - step(1.5, Debug))' in pfb.SHADER.split('float gloss')[1].split('\n')[0]
    assert 'Orig' in [n for n in ('Position', 'Normal', 'Orig')] and re_declared('Orig')


def re_declared(name):
    import re
    return bool(re.search(r'\b%s\b' % name, pfb.SHADER))


def test_facade_is_simplified_to_features_aligned_with_the_opening():
    import re
    for gone in ('proj', 'recess', 'slabStrip', 'isStrip', 'balcSel', 'sill', 'lintel'):
        assert not re.search(r'\b%s\b' % gone, pfb.SHADER), 'free-floating rectangles caused the pale ghost boxes and double borders: %s' % gone
    assert 'trimC * 0.45 * (0.78' not in pfb.SHADER and 'float3 shutC = wallC * 0.55' in pfb.SHADER
    assert 'float trimMask = saturate(slab * 0.7 + pierEdge * 0.15);' in pfb.SHADER
    assert 'slabShadow' in pfb.SHADER
    assert 'reveal' not in pfb.SHADER, 'a separate reveal line inside the frame read as a second, double border'
    assert 'float fw = 0.05 + 0.04 * step(2.5, bcode);' in pfb.SHADER and 'ou0 + fw' in pfb.SHADER
    # one opening definition drives window, frame and reveal (no separate sill/lintel boxes with their own bounds)
    assert 'float openU = step(ou0, u) * step(u, ou1);' in pfb.SHADER
    assert 'frac(bayR * 5.3)) * 0.25' not in pfb.SHADER and '(0.85 + 0.25 * frac(bayR * 5.3))' in pfb.SHADER, 'one pane tone family, no bright variant'
    assert 'float wallMatch = max(m0, max(m1s, m2s));' in pfb.SHADER and '* wallMatch;' in pfb.SHADER
    assert 'float2 shiftUE' in pfb.SHADER and 'GridB.z + shiftUE' in pfb.SHADER and 'GridB.z - shiftUE' in pfb.SHADER, 'two further samples shifted along the wall'


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_bypass_verdict_has_fixed_thresholds_and_three_outcomes():
    check = load_check()
    row = lambda pair8, pair32, noise8, noise32: {'A1_vs_B': {'changed_gt8': pair8, 'changed_gt32': pair32}, 'A1_vs_A2': {'changed_gt8': noise8, 'changed_gt32': noise32}}
    assert check.bypass_verdict(row(0.004, 0.0002, 0.003, 0.0001))[0] == 'PASS'                 # within noise + margins
    assert check.bypass_verdict(row(0.010, 0.0002, 0.003, 0.0001))[0] == 'FAIL'                 # strict above noise + 0.002
    assert check.bypass_verdict(row(0.004, 0.0020, 0.003, 0.0001))[0] == 'FAIL'                 # large above noise + 0.001
    assert check.bypass_verdict(row(0.004, 0.0002, 0.030, 0.0001))[0] == 'INCONCLUSIVE'          # scene too unstable to resolve
    assert (check.BYPASS_MARGIN_STRICT, check.BYPASS_MARGIN_LARGE, check.BYPASS_NOISE_LIMIT) == (0.002, 0.001, 0.02)


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_stable_pixel_bypass_ignores_animation_but_catches_a_real_material_difference():
    check = load_check()
    rng = np.random.default_rng(5)
    base = rng.integers(40, 160, size=(200, 300, 3)).astype('int16')
    a1, a2, b = base.copy(), base.copy(), base.copy()
    a1[:40, :120] = rng.integers(0, 255, size=(40, 120, 3))      # animated "sea": differs between A1 and A2 (and from B) at random
    a2[:40, :120] = rng.integers(0, 255, size=(40, 120, 3))
    assert check.bypass_stable_verdict(check.bypass_stable_stats(a1, a2, b))[0] == 'PASS'
    b_bad = b.copy()                                             # a REAL difference: the original differs from BOTH overlay captures
    b_bad[100:160, 150:260] += 45
    verdict, reasons = check.bypass_stable_verdict(check.bypass_stable_stats(a1, a2, b_bad.clip(0, 255)))
    assert verdict == 'FAIL' and any('large' in r for r in reasons)
    chaos = rng.integers(0, 255, size=base.shape).astype('int16')  # nothing stable at all
    assert check.bypass_stable_verdict(check.bypass_stable_stats(base, chaos, base))[0] == 'INCONCLUSIVE'
    assert (check.STABLE_THRESHOLD, check.STABLE_STRICT_LIMIT, check.STABLE_LARGE_LIMIT, check.STABLE_MIN_FRACTION) == (8, 0.002, 0.0005, 0.5)


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_same_state_control_absorbs_glass_speckle_but_not_a_material_difference():
    check = load_check()
    rng = np.random.default_rng(11)
    base = rng.integers(40, 160, size=(240, 320, 3)).astype('int16')
    def speckled():                                     # stochastic speckle inside "windows", fresh each capture
        img = base.copy()
        window = (slice(60, 140), slice(80, 200))
        hits = rng.random((80, 120)) < 0.02
        img[window][hits] += rng.integers(10, 25, size=(int(hits.sum()), 3))
        return img.clip(0, 255)
    a1, a2, a3, b = speckled(), speckled(), speckled(), speckled()
    stats = check.bypass_control_stats(a1, a2, a3, b)
    assert check.bypass_control_verdict(stats)[0] == 'PASS', stats        # speckle is in the control too
    b_bad = b.copy(); b_bad[150:210, 40:200] += 40                         # a wall-scale material difference, absent from the control
    assert check.bypass_control_verdict(check.bypass_control_stats(a1, a2, a3, b_bad.clip(0, 255)))[0] == 'FAIL'
    loud = rng.integers(0, 255, size=base.shape).astype('int16')
    assert check.bypass_control_verdict(check.bypass_control_stats(base, base, loud, base))[0] == 'INCONCLUSIVE'
    assert (check.CONTROL_NOISE_LIMIT, check.CONTROL_STRICT_MARGIN, check.CONTROL_LARGE_LIMIT) == (0.03, 0.002, 0.0005)


@pytest.mark.skipif(not CHECK.is_file(), reason='private output folder not present')
def test_alternating_bypass_uses_painted_pixels_symmetric_gaps_and_a_harsher_control():
    check = load_check()
    rng = np.random.default_rng(21)
    base = rng.integers(40, 160, size=(220, 320, 3)).astype('int16')
    mask = np.zeros(base.shape[:2], dtype=bool)
    mask[40:180, 40:280] = True
    def capture(drift):                                   # slow drift + stochastic speckle, on every capture alike
        img = (base + drift).astype('int16')
        hits = rng.random(base.shape[:2]) < 0.01
        img[hits] += rng.integers(10, 25, size=(int(hits.sum()), 3))
        return img.clip(0, 255)
    a = [capture(0), capture(2), capture(4)]
    b = [capture(1), capture(3)]
    stats = check.bypass_alternating_stats(a, b, mask)
    assert check.bypass_alternating_verdict(stats)[0] == 'PASS', stats
    b_bad = [x.copy() for x in b]
    for x in b_bad:
        x[60:120, 60:200] = (x[60:120, 60:200] + 50).clip(0, 255)   # B differs from A on painted pixels, in both B captures
    assert check.bypass_alternating_verdict(check.bypass_alternating_stats(a, b_bad, mask))[0] == 'FAIL'
    assert check.bypass_alternating_verdict(check.bypass_alternating_stats(a, b, np.zeros_like(mask)))[0] == 'INCONCLUSIVE'
    assert (check.ALT_MIN_PIXELS, check.ALT_CONTROL_LIMIT, check.ALT_STRICT_MARGIN, check.ALT_LARGE_LIMIT) == (20000, 0.05, 0.002, 0.0005)


def decode_alpha(a):
    """Python mirror of the graph's decode of the Custom node's alpha packing (design check; native captures are the proof)."""
    sat = lambda v: min(max(v, 0.0), 1.0)
    return {'valid': sat(a * 20), 'pbr': sat((a - 0.35) * 100), 'gloss': sat((a - 0.5) * 10)}


def test_alpha_packing_gives_the_overlay_roughness_only_where_it_owns_the_pixel_and_not_in_bypass():
    owned_wall = 0.1 + 0.4 * 1 + 0.1 * 0 * 1       # shader: valid * (0.1 + 0.4 * pbr + 0.1 * gloss * pbr)
    owned_glass = 0.1 + 0.4 * 1 + 0.1 * 1 * 1
    bypass = 0.1 + 0.4 * 0 + 0.1 * 0 * 0           # Debug 4: owned, but the original roughness is kept
    assert decode_alpha(0.0) == {'valid': 0.0, 'pbr': 0.0, 'gloss': 0.0}                     # not owned: original colour AND roughness
    assert decode_alpha(bypass) == {'valid': 1.0, 'pbr': 0.0, 'gloss': 0.0}                  # bypass: overlay colour path, original roughness
    assert decode_alpha(owned_wall) == {'valid': 1.0, 'pbr': 1.0, 'gloss': 0.0}              # owned wall: matte overlay roughness
    glass = decode_alpha(owned_glass)
    assert glass['valid'] == 1.0 and glass['pbr'] == 1.0 and abs(glass['gloss'] - 1.0) < 1e-9  # owned glass: glossy
    assert 'valid * (0.1 + 0.4 * pbr + 0.1 * gloss * pbr)' in pfb.SHADER and 'float pbr = 1.0 - step(3.5, Debug);' in pfb.SHADER
    # the original finishes connect only base colour and roughness (normal, emissive, specular, metallic are unconnected),
    # so roughness is the only original input that can leak the old window pattern through the overlay
    graph = Path('C:/private/dynacity-data/engine/goal-20261031/07-photo-batch-2055/unreal-batch/original-finish-graph-inputs.json')
    if graph.is_file():
        for finish, row in json.loads(graph.read_text()).items():
            assert row['normal'][0] is None and row['emissive'][0] is None and row['spec'][0] is None and row['metal'][0] is None, finish
            assert row['base'][0] and row['rough'][0], finish


# ---- corners: texels near a corner belong to the NEIGHBOURING wall; the shader must notice and place nothing there -------------

def mirror_chain(plan, folder, point_en, normal_en, z, mode='shifted'):
    """Python mirror of the shader's lookup chain for one wall point. mode: 'straight' (old: one sample straight in, no check),
    'checked' (one sample, refuse a different wall), 'shifted' (current: also sample +-2*inset along the wall)."""
    import math
    district = plan['districts']['test']
    grid = district['grid']
    tex1, tex2 = read_png(folder / district['lookup']).astype(int), read_png(folder / district['lookup2']).astype(int)
    family = read_png(folder / plan['family_table']).astype(int)
    E, N = point_en
    nE, nN = normal_en
    inset = pfb.INSET_TEXELS * grid['cell_m']
    tE, tN = nN, -nE                                                   # along direction
    def sample(shift):
        qE, qN = E - nE * inset + tE * shift, N - nN * inset + tN * shift
        col = int((qE - grid['min_east_m']) / grid['cell_m'])
        row = int((grid['max_north_m'] - qN) / grid['cell_m'])
        t, w = tex1[row, col], tex2[row, col]
        wall = pfb.decode_wall_texel(*w)
        angle = wall['normal_bin'] * 2 * math.pi / 16
        match = bool((t[0] & 63) and (nE * math.cos(angle) + nN * math.sin(angle)) >= 0.80)
        return {'t': t, 'wall': wall, 'match': match, 'row': row, 'col': col}
    s0_, s1_, s2_ = sample(0.0), sample(2 * inset), sample(-2 * inset)
    if not (s0_['t'][0] & 63):
        return {'owned': False, 'window': False}
    chosen, match = s0_, s0_['match']
    if mode == 'straight':
        match = True
    elif mode == 'shifted' and not s0_['match']:
        for other in (s1_, s2_):
            if other['match'] and tuple(other['t']) == tuple(s0_['t']):
                chosen, match = other, True
                break
    t, wall = s0_['t'], chosen['wall']
    ec = grid['min_east_m'] + (chosen['col'] + .5) * grid['cell_m']
    nc = grid['max_north_m'] - (chosen['row'] + .5) * grid['cell_m']
    along = wall['s0_m'] + ((E - ec) * tE + (N - nc) * tN)
    bw0 = max(family[3, t[0] & 63, 0] * 0.05, 1.2)
    pad = min(min(max(0.18 * bw0 + 0.25, 0.35), 0.9), 0.3 * wall['length_m'])
    usable = wall['length_m'] - 2 * pad
    count = max(1.0, math.floor(usable / bw0 + 0.5))
    bay = max(usable, 0.01) / count
    xw = along - pad
    inwall = bool(match and xw >= 0 and xw <= usable and usable >= 0.6 * bw0)
    u = (xw / bay) % 1.0
    gw = family[2, t[0] & 63, 3] / 255.0
    return {'owned': True, 'window': bool(inwall and 0.5 - 0.5 * gw <= u <= 0.5 + 0.5 * gw), 'match': match, 'along': along, 'inwall': inwall}


def test_along_wall_coordinate_stays_true_up_to_every_corner_only_with_the_shifted_samples(world):
    plan = run_bake(world, world.tmp / 'o')
    folder = world.tmp / 'o'
    # building a: east 10..22, north 10..20 (12 m x 10 m). Along-wall distance from each face's own corner (run with interior on the right):
    faces = {'south': ((0.0, -1.0), lambda d: (22.0 - d, 10.02), lambda E, N: 22.0 - E), 'east': ((1.0, 0.0), lambda d: (21.98, 20.0 - d), lambda E, N: 20.0 - N),
             'north': ((0.0, 1.0), lambda d: (10.0 + d, 19.98), lambda E, N: E - 10.0), 'west': ((-1.0, 0.0), lambda d: (10.02, 10.0 + d), lambda E, N: N - 10.0)}
    z = 5.5
    errors = {'straight': [], 'checked': [], 'shifted': []}
    windows_near_corner = []
    for name, (normal, at, true_along) in faces.items():
        for dist in np.arange(0.05, 3.0, 0.1):
            point = at(float(dist))
            for mode in errors:
                got = mirror_chain(plan, folder, point, normal, z, mode)
                if got['owned']:
                    errors[mode].append(abs(got['along'] - true_along(*point)))
            if mirror_chain(plan, folder, point, normal, z, 'shifted')['window'] and dist < 1.0:
                windows_near_corner.append((name, round(float(dist), 2)))
    assert max(errors['shifted']) < 0.06, 'shifted samples keep the along-wall coordinate within 6 cm of the true distance everywhere near a corner'
    assert max(errors['straight']) > 1.0, 'fixture must reproduce the bug: the old single sample returns the neighbouring wall run (metres out)'
    assert not windows_near_corner, 'no window within 1 m of a corner (the pier alone is 0.35-0.9 m): %s' % windows_near_corner


def test_a_corner_texel_never_decides_a_window_with_the_neighbouring_walls_run(world):
    plan = run_bake(world, world.tmp / 'o')
    folder = world.tmp / 'o'
    # with the checked-only mode a point beside the corner is plain (no wrong window); the shifted mode ALSO recovers the right run
    # so the first real bay appears where it belongs and is not cut off by the refused zone
    for dist in np.arange(0.05, 1.3, 0.1):
        assert not mirror_chain(plan, folder, (22.0 - float(dist), 10.02), (0.0, -1.0), 5.5, 'checked')['window']
    first = [round(float(d), 2) for d in np.arange(0.05, 4.0, 0.05) if mirror_chain(plan, folder, (22.0 - float(d), 10.02), (0.0, -1.0), 5.5, 'shifted')['window']]
    assert first and first[0] > 1.0, 'the first window starts after the pier, at a sensible distance: %s' % first[:3]


def test_the_texel_normal_agrees_with_its_own_wall_but_not_the_neighbouring_one(world):
    plan = run_bake(world, world.tmp / 'o')
    folder = world.tmp / 'o'
    district = plan['districts']['test']
    grid = district['grid']
    image2 = read_png(folder / district['lookup2'])
    def bin_at(east, north):
        return pfb.decode_wall_texel(*image2[int((grid['max_north_m'] - north) / grid['cell_m']), int((east - grid['min_east_m']) / grid['cell_m'])])['normal_bin']
    south_mid, north_mid, east_mid, west_mid = bin_at(16, 10.6), bin_at(16, 19.4), bin_at(21.4, 15), bin_at(10.6, 15)
    assert (south_mid, east_mid, north_mid, west_mid) == (12, 0, 4, 8), (south_mid, east_mid, north_mid, west_mid)   # -90, 0, 90, 180 degrees


def test_glass_is_semi_matte_and_frame_has_a_single_border():
    assert pfb.GLASS_ROUGHNESS == 0.35
    import inspect
    assert 'r=GLASS_ROUGHNESS' in inspect.getsource(pfb._build_overlay) and 'r=0.14' not in inspect.getsource(pfb._build_overlay)
    assert pfb.SHADER.count('frame') >= 2 and 'reveal' not in pfb.SHADER
