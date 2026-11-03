"""Drape illustrative lane paint onto imported asphalt without changing roads."""
import argparse
import json

from dynacity.street_twin_drape import export_draped_finish


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--surface', action='append', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-vertical-delta-m', type=float, default=2.)
    args = parser.parse_args()
    result = export_draped_finish(args.manifest, args.surface, args.output,
                                 max_vertical_delta_m=args.max_vertical_delta_m)
    print(json.dumps([{'tile_id': tile['tile_id'], 'asset': tile.get('asset'), 'drape': tile.get('drape')}
                      for tile in result['tiles']], indent=2))
