"""Isolated actor transaction tests; no real Unreal or evidence writes."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

@pytest.fixture
def env(monkeypatch, tmp_path):
    actors, destroyed, fail = {}, [], [None]
    class Actor:
        def __init__(self, path, label):
            self.path, self.label, self.tags, self.transform = path, label, [], 'original'
            self.comp = NS(static_mesh=None)
            self.comp.set_static_mesh = lambda mesh: setattr(self.comp, 'static_mesh', mesh)
            self.comp.set_material = self.material
        def material(self, index, value):
            if fail[0] == 'material' and self.path == 'spawn2': raise ValueError('material failure')
        def get_path_name(self): return self.path
        def get_actor_label(self): return self.label
        def get_actor_transform(self): return self.transform
        def set_actor_label(self, value): self.label = value
        def set_actor_transform(self, *args):
            if fail[0] == 'transform' and self.path == 'spawn2': raise ValueError('transform failure')
            self.transform = args[0]
    actors['reference'] = Actor('reference', 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)')
    actors['foreign'] = Actor('foreign', 'keep me')
    def spawn(*args):
        p = 'spawn' + str(len([p for p in actors if p.startswith('spawn')]) + 1)
        a = Actor(p, 'unnamed'); actors[p] = a; return a
    def destroy(a):
        destroyed.append(a.path); del actors[a.path]; return True
    editor = NS(spawn_actor_from_class=spawn, destroy_actor=destroy)
    current = {'project': None, 'map': None, 'pie': False}
    mesh = NS(static_materials=[NS(material_slot_name='slot')])
    unreal = NS(Paths=NS(get_project_file_path=lambda: current['project']),
                UnrealEditorSubsystem='world', LevelEditorSubsystem='play', EditorActorSubsystem='actor',
                StaticMeshActor='meshactor', Vector=lambda: None, load_asset=lambda path: mesh)
    unreal.WorldPartitionBlueprintLibrary = NS(get_actor_descs=lambda: [])
    unreal.Paths.convert_relative_path_to_full = lambda path: path
    unreal.get_editor_subsystem = lambda kind: {'world': NS(get_editor_world=lambda: NS(get_path_name=lambda: current['map'])),
        'play': NS(is_in_play_in_editor=lambda: current['pie']), 'actor': editor}[kind]
    ci = NS(city_world=NS(_actors=lambda: list(actors.values())), _component=lambda a: a.comp,
        _state=lambda a: {'label': a.label, 'transform': a.transform, 'tags': list(a.tags)},
        _read=lambda p: json.loads(Path(p).read_text()), _sha=lambda p: 'hash',
        snapshot=lambda: {'actors': {'building': {}}}, _load_batches=lambda: None,
        cb=NS(_ready=lambda: None, _disable_visual_collision=lambda *args: None))
    monkeypatch.setitem(sys.modules, 'unreal', unreal)
    monkeypatch.setitem(sys.modules, 'corridor_reconstruction_install', ci)
    path = Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/corridor_feature_bundle_pilot.py')
    spec = importlib.util.spec_from_file_location('isolated_bundle', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    current.update(project=module.EXPECTED_PROJECT, map=module.EXPECTED_MAP)
    module.BUNDLE['root'] = tmp_path
    for i, feature in enumerate(module.BUNDLE['features']):
        feature.update(expected_sha256='hash', expected_slots={'slot'}, mesh_path_contains='/Pilot/')
        source = tmp_path / f'spec{i}.json'
        source.write_text(json.dumps({'source_id': feature['source_id'], 'geometry': 'test.glb', 'fraction': .7}))
        prepared = tmp_path / f'prepared{i}.json'
        prepared.write_text(json.dumps({'sha256': 'hash', 'spec': str(source), 'mesh': '/Pilot/Mesh', 'materials': {'slot': '/Mat'}}))
        feature['prepared_path'] = prepared
    return NS(m=module, actors=actors, destroyed=destroyed, fail=fail, ci=ci, current=current, unreal=unreal)

@pytest.mark.parametrize('failure', ['material', 'transform'])
def test_second_spawn_failure_cleans_both_and_preserves_foreign(env, failure):
    before = env.m._snapshot(); env.fail[0] = failure
    with pytest.raises(ValueError, match='failure'): env.m.apply()
    assert env.destroyed == ['spawn2', 'spawn1']
    assert env.m._snapshot() == before

def test_success_reverse_rollback_and_foreign_same_label(env):
    env.actors['foreign'].label = env.m.BUNDLE['labels'][0]
    before = env.m._snapshot()
    assert env.m.apply()['existing_actors_exact']
    assert env.m.rollback()['original_static_actors_exact']
    assert env.destroyed == ['spawn2', 'spawn1']
    assert env.m._snapshot() == before

def test_rollback_refuses_changed_owner(env):
    env.m.apply(); env.actors['spawn2'].tags = []
    with pytest.raises(RuntimeError, match='Ownership'): env.m.rollback()
    assert env.destroyed == [] and 'foreign' in env.actors

@pytest.mark.parametrize('field,value', [('project', 'other.uproject'), ('map', '/Game/Other'), ('pie', True)])
def test_guards_refuse_before_spawn(env, field, value):
    env.current[field] = value
    with pytest.raises(RuntimeError): env.m.apply()
    assert set(env.actors) == {'reference', 'foreign'}

@pytest.mark.parametrize('kind', ['hash', 'identity', 'materials'])
def test_bad_feature_data_refused_without_spawn(env, kind):
    feature = env.m.BUNDLE['features'][0]
    if kind == 'hash': env.ci._sha = lambda p: 'wrong'
    if kind == 'identity': feature['source_id'] = 'wrong'
    if kind == 'materials': env.unreal.load_asset = lambda p: None if p == '/Mat' else NS(static_materials=[NS(material_slot_name='slot')])
    with pytest.raises(ValueError): env.m.apply()
    assert set(env.actors) == {'reference', 'foreign'}
