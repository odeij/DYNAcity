"""Ground for the web viewer: one lon/lat height grid, shared by the page's sampler and the terrain tiles.

The viewer works in longitude/latitude, the ground models in a UTM grid. `ViewerTerrain.from_ground`
resamples a `GroundModel` (normally `terrain.merge_ground` of the Beirut 001 ground and GLO-30) once onto a
regular lon/lat grid of whole centimetres. Two things read that same grid, so nothing can disagree:

* the page downloads it (`to_bytes`, gzip of little-endian int16) and `groundZ(lon, lat)` in
  `viewer_template.html` samples it bilinearly for every layer's z;
* MapLibre's raster-dem source reads Terrarium PNG tiles (`terrarium_tile`) cut from it with the same
  bilinear rule, so the relief the basemap is draped over is the relief the layers stand on.

Sea (NaN in the ground model open to the grid's edge) is 0 m, except within `COAST_FILL_M` of a shore lower
than `COAST_FILL_MAX_M`: those cells take the shore's height, so coastal roads and quays the elevation model
reads as water do not drop to sea level. Voids enclosed by land take the nearest land height. Beyond the grid the nearest edge value holds, so there is no cliff at
its border. Heights are metres above the EGM2008 geoid, as the sources publish them; the viewer treats them
as height above its z = 0 plane. This is an approximate filtered surface model, not a survey.

Beyond the city grid the relief tiles can read a coarser country-wide `ReliefGrid` (`serve --relief`, built by
`fetch-relief`: raw GLO-30 over all of Lebanon). It only shapes the basemap's hills and mountains outside the
city; the page never downloads it, and `groundZ` still holds the city grid's edge value out there (nothing the
viewer draws stands beyond it). The two are feathered over `RELIEF_FEATHER_DEG` inside the city grid's edge.
"""

from __future__ import annotations

import gzip
import math
import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from .terrain import GroundModel

TILE_SIZE = 256
# lon/lat spacing of the shared grid: 1e-4 deg is 9.2 m east-west and 11.1 m north-south at Beirut.
CELL_DEG = 1e-4
MAX_CM = 32767
# Ground-model gaps this close to land take the nearest land height instead of sea level (0 m).
COAST_FILL_M = 60.0
COAST_FILL_MAX_M = 6.0
# Width of the band inside the city grid's edge over which the tiles fade from the country relief to the city grid.
RELIEF_FEATHER_DEG = 0.01
# Beyond the country relief its edge would repeat outwards as streaks: over this distance the height fades
# from the edge value to a heavily smoothed one (block mean of RELIEF_BLOCK cells, then a Gaussian blur).
RELIEF_OUTER_FADE_DEG = 0.05
RELIEF_BLOCK = 60


def _fill_near_land(height: np.ndarray, reach_cells: float) -> np.ndarray:
    """Gaps enclosed by land take the nearest land height; gaps open to the grid's edge (the sea) do so only
    within `reach_cells` of a shore lower than COAST_FILL_MAX_M, and are 0 m beyond."""
    from scipy import ndimage

    gap = ~np.isfinite(height)
    if not gap.any() or gap.all():
        return np.nan_to_num(height, nan=0.0)
    distance, (rows, cols) = ndimage.distance_transform_edt(gap, return_indices=True)
    filled = height[rows, cols]
    labels, _ = ndimage.label(gap)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    sea = np.isin(labels, border[border > 0])
    # Only low shores: sea beside a cliff (e.g. Raouche) stays sea rather than becoming a plateau.
    return np.where(sea & ((distance > reach_cells) | (filled > COAST_FILL_MAX_M)), 0.0, filled)


@dataclass(frozen=True)
class ReliefGrid:
    """Country-wide relief for the basemap tiles: `dm[row, col]` (int16 decimetres, up to 3276 m) is at
    (west + col*cell, north - row*cell). Sea and voids are 0 m; beyond the grid the nearest edge value holds."""

    west: float
    north: float
    cell_deg: float
    dm: np.ndarray
    source: str = ""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        rows, cols = self.dm.shape
        return (self.west, self.north - (rows - 1) * self.cell_deg, self.west + (cols - 1) * self.cell_deg, self.north)

    def sample(self, lon, lat) -> np.ndarray:
        """Bilinear height in metres at lon/lat points. Off the grid the edge value fades within
        RELIEF_OUTER_FADE_DEG into a smoothed edge, so the border does not streak out to the horizon."""

        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        fine = _bilinear(self.dm, self.west, self.north, self.cell_deg, lon, lat) / 10.0
        west, south, east, north = self.bounds
        dx = np.maximum(np.maximum(west - lon, lon - east), 0.0)
        dy = np.maximum(np.maximum(south - lat, lat - north), 0.0)
        weight = np.clip(np.hypot(dx, dy) / RELIEF_OUTER_FADE_DEG, 0.0, 1.0)
        if not (weight > 0).any():
            return fine
        grid, c_west, c_north, c_cell = self._smoothed
        return (1.0 - weight) * fine + weight * _bilinear(grid, c_west, c_north, c_cell, lon, lat)

    @cached_property
    def _smoothed(self) -> tuple[np.ndarray, float, float, float]:
        """Block means of the grid, blurred: (metres, west, north, cell) of a coarse lon/lat grid."""

        from scipy import ndimage

        b = RELIEF_BLOCK
        rows, cols = self.dm.shape
        r, c = max(rows // b, 1), max(cols // b, 1)
        blocks = self.dm[:r * b, :c * b].astype(np.float64).reshape(r, b, c, b).mean(axis=(1, 3)) / 10.0
        blurred = ndimage.gaussian_filter(blocks, 2.0, mode="nearest")
        half = (b - 1) / 2 * self.cell_deg
        return blurred, self.west + half, self.north - half, b * self.cell_deg

    def save(self, path) -> None:
        np.savez_compressed(path, west=self.west, north=self.north, cell_deg=self.cell_deg,
                            dm=self.dm.astype(np.int16), source=self.source)

    @classmethod
    def load(cls, path) -> ReliefGrid:
        with np.load(path, allow_pickle=False) as data:
            return cls(west=float(data["west"]), north=float(data["north"]), cell_deg=float(data["cell_deg"]),
                       dm=data["dm"].astype(np.int16), source=str(data["source"]))


def _bilinear(grid: np.ndarray, west: float, north: float, cell: float, lon, lat) -> np.ndarray:
    """Bilinear value of a north-up lon/lat grid at points, clamped to its edge."""

    rows, cols = grid.shape
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    col = np.clip((lon - west) / cell, 0.0, cols - 1.0)
    row = np.clip((north - lat) / cell, 0.0, rows - 1.0)
    c0 = np.minimum(np.floor(col).astype(int), max(cols - 2, 0))
    r0 = np.minimum(np.floor(row).astype(int), max(rows - 2, 0))
    fc, fr = col - c0, row - r0
    c1, r1 = np.minimum(c0 + 1, cols - 1), np.minimum(r0 + 1, rows - 1)
    g = grid
    return (g[r0, c0] * (1 - fc) * (1 - fr) + g[r0, c1] * fc * (1 - fr)
            + g[r1, c0] * (1 - fc) * fr + g[r1, c1] * fc * fr)


@dataclass(frozen=True)
class ViewerTerrain:
    """Heights in whole centimetres on a lon/lat grid; `cm[row, col]` is at (west + col*cell, north - row*cell)."""

    west: float
    north: float
    cell_deg: float
    cm: np.ndarray  # int16, rows x cols
    attribution: str = ""
    # Optional country-wide relief the tiles fall back to beyond this grid (see the module docstring).
    backdrop: ReliefGrid | None = None

    @property
    def rows(self) -> int:
        return int(self.cm.shape[0])

    @property
    def cols(self) -> int:
        return int(self.cm.shape[1])

    @classmethod
    def from_ground(cls, ground: GroundModel, *, cell_deg: float = CELL_DEG) -> ViewerTerrain:
        from pyproj import Transformer

        rows, cols = ground.ground.shape
        east_x, south_y = ground.x0 + cols * ground.cell_m, ground.y0 - rows * ground.cell_m
        to_ll = Transformer.from_crs(ground.crs, "EPSG:4326", always_xy=True)
        corners = to_ll.transform([ground.x0, east_x, ground.x0, east_x], [ground.y0, ground.y0, south_y, south_y])
        west = math.floor(min(corners[0]) / cell_deg) * cell_deg
        east = math.ceil(max(corners[0]) / cell_deg) * cell_deg
        south = math.floor(min(corners[1]) / cell_deg) * cell_deg
        north = math.ceil(max(corners[1]) / cell_deg) * cell_deg
        n_cols = int(round((east - west) / cell_deg)) + 1
        n_rows = int(round((north - south) / cell_deg)) + 1
        lon = west + np.arange(n_cols) * cell_deg
        lat = north - np.arange(n_rows) * cell_deg
        lon_g, lat_g = np.meshgrid(lon, lat)
        x, y = Transformer.from_crs("EPSG:4326", ground.crs, always_xy=True).transform(lon_g.ravel(), lat_g.ravel())
        # Nearest edge value beyond the model, so the border is not a cliff.
        x = np.clip(x, ground.x0 + ground.cell_m / 2, east_x - ground.cell_m / 2)
        y = np.clip(y, south_y + ground.cell_m / 2, ground.y0 - ground.cell_m / 2)
        height = _fill_near_land(ground.sample(x, y).reshape(n_rows, n_cols), COAST_FILL_M / (cell_deg * 111_320.0))
        cm = np.clip(np.rint(height * 100.0), -MAX_CM, MAX_CM).astype(np.int16)
        return cls(west=float(west), north=float(north), cell_deg=cell_deg, cm=cm, attribution=ground.source)

    def sample(self, lon, lat) -> np.ndarray:
        """Bilinear ground height in metres at lon/lat points; the nearest edge value off the grid."""

        return _bilinear(self.cm, self.west, self.north, self.cell_deg, lon, lat) / 100.0

    def relief(self, lon, lat) -> np.ndarray:
        """Height the relief tiles draw: this grid inside it, the backdrop beyond, feathered at the edge."""

        city = self.sample(lon, lat)
        if self.backdrop is None:
            return city
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        west, south, east, north = self.meta("", "")["bounds"]
        inset = np.minimum.reduce([lon - west, east - lon, lat - south, north - lat])
        weight = np.clip(inset / RELIEF_FEATHER_DEG, 0.0, 1.0)
        if not (weight < 1.0).any():
            return city
        return weight * city + (1.0 - weight) * self.backdrop.sample(lon, lat)

    def meta(self, url: str, tiles: str) -> dict:
        """What the page needs to decode and sample the grid; goes into the viewer payload."""

        return {
            "url": url, "tiles": tiles, "west": self.west, "north": self.north, "cell": self.cell_deg,
            "cols": self.cols, "rows": self.rows, "unit_m": 0.01, "attribution": self.attribution,
            "relief": self.backdrop is not None,
            "bounds": [self.west, self.north - (self.rows - 1) * self.cell_deg,
                       self.west + (self.cols - 1) * self.cell_deg, self.north],
        }

    def to_bytes(self) -> bytes:
        """Gzip of the little-endian int16 grid, row by row from the north-west."""

        return gzip.compress(self.cm.astype("<i2").tobytes(), compresslevel=9)

    def terrarium_tile(self, z: int, x: int, y: int) -> bytes:
        """A 256 px Terrarium-encoded PNG of the grid for web-mercator tile z/x/y (height = R*256 + G + B/256 - 32768)."""

        n = 2.0 ** z
        px = (x + (np.arange(TILE_SIZE) + 0.5) / TILE_SIZE) / n
        py = (y + (np.arange(TILE_SIZE) + 0.5) / TILE_SIZE) / n
        lon = px * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * py))))
        lon_g, lat_g = np.meshgrid(lon, lat)
        value = np.clip(self.relief(lon_g, lat_g) + 32768.0, 0.0, 65535.99)
        whole = np.floor(value)
        rgb = np.stack([np.floor(whole / 256), whole % 256, np.floor((value - whole) * 256)], axis=-1).astype(np.uint8)
        return encode_png_rgb(rgb)


def encode_png_rgb(rgb: np.ndarray) -> bytes:
    """Minimal 8-bit RGB PNG encoder, so the tile route needs no imaging library."""

    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class TileCache:
    """A small LRU of encoded tiles; a pan re-requests the same few."""

    def __init__(self, terrain: ViewerTerrain, size: int = 256):
        self.terrain, self.size, self._tiles = terrain, size, OrderedDict()

    def get(self, z: int, x: int, y: int) -> bytes:
        key = (z, x, y)
        if key in self._tiles:
            self._tiles.move_to_end(key)
            return self._tiles[key]
        tile = self.terrain.terrarium_tile(z, x, y)
        self._tiles[key] = tile
        if len(self._tiles) > self.size:
            self._tiles.popitem(last=False)
        return tile
