"""Auditable planning terrain; no filtering, hydraulic conditioning or datum conversion.

Grid validation and raster ingestion require the optional ``terrain`` dependency. Inputs are local
rasters, with metre elevations and a caller-declared vertical reference. A DTM
declaration is provenance supplied by the producer, not a classification inferred
from pixel values. NaN means unavailable, never sea or a zero elevation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np

TerrainKind = Literal["bare_earth", "surface", "estimated_ground", "unknown"]


def _rasterio():
    try:
        import rasterio
    except ImportError as exc:
        raise ImportError("Planning terrain requires pip install -e '.[terrain]'") from exc
    return rasterio


def _metric_crs(value: str):
    crs = _rasterio().crs.CRS.from_user_input(value)
    if not crs.is_projected or not math.isclose(crs.linear_units_factor[1], 1.0):
        raise ValueError("target/grid CRS must be projected with metre horizontal units")
    return crs


@dataclass(frozen=True)
class TerrainProvenance:
    """Producer declarations; heights are metres in ``vertical_reference``.

    ``source_resolution_m`` is original effective horizontal detail, before any
    prior resampling (if known); it cannot be finer than this file's postings.
    A hash identifies the ingested file but does not authenticate its declarations.
    """

    source: str
    kind: TerrainKind
    vertical_reference: str
    evidence: str
    elevation_unit: str = "m"
    source_resolution_m: float | None = None
    acquisition_date: str | None = None
    vertical_accuracy_m: float | None = None

    def __post_init__(self):
        for field in ("source", "vertical_reference", "evidence"):
            value = getattr(self, field)
            if not value.strip() or value.strip().lower() in {"unknown", "missing", "unspecified"}:
                raise ValueError(f"{field} must be explicitly declared")
        if self.kind not in {"bare_earth", "surface", "estimated_ground", "unknown"}:
            raise ValueError("invalid terrain kind")
        if self.elevation_unit != "m":
            raise ValueError("elevation_unit must be m; convert and document other units upstream")
        for field in ("source_resolution_m", "vertical_accuracy_m"):
            value = getattr(self, field)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{field} must be finite and positive")


@dataclass(frozen=True)
class TerrainQuality:
    valid_fraction: float
    valid_cells: int
    total_cells: int
    elevation_min_m: float
    elevation_max_m: float
    effective_resolution_m: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TerrainGrid:
    """North-up square grid, outer west/north origin; read-only metre heights.

    ``elevation_m[r,c]`` is at ``x0+(c+.5)*cell_m, y0-(r+.5)*cell_m``.
    ``valid`` is authoritative. Extent is (west, south, east, north), in grid CRS.
    Source resolution and CRS describe the input file, before reprojection.
    """

    crs: str
    x0: float
    y0: float
    cell_m: float
    elevation_m: np.ndarray
    provenance: TerrainProvenance
    source_crs: str
    source_resolution: tuple[float, float]
    source_resolution_unit: str
    source_nodata: float | None
    source_sha256: str
    resampling: str = "none"

    def __post_init__(self):
        _metric_crs(self.crs)
        if not all(math.isfinite(v) for v in (self.x0, self.y0, self.cell_m)) or self.cell_m <= 0:
            raise ValueError("grid origin must be finite and cell_m positive")
        z = np.array(self.elevation_m, dtype=np.float64, copy=True)
        if z.ndim != 2 or not z.size or not np.isfinite(z).any():
            raise ValueError("terrain must be a nonempty 2D grid with valid elevations")
        if np.isinf(z).any():
            raise ValueError("infinite elevations must be masked as nodata")
        if len(self.source_resolution) != 2 or any(not math.isfinite(v) or v <= 0 for v in self.source_resolution):
            raise ValueError("source resolution must have two finite positive values")
        if self.source_resolution_unit not in {"degree", "metre"} and not self.provenance.source_resolution_m:
            raise ValueError("non-metric native resolution requires source_resolution_m")
        z.setflags(write=False)
        object.__setattr__(self, "elevation_m", z)

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.elevation_m)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        rows, cols = self.elevation_m.shape
        return self.x0, self.y0 - rows * self.cell_m, self.x0 + cols * self.cell_m, self.y0

    def quality(self, *, street_width_m: float = 6.0) -> TerrainQuality:
        """A resolution screen, not a claim that terrain resolves a particular street.

        Three cells across a caller-specified street is only a conservative
        screening heuristic, not a validated model resolution requirement.
        """
        if not math.isfinite(street_width_m) or street_width_m <= 0:
            raise ValueError("street_width_m must be finite and positive")
        detail = self.provenance.source_resolution_m
        if detail is None:
            # Geographic native posting cannot safely be equated to metres here.
            detail = max(self.source_resolution) if self.source_resolution_unit == "metre" else self.cell_m
        effective = max(self.cell_m, detail)
        warnings = ["Underpasses, culverts and multi-level streets require surveyed connectivity and elevations; a single raster cannot establish them."]
        if self.provenance.kind != "bare_earth":
            warnings.append(f"Terrain is {self.provenance.kind}, not verified bare earth; roofs/vegetation/filter artefacts may obstruct flow.")
        if effective > street_width_m / 3:
            warnings.append("Terrain cannot resolve the requested street width with three independent source cells; resampling adds no source detail.")
        if self.provenance.vertical_accuracy_m is None:
            warnings.append("Vertical accuracy is undeclared; shallow flood-depth reliability is unverified.")
        if self.provenance.source_resolution_m is None:
            warnings.append("Original effective source resolution is undeclared; native file posting is only a lower bound on source detail.")
        count = int(self.valid.sum())
        if count < self.elevation_m.size:
            warnings.append("Nodata exists in the domain; supply explicit model boundaries/masks and do not fill it as zero or assume sea.")
        return TerrainQuality(count / self.elevation_m.size, count, self.elevation_m.size,
                              float(np.nanmin(self.elevation_m)), float(np.nanmax(self.elevation_m)),
                              effective, tuple(warnings))

    def sample(self, x, y) -> np.ndarray:
        """Nearest cell, metre coordinates in this CRS; NaN outside/nodata.

        Uses half-open outer bounds west<=x<east, south<y<=north. Nonfinite
        queries return NaN. No interpolation across gaps or implied CRS conversion.
        """
        x, y = np.broadcast_arrays(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        finite = np.isfinite(x) & np.isfinite(y)
        c = np.floor(np.where(finite, (x - self.x0) / self.cell_m, -1)).astype(np.int64)
        r = np.floor(np.where(finite, (self.y0 - y) / self.cell_m, -1)).astype(np.int64)
        rows, cols = self.elevation_m.shape
        inside = finite & (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
        return np.where(inside, self.elevation_m[np.clip(r, 0, rows - 1), np.clip(c, 0, cols - 1)], np.nan)

    def save(self, path: str | Path) -> None:
        """Save outside Git; JSON metadata and arrays, never pickle."""
        meta = {k: v for k, v in self.__dict__.items() if k != "elevation_m"}
        meta["provenance"] = asdict(self.provenance)
        meta["schema_version"] = 1
        np.savez_compressed(path, metadata=json.dumps(meta), elevation_m=self.elevation_m)

    @classmethod
    def load(cls, path: str | Path) -> TerrainGrid:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["metadata"]))
            if meta.pop("schema_version") != 1:
                raise ValueError("unsupported terrain schema")
            meta["provenance"] = TerrainProvenance(**meta["provenance"])
            meta["source_resolution"] = tuple(meta["source_resolution"])
            return cls(elevation_m=data["elevation_m"], **meta)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_terrain_raster(
    path: str | Path, *, provenance: TerrainProvenance, target_crs: str,
    cell_m: float | None = None, bounds: tuple[float, float, float, float] | None = None,
    resampling: Literal["nearest", "bilinear"] = "nearest",
    min_valid_fraction: float = 0.0, max_cells: int = 10_000_000,
) -> TerrainGrid:
    """Ingest local single-band terrain into a metric, north-up planning grid.

    ``bounds`` is the exact required west/south/east/north domain in target CRS;
    spans must be integer cell multiples. Without it, coverage is rounded up.
    Invalid source cells remain invalid under nearest-neighbour mask transport,
    including with bilinear values. No hole filling, sea masking, ground filtering,
    or vertical-reference transformation occurs. Raster scales/offsets are applied.
    Geographic sources are supported only with a metric target CRS. Effective
    resolution is conservatively retained from native postings and declarations.
    """
    rio = _rasterio()
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import calculate_default_transform, reproject, transform, transform_bounds

    path = Path(path)
    if not path.is_file():
        raise ValueError("terrain input must be an existing local raster file")
    dst_crs = _metric_crs(target_crs)
    if resampling not in {"nearest", "bilinear"}:
        raise ValueError("resampling must be nearest or bilinear")
    if not math.isfinite(min_valid_fraction) or not 0 <= min_valid_fraction <= 1:
        raise ValueError("min_valid_fraction must be between zero and one")
    if not isinstance(max_cells, int) or max_cells <= 0:
        raise ValueError("max_cells must be a positive integer")
    with rio.open(path) as src:
        if src.count != 1 or src.crs is None:
            raise ValueError("terrain requires one band and an embedded source CRS")
        t = src.transform
        if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
            raise ValueError("source raster must be north-up without rotation; rectify upstream")
        unit = src.units[0]
        if unit and unit.lower() not in {"m", "metre", "meter", "metres", "meters"}:
            raise ValueError("source elevation unit conflicts with metre declaration")
        # Horizontal warp deliberately does not apply vertical datum changes.
        default, _, _ = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        if cell_m is None:
            cell_m = max(abs(default.a), abs(default.e))
        if not math.isfinite(cell_m) or cell_m <= 0:
            raise ValueError("cell_m must be finite and positive")
        extent = transform_bounds(src.crs, dst_crs, *src.bounds, densify_pts=21)
        if bounds is None:
            west, south, east, north = extent
            cols = math.ceil((east - west) / cell_m)
            rows = math.ceil((north - south) / cell_m)
        else:
            if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
                raise ValueError("bounds must have four finite coordinates")
            west, south, east, north = bounds
            if east <= west or north <= south:
                raise ValueError("bounds must have positive width and height")
            spans = ((east - west) / cell_m, (north - south) / cell_m)
            if not all(math.isclose(v, round(v), abs_tol=1e-7, rel_tol=0) for v in spans):
                raise ValueError("bounds spans must be integer multiples of cell_m")
            cols, rows = (round(v) for v in spans)
            if east <= extent[0] or west >= extent[2] or north <= extent[1] or south >= extent[3]:
                raise ValueError("requested bounds do not overlap source extent")
        if rows <= 0 or cols <= 0 or rows * cols > max_cells:
            raise ValueError("output grid empty or exceeds max_cells")
        if src.width * src.height > max_cells:
            raise ValueError("source raster exceeds max_cells; supply a bounded local crop")
        data = src.read(1, masked=True).astype(np.float64)
        values = data.filled(np.nan) * src.scales[0] + src.offsets[0]
        values[~np.isfinite(values)] = np.nan
        destination = np.full((rows, cols), np.nan)
        dst_valid = np.zeros((rows, cols), dtype=np.uint8)
        dst_transform = from_origin(west, north, cell_m, cell_m)
        warp = dict(src_transform=t, src_crs=src.crs, dst_transform=dst_transform, dst_crs=dst_crs)
        reproject(values, destination, src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=getattr(Resampling, resampling), **warp)
        reproject(np.isfinite(values).astype(np.uint8), dst_valid, dst_nodata=0,
                  resampling=Resampling.nearest, **warp)
        destination[dst_valid == 0] = np.nan
        fraction = float(np.isfinite(destination).mean())
        if fraction == 0 or fraction < min_valid_fraction:
            raise ValueError(f"valid terrain coverage {fraction:.3f} below required {min_valid_fraction:.3f}, or empty")
        # Native posting measured near source centre in the output metric CRS.
        cx, cy = (src.bounds.left + src.bounds.right) / 2, (src.bounds.bottom + src.bounds.top) / 2
        xx, yy = transform(src.crs, dst_crs, [cx, cx + t.a, cx], [cy, cy, cy + abs(t.e)])
        posting_m = max(math.hypot(xx[1] - xx[0], yy[1] - yy[0]), math.hypot(xx[2] - xx[0], yy[2] - yy[0]))
        p = asdict(provenance)
        p["source_resolution_m"] = max(posting_m, provenance.source_resolution_m or 0)
        return TerrainGrid(str(dst_crs), west, north, cell_m, destination, TerrainProvenance(**p),
                           str(src.crs), (abs(t.a), abs(t.e)),
                           "degree" if src.crs.is_geographic else src.crs.linear_units,
                           src.nodata, _sha256(path), resampling)


def load_legacy_copernicus(path: str | Path) -> TerrainGrid:
    """Explicit adapter for this project's existing filtered GLO-30 NPZ only.

    The stored attribution must identify Copernicus. EGM2008 and nominal 30 m
    originate in the pipeline/product documentation, not the legacy NPZ header.
    Filtered-out sea/low terrain is unrecoverable; never promote to surveyed DTM.
    """
    path = Path(path)
    with np.load(path, allow_pickle=False) as data:
        source = str(data["source"])
        if "Copernicus DEM GLO-30" not in source:
            raise ValueError("legacy adapter requires identified Copernicus DEM GLO-30")
        p = TerrainProvenance(source, "estimated_ground", "EGM2008 (EPSG:3855)",
                              "Legacy terrain.py: 10th percentile ~150 m then Gaussian blur; <=0 m masked. "
                              "Vertical reference and nominal source resolution from Copernicus product specification, not NPZ metadata.",
                              source_resolution_m=30.0)
        return TerrainGrid(str(data["crs"]), float(data["x0"]), float(data["y0"]),
                           float(data["cell_m"]), data["ground"], p, "EPSG:4326",
                           (1 / 3600, 1 / 3600), "degree", None, _sha256(path),
                           "legacy bilinear after percentile filtering; nominal source posting")
