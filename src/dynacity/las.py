"""Small, dependency-light LAS 1.2 reader and explicit PDAL pipeline builder."""

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
        source = Path(path)
        with source.open("rb") as handle:
            header = handle.read(375)
        if len(header) < 227 or header[:4] != b"LASF":
            raise ValueError(f"not a supported LAS file: {source}")
        major, minor = header[24], header[25]
        version = f"{major}.{minor}"
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
            point_format=header[104] & 0x3F,
            point_record_length=struct.unpack_from("<H", header, 105)[0],
            point_count=int(point_count),
            scale=struct.unpack_from("<ddd", header, 131),
            offset=struct.unpack_from("<ddd", header, 155),
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

