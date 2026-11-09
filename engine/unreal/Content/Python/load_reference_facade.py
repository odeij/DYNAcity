"""Load the saved LGB reference-facade actors that World Partition leaves unloaded.

The original Remeil batch is saved hidden, so while these actors are unloaded the
LGB tower and its batch-mates disappear from the editor view. Loading changes the
editor session only; it saves nothing.
"""
import json
from pathlib import Path

ROOT = Path('C:/private/dynacity-data/engine/facade-reference-20261030')
AREA = 'Remeil'
SOURCE_ID = 'beirut001:d31eb718-f947-49d5-b82d-14838045a9c7'
ACTOR_PREFIX = '/Game/Maps/BeirutCityBatched.BeirutCityBatched:PersistentLevel.'


def owned_guids(descs, paths):
    """GUIDs of exactly the owned actor paths; refuse if any is not registered."""
    wanted = set(paths)
    found = {str(d.actor_path): d.guid for d in descs
             if str(getattr(d, 'actor_path', '')) in wanted}
    missing = wanted - set(found)
    if missing:
        raise RuntimeError('Reference facade actors not registered: ' + ', '.join(sorted(missing)))
    return [found[path] for path in paths]


def installed_guids(descs, area=None):
    """Include an installed replacement in its district's normal editor load.

    Installation is optional, but an existing manifest must describe the complete
    saved three-actor replacement. Validate before loading any city actors so an
    incomplete installation cannot silently leave the hidden source batch bare.
    Labels are presentation text and never establish ownership.
    """
    if area is not None and area != AREA:
        return []
    manifest = ROOT / 'native-install.json'
    if not manifest.is_file():
        return []
    report = json.loads(manifest.read_text(encoding='utf-8'))
    paths = report.get('actors') if isinstance(report, dict) else None
    if (not isinstance(report, dict) or report.get('source_id') != SOURCE_ID
            or not isinstance(paths, list) or len(paths) != 3
            or any(not isinstance(path, str) or not path.startswith(ACTOR_PREFIX)
                   for path in paths) or len(set(paths)) != 3
            or report.get('source_actor') in paths):
        raise RuntimeError('Invalid LGB reference facade installation manifest')
    return owned_guids(descs, paths)


def run():
    import unreal
    if not (ROOT / 'native-install.json').is_file():
        raise RuntimeError('Reference facade installation manifest is missing')
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors(installed_guids(descs))
