import gzip
import io
import math
import zlib

import numpy as np
import pytest

from dynacity.terrain import GroundModel
from dynacity.terrain_viewer import TILE_SIZE, TileCache, ViewerTerrain, encode_png_rgb


def hill() -> GroundModel:
    """UTM 36N ground, 10 m cells over a 2 km square near Beirut: 0.05 m per metre eastwards, sea (NaN) in the west."""

    x0, y0, n = 733000.0, 3752000.0, 200
    x = x0 + (np.arange(n) + 0.5) * 10.0
    ground = np.tile(0.05 * (x - x0), (n, 1))
    ground[:, :20] = np.nan
    return GroundModel(crs="EPSG:32636", x0=x0, y0=y0, cell_m=10.0, ground=ground, source="synthetic hill")


def decode_png(png: bytes) -> np.ndarray:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, data, size = 8, b"", None
    while pos < len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == int.from_bytes(png[pos + 8 + length:pos + 12 + length], "big")
        if kind == b"IHDR":
            size = int.from_bytes(body[:4], "big"), int.from_bytes(body[4:8], "big")
        if kind == b"IDAT":
            data += body
        pos += 12 + length
    w, h = size
    rows = np.frombuffer(zlib.decompress(data), dtype=np.uint8).reshape(h, 1 + 3 * w)
    assert (rows[:, 0] == 0).all()
    return rows[:, 1:].reshape(h, w, 3)


def lonlat(x: float, y: float) -> tuple[float, float]:
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:32636", "EPSG:4326", always_xy=True).transform(x, y)


def test_grid_matches_the_ground_model_it_was_cut_from():
    ground = hill()
    terrain = ViewerTerrain.from_ground(ground)
    assert terrain.cm.dtype == np.int16
    for x, y in [(734000.0, 3751000.0), (734500.0, 3751200.0), (733400.0, 3751800.0)]:
        lon, lat = lonlat(x, y)
        # 1 cm height rounding, plus the lon/lat grid's own bilinear error on a plane (none) and its 10 cm tolerance.
        assert terrain.sample(lon, lat) == pytest.approx(ground.sample([x], [y])[0], abs=0.05)


def test_sea_is_zero_and_the_edge_value_holds_beyond_the_grid():
    ground = hill()
    terrain = ViewerTerrain.from_ground(ground)
    assert terrain.sample(*lonlat(733050.0, 3751000.0)) == 0.0  # NaN cells: sea
    east = terrain.sample(*lonlat(ground.x0 + 1995.0, 3751000.0))
    assert terrain.sample(*lonlat(ground.x0 + 6000.0, 3751000.0)) == pytest.approx(east, abs=0.2)
    assert terrain.sample(terrain.west - 1.0, terrain.north + 1.0) == terrain.sample(terrain.west, terrain.north)


def test_the_page_decodes_the_same_numbers_from_the_gzipped_bytes():
    terrain = ViewerTerrain.from_ground(hill())
    meta = terrain.meta("/g.bin", "/t/{z}/{x}/{y}.png")
    assert meta["cols"] * meta["rows"] * 2 == len(gzip.decompress(terrain.to_bytes()))
    again = np.frombuffer(gzip.decompress(terrain.to_bytes()), dtype="<i2").reshape(meta["rows"], meta["cols"])
    assert (again == terrain.cm).all()
    assert meta["unit_m"] == 0.01 and meta["tiles"].endswith("{y}.png")
    west, south, east, north = meta["bounds"]
    assert west == meta["west"] and north == meta["north"] and south < north and east > west


def test_terrarium_tile_decodes_to_the_same_heights():
    ground = hill()
    terrain = ViewerTerrain.from_ground(ground)
    lon, lat = lonlat(734000.0, 3751000.0)
    z = 14
    n = 2 ** z
    x, y = int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    rgb = decode_png(terrain.terrarium_tile(z, x, y)).astype(float)
    assert rgb.shape == (TILE_SIZE, TILE_SIZE, 3)
    height = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
    # Pixel centres: compare with the grid sampled at the same lon/lat.
    px = (x + (np.arange(TILE_SIZE) + 0.5) / TILE_SIZE) / n * 360 - 180
    py = (y + (np.arange(TILE_SIZE) + 0.5) / TILE_SIZE) / n
    lat_px = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))
    expected = terrain.sample(*np.meshgrid(px, lat_px))
    assert np.abs(height - expected).max() < 0.005  # Terrarium's 1/256 m steps


def test_png_encoder_round_trips_and_tile_cache_returns_the_same_bytes():
    rgb = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    assert (decode_png(encode_png_rgb(rgb)) == rgb).all()
    cache = TileCache(ViewerTerrain.from_ground(hill()), size=2)
    first = cache.get(14, 9544, 6392)
    assert cache.get(14, 9544, 6392) is first
    cache.get(14, 1, 1), cache.get(14, 2, 2)
    assert cache.get(14, 9544, 6392) == first  # evicted, recomputed identically


def test_service_serves_the_grid_and_tiles_only_when_a_terrain_is_loaded():
    from fastapi.testclient import TestClient
    from dynacity.service import create_app
    from test_models_engine_api import engine_fixture

    terrain = ViewerTerrain.from_ground(hill())
    client = TestClient(create_app(engine_fixture(), viewer_terrain=terrain))
    grid = client.get("/v1/viewer/terrain.bin")
    assert grid.status_code == 200 and grid.headers["content-encoding"] == "gzip"
    assert np.frombuffer(grid.content, dtype='<i2').reshape(terrain.cm.shape).tolist() == terrain.cm.tolist()  # the client gunzips
    assert len(terrain.to_bytes()) < 2 * terrain.cm.size
    tile = client.get("/v1/viewer/terrain/14/9544/6392.png")
    assert tile.status_code == 200 and tile.headers["content-type"] == "image/png"
    assert client.get("/v1/viewer/terrain/3/9/0.png").status_code == 404  # x outside 2**z
    bare = TestClient(create_app(engine_fixture()))
    assert bare.get("/v1/viewer/terrain.bin").status_code == 404
