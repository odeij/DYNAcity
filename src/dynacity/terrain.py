"""Ground elevation for hazards that follow terrain (floods).

`fetch_ground_model` reads the Copernicus GLO-30 elevation model over the
snapshot's extent (HTTP range reads of the public cloud-optimised GeoTIFF; no
whole-tile download), turns it into an approximate bare-earth surface, and
resamples it onto a regular grid in the snapshot's UTM CRS. The result is a
small `.npz` kept under `DYNACITY_DATA_ROOT`.

GLO-30 is a *surface* model: in a dense city its 30 m cells mostly sit on
roofs. The ground is approximated by a low percentile over a ~150 m window
(streets, courtyards and open lots pull it down to street level) followed by
light smoothing. That is good enough to tell the seafront from Achrafieh hill,
not to resolve a street-scale depression. Cells at or below sea level in the
source are treated as sea and carry no ground.

Heights are metres above the EGM2008 geoid, as published.

`rasterio` is only needed to fetch (`pip install -e '.[terrain]'`); loading
and sampling a saved model needs numpy alone.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .contracts import UrbanStateSnapshot
from .viewer import _utm_zone, utm_to_lonlat

COPERNICUS_TILE_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{lat}_00_{lon}_00_DEM/Copernicus_DSM_COG_10_{lat}_00_{lon}_00_DEM.tif"
)
ATTRIBUTION = (
    "Elevation: Copernicus DEM GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
    "2014-2018, provided under COPERNICUS by the European Union and ESA."
)
SEA_LEVEL_M = 0.0
# Ground filter: low percentile over a window this wide, then a light blur.
GROUND_WINDOW_M = 150.0
GROUND_PERCENTILE = 10.0


@dataclass(frozen=True)
class GroundModel:
    """Approximate ground elevation on a north-up grid in the snapshot CRS.

    `ground[row, col]` is the cell centred at
    (`x0 + (col + 0.5) * cell_m`, `y0 - (row + 0.5) * cell_m`); NaN is sea.
    """

    crs: str
    x0: float
    y0: float
    cell_m: float
    ground: np.ndarray
    source: str = ATTRIBUTION

    def sample(self, x: np.ndarray | Sequence[float], y: np.ndarray | Sequence[float]) -> np.ndarray:
        """Bilinear ground elevation at snapshot-CRS points; NaN off the grid or at sea."""

        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        col = (x - self.x0) / self.cell_m - 0.5
        row = (self.y0 - y) / self.cell_m - 0.5
        rows, cols = self.ground.shape
        inside = (col >= 0) & (row >= 0) & (col <= cols - 1) & (row <= rows - 1)
        c0 = np.clip(np.floor(col).astype(int), 0, max(cols - 2, 0))
        r0 = np.clip(np.floor(row).astype(int), 0, max(rows - 2, 0))
        fc = np.clip(col - c0, 0.0, 1.0)
        fr = np.clip(row - r0, 0.0, 1.0)
        c1 = np.minimum(c0 + 1, cols - 1)
        r1 = np.minimum(r0 + 1, rows - 1)
        g = self.ground
        value = (
            g[r0, c0] * (1 - fc) * (1 - fr) + g[r0, c1] * fc * (1 - fr)
            + g[r1, c0] * (1 - fc) * fr + g[r1, c1] * fc * fr
        )
        return np.where(inside, value, np.nan)

    def centres(self, x_min: float, y_min: float, x_max: float, y_max: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cell centres (x, y) and ground, as 2D north-up arrays, for the cells whose centres lie in the box."""

        rows, cols = self.ground.shape
        c_lo = max(0, math.floor((x_min - self.x0) / self.cell_m))
        c_hi = min(cols, math.ceil((x_max - self.x0) / self.cell_m))
        r_lo = max(0, math.floor((self.y0 - y_max) / self.cell_m))
        r_hi = min(rows, math.ceil((self.y0 - y_min) / self.cell_m))
        if c_lo >= c_hi or r_lo >= r_hi:
            empty = np.zeros((0, 0))
            return empty, empty, empty
        cc, rr = np.meshgrid(np.arange(c_lo, c_hi), np.arange(r_lo, r_hi))
        x = self.x0 + (cc + 0.5) * self.cell_m
        y = self.y0 - (rr + 0.5) * self.cell_m
        return x, y, self.ground[r_lo:r_hi, c_lo:c_hi]

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path, crs=self.crs, x0=self.x0, y0=self.y0, cell_m=self.cell_m,
            ground=self.ground.astype(np.float32), source=self.source,
        )

    @classmethod
    def load(cls, path: str | Path) -> GroundModel:
        with np.load(path, allow_pickle=False) as data:
            return cls(
                crs=str(data["crs"]), x0=float(data["x0"]), y0=float(data["y0"]), cell_m=float(data["cell_m"]),
                ground=data["ground"].astype(np.float64), source=str(data["source"]),
            )


def attach_ground_elevation(snapshot: UrbanStateSnapshot, ground: GroundModel) -> UrbanStateSnapshot:
    """A copy of the snapshot with `ground_elevation_m` set wherever a centroid lands on land."""

    if ground.crs != snapshot.crs:
        raise ValueError(f"ground model is in {ground.crs}, snapshot in {snapshot.crs}")
    located = [i for i, o in enumerate(snapshot.objects) if o.centroid_x_m is not None and o.centroid_y_m is not None]
    heights = ground.sample(
        [snapshot.objects[i].centroid_x_m for i in located], [snapshot.objects[i].centroid_y_m for i in located]
    )
    objects = list(snapshot.objects)
    for i, h in zip(located, heights, strict=True):
        objects[i] = objects[i].model_copy(update={"ground_elevation_m": None if np.isnan(h) else round(float(h), 2)})
    return snapshot.model_copy(update={"objects": objects})


def ground_from_surface(surface: np.ndarray, cell_m: float) -> np.ndarray:
    """Approximate bare earth from a surface model on a regular grid; sea (≤ 0 m) becomes NaN."""

    from scipy import ndimage

    sea = ~np.isfinite(surface) | (surface <= SEA_LEVEL_M)
    # Sea gets +inf so a coastal window's low percentile comes from land.
    land = np.where(sea, np.inf, surface)
    size = max(3, int(round(GROUND_WINDOW_M / cell_m)) | 1)
    low = ndimage.percentile_filter(land, GROUND_PERCENTILE, size=size, mode="nearest")
    low = np.where(np.isfinite(low), low, np.nan)
    # Normalised blur over land only, so the sea does not drag the coast down.
    weight = ndimage.gaussian_filter((~sea & np.isfinite(low)).astype(float), 1.0)
    blurred = ndimage.gaussian_filter(np.nan_to_num(np.where(sea, 0.0, low)), 1.0)
    ground = np.where(weight > 1e-6, blurred / np.maximum(weight, 1e-6), np.nan)
    return np.where(sea, np.nan, np.maximum(ground, SEA_LEVEL_M + 0.1))


def fetch_ground_model(snapshot: UrbanStateSnapshot, *, cell_m: float = 10.0, margin_m: float = 1500.0) -> GroundModel:
    """Copernicus GLO-30 over the snapshot's buildings (plus a margin) → `GroundModel`."""

    import rasterio
    from rasterio.windows import Window, from_bounds

    zone, north = _utm_zone(snapshot.crs)
    xs = [o.centroid_x_m for o in snapshot.objects if o.centroid_x_m is not None]
    ys = [o.centroid_y_m for o in snapshot.objects if o.centroid_y_m is not None]
    if not xs:
        raise ValueError("snapshot has no building centroids to size the terrain grid")
    x0, x1 = math.floor(min(xs) - margin_m), math.ceil(max(xs) + margin_m)
    y0, y1 = math.floor(min(ys) - margin_m), math.ceil(max(ys) + margin_m)
    cols, rows = int((x1 - x0) / cell_m), int((y1 - y0) / cell_m)
    xc = x0 + (np.arange(cols) + 0.5) * cell_m
    yc = y1 - (np.arange(rows) + 0.5) * cell_m

    # Geographic position of every target cell centre.
    lon = np.empty((rows, cols))
    lat = np.empty((rows, cols))
    for r, y in enumerate(yc):
        for c, x in enumerate(xc):
            lon[r, c], lat[r, c] = utm_to_lonlat(float(x), float(y), zone, north)

    lon_lo, lon_hi, lat_lo, lat_hi = lon.min(), lon.max(), lat.min(), lat.max()
    if math.floor(lon_lo) != math.floor(lon_hi) or math.floor(lat_lo) != math.floor(lat_hi):
        raise ValueError("snapshot extent crosses a 1° Copernicus tile boundary; not supported yet")
    url = COPERNICUS_TILE_URL.format(lat=_hemi(math.floor(lat_lo), "N", "S", 2), lon=_hemi(math.floor(lon_lo), "E", "W", 3))
    pad = 0.003  # ~300 m, so the ground filter has context at the edges
    with rasterio.open(url) as src:
        window = from_bounds(lon_lo - pad, lat_lo - pad, lon_hi + pad, lat_hi + pad, src.transform)
        window = window.round_offsets().round_lengths()
        window = Window(window.col_off, window.row_off, window.width + 1, window.height + 1)
        surface = src.read(1, window=window, boundless=True, fill_value=np.nan).astype(np.float64)
        transform = src.window_transform(window)
    # Filter on the source grid, where one cell is ~30 m (narrower east-west).
    src_cell_m = abs(transform.e) * 111_320.0
    ground_src = ground_from_surface(surface, src_cell_m)

    # Bilinear resample onto the UTM grid (pixel-is-area: centres at +0.5).
    col = (lon - transform.c) / transform.a - 0.5
    row = (lat - transform.f) / transform.e - 0.5
    src_model = GroundModel(crs="source", x0=-0.5, y0=0.5, cell_m=1.0, ground=ground_src)
    ground = src_model.sample(col, -row).reshape(rows, cols)
    return GroundModel(crs=snapshot.crs, x0=float(x0), y0=float(y1), cell_m=cell_m, ground=ground)


def _hemi(value: int, positive: str, negative: str, width: int) -> str:
    return f"{positive if value >= 0 else negative}{abs(value):0{width}d}"
