"""Write the municipal Beirut boundary as an inside/outside mask for Unreal.

`city_clip.clip` (Unreal Python) reads it to cut the city map to Beirut. The mask
is in Unreal centimetres about the shared city origin (X east, Y south); a cell is
inside when its centre lies in the union of the 12 production areas.
"""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity

from .engine_districts import city_boundary


def boundary_mask(boundary, origin, cell_m=5.):
    """`boundary` in EPSG:32636 metres; returns the JSON-ready mask."""
    ox, oy = origin[0], origin[1]
    local = affinity.affine_transform(boundary, [100, 0, 0, -100, -100*ox, 100*oy])
    cell = cell_m*100
    x0, y0, x1, y1 = local.bounds
    x0 -= cell; y0 -= cell; x1 += cell; y1 += cell
    width = int((x1-x0)//cell)+1; height = int((y1-y0)//cell)+1
    gx, gy = np.meshgrid(x0+(np.arange(width)+.5)*cell, y0+(np.arange(height)+.5)*cell)
    inside = shapely.contains_xy(local, gx.ravel(), gy.ravel()).astype(np.uint8)
    return {'schema': 'dynacity-city-mask/1', 'x0': x0, 'y0': y0, 'cell_cm': cell, 'width': width, 'height': height,
            'frame': 'Unreal cm: X east, Y south, about the city origin', 'origin_utm_m': list(origin),
            'inside_share': float(inside.mean()), 'mask_b64': base64.b64encode(np.packbits(inside).tobytes()).decode()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--districts', required=True, help='beirut-districts.json')
    parser.add_argument('--city', required=True, help='city-batches.json, for the shared origin')
    parser.add_argument('--output', required=True)
    parser.add_argument('--cell-m', type=float, default=5.)
    args = parser.parse_args()
    boundary = city_boundary(json.loads(Path(args.districts).read_text(encoding='utf-8')))
    origin = json.loads(Path(args.city).read_text())['origin_utm_m']
    mask = boundary_mask(boundary, origin, args.cell_m)
    Path(args.output).write_text(json.dumps(mask), encoding='utf-8')
    print(json.dumps({k: mask[k] for k in ('width', 'height', 'cell_cm', 'inside_share')}))


if __name__ == '__main__':
    main()
