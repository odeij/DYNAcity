import importlib.util,sys,types
from pathlib import Path
from unittest.mock import patch

class Vector:
 def __init__(self,x=0,y=0,z=0):self.x,self.y,self.z=x,y,z
 def __add__(self,b):return Vector(self.x+b.x,self.y+b.y,self.z+b.z)
 def __sub__(self,b):return Vector(self.x-b.x,self.y-b.y,self.z-b.z)

class Actor:
 tags=[]
 def __init__(self,label):self.label=label
 def get_actor_label(self):return self.label

def test_parking_requires_actual_land_support():
 result=[None]
 unreal=types.SimpleNamespace(Vector=Vector,UnrealEditorSubsystem=object,get_editor_subsystem=lambda cls:types.SimpleNamespace(get_editor_world=lambda:object()),SystemLibrary=types.SimpleNamespace(line_trace_single=lambda *a:result[0]),TraceTypeQuery=types.SimpleNamespace(TRACE_TYPE_QUERY1=1),DrawDebugTrace=types.SimpleNamespace(NONE=0))
 spec=importlib.util.spec_from_file_location('sea_parking_test',Path(__file__).parents[1]/'engine/unreal/Content/Python/sea_street_life.py');module=importlib.util.module_from_spec(spec)
 with patch.dict(sys.modules,{'unreal':unreal}):spec.loader.exec_module(module)
 assert not module._parking_support(Vector(0,0,100))
 def hit(label,z,normal=1):
  v=[None]*10;v[0]=True;v[4]=Vector(0,0,z);v[6]=Vector(0,0,normal);v[9]=Actor(label);return v
 result[0]=hit('DynaCity_Sea',100);assert not module._parking_support(Vector(0,0,100))
 result[0]=hit('source road',98);assert module._parking_support(Vector(0,0,100))
 result[0]=hit('source road',0);assert not module._parking_support(Vector(0,0,100))
 result[0]=hit('source road',100,.5);assert not module._parking_support(Vector(0,0,100))
 result[0]=hit('other water',100);result[0][9].tags=['DYNACITY_WATER_SURFACE'];assert not module._parking_support(Vector(0,0,100))
