"""A visual district copy must not regain its broad mesh collision in PIE."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def load(monkeypatch):
    unreal=SimpleNamespace(uclass=lambda:lambda cls:cls,ToolsetDefinition=object,
                           StaticMeshComponent=object,CollisionEnabled=SimpleNamespace(NO_COLLISION='none'))
    monkeypatch.setitem(sys.modules,'unreal',unreal)
    monkeypatch.setitem(sys.modules,'toolset_registry',SimpleNamespace(tool_call=lambda fn:fn))
    path=Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/captured_buildings.py'
    spec=importlib.util.spec_from_file_location('capture_visual_collision_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class Component:
    def __init__(self):
        self.default=True
        self.enabled='query'
        self.profile='BlockAll'
    def set_editor_property(self,key,value):
        assert key=='use_default_collision'
        self.default=value
    def get_editor_property(self,key):return self.default
    def set_collision_profile_name(self,value):self.profile=value
    def set_collision_enabled(self,value):self.enabled=value
    def get_collision_enabled(self):return self.enabled
    def recreate_physics(self):
        # Simulate mesh-default settings being adopted during PIE creation.
        if self.default:self.enabled='query'


class Actor:
    def __init__(self):self.enabled=True;self.component=Component()
    def set_actor_enable_collision(self,value):self.enabled=value
    def get_actor_enable_collision(self):return self.enabled
    def get_component_by_class(self,cls):return self.component


def test_visual_disable_survives_physics_recreation_without_touching_source(monkeypatch):
    module=load(monkeypatch)
    source,visual=Actor(),Actor()
    # Disabling just the current component state was insufficient.
    visual.component.set_collision_enabled('none')
    visual.component.recreate_physics()
    assert visual.component.enabled=='query'
    module._disable_visual_collision(visual,visual.component)
    visual.component.recreate_physics()
    assert module._visual_collision_disabled(visual)
    assert visual.component.profile=='NoCollision'
    assert source.enabled and source.component.default and source.component.enabled=='query'


def test_verification_rejects_actor_or_mesh_default_collision_drift(monkeypatch):
    module=load(monkeypatch);visual=Actor()
    module._disable_visual_collision(visual,visual.component)
    visual.enabled=True
    assert not module._visual_collision_disabled(visual)
    visual.enabled=False;visual.component.default=True
    assert not module._visual_collision_disabled(visual)
