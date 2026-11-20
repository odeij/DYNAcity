"""Does the canopy height model line up with building footprints? A shift test, not a correction.

Trees stand beside buildings, not on them, so canopy (>= 3 m) overlaps footprints least when the two are registered.
The script resamples the height model to a 1 m UTM grid over the corridor's box, rasterises building footprints, and
computes the share of canopy cells lying on footprints for every canopy shift in a +-60 m window (one FFT cross-correlation).
The shift with the lowest overlap is the apparent displacement of the canopy relative to the footprints. It can come from
the model (off-nadir imagery, crown-top parallax), from the footprints, or both; nothing is corrected from it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import transform
from shapely.geometry import shape
from shapely.ops import transform as shp_transform


def load_footprints(path: str, box: tuple[float, float, float, float], crs_in: str) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    x0, y0, x1, y1 = box
    out = []
    for feature in data["features"]:
        g = shape(feature["geometry"])
        if crs_in != "EPSG:32636":
            if not (35.50 < g.bounds[0] < 35.56 and 33.86 < g.bounds[1] < 33.92):
                continue
            g = shp_transform(lambda x, y, z=None: tuple(np.asarray(t) for t in transform(crs_in, "EPSG:32636", list(x), list(y))), g)
        if g.is_valid and g.bounds[2] > x0 and g.bounds[0] < x1 and g.bounds[3] > y0 and g.bounds[1] < y1:
            out.append(g)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chm", required=True)
    ap.add_argument("--box", type=float, nargs=4, default=[733090, 3750880, 734500, 3754060], help="x0 y0 x1 y1 in EPSG:32636")
    ap.add_argument("--footprints", nargs=2, action="append", metavar=("NAME", "PATH"), required=True,
                    help="building GeoJSON; lon/lat unless the NAME ends with -utm")
    ap.add_argument("--output", required=True)
    ap.add_argument("--min-height-m", type=float, default=3.0)
    args = ap.parse_args()
    x0, y0, x1, y1 = args.box
    height, width = int(y1 - y0), int(x1 - x0)
    tf = from_origin(x0, y1, 1.0, 1.0)
    ys, xs = np.mgrid[0:height, 0:width]
    lon, lat = transform("EPSG:32636", "EPSG:3857", (x0 + xs + 0.5).ravel().tolist(), (y1 - ys - 0.5).ravel().tolist())
    with rasterio.open(args.chm) as src:
        col = np.floor((np.asarray(lon) - src.transform.c) / src.transform.a).astype(int)
        row = np.floor((np.asarray(lat) - src.transform.f) / src.transform.e).astype(int)
        r0, c0 = row.min(), col.min()
        band = src.read(1, window=((r0, row.max() + 1), (c0, col.max() + 1)))
    canopy = (band[row - r0, col - c0].reshape(height, width) >= args.min_height_m)
    fa = np.fft.rfft2(canopy.astype(np.float32))
    results = {}
    for name, path in args.footprints:
        polys = load_footprints(path, tuple(args.box), "EPSG:32636" if name.endswith("-utm") else "EPSG:4326")
        mask = rasterize([(p, 1) for p in polys], out_shape=(height, width), transform=tf).astype(bool)
        corr = np.fft.irfft2(fa * np.conj(np.fft.rfft2(mask.astype(np.float32))), s=canopy.shape) / canopy.sum()
        grid = [(float(corr[dy % height, dx % width]), dx, dy) for dy in range(-60, 61, 3) for dx in range(-60, 61, 3)]
        grid.sort()
        best, zero = grid[0], next(g for g in grid if g[1] == 0 and g[2] == 0)
        results[name] = {
            "footprints": len(polys), "footprint_share_of_box": round(float(mask.mean()), 4),
            "canopy_share_of_box": round(float(canopy.mean()), 4),
            "canopy_on_footprints_as_given": round(zero[0], 4),
            "canopy_on_footprints_at_best_shift": round(best[0], 4),
            "median_over_all_shifts": round(float(np.median([g[0] for g in grid])), 4),
            "best_shift_m": {"canopy_appears_east_of_truth": best[1], "canopy_appears_south_of_truth": best[2]},
            "reading": "Canopy sampled this far east/south of the footprints overlaps them least, so the canopy appears displaced "
                       "by about this much east/south relative to the footprints (or the footprints the opposite way).",
        }
        print(name, json.dumps(results[name]))
    Path(args.output).write_text(json.dumps({"canopy_min_height_m": args.min_height_m, "box_epsg32636": args.box,
                                             "grid_step_m": 3, "window_m": 60, "results": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
