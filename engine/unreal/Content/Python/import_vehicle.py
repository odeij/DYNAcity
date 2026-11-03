"""Import the generated illustrative sedan without changing the source scene."""
from pathlib import Path
import unreal


def build(filename):
    raise RuntimeError('Procedural sedan import is retired. Install a finished library vehicle with library_assets.install().')


def activate():
    import importlib
    import preview_traffic
    preview_traffic.stop()
    importlib.reload(preview_traffic)
    preview_traffic.start_from_config()
