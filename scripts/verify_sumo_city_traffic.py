"""Verify an owned HTTP SUMO session and save mapped trajectories for inspection."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python'))
from traffic_appearance import validate
from traffic_client import TrafficClient


def verify(index_path, active_path, output, batches=6):
    index = json.loads(Path(index_path).read_text(encoding='utf-8'))
    active = validate(json.loads(Path(active_path).read_text(encoding='utf-8')))
    mapping_path = active.get('traffic_road_mapping')
    mapping = json.loads(Path(mapping_path).read_text()) if mapping_path else None
    client = TrafficClient(index, 'http://127.0.0.1:8014', [35.522,33.895,35.530,33.901], road_mapping=mapping)
    client.appearance = active
    client.request['mixed'] = active.get('traffic_mixed', False)
    report = {'schema': 'dynacity-sumo-city-verification/1', 'batches': [], 'passed': False,
              'limits': ['Synthetic demand; historical aggregate vehicle mix is not current calibration.',
                         'API trajectories are not proof of Unreal instances or FPS.']}
    frames = []
    try:
        for _ in range(batches):
            started = time.perf_counter()
            batch = json.loads(client.batch())
            frames.extend(batch['frames'])
            report['batches'].append({**client.last_batch_stats, 'request_s': time.perf_counter()-started})
            print(json.dumps(report['batches'][-1]), flush=True)
        report['owned_session'] = client.session
        report['passed'] = (len(frames)>1 and frames[-1]['time_s']>frames[0]['time_s']
                            and all(a['time_s']<b['time_s'] for a,b in zip(frames,frames[1:]))
                            and sum(b['moving_trajectory_pairs'] for b in report['batches'])>0
                            and all(b['vehicle_samples']>0 for b in report['batches']))
    except Exception as error:
        report['error'] = str(error)
    finally:
        try:
            client.close()
            report['owned_session_closed'] = True
        except Exception as error:
            report['owned_session_closed'] = False
            report['close_error'] = str(error)
            report['passed'] = False
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'api-verification.json').write_text(json.dumps(report, indent=2))
        (folder/'mapped-frames.json').write_text(json.dumps({'frames': frames}, separators=(',', ':')))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', required=True)
    parser.add_argument('--active', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--batches', type=int, default=6)
    args = parser.parse_args()
    if not 2 <= args.batches <= 20:
        parser.error('Use 2-20 bounded batches.')
    result = verify(args.index, args.active, args.output, args.batches)
    print(json.dumps({key:value for key,value in result.items() if key!='batches'}))
    raise SystemExit(0 if result['passed'] else 1)
