from __future__ import annotations

import struct
from pathlib import Path

import pytest


def bbed_feature(
    object_id: int,
    *,
    status_2018: str = "Under Construction",
    status_2022: str = "Complete Residential",
    status_2024: str = "Complete Building",
    x: float = 0.0,
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "OBJECTID": object_id,
            "BULBuildingID": object_id,
            "ParcelID": f"P-{object_id}",
            "Sector": "Test Sector",
            "Building_Use": "Residential",
            "NoofFloor": 4 + object_id % 3,
            "Building_Hight_m": 15 + object_id % 5,
            "YearCompleted": 2021,
            "PermitYear": 2016,
            "Status2018": status_2018,
            "Status2022": status_2022,
            "F5__Current_status": status_2024,
            "Shape__Area": 100.0,
            "Shape__Length": 40.0,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[x, 0.0], [x + 10.0, 0.0], [x + 10.0, 10.0], [x, 10.0], [x, 0.0]]
            ],
        },
    }


@pytest.fixture
def bbed_collection() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [bbed_feature(1), bbed_feature(2, x=20.0)],
    }


def write_las(path: Path, points: list[tuple[int, int, int, int, int, int, int]]) -> Path:
    header = bytearray(227)
    header[:4] = b"LASF"
    header[24] = 1
    header[25] = 2
    header[58:67] = b"pytest\x00\x00\x00"
    struct.pack_into("<H", header, 90, 201)
    struct.pack_into("<H", header, 92, 2026)
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<I", header, 96, 227)
    struct.pack_into("<I", header, 100, 0)
    header[104] = 2
    struct.pack_into("<H", header, 105, 26)
    struct.pack_into("<I", header, 107, len(points))
    struct.pack_into("<ddd", header, 131, 0.01, 0.01, 0.01)
    struct.pack_into("<ddd", header, 155, 0.0, 0.0, 0.0)
    xs = [point[0] * 0.01 for point in points]
    ys = [point[1] * 0.01 for point in points]
    zs = [point[2] * 0.01 for point in points]
    struct.pack_into("<d", header, 179, max(xs))
    struct.pack_into("<d", header, 187, min(xs))
    struct.pack_into("<d", header, 195, max(ys))
    struct.pack_into("<d", header, 203, min(ys))
    struct.pack_into("<d", header, 211, max(zs))
    struct.pack_into("<d", header, 219, min(zs))
    with path.open("wb") as handle:
        handle.write(header)
        for x, y, z, classification, red, green, blue in points:
            handle.write(
                struct.pack(
                    "<iiiHBBbBH3H",
                    x,
                    y,
                    z,
                    0,
                    1,
                    classification,
                    0,
                    0,
                    0,
                    red,
                    green,
                    blue,
                )
            )
    return path

