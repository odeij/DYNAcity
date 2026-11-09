"""External terrain and street layers must accompany the source city on reopen."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest


def load_module(monkeypatch, labels, reference=None):
    loaded=[]
    library=SimpleNamespace(
        get_actor_descs=lambda:[SimpleNamespace(label=label,guid=i) for i,label in enumerate(labels)],
        load_actors=lambda guids:loaded.extend(guids))
    monkeypatch.setitem(sys.modules,'unreal',SimpleNamespace(WorldPartitionBlueprintLibrary=library))
    monkeypatch.setitem(sys.modules,'load_reference_facade',reference or SimpleNamespace(installed_guids=lambda descs,area:[]))
    path=Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/city_world.py'
    spec=importlib.util.spec_from_file_location('city_loading_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_current',lambda:None)
    monkeypatch.setattr(module,'_actors',lambda:[])
    return module,loaded


def test_reopen_loads_extension_and_all_visible_street_layers(monkeypatch):
    labels=['buildings_0','roads_0','source-ground','beirut001_transport',
            'StreetCover_TerrainExtension','StreetCover_park','StreetLinear_hedge',
            'Street_tree','StreetCollision_poles','DYNACITY_BASIS_X','Unrelated']
    module,loaded=load_module(monkeypatch,labels)
    assert module.load_editor_geometry()==9
    assert loaded==list(range(9))


def test_checkpoints_use_active_project_when_module_is_loaded_from_repository(monkeypatch,tmp_path):
    module,_=load_module(monkeypatch,[])
    monkeypatch.setattr(module.unreal,'Paths',SimpleNamespace(project_dir=lambda:str(tmp_path)),raising=False)
    assert module._checkpoint()==tmp_path/'Saved'/'DynaCity'/'city-import-BeirutCityBatched.json'


def test_extension_inherits_ground_material_without_changing_other_covers(monkeypatch):
    module,_=load_module(monkeypatch,[])
    monkeypatch.setattr(module.unreal,'StaticMeshComponent',object,raising=False)
    class Component:
        def __init__(self,material):self.material=material
        def get_material(self,slot):return self.material
        def get_num_materials(self):return 1
        def set_material(self,slot,material):self.material=material
        def modify(self):pass
    class Actor:
        def __init__(self,label,material):self.label=label;self.component=Component(material)
        def get_actor_label(self):return self.label
        def get_component_by_class(self,cls):return self.component
        def modify(self):pass
    source=Actor('source-ground','textured-ground')
    extension=Actor('StreetCover_TerrainExtension','flat-beige')
    park=Actor('StreetCover_park','grass')
    monkeypatch.setattr(module,'_actors',lambda:[source,extension,park])
    assert module.match_ground_finish()==1
    assert extension.component.material=='textured-ground'
    assert park.component.material=='grass'
    assert module.match_ground_finish()==0


def reference_installation(monkeypatch, tmp_path):
    path=Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/load_reference_facade.py'
    spec=importlib.util.spec_from_file_location('reference_loading_integration',path)
    reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
    reference.ROOT=tmp_path
    paths=[reference.ACTOR_PREFIX+name for name in ('Visual','Ledges','Glass')]
    manifest={'source_id':reference.SOURCE_ID,'actors':paths}
    manifest_path=tmp_path/'native-install.json'
    manifest_path.write_text(json.dumps(manifest))
    module,loaded=load_module(monkeypatch,[],reference)
    descs=[SimpleNamespace(label='buildings_remeil',actor_path='source/remeil',guid=0),
           SimpleNamespace(label='buildings_achrafieh',actor_path='source/achrafieh',guid=1)]
    # Renamed owned actors must still load; an unrelated same-label actor must not.
    descs += [SimpleNamespace(label='Renamed reference '+str(i),actor_path=p,guid=i+2)
              for i,p in enumerate(paths)]
    descs += [SimpleNamespace(label='Reference visual source batch - LGB historical facade',
                             actor_path=reference.ACTOR_PREFIX+'Unrelated',guid=5)]
    module.unreal.WorldPartitionBlueprintLibrary.get_actor_descs=lambda:descs
    checkpoint=tmp_path/'checkpoint.json'
    checkpoint.write_text(json.dumps({'packages':[
        {'production_group':'Remeil','asset':'buildings_remeil'},
        {'production_group':'Achrafieh','asset':'buildings_achrafieh'}]}))
    monkeypatch.setattr(module,'_checkpoint',lambda:checkpoint)
    monkeypatch.setattr(module,'_names',lambda asset:[asset])
    return module,loaded,descs,manifest,manifest_path


@pytest.mark.parametrize('area,expected',[(None,[0,1,2,3,4]),('Remeil',[0,2,3,4]),('Achrafieh',[1])])
def test_normal_city_load_restores_only_installation_owned_replacement(monkeypatch,tmp_path,area,expected):
    module,loaded,*_=reference_installation(monkeypatch,tmp_path)
    assert module.load_editor_geometry(area)==len(expected)
    assert loaded==expected


def test_missing_replacement_aborts_before_any_city_loading(monkeypatch,tmp_path):
    module,loaded,descs,*_=reference_installation(monkeypatch,tmp_path)
    descs.pop(4)
    with pytest.raises(RuntimeError,match='not registered'):
        module.load_editor_geometry('Remeil')
    assert loaded==[]


@pytest.mark.parametrize('problem',['duplicate','wrong_source','wrong_map'])
def test_invalid_installation_aborts_before_any_city_loading(monkeypatch,tmp_path,problem):
    module,loaded,_,manifest,path=reference_installation(monkeypatch,tmp_path)
    if problem=='duplicate':manifest['actors'][1]=manifest['actors'][0]
    elif problem=='wrong_source':manifest['source_id']='different-building'
    else:manifest['actors'][0]='/Game/OtherMap:PersistentLevel.Visual'
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='Invalid LGB'):
        module.load_editor_geometry()
    assert loaded==[]


def test_city_without_optional_installation_still_loads_its_sources(monkeypatch,tmp_path):
    module,loaded,_,_,path=reference_installation(monkeypatch,tmp_path)
    path.unlink()
    assert module.load_editor_geometry()==2
    assert loaded==[0,1]
