"""Measured LAS positions and RGB for local viewing, separate from flood terrain."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .las import LASHeader, iter_point_chunks


def export_point_cloud(source, output_dir, *, crs, crs_evidence, max_points=10_000_000):
    """Export every point. Horizontal reprojection only; disclose display Z shift.

    Little-endian float64 longitude/latitude/display-Z and uint8 source RGB are
    separate buffers for deck.gl. No geometry fitting, inferred points or ground
    classification. The original LAS remains unchanged.
    """
    from pyproj import CRS, Transformer

    from .las_surface import export_las_surface
    source, destination = Path(source), Path(output_dir)
    if destination.exists():
        raise ValueError("point-cloud output directory already exists")
    source_crs = CRS.from_user_input(crs)
    if not source_crs.is_projected or not crs_evidence.strip():
        raise ValueError("projected source CRS and evidence must be supplied")
    header = LASHeader.read(source)
    if not header.has_rgb:
        raise ValueError("source has no RGB; do not invent source colours")
    # Reuse strict input validation, hashing and full classification/coverage QA.
    # An explicit review cell size does not alter the rendered point positions.
    review = export_las_surface(source, destination / "surface-review.npz", cell_m=1,
                                horizontal_crs=crs, crs_evidence=crs_evidence,
                                max_points=max_points)
    transform = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    z_shift = header.bounds[4]
    total = 0
    with (destination / "positions.bin").open("wb") as pos, (destination / "colors.bin").open("wb") as rgb:
        for chunk in iter_point_chunks(source, max_points=max_points):
            lon, lat = transform.transform(chunk.x, chunk.y)
            positions = np.column_stack((lon, lat, chunk.z - z_shift)).astype("<f8")
            if not np.isfinite(positions).all():
                raise ValueError("invalid reprojected positions")
            positions.tofile(pos)
            # LAS RGB is unsigned 16-bit; preserve the high byte for display.
            # Some writers store unscaled 8-bit RGB; decide globally below.
            np.column_stack((chunk.red, chunk.green, chunk.blue)).astype("<u2").tofile(rgb)
            total += len(chunk)
    colors16 = np.fromfile(destination / "colors.bin", dtype="<u2").reshape(total, 3)
    scaled = int(colors16.max()) > 255
    colors = (colors16 >> 8 if scaled else colors16).astype(np.uint8)
    colors.tofile(destination / "colors.bin")
    x0, x1, y0, y1, _, _ = header.bounds
    xx, yy = transform.transform([x0, x1, x1, x0], [y0, y0, y1, y1])
    footprint = [[float(x), float(y)] for x, y in zip(xx, yy)]
    footprint.append(footprint[0])
    meta = {
        "schema": "dynacity-point-cloud/1", "name": source.name,
        "point_count": total, "source_sha256": review["source_sha256"],
        "source_crs": source_crs.to_string(), "crs_evidence": crs_evidence,
        "footprint": footprint,
        "bounds": [[min(xx), min(yy)], [max(xx), max(yy)]],
        "vertical_datum": None, "display_z_offset": z_shift,
        "vertical_note": "Display Z = source Z minus the tile minimum; no vertical datum transformation. Not flood terrain.",
        "color_conversion": "high byte of 16-bit source RGB" if scaled else "8-bit source RGB",
        "sampling": "all source points; no decimation",
    }
    for name in ("positions", "colors"):
        with (destination / f"{name}.bin").open("rb") as stream:
            meta[f"{name}_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    (destination / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


@dataclass(frozen=True)
class PointCloudView:
    metadata: dict
    positions: bytes
    colors: bytes

    @classmethod
    def load(cls, directory):
        root = Path(directory)
        meta = json.loads((root / "metadata.json").read_text())
        if meta.get("schema") != "dynacity-point-cloud/1":
            raise ValueError("unsupported point-cloud view schema")
        count = meta.get("point_count")
        if not isinstance(count, int) or not 0 < count <= 10_000_000:
            raise ValueError("invalid point count")
        buffers = []
        for name, width in (("positions", 24), ("colors", 3)):
            path = root / f"{name}.bin"
            if path.stat().st_size != count * width:
                raise ValueError(f"invalid {name} buffer size")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != meta.get(f"{name}_sha256"):
                raise ValueError(f"{name} checksum mismatch")
            buffers.append(content)
        xyz = np.frombuffer(buffers[0], dtype="<f8").reshape(count, 3)
        if not np.isfinite(xyz).all() or np.any(np.abs(xyz[:, 0]) > 180) or np.any(np.abs(xyz[:, 1]) > 85):
            raise ValueError("invalid point coordinates")
        footprint = np.asarray(meta["footprint"], dtype=float)
        if footprint.shape != (5, 2) or not np.isfinite(footprint).all():
            raise ValueError("invalid cloud footprint")
        alignment = meta.get('display_alignment')
        if alignment is not None:
            if alignment.get('kind') != 'visual_road_anchor' or not np.isfinite(alignment.get('translation_z_m',np.nan)):
                raise ValueError('invalid visual point-cloud alignment')
            if alignment.get('changes_source_coordinates') is not False:
                raise ValueError('display alignment must preserve source coordinates')
        return cls(meta, *buffers)
