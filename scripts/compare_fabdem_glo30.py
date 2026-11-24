"""Compare FABDEM with the served GLO-30 ground and the Beirut 001 ground; write stats JSON and two PNGs.

Reads three private ground files (all `GroundModel` .npz): the served GLO-30 file, the FABDEM file on the same
lattice (`dynacity fetch-fabdem`) and the Beirut 001 pack. Writes `fabdem-stats.json`,
`fabdem-minus-glo30.png` (difference map with the Beirut 001 ground coverage outlined) and
`fabdem-seam-profile.png` (a profile across the Beirut 001 / fallback seam after `terrain.merge_ground`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
from _pil_plot import INK, MUTED, PAPER, colorbar, diverging, line_chart, scale_bar, text  # noqa: E402

from dynacity.beirut001_pack import Beirut001Pack  # noqa: E402
from dynacity.terrain import SRC_PACK, GroundModel, merge_ground  # noqa: E402


def stats(values: np.ndarray) -> dict:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {"cells": 0}
    a = np.abs(v)
    return {
        "cells": int(v.size),
        "signed_m": {"median": r(np.median(v)), "mean": r(v.mean()), "p10": r(np.percentile(v, 10)), "p90": r(np.percentile(v, 90))},
        "abs_m": {"median": r(np.median(a)), "p10": r(np.percentile(a, 10)), "p90": r(np.percentile(a, 90)),
                  "p99": r(np.percentile(a, 99)), "max": r(a.max()), "rms": r(np.sqrt((v ** 2).mean()))},
    }


def r(x) -> float:
    return round(float(x), 3)


def centres(model: GroundModel) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = model.ground.shape
    xs = model.x0 + (np.arange(cols) + 0.5) * model.cell_m
    ys = model.y0 - (np.arange(rows) + 0.5) * model.cell_m
    return np.meshgrid(xs, ys)


def sample_on(model: GroundModel, like: GroundModel) -> np.ndarray:
    xx, yy = centres(like)
    return model.sample(xx.ravel(), yy.ravel()).reshape(like.ground.shape)


def outline(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask, structure=np.ones((3, 3), bool), border_value=0)


def difference_map(path: Path, diff: np.ndarray, covered: np.ndarray, cell_m: float, limit: float, stats_in: dict, stats_out: dict) -> None:
    rows, cols = diff.shape
    scale = 1
    pad_l, pad_t, pad_r, pad_b = 20, 74, 20, 96
    image = Image.new("RGB", (cols * scale + pad_l + pad_r, rows * scale + pad_t + pad_b), PAPER)
    rgb = diverging(diff, limit)
    rgb[~np.isfinite(diff)] = (206, 214, 224)  # sea / no ground in either model
    edge = outline(covered)
    rgb[edge] = (0, 0, 0)
    rgb[ndimage.binary_dilation(edge, iterations=1) & ~edge & np.isfinite(diff)] = (30, 30, 30)
    image.paste(Image.fromarray(rgb), (pad_l, pad_t))
    d = ImageDraw.Draw(image)
    text(d, (pad_l, 12), "FABDEM V1-2 minus GLO-30 (as served: 150 m low-percentile street-level filter)", 19, bold=True)
    text(d, (pad_l, 40), "Heights in metres; UTM 36N, 10 m cells. Black outline: Beirut 001 ground coverage. Grey: sea / no ground.", 13, MUTED)
    y = pad_t + rows * scale + 22
    colorbar(d, image, pad_l, y, 420, 14, lambda v: diverging(v, limit), -limit, limit, "FABDEM - GLO-30 (m), clipped at +-%g" % limit,
             [-limit, -limit / 2, 0, limit / 2, limit])
    scale_bar(d, pad_l + 500, y + 10, cell_m / scale, 1000)
    for k, (name, s) in enumerate([("inside Beirut 001 coverage", stats_in), ("outside it", stats_out)]):
        text(d, (pad_l + 620, y - 8 + 20 * k), f"{name}: median {s['signed_m']['median']:+.2f} m, p10/p90 {s['signed_m']['p10']:+.1f}/{s['signed_m']['p90']:+.1f} m", 12)
    text(d, (pad_l + cols * scale, rows * scale + pad_t + 66), "Heights above EGM2008. N is up.", 12, MUTED, anchor="ra")
    image.save(path)


def seam_transect(merged: GroundModel, known: np.ndarray, fallback_valid: np.ndarray, half_cells: int = 30) -> tuple[int, int]:
    """(row, col of the last pack cell) of an east-going pack-to-fallback seam with land both sides."""
    candidates = []
    rows, cols = known.shape
    for row in range(rows):
        line = known[row]
        for col in range(half_cells, cols - half_cells - 1):
            if line[col - half_cells:col + 1].all() and not line[col + 1:col + 1 + half_cells].any() and fallback_valid[row, col + 1:col + 1 + half_cells].all():
                candidates.append((row, col))
    if not candidates:
        raise SystemExit("no clean east-going seam found for the profile")
    return candidates[len(candidates) // 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glo30", required=True)
    ap.add_argument("--fabdem", required=True)
    ap.add_argument("--pack", required=True, help="Beirut 001 pack (.npz) whose ground is used")
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    glo, fab = GroundModel.load(args.glo30), GroundModel.load(args.fabdem)
    pack = Beirut001Pack.load(args.pack).ground
    assert (glo.x0, glo.y0, glo.cell_m, glo.ground.shape) == (fab.x0, fab.y0, fab.cell_m, fab.ground.shape), "lattices differ"

    pack_on_glo = sample_on(pack, glo)
    covered = np.isfinite(pack_on_glo)
    both = np.isfinite(glo.ground) & np.isfinite(fab.ground)
    diff = np.where(both, fab.ground - glo.ground, np.nan)
    inside, outside = np.where(covered, diff, np.nan), np.where(~covered, diff, np.nan)
    result = {
        "files": {"glo30_served": args.glo30, "fabdem": args.fabdem, "pack": args.pack},
        "grid": {"crs": glo.crs, "cell_m": glo.cell_m, "shape_rows_cols": list(glo.ground.shape),
                 "land_cells_glo30": int(np.isfinite(glo.ground).sum()), "land_cells_fabdem": int(np.isfinite(fab.ground).sum()),
                 "cells_land_in_one_only": int((np.isfinite(glo.ground) ^ np.isfinite(fab.ground)).sum())},
        "beirut001_ground_covers_cells": int(covered.sum()),
        "definition": "FABDEM minus GLO-30 on the served lattice. Signed difference; abs_m is the absolute value. "
                      "GLO-30 is the served file (percentile street-level filter, terrain.ground_from_surface); FABDEM is unfiltered.",
        "fabdem_minus_glo30": {"all_land": stats(diff), "inside_beirut001_coverage": stats(inside), "outside_beirut001_coverage": stats(outside)},
    }
    bins = [(0, 20), (20, 50), (50, 100), (100, 450)]
    result["fabdem_minus_glo30"]["by_fabdem_elevation_m"] = {
        f"{lo}-{hi}": stats(np.where((fab.ground >= lo) & (fab.ground < hi), diff, np.nan)) for lo, hi in bins}

    # FABDEM and GLO-30 against the Beirut 001 ground, at the pack's own cell centres.
    f_on_pack, g_on_pack = sample_on(fab, pack), sample_on(glo, pack)
    result["against_beirut001_ground"] = {
        "sampling": "bilinear at the Beirut 001 grid's cell centres; cells where both models and the pack are known",
        "fabdem_minus_beirut001": stats(np.where(np.isfinite(g_on_pack), f_on_pack - pack.ground, np.nan)),
        "glo30_minus_beirut001": stats(np.where(np.isfinite(f_on_pack), g_on_pack - pack.ground, np.nan)),
    }
    # Spatial structure: is the FABDEM - Beirut 001 gap a constant offset (datum) or a tilt?
    gap = np.where(np.isfinite(g_on_pack), f_on_pack - pack.ground, np.nan)
    px, py = centres(pack)
    ok = np.isfinite(gap)
    design = np.column_stack([px[ok] - px[ok].mean(), py[ok] - py[ok].mean(), np.ones(ok.sum())])
    coef = np.linalg.lstsq(design, gap[ok], rcond=None)[0]
    resid = gap[ok] - design @ coef
    result["against_beirut001_ground"]["fabdem_gap_plane_fit"] = {
        "east_slope_m_per_km": r(coef[0] * 1000), "north_slope_m_per_km": r(coef[1] * 1000), "offset_m": r(coef[2]),
        "residual_abs_median_m": r(np.median(np.abs(resid))), "residual_abs_p90_m": r(np.percentile(np.abs(resid), 90))}

    # Seam behaviour after merge_ground (Beirut 001 where it covers, the other model elsewhere).
    merged = {"glo30": merge_ground(pack, glo, extrapolate_m=400.0), "fabdem": merge_ground(pack, fab, extrapolate_m=400.0)}
    seam_stats = {}
    for name, m in merged.items():
        known = m.provenance == SRC_PACK
        land = np.isfinite(m.ground)
        steps = []
        for axis in (0, 1):
            a = known
            b = np.roll(known, -1, axis=axis)
            ga, gb = m.ground, np.roll(m.ground, -1, axis=axis)
            pair = (a & ~b & land & np.isfinite(gb)) | (~a & b & land & np.isfinite(gb))
            if axis == 0:
                pair[-1, :] = False
            else:
                pair[:, -1] = False
            steps.append(np.abs(ga - gb)[pair])
        steps = np.concatenate(steps)
        inner = known & np.roll(known, -1, axis=1) & np.isfinite(np.roll(m.ground, -1, axis=1))
        natural = np.abs(m.ground - np.roll(m.ground, -1, axis=1))[inner]
        fallback_here = sample_on(glo if name == "glo30" else fab, m)
        shift = np.where(~known & np.isfinite(m.ground) & np.isfinite(fallback_here), m.ground - fallback_here, np.nan)
        ring = ~known & ndimage.binary_dilation(known, structure=np.ones((3, 3), bool)) & np.isfinite(shift)
        far = ~known & ~ndimage.binary_dilation(known, structure=np.ones((3, 3), bool), iterations=7) & np.isfinite(shift)
        seam_stats[name] = {
            "seam_neighbour_pairs": int(steps.size),
            "step_across_seam_m": {"median": r(np.median(steps)), "p90": r(np.percentile(steps, 90)), "max": r(steps.max())},
            "step_between_neighbouring_pack_cells_m": {"median": r(np.median(natural)), "p90": r(np.percentile(natural, 90))},
            "offset_added_to_fallback_m": {
                "first_ring_median": r(np.nanmedian(shift[ring])), "first_ring_p10": r(np.nanpercentile(shift[ring], 10)),
                "first_ring_p90": r(np.nanpercentile(shift[ring], 90)),
                "beyond_70m_median": r(np.nanmedian(shift[far])), "beyond_70m_p10": r(np.nanpercentile(shift[far], 10)),
                "beyond_70m_p90": r(np.nanpercentile(shift[far], 90))},
        }
    result["seam_after_merge_ground"] = {
        "blend_m": 60.0, "extrapolate_m": 400.0, "merged_grid": list(merged["fabdem"].ground.shape), **seam_stats,
        "reading": "step_across_seam: height difference between a Beirut 001 cell and its adjacent fallback cell in the merged grid; "
                   "offset_added_to_fallback: merged height minus the unshifted fallback model, i.e. how far merge_ground moves it.",
    }

    # Profile across the seam.
    m_f, m_g = merged["fabdem"], merged["glo30"]
    known = m_f.provenance == SRC_PACK
    fab_here, glo_here = sample_on(fab, m_f), sample_on(glo, m_f)
    row, col = seam_transect(m_f, known, np.isfinite(fab_here) & np.isfinite(glo_here))
    cols_idx = np.arange(col - 30, col + 31)
    dist = (cols_idx - col - 0.5) * m_f.cell_m
    xx, yy = centres(m_f)
    pack_here = pack.sample(xx[row, cols_idx], yy[row, cols_idx])
    series = [
        {"x": dist, "y": np.where(known[row, cols_idx], pack_here, np.nan), "color": (20, 20, 20), "width": 3, "label": "Beirut 001 ground"},
        {"x": dist, "y": glo_here[row, cols_idx], "color": (200, 120, 30), "width": 1, "label": "GLO-30 filtered, raw (unshifted)"},
        {"x": dist, "y": fab_here[row, cols_idx], "color": (30, 110, 190), "width": 1, "label": "FABDEM, raw (unshifted)"},
        {"x": dist, "y": np.where(~known[row, cols_idx], m_g.ground[row, cols_idx], np.nan), "color": (200, 120, 30), "width": 3, "label": "merged outside seam, GLO-30"},
        {"x": dist, "y": np.where(~known[row, cols_idx], m_f.ground[row, cols_idx], np.nan), "color": (30, 110, 190), "width": 3, "label": "merged outside seam, FABDEM"},
    ]
    allv = np.concatenate([np.asarray(s["y"])[np.isfinite(s["y"])] for s in series])
    lo, hi = np.floor(allv.min() - 1), np.ceil(allv.max() + 1)
    shift_f = np.where(~known[row, cols_idx], m_f.ground[row, cols_idx] - fab_here[row, cols_idx], np.nan)
    shift_g = np.where(~known[row, cols_idx], m_g.ground[row, cols_idx] - glo_here[row, cols_idx], np.nan)
    image = Image.new("RGB", (1100, 860), PAPER)
    d = ImageDraw.Draw(image)
    px_x, py_y = xx[row, col], yy[row, col]
    text(d, (60, 18), "Profile across the Beirut 001 / fallback seam after merge_ground", 20, bold=True)
    text(d, (60, 46), f"West-east line at northing {py_y:.0f} m, easting {px_x:.0f} m at the seam (UTM 36N), 10 m cells. Distance 0 = last Beirut 001 cell; positive = fallback side.", 13, MUTED)
    ticks = list(range(-300, 301, 100))
    line_chart(image, (90, 110, 1060, 470), series, (-300, 300), (float(lo), float(hi)), "distance from the seam (m)", "ground (m)",
               "Ground height along the line", vlines=[(0.0, "seam")], xticks=ticks, yticks=list(np.arange(lo, hi + 0.1, max(1, round((hi - lo) / 6)))))
    sl = max(abs(np.nanmin(np.concatenate([shift_f, shift_g]))), abs(np.nanmax(np.concatenate([shift_f, shift_g]))), 1.0)
    line_chart(image, (90, 560, 1060, 790), [
        {"x": dist, "y": shift_g, "color": (200, 120, 30), "width": 3, "label": "GLO-30 offset applied"},
        {"x": dist, "y": shift_f, "color": (30, 110, 190), "width": 3, "label": "FABDEM offset applied"}],
        (-300, 300), (float(-np.ceil(sl)), float(np.ceil(sl))), "distance from the seam (m)", "offset (m)",
        "Offset merge_ground adds to the fallback model (merged minus raw)", vlines=[(0.0, "seam")], xticks=ticks,
        yticks=[float(t) for t in np.arange(-np.ceil(sl), np.ceil(sl) + 0.1, max(1, round(2 * np.ceil(sl) / 6)))])
    text(d, (60, 830), "Offsets fade from the local seam difference to a regional one over 60 m; Beirut 001 cells are never changed.", 12, MUTED)
    image.save(out / "fabdem-seam-profile.png")
    result["seam_profile_transect"] = {"row": int(row), "northing_m": r(py_y), "easting_at_seam_m": r(px_x),
                                       "offset_fabdem_at_seam_m": r(shift_f[31]), "offset_glo30_at_seam_m": r(shift_g[31]),
                                       "offset_fabdem_at_+300m": r(shift_f[-1]), "offset_glo30_at_+300m": r(shift_g[-1])}

    difference_map(out / "fabdem-minus-glo30.png", diff, covered, glo.cell_m, 10.0,
                   result["fabdem_minus_glo30"]["inside_beirut001_coverage"], result["fabdem_minus_glo30"]["outside_beirut001_coverage"])
    (out / "fabdem-stats.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("fabdem_minus_glo30", "against_beirut001_ground", "seam_after_merge_ground")}, indent=1)[:6000])


if __name__ == "__main__":
    main()
