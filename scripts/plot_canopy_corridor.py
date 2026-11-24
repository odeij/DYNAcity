"""Draw the corridor canopy height model with candidate crown tops and OSM-mapped trees (Pillow, north up).

Reads the products of `dynacity build-canopy`: `chm-corridor-clip.tif`, `corridor-canopy-candidates.geojson`,
`corridor-osm-tree-heights.json`, `canopy-summary.json`, and the corridor line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.warp import transform

sys.path.insert(0, str(Path(__file__).parent))
from _pil_plot import GRID, INK, MUTED, PAPER, colorbar, scale_bar, sequential, text  # noqa: E402

TARGET_M_PER_PX = 2.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canopy-dir", required=True)
    ap.add_argument("--corridor", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    cdir = Path(args.canopy_dir)
    summary = json.loads((cdir / "canopy-summary.json").read_text(encoding="utf-8"))
    cand = json.loads((cdir / "corridor-canopy-candidates.geojson").read_text(encoding="utf-8"))["features"]
    trees = json.loads((cdir / "corridor-osm-tree-heights.json").read_text(encoding="utf-8"))["trees"]
    line = json.loads(Path(args.corridor).read_text(encoding="utf-8"))
    unmapped_m = summary["unmapped"]["distance_m"]
    min_h = summary["parameters"]["min_height_m"]

    with rasterio.open(cdir / "chm-corridor-clip.tif") as src:
        chm = src.read(1)
        t, crs = src.transform, src.crs
    k = max(1, round(TARGET_M_PER_PX / summary["ground_pixel_m"]))
    rows, cols = (chm.shape[0] // k) * k, (chm.shape[1] // k) * k
    pooled = np.nanmax(chm[:rows, :cols].reshape(rows // k, k, cols // k, k), axis=(1, 3)) if np.isfinite(chm).any() else chm
    pooled = np.where(np.isfinite(pooled), pooled, np.nan)
    m_per_px = k * summary["ground_pixel_m"]

    def to_px(lon, lat):
        x, y = transform("EPSG:4326", crs, list(lon), list(lat))
        return (np.asarray(x) - t.c) / (t.a * k), (np.asarray(y) - t.f) / (t.e * k)

    h, w = pooled.shape
    rgb = sequential(pooled, min_h, 25.0)
    rgb[~np.isfinite(pooled)] = (245, 245, 245)
    rgb[np.isfinite(pooled) & (pooled < min_h)] = (236, 238, 232)
    margin_l, margin_t, panel = 24, 96, 440
    image = Image.new("RGB", (w + margin_l * 2 + panel, h + margin_t + 60), PAPER)
    image.paste(Image.fromarray(rgb), (margin_l, margin_t))
    d = ImageDraw.Draw(image)
    ox, oy = margin_l, margin_t

    # Corridor line.
    for feature in line["features"]:
        coords = feature["geometry"]["coordinates"]
        x, y = to_px([c[0] for c in coords], [c[1] for c in coords])
        d.line([(ox + a, oy + b) for a, b in zip(x, y)], fill=(20, 20, 20), width=3)

    far = [f for f in cand if f["properties"]["likely_unmapped"]]
    near = [f for f in cand if not f["properties"]["likely_unmapped"]]
    for group, colour, r in ((near, (232, 140, 30), 1.6), (far, (214, 30, 160), 1.6)):
        x, y = to_px([f["geometry"]["coordinates"][0] for f in group], [f["geometry"]["coordinates"][1] for f in group])
        for a, b in zip(x, y):
            d.ellipse([ox + a - r, oy + b - r, ox + a + r, oy + b + r], fill=colour)
    x, y = to_px([tr["lon"] for tr in trees], [tr["lat"] for tr in trees])
    for a, b in zip(x, y):
        d.ellipse([ox + a - 4, oy + b - 4, ox + a + 4, oy + b + 4], outline=(10, 80, 200), width=2)

    text(d, (margin_l, 14), "Meta/WRI canopy height model over the Al Masar al Akhdar corridor (250 m buffer)", 20, bold=True)
    text(d, (margin_l, 44), f"Model estimates from satellite imagery, tile {summary['tile']}, ~1 m cells shown at {m_per_px:.0f} m (maximum per block). Candidates are likely crown tops, not observed trunks.", 13, MUTED)
    text(d, (margin_l, 64), "Imagery capture dates by footprint: " + "; ".join(f"{a} ({b * 100:.0f}% of buffer)" for a, b in summary["acquisition_date_share_of_buffer"].items() if b > 0.005), 13, MUTED)

    px0 = w + margin_l * 2
    y = margin_t
    c = summary["candidates"]
    unm = summary["unmapped"]
    o = summary["osm_trees"]
    colorbar(d, image, px0, y + 16, 300, 14, lambda v: sequential(v, min_h, 25.0), min_h, 25.0, "Canopy height (m); < %g m not drawn as canopy" % min_h, [3, 10, 15, 20, 25])
    y += 80
    d.ellipse([px0, y, px0 + 8, y + 8], fill=(232, 140, 30))
    text(d, (px0 + 18, y - 3), f"Candidate within {unmapped_m:g} m of a mapped tree ({c['count'] - unm['candidates_farther_than']})", 13)
    y += 26
    d.ellipse([px0, y, px0 + 8, y + 8], fill=(200, 20, 150))
    text(d, (px0 + 18, y - 3), f"Candidate > {unmapped_m:g} m from any mapped tree ({unm['candidates_farther_than']})", 13)
    y += 26
    d.ellipse([px0, y, px0 + 8, y + 8], outline=(10, 80, 200), width=2)
    text(d, (px0 + 18, y - 3), f"OSM tree node in buffer ({o['nodes_in_buffer']})", 13)
    y += 26
    d.line([px0, y + 4, px0 + 8, y + 4], fill=(20, 20, 20), width=3)
    text(d, (px0 + 18, y - 3), "Corridor line (OSM abandoned railway)", 13)
    y += 44
    text(d, (px0, y), "Candidates (local maxima >= %g m, spacing >= %g m)" % (min_h, summary["parameters"]["min_spacing_m"]), 13, bold=True)
    hs = c["height_m"]
    for s in (f"{c['count']} candidates, {c['per_hectare']} per hectare",
              f"height median {hs['median']} m, p05-p95 {hs['p05']}-{hs['p95']} m, max {hs['max']} m",
              f"{unm['candidates_farther_than']} ({unm['share'] * 100:.0f}%) > {unm['distance_m']:g} m from a mapped tree"):
        y += 22
        text(d, (px0, y), s, 13)
    y += 40
    text(d, (px0, y), "OSM-mapped trees", 13, bold=True)
    for s in (f"{o['nodes_in_buffer']} nodes in the buffer; {o['with_canopy_ge_min_height']} have canopy >= {min_h:g} m within 3 m",
              f"height median {o['height_m'].get('median', 'n/a')} m, max {o['height_m'].get('max', 'n/a')} m"):
        y += 22
        text(d, (px0, y), s, 13)
    y += 40
    text(d, (px0, y), "Limits", 13, bold=True)
    for s in ("Heights are model estimates, biased low for", "tall trees; roofs can leak in as canopy.", "Trunk position, species and ground contact",
              "are unverified. Per-pixel date is not given.", "Source: Meta and WRI (2024), CC BY 4.0;", "source imagery (c) Maxar."):
        y += 20
        text(d, (px0, y), s, 12, MUTED)
    # Inset at native resolution around a stretch of the line.
    flat = [c for f in line["features"] for c in f["geometry"]["coordinates"]]
    cx, cy = transform("EPSG:4326", crs, [flat[len(flat) // 4][0]], [flat[len(flat) // 4][1]])
    ccol, crow = int((cx[0] - t.c) / t.a), int((cy[0] - t.f) / t.e)
    half, zoom = 110, 1.8
    r0, c0 = max(crow - half, 0), max(ccol - half, 0)
    crop = chm[r0:r0 + 2 * half, c0:c0 + 2 * half]
    crop_rgb = sequential(crop, min_h, 25.0)
    crop_rgb[~np.isfinite(crop)] = (245, 245, 245)
    crop_rgb[np.isfinite(crop) & (crop < min_h)] = (236, 238, 232)
    inset = Image.fromarray(crop_rgb).resize((int(crop.shape[1] * zoom), int(crop.shape[0] * zoom)), Image.NEAREST)
    ix, iy = px0, h + margin_t + 60 - inset.size[1] - 30
    iy = max(iy, y + 50)
    text(d, (ix, iy - 10), "Zoom: 220 m square at native resolution", 13, bold=True, anchor="ls")
    image.paste(inset, (ix, iy))
    d.rectangle([ix, iy, ix + inset.size[0], iy + inset.size[1]], outline=INK)
    for group, colour in ((near, (232, 140, 30)), (far, (214, 30, 160))):
        gx, gy = to_px([f["geometry"]["coordinates"][0] for f in group], [f["geometry"]["coordinates"][1] for f in group])
        for a, b in zip(gx * k, gy * k):
            u, v = (a - c0) * zoom, (b - r0) * zoom
            if 0 <= u < inset.size[0] and 0 <= v < inset.size[1]:
                d.ellipse([ix + u - 3, iy + v - 3, ix + u + 3, iy + v + 3], fill=colour, outline=PAPER)
    for feature in line["features"]:
        coords = feature["geometry"]["coordinates"]
        gx, gy = to_px([c[0] for c in coords], [c[1] for c in coords])
        pts = [(ix + (a * k - c0) * zoom, iy + (b * k - r0) * zoom) for a, b in zip(gx, gy)]
        d.line([p for p in pts if ix <= p[0] <= ix + inset.size[0] and iy <= p[1] <= iy + inset.size[1]], fill=(20, 20, 20), width=2)
    scale_bar(d, ix, iy + inset.size[1] + 22, 1 / zoom * summary["ground_pixel_m"], 50)
    scale_bar(d, margin_l, margin_t + h + 28, m_per_px, 250)
    text(d, (margin_l + 330, margin_t + h + 28), "N is up", 13, MUTED, anchor="ls")
    image.save(args.output)
    print(f"{args.output}: {image.size}")


if __name__ == "__main__":
    main()
