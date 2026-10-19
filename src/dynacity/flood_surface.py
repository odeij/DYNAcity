"""Import explicitly precomputed hydraulic grids; this module does not solve flow.

NPZ bundles contain depth_m[T,Y,X], optional velocity_m_s[T,Y,X,2], and
metadata_json (a scalar JSON string). Raster imports require rasterio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np


IMPORT_WARNING = "Precomputed hydraulic output; importer does not run or validate a hydraulic solver."


def solver_runtime_status() -> dict[str, Any]:
    """Discovery only; installed packages are not proof of a working solver."""
    available = importlib.util.find_spec("anuga") is not None
    return {"engine": "ANUGA", "package_detected": available, "runner_implemented": True,
            "runner": "dynacity.flood_solver", "real_data_ready": False,
            "reason": ("Runner implemented; package discovered, not an import/health check. Explicit documented inputs required; no validated Beirut dataset is bundled."
                       if available else "Missing Python runtime dependency: anuga (not installed in this interpreter).")}


def _required_text(obj: dict, key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return value


def _metadata(metadata: dict, frames: int) -> dict:
    # JSON copy prevents callers mutating a validated input dictionary in place.
    m = json.loads(json.dumps(metadata, allow_nan=False))
    if m.get("schema") != "dynacity.hydraulic-grid.v1":
        raise ValueError("unsupported hydraulic-grid schema")
    if m.get("output_kind") != "precomputed_hydraulic":
        raise ValueError("output_kind must explicitly be precomputed_hydraulic")
    for key in ("crs", "vertical_datum", "terrain_id", "terrain_provenance"):
        _required_text(m, key)
    if m.get("horizontal_units") != "m" or m.get("depth_units") != "m":
        raise ValueError("horizontal_units and depth_units must be m")
    # Axis-aligned north-up grid: upper left outer corner and positive cell sizes.
    transform = np.asarray(m.get("grid", []), dtype=float)
    if transform.shape != (4,) or not np.isfinite(transform).all() or (transform[2:] <= 0).any():
        raise ValueError("grid must be [x_min, y_max, dx_m, dy_m] with positive resolution")
    times = m.get("timestamps", [])
    if not isinstance(times, list) or len(times) != frames:
        raise ValueError("timestamps must match depth frames")
    parsed = []
    for value in times:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.utcoffset() is None:
            raise ValueError("timestamps require a timezone")
        parsed.append(t)
    if any(b <= a for a, b in zip(parsed, parsed[1:])):
        raise ValueError("timestamps must increase strictly")
    model = m.get("model", {})
    for key in ("name", "version", "run_id", "source", "evidence"):
        _required_text(model, key)
    if model["evidence"] not in {"synthetic", "estimated", "measured", "mixed"}:
        raise ValueError("model evidence must be synthetic, estimated, measured or mixed")
    # Input references record what the external run actually used, not requested UI rain.
    for name, units in (("rainfall", "m/s"), ("boundary", "m or m3/s"), ("drainage_exchange", "m3/s")):
        record = m.get("forcing", {}).get(name, {})
        if record.get("status") not in {"supplied", "none", "unknown"}:
            raise ValueError(f"forcing.{name} requires supplied/none/unknown status")
        _required_text(record, "description")
        if record["status"] == "supplied":
            _required_text(record, "source")
            if record.get("units") != units:
                raise ValueError(f"forcing.{name} units must be {units}")
    nodata = m.get("nodata")
    if nodata is not None and (not isinstance(nodata, (int, float)) or not np.isfinite(nodata) or nodata >= 0):
        raise ValueError("nodata must be null (NaN) or a finite negative sentinel")
    if not isinstance(m.get("warnings", []), list) or not all(isinstance(w, str) for w in m.get("warnings", [])):
        raise ValueError("warnings must be strings")
    m["warnings"] = list(dict.fromkeys([IMPORT_WARNING, *m.get("warnings", [])]))
    for name, record in m["forcing"].items():
        if record["status"] == "unknown":
            m["warnings"].append(f"External model {name} forcing is unknown.")
    if m["vertical_datum"].lower() == "unknown":
        m["warnings"].append("Vertical datum unknown; do not combine absolute stages with terrain.")
    return m


@dataclass
class HydraulicOutput:
    """Validated grid snapshots. NaN means invalid; zero depth means explicitly dry.

    Velocity components follow projected CRS x/y axes, in m/s; missing is None.
    All arrays are copies. Metadata describes source outputs, not calibration.
    """
    depth_m: np.ndarray
    metadata: dict
    velocity_m_s: np.ndarray | None = None

    def __post_init__(self):
        depth = np.array(self.depth_m, dtype=float, copy=True)
        if depth.ndim != 3 or any(n == 0 for n in depth.shape):
            raise ValueError("depth_m requires nonempty [time, row, column]")
        self.metadata = _metadata(self.metadata, depth.shape[0])
        nodata = self.metadata.get("nodata")
        if nodata is not None:
            depth[depth == nodata] = np.nan
        if np.isinf(depth).any() or (depth[np.isfinite(depth)] < 0).any():
            raise ValueError("depth must be nonnegative finite metres or nodata")
        if not np.isfinite(depth).reshape(depth.shape[0], -1).any(axis=1).all():
            raise ValueError("each frame must contain valid depth cells")
        self.depth_m = depth
        if self.velocity_m_s is not None:
            if self.metadata.get("velocity_units") != "m/s":
                raise ValueError("velocity_units must be m/s")
            velocity = np.array(self.velocity_m_s, dtype=float, copy=True)
            if velocity.shape != (*depth.shape, 2):
                raise ValueError("velocity requires [time, row, column, xy]")
            if nodata is not None:
                velocity[velocity == nodata] = np.nan
            if np.isinf(velocity).any():
                raise ValueError("velocity cannot contain infinity")
            velocity[~np.isfinite(depth)] = np.nan
            self.velocity_m_s = velocity
        else:
            self.metadata["warnings"].append("Velocity unavailable; do not interpret as zero or infer flow hazard.")

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.depth_m)

    def volumes_m3(self) -> np.ndarray:
        """Depth times planar cell area over valid cells (raster storage only)."""
        dx, dy = self.metadata["grid"][2:]
        return np.nansum(self.depth_m, axis=(1, 2)) * dx * dy

    def mass_balance_residual_m3(self, net_interval_volume_m3: Sequence[float]) -> np.ndarray:
        """Δstorage minus caller-supplied net inflow per output interval.

        Include rainfall, boundary flux, drainage, infiltration and all sources/sinks.
        Refuse changing masks: coverage changes are not storage changes. This tests
        raster accounting, not the external solver's native mesh conservation.
        """
        if not np.all(self.valid == self.valid[0]):
            raise ValueError("mass balance requires a constant validity mask")
        net = np.asarray(net_interval_volume_m3, dtype=float)
        if net.shape != (len(self.depth_m) - 1,) or not np.isfinite(net).all():
            raise ValueError("one finite net volume per output interval required")
        return np.diff(self.volumes_m3()) - net

    def save(self, path: str | Path) -> None:
        """Write only to caller-chosen external/private artifact storage."""
        arrays = {"depth_m": self.depth_m, "metadata_json": np.array(json.dumps(self.metadata, allow_nan=False))}
        if self.velocity_m_s is not None:
            arrays["velocity_m_s"] = self.velocity_m_s
        np.savez_compressed(path, **arrays)


def load_hydraulic_output(path: str | Path) -> HydraulicOutput:
    """Load a safe (no Python pickle) NPZ bundle and record the artifact SHA-256."""
    path = Path(path)
    with np.load(path, allow_pickle=False) as data:
        output = HydraulicOutput(data["depth_m"], json.loads(str(data["metadata_json"].item())),
                                 data["velocity_m_s"] if "velocity_m_s" in data else None)
    output.metadata["artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def import_depth_rasters(paths: Sequence[str | Path], metadata: dict) -> HydraulicOutput:
    """Import one single-band depth raster per timestamp, without resampling.

    Supports GDAL-readable ANUGA ASC/ERS and HEC-RAS stored GeoTIFF exports.
    Caller must declare units as metres; this function never guesses/converts feet.
    Requires identical projected metre CRS and north-up affine grid. Missing data
    is preserved. Terrain identity/datum and run provenance come from metadata.
    """
    try:
        import rasterio
    except ImportError as exc:
        raise RuntimeError("Raster import requires optional dependency rasterio (terrain extra)") from exc
    if not paths:
        raise ValueError("at least one depth raster required")
    arrays, sources, reference = [], [], None
    for path in paths:
        path = Path(path)
        with rasterio.open(path) as src:
            if src.count != 1 or src.crs is None or not src.crs.is_projected or src.crs.linear_units != "metre":
                raise ValueError("depth raster must be single-band with projected metre CRS")
            t = src.transform
            if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
                raise ValueError("only north-up unrotated depth rasters supported")
            geometry = (src.crs, src.transform, src.shape)
            if reference is not None and geometry != reference:
                raise ValueError("depth rasters must have identical CRS, transform and shape")
            reference = geometry
            if src.crs != rasterio.crs.CRS.from_user_input(metadata["crs"]):
                raise ValueError("raster CRS disagrees with metadata")
            if not np.allclose(metadata["grid"], [t.c, t.f, t.a, -t.e], rtol=0, atol=1e-8):
                raise ValueError("raster grid disagrees with metadata")
            arrays.append(src.read(1, masked=True).astype(float).filled(np.nan))
        sources.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    output = HydraulicOutput(np.stack(arrays), metadata)
    output.metadata["imported_rasters"] = sources
    output.metadata["warnings"].append("Rasterization may change native mesh storage; volume is a raster diagnostic.")
    return output
