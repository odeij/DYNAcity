"""Bare-earth ground from FABDEM V1-2 (Forest And Buildings removed Copernicus DEM).

FABDEM (Hawker et al. 2022, University of Bristol) is Copernicus GLO-30 with a machine-learning
correction that removes building and tree heights. `fabdem_ground_model` resamples it onto the
lattice of an existing `GroundModel` (normally the served GLO-30 file), so the result is the
same `.npz` format over the same extent and `dynacity serve --terrain` reads it unchanged.

Unlike GLO-30, FABDEM is not passed through `terrain.ground_from_surface`: that filter lowers
roofs to street level, which FABDEM has already done in the source. Only the existing sea rule is
kept (cells at or below 0 m carry no ground, the same as the GLO-30 file) so the two files share
one coastline.

The licence is CC BY-NC-SA 4.0: non-commercial, share-alike. Heights are metres above EGM2008
and are a model estimate (vertical error a few metres, larger on steep or densely built slopes).
`rasterio` is needed here (`pip install -e '.[terrain]'`).
"""
from __future__ import annotations

import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .open_sources import HttpRangeFile, head, ledger_entry
from .terrain import SEA_LEVEL_M, GroundModel

FABDEM_ZIP_URL = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/{block}_FABDEM_V1-2.zip"
FABDEM_PAGE = "https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn"
FABDEM_DOI = "https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn"
FABDEM_LICENCE = "CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/); commercial use: fabdem@fathom.global"
ATTRIBUTION = (
    "Elevation: FABDEM V1-2 (Forest And Buildings removed Copernicus DEM), Hawker, L., Uhe, P., Paulo, L., "
    "Sosa, J., Savage, J., Sampson, C. and Neal, J., 2022, Environmental Research Letters 17(2), "
    "doi:10.1088/1748-9326/ac4d4f; data doi:10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn, University of Bristol, "
    "CC BY-NC-SA 4.0. Derived from Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and "
    "Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA."
)


def tile_name(lat: int, lon: int) -> str:
    """`N33E035` for the 1 degree tile whose south-west corner is (lat, lon)."""
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def block_name(lat: int, lon: int) -> str:
    """The 10 degree block archive holding that tile, e.g. `N30E030-N40E040`."""
    south, west = math.floor(lat / 10) * 10, math.floor(lon / 10) * 10
    return f"{tile_name(south, west)}-{tile_name(south + 10, west + 10)}"


def fetch_tile(lat: int, lon: int, raw_dir: Path, *, max_bytes: int = 200_000_000) -> dict:
    """Extract one 1 degree tile from the publisher's 10 degree zip with range requests; return its ledger entry.

    Only the zip's central directory and the tile's bytes are fetched (the whole archive is ~1.6 GB).
    The ledger entry hashes the extracted tile and records the archive's size, ETag and Last-Modified.
    """
    url = FABDEM_ZIP_URL.format(block=block_name(lat, lon))
    member = f"{tile_name(lat, lon)}_FABDEM_V1-2.tif"
    headers = head(url)
    remote = HttpRangeFile(url, int(headers["content-length"]))
    started = datetime.now(timezone.utc)
    with zipfile.ZipFile(remote) as archive:
        info = archive.getinfo(member)
        if info.file_size > max_bytes:
            raise ValueError(f"{member} is {info.file_size} bytes, over the {max_bytes} byte cap")
        raw_dir.mkdir(parents=True, exist_ok=True)
        destination = raw_dir / member
        with archive.open(member) as source, open(destination, "wb") as out:
            while block := source.read(1 << 20):
                out.write(block)
    return ledger_entry(
        url, destination, headers, started,
        zip_member=member, zip_bytes=int(headers["content-length"]), http_bytes_fetched=remote.bytes_fetched,
        note="tile extracted from the publisher's archive by HTTP range requests; the archive itself was not downloaded",
    )


def surface_to_lattice(like: GroundModel, tif_path: str | Path) -> np.ndarray:
    """Bilinear FABDEM heights at the cell centres of `like`; NaN at sea and off the tile."""
    import rasterio
    from rasterio.warp import transform

    rows, cols = like.ground.shape
    xs = like.x0 + (np.arange(cols) + 0.5) * like.cell_m
    ys = like.y0 - (np.arange(rows) + 0.5) * like.cell_m
    xx, yy = np.meshgrid(xs, ys)
    lon, lat = transform(like.crs, "EPSG:4326", xx.ravel().tolist(), yy.ravel().tolist())
    lon, lat = np.asarray(lon).reshape(rows, cols), np.asarray(lat).reshape(rows, cols)
    with rasterio.open(tif_path) as src:
        band = src.read(1).astype(np.float64)
        nodata = src.nodata
        t = src.transform
    invalid = ~np.isfinite(band) | (band <= SEA_LEVEL_M)
    if nodata is not None:
        invalid |= band == nodata
    land = np.where(invalid, np.nan, band)
    # Pixel-is-area grid: cell centres at +0.5 (the same convention terrain.fetch_ground_model uses).
    col = (lon - t.c) / t.a - 0.5
    row = (lat - t.f) / t.e - 0.5
    source = GroundModel(crs="source", x0=-0.5, y0=0.5, cell_m=1.0, ground=land)
    return source.sample(col, -row).reshape(rows, cols)


def fabdem_ground_model(like: GroundModel, tif_path: str | Path) -> GroundModel:
    """FABDEM on the lattice of `like` (same CRS, origin, cell size and shape), as a `GroundModel`."""
    ground = surface_to_lattice(like, tif_path)
    # Match the GLO-30 file's floor of 0.1 m above the sea on land cells.
    ground = np.where(np.isfinite(ground), np.maximum(ground, SEA_LEVEL_M + 0.1), np.nan)
    return GroundModel(crs=like.crs, x0=like.x0, y0=like.y0, cell_m=like.cell_m, ground=ground, source=ATTRIBUTION)


def lattice_tiles(like: GroundModel) -> list[tuple[int, int]]:
    """The 1 degree tiles (lat, lon of the south-west corner) that the lattice's cells fall in."""
    from rasterio.warp import transform

    rows, cols = like.ground.shape
    corners_x = [like.x0, like.x0 + cols * like.cell_m]
    corners_y = [like.y0, like.y0 - rows * like.cell_m]
    lon, lat = transform(like.crs, "EPSG:4326", [x for x in corners_x for _ in corners_y],
                         [y for _ in corners_x for y in corners_y])
    lats = range(math.floor(min(lat)), math.floor(max(lat)) + 1)
    lons = range(math.floor(min(lon)), math.floor(max(lon)) + 1)
    return [(la, lo) for la in lats for lo in lons]
