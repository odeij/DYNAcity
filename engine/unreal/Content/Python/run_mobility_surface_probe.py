"""Capture the native wheel support query for the owned traffic check."""
import importlib.util
from pathlib import Path

path = Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/verify_efasim_mobility.py')
spec = importlib.util.spec_from_file_location('_dynacity_surface_probe', path)
surface_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface_module)
print(surface_module.surface_probe())
