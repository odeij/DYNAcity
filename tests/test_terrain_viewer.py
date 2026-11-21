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


def test_voids_and_low_shores_are_filled_but_open_sea_and_sea_cliffs_stay_at_zero():
    # The shore (x = 733200) is lowered to 0.25 m, so sea within COAST_FILL_M of it takes about that.
    ground = hill()
    ground.ground[:, 20:] -= 10.0
    # A cliff: a 30 m high strip along the north, with a gap (an inland void) in it.
    ground.ground[:30, 20:] = 30.0
    ground.ground[10:13, 150:153] = np.nan
    # A void inland, away from the sea.
    ground.ground[100:103, 100:103] = np.nan
    terrain = ViewerTerrain.from_ground(ground)
    shore = terrain.sample(*lonlat(733205.0, 3751000.0))
    assert terrain.sample(*lonlat(733170.0, 3751000.0)) == pytest.approx(shore, abs=0.3)  # 35 m out: filled
    assert terrain.sample(*lonlat(733050.0, 3751000.0)) == 0.0  # 150 m out: sea
    assert terrain.sample(*lonlat(733170.0, 3751850.0)) == 0.0  # beside the 30 m cliff: sea, not a plateau
    assert terrain.sample(*lonlat(734015.0, 3750985.0)) == pytest.approx(0.05 * 1015 - 10.0, abs=1.0)  # void filled
    assert terrain.sample(*lonlat(734515.0, 3751885.0)) == pytest.approx(30.0, abs=0.5)  # void in the cliff top too


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


def test_relief_backdrop_takes_over_beyond_the_city_grid_and_feathers_at_its_edge(tmp_path):
    from dataclasses import replace

    from dynacity.terrain_viewer import RELIEF_FEATHER_DEG, ReliefGrid

    # A 6 km city grid, so its centre lies well inside the feather band.
    x = 733000.0 + (np.arange(600) + 0.5) * 10.0
    city = GroundModel(crs="EPSG:32636", x0=733000.0, y0=3755000.0, cell_m=10.0,
                       ground=np.tile(0.05 * (x - 733000.0), (600, 1)), source="synthetic hill")
    terrain = ViewerTerrain.from_ground(city)
    west, south, east, north = terrain.meta("", "")["bounds"]
    # A flat 1500 m plateau over a much wider box, saved and loaded the way serve --relief reads it.
    relief = ReliefGrid(west=west - 1.0, north=north + 1.0, cell_deg=1 / 3600, source="synthetic plateau",
                        dm=np.full((int(2.2 * 3600), int(2.2 * 3600)), 15000, dtype=np.int16))
    relief.save(tmp_path / "relief.npz")
    relief = ReliefGrid.load(tmp_path / "relief.npz")
    assert relief.sample(west - 0.5, north + 0.5) == pytest.approx(1500.0)

    both = replace(terrain, backdrop=relief)
    centre = ((west + east) / 2, (south + north) / 2)
    # Deep inside the city grid nothing changes; far beyond it the country relief shows; halfway into the band it is a mix.
    assert both.relief(*centre) == pytest.approx(terrain.sample(*centre))
    assert both.relief(west - 0.3, centre[1]) == pytest.approx(1500.0)
    edge = both.relief(east - RELIEF_FEATHER_DEG / 2, centre[1])
    assert edge == pytest.approx(0.5 * terrain.sample(east - RELIEF_FEATHER_DEG / 2, centre[1]) + 750.0, abs=0.5)
    # The page's own sampler never sees the backdrop: the city grid's edge value holds out there.
    assert both.sample(west - 0.3, centre[1]) == terrain.sample(west - 0.3, centre[1])
    # And the Terrarium tiles carry it.
    z = 9
    n = 2 ** z
    x = int((west - 0.3 + 180.0) / 360.0 * n)
    lat_rad = math.radians(centre[1])
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    rgb = decode_png(both.terrarium_tile(z, x, y)).astype(np.float64)
    heights = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
    assert heights.max() == pytest.approx(1500.0, abs=0.1)


def test_relief_fades_into_a_smoothed_edge_beyond_its_grid():
    from dynacity.terrain_viewer import RELIEF_OUTER_FADE_DEG, ReliefGrid

    # Alternating 0 / 1000 m columns: clamped, the east edge would streak outwards forever.
    dm = np.zeros((600, 600), dtype=np.int16)
    dm[:, ::2] = 10000
    relief = ReliefGrid(west=35.0, north=34.0, cell_deg=1 / 3600, dm=dm)
    west, south, east, north = relief.bounds
    lat = np.linspace(south + 0.02, north - 0.02, 50)
    at_edge = relief.sample(np.full(50, east), lat)
    beyond = relief.sample(np.full(50, east + 2 * RELIEF_OUTER_FADE_DEG), lat)
    assert at_edge.std() < 1.0  # one column: constant along it
    assert beyond == pytest.approx(500.0, abs=5.0)  # the mean, not one column's value
    # Inside the grid it is exact.
    assert relief.sample(35.0, 34.0) == pytest.approx(1000.0)
    # Halfway through the fade it is a mix of the two.
    mid = relief.sample(east + RELIEF_OUTER_FADE_DEG / 2, (south + north) / 2)
    assert mid == pytest.approx(0.5 * at_edge[25] + 0.5 * 500.0, abs=5.0)
