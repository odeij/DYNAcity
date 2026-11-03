"""Copy the source-only Unreal scaffold alongside private exported assets."""
import argparse
import json
from pathlib import Path
import shutil


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();assets=Path(args.assets).resolve();output=Path(args.output).resolve()
    if output.exists():raise FileExistsError('Refusing to replace an existing Unreal project')
    for name in ('neighbourhood.glb','traffic-replay.json'):
        if not (assets/name).is_file():raise FileNotFoundError(assets/name)
    template=Path(__file__).resolve().parents[1]/'engine'/'unreal'
    shutil.copytree(template,output)
    (output/'prototype_config.json').write_text(json.dumps({
        'scene_glb':str(assets/'neighbourhood.glb'),
        'traffic_replay':str(assets/'traffic-replay.json'),
        'status':'Source scaffold prepared; Unreal import and editor replay require verification',
    },indent=2),encoding='utf-8')
    (output/'README.md').write_text('''# DynaCITY Beirut prototype\n
Open DynaCityBeirut.uproject after Unreal finishes installing. Enable the Python
console through Output Log, then run:

    import import_neighbourhood; import_neighbourhood.build_from_config()

Wait for the import queue to finish, then save all assets and the map. This is an
editor import; do not quit immediately after the asynchronous import starts.

To test recorded SUMO traffic in the editor (outside Play mode):

    import preview_traffic; preview_traffic.start_from_config()

To remove only preview vehicles and signals:

    preview_traffic.stop()

This is a source scaffold, not a verified packaged application. Tools > Explore
Beirut provides playback, camera presets and source inspection. Optional sedan
and facade assets can be prepared with the repository scripts; otherwise replay
uses its cube fallback. Signal markers use the recorded lamp colours. Tools >
Point clouds uses privately configured capture paths and a common UTM origin.
Full signal poles, a live runtime client, driving physics and performance
comparison remain future work.

Keep the GLB and replay in their configured private location. Building alignment
is inferred; materials and support/tree forms are illustrative. Source datasets
remain private and their redistribution rights are not assumed.
''',encoding='utf-8')
    print(output/'DynaCityBeirut.uproject')


if __name__=='__main__':main()
