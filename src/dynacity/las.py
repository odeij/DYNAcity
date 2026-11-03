"""Small, dependency-light LAS 1.2 reader and explicit PDAL pipeline builder.

Reads LAS headers and points by unpacking the binary format directly, so the
lightweight install path (`pip install -e '.[dev]'`) can inspect point clouds
and run the test suite without PDAL/GDAL. Real conversion work still shells out
to PDAL; this module only builds the pipeline definition.

Two facts about this project's files drive the design:

- The headers carry no CRS (`vlr_count` is 0). EPSG:32636 is therefore
  *asserted* by `copc_pipeline`, established externally by aligning to BBED
  rather than read from the data. Getting this wrong misplaces every building.
- The clouds are large — most exceed the 10M-point guard in
  `iter_point_chunks` — so header-only inspection is the default and full
  reads are opt-in.

Despite the `.las` extension and this project's `lidar_*` naming, the source
data is photogrammetric rather than laser-scanned; see the provenance note in
README. Nothing in this module depends on which it is.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class LASHeader:
    path: Path
    version: str
    software: str
    header_size: int
    point_data_offset: int
    vlr_count: int
    point_format: int
    point_record_length: int
    point_count: int
    scale: tuple[float, float, float]
    offset: tuple[float, float, float]
    bounds: tuple[float, float, float, float, float, float]
    creation_year: int
    creation_day_of_year: int

    @classmethod
    def read(cls, path: str | Path) -> "LASHeader":
        """Parse the fixed-layout public header block.

        Reads only the first 375 bytes — the maximum public header size across
        LAS 1.0–1.4 — so this is O(1) regardless of file size and safe to call
        on multi-gigabyte clouds. The byte offsets below are from the LAS
        specification and are absolute positions within that block.
        """

        source = Path(path)
        with source.open("rb") as handle:
            header = handle.read(375)
        if len(header) < 227 or header[:4] != b"LASF":
            raise ValueError(f"not a supported LAS file: {source}")
        major, minor = header[24], header[25]
        version = f"{major}.{minor}"
        # LAS 1.4 added a 64-bit point count because the legacy 32-bit field
        # overflows past ~4.3 billion points; it may legitimately be 0 there,
        # in which case the legacy field is still authoritative.
        legacy_count = struct.unpack_from("<I", header, 107)[0]
        point_count = legacy_count
        if version == "1.4" and len(header) >= 255:
            extended_count = struct.unpack_from("<Q", header, 247)[0]
            point_count = extended_count or legacy_count
        return cls(
            path=source,
            version=version,
            software=header[58:90].decode("ascii", errors="replace").strip("\x00 "),
            header_size=struct.unpack_from("<H", header, 94)[0],
            point_data_offset=struct.unpack_from("<I", header, 96)[0],
            vlr_count=struct.unpack_from("<I", header, 100)[0],
            # Top two bits flag LAZ compression, not the format number.
            point_format=header[104] & 0x3F,
            point_record_length=struct.unpack_from("<H", header, 105)[0],
            point_count=int(point_count),
            scale=struct.unpack_from("<ddd", header, 131),
            offset=struct.unpack_from("<ddd", header, 155),
            # LAS stores max before min for each axis; this reorders them to
            # the conventional (minx, maxx, miny, maxy, minz, maxz).
            bounds=(
                struct.unpack_from("<d", header, 187)[0],
                struct.unpack_from("<d", header, 179)[0],
                struct.unpack_from("<d", header, 203)[0],
                struct.unpack_from("<d", header, 195)[0],
                struct.unpack_from("<d", header, 219)[0],
                struct.unpack_from("<d", header, 211)[0],
            ),
            creation_year=struct.unpack_from("<H", header, 92)[0],
            creation_day_of_year=struct.unpack_from("<H", header, 90)[0],
        )

    @property
    def has_rgb(self) -> bool:
        return self.point_format in {2, 3, 5, 7, 8, 10}

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "version": self.version,
            "software": self.software,
            "point_count": self.point_count,
            "point_format": self.point_format,
            "point_record_length": self.point_record_length,
            "vlr_count": self.vlr_count,
            "scale": self.scale,
            "offset": self.offset,
            "bounds": self.bounds,
            "creation_year": self.creation_year,
            "creation_day_of_year": self.creation_day_of_year,
            "embedded_crs": None if self.vlr_count == 0 else "inspect_vlrs",
        }


@dataclass(frozen=True)
class LASPointChunk:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    classification: np.ndarray
    red: np.ndarray | None = None
    green: np.ndarray | None = None
    blue: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.x)


def _point_dtype(header: LASHeader) -> np.dtype:
    """Build a numpy view over the raw point records.

    Only the fields this project uses are described (position, classification,
    optional RGB); `offsets` plus `itemsize` let numpy stride over the rest of
    each record without copying or decoding it. `itemsize` must come from the
    header rather than be computed, since records can carry extra bytes beyond
    the standard format.
    """

    if header.point_format not in {0, 1, 2, 3}:
        raise ValueError(
            f"minimal reader supports LAS point formats 0-3, got {header.point_format}; use PDAL"
        )
    names = ["x", "y", "z", "classification"]
    formats: list[str] = ["<i4", "<i4", "<i4", "u1"]
    offsets = [0, 4, 8, 15]
    if header.point_format in {2, 3}:
        rgb_offset = 20 if header.point_format == 2 else 28
        names.extend(["red", "green", "blue"])
        formats.extend(["<u2", "<u2", "<u2"])
        offsets.extend([rgb_offset, rgb_offset + 2, rgb_offset + 4])
    return np.dtype(
        {
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": header.point_record_length,
        }
    )


def iter_point_chunks(
    path: str | Path,
    *,
    chunk_points: int = 500_000,
    max_points: int = 10_000_000,
) -> Iterator[LASPointChunk]:
    """Stream decoded points in chunks, refusing files above `max_points`.

    The guard is a refusal, not a truncation: silently reading the first 10M
    points of a 98M-point cloud would produce morphology features for a
    spatially arbitrary corner of the survey and no indication anything was
    missing. Callers who genuinely want a partial read must raise the limit
    explicitly (`--max-points`).

    Note that raising it is often not enough — `features.load_points`
    materialises every chunk, so ~100M points needs multiple GB of RAM. For the
    larger clouds in this project the intended path is COPC tiling via PDAL, as
    the error message says.

    Coordinates are stored as scaled integers; the scale/offset applied here is
    what converts them back to CRS units.
    """

    header = LASHeader.read(path)
    if header.point_count > max_points:
        raise ValueError(
            f"{header.path.name} has {header.point_count:,} points; maximum is "
            f"{max_points:,}. Use PDAL/COPC tiling or explicitly raise the guard."
        )
    dtype = _point_dtype(header)
    with header.path.open("rb") as handle:
        handle.seek(header.point_data_offset)
        remaining = header.point_count
        while remaining:
            count = min(chunk_points, remaining)
            records = np.fromfile(handle, dtype=dtype, count=count)
            if len(records) == 0:
                break
            scale = header.scale
            offset = header.offset
            kwargs = {
                "x": records["x"].astype(np.float64) * scale[0] + offset[0],
                "y": records["y"].astype(np.float64) * scale[1] + offset[1],
                "z": records["z"].astype(np.float64) * scale[2] + offset[2],
                # Low 5 bits are the class; the upper bits are synthetic /
                # key-point / withheld flags.
                "classification": records["classification"] & 0x1F,
            }
            if header.has_rgb:
                kwargs.update(
                    red=records["red"].copy(),
                    green=records["green"].copy(),
                    blue=records["blue"].copy(),
                )
            yield LASPointChunk(**kwargs)
            remaining -= len(records)


def classification_sample(path: str | Path, sample_size: int = 20_000) -> dict[int, int]:
    """Estimate the classification histogram by evenly-spaced seeking.

    Deliberately not a sequential read: it seeks to `sample_size` positions
    spread across the file, so it works on clouds far past the `max_points`
    guard and costs the same regardless of size. Even spacing rather than random
    sampling keeps the result deterministic.

    Useful mainly as a QA check for whether a cloud is classified at all — in
    this project's data the classifications are unassigned.
    """

    header = LASHeader.read(path)
    dtype = _point_dtype(header)
    sample_size = max(1, min(sample_size, header.point_count))
    indexes = np.linspace(0, header.point_count - 1, sample_size, dtype=np.int64)
    counts: dict[int, int] = {}
    with header.path.open("rb") as handle:
        for index in indexes:
            handle.seek(header.point_data_offset + int(index) * header.point_record_length)
            record = np.fromfile(handle, dtype=dtype, count=1)
            if not len(record):
                continue
            value = int(record["classification"][0] & 0x1F)
            counts[value] = counts.get(value, 0) + 1
    return counts


def copc_pipeline(
    source: str | Path,
    destination: str | Path,
    *,
    source_crs: str = "EPSG:32636",
) -> list[dict | str]:
    """Build the PDAL pipeline converting a LAS file to COPC.

    `override_srs` (not `a_srs`) on the reader is the important detail: these
    files embed no CRS, so the pipeline *asserts* one rather than reprojecting.
    EPSG:32636 (UTM 36N) was established externally by aligning to BBED —
    it is an empirical finding about this dataset, not a default worth changing
    casually.

    Returned as data rather than executed so `--dry-run` can print the exact
    pipeline for review before anything is written.
    """

    return [
        {
            "type": "readers.las",
            "filename": str(Path(source)),
            "override_srs": source_crs,
        },
        {
            "type": "writers.copc",
            "filename": str(Path(destination)),
            "a_srs": source_crs,
            "enhanced_srs_vlrs": True,
            "forward": "header,scale,offset",
            "pdal_metadata": True,
            "pipeline": True,
        },
    ]


def run_pdal_pipeline(pipeline: list[dict | str]) -> None:
    executable = shutil.which("pdal")
    if executable is None:
        raise RuntimeError("PDAL is not installed; create the conda environment first")
    completed = subprocess.run(
        [executable, "pipeline", "--stdin"],
        input=json.dumps(pipeline),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "PDAL pipeline failed")

