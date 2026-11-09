"""Run read-only PIE wheel queries without reloading an active verification."""
import importlib.util
from pathlib import Path


def probe():
    path = Path(__file__).with_name('verify_efasim_mobility.py')
    spec = importlib.util.spec_from_file_location('_dynacity_surface_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.surface_probe()
