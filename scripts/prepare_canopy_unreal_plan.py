"""Write the offline placement plan for the Unreal corridor canopy estimate (stage 1 of the open-data run).

Filters the Meta/WRI crown-peak candidates (off building footprints, at least 3 m), converts WGS84 -> EPSG:32636 -> city
metres -> Unreal centimetres, and drops candidates in the sea (FABDEM land mask) or on an OSM carriageway (lane-derived
width, the rule that builds the Unreal road meshes).  The editor stage adds the checks that need the live scene.
Keep the output outside Git.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine' / 'unreal' / 'Content' / 'Python'))
from canopy_unreal import CITY_BASIS, build_plan, carriageway_index  # noqa: E402  (pure module shared with the editor)
from dynacity.terrain import GroundModel
from dynacity.traffic import TrafficModel, road_widths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidates', required=True)
    p.add_argument('--city', required=True, help='Project city-import json holding origin_utm_m')
    p.add_argument('--land-mask', required=True, help='Ground npz; NaN cells are sea (e.g. terrain-fabdem-roads.npz)')
    p.add_argument('--traffic', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    origin = json.loads(Path(args.city).read_text(encoding='utf-8'))['origin_utm_m']
    candidates = json.loads(Path(args.candidates).read_text(encoding='utf-8'))
    model = TrafficModel.load(args.traffic)
    roads = list(zip(model.geometry, road_widths(model).tolist()))
    plan = build_plan(candidates, origin, CITY_BASIS, ground_mask=GroundModel.load(args.land_mask),
                      carriageways=carriageway_index(roads, origin))
    plan['source'] = candidates.get('source')
    plan['inputs'] = {'candidates': str(Path(args.candidates)), 'land_mask': str(Path(args.land_mask)), 'traffic': str(Path(args.traffic))}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan), encoding='utf-8')
    print(json.dumps({k: plan[k] for k in ('candidates_in', 'offline_skipped')} | {'placeable_offline': len(plan['rows'])}, indent=2))


if __name__ == '__main__':
    main()
