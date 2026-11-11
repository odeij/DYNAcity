"""Validate and move only the owned PIE check to the source street pilot."""
import json
from pathlib import Path
import unreal


def focus():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    pawn = unreal.GameplayStatics.get_player_pawn(world, 0) if world else None
    if pawn is None:
        raise RuntimeError('Owned PIE pawn is unavailable')
    # The verified imported GLBs use east/south/up centimetres. Resolve this
    # source-backed point directly; editor actor enumeration includes two
    # worlds during PIE and cannot uniquely resolve editor basis markers.
    position = unreal.Vector(242364.17, -149169.1, 1199.97)
    accepted = pawn.try_location(position, 0., 'Charles Malek visual pilot')
    result = {'accepted':accepted,'status':json.loads(pawn.get_drive_status())}
    Path('C:/private/dynacity-data/engine/street-twin-20261029/mobility/pilot-location-check.json').write_text(json.dumps(result,indent=2))
    return result


print(json.dumps(focus()))
