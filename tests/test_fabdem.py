import io
import zipfile

import numpy as np
import pytest

from dynacity import open_sources
from dynacity.fabdem import block_name, fabdem_ground_model, lattice_tiles, tile_name
from dynacity.terrain import GroundModel

rasterio = pytest.importorskip("rasterio")


def write_tile(path, lat0, lon0, values, nodata=-9999.0):
    """A 1 degree-style GeoTIFF with the given values on a lon/lat grid whose NW corner is (lon0, lat0)."""
    from rasterio.transform import from_origin
    rows, cols = values.shape
    step = 0.01
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(lon0, lat0, step, step), nodata=nodata) as dst:
        dst.write(values.astype("float32"), 1)


def lattice(crs="EPSG:32636"):
    # ~1 km square near Beirut on 100 m cells.
    return GroundModel(crs=crs, x0=733000.0, y0=3750000.0, cell_m=100.0, ground=np.zeros((10, 10)))


def test_tile_and_block_names():
    assert tile_name(33, 35) == "N33E035" and tile_name(-1, -5) == "S01W005"
    assert block_name(33, 35) == "N30E030-N40E040"
    assert block_name(0, 0) == "N00E000-N10E010"


def test_fabdem_on_the_lattice_is_not_filtered_and_keeps_the_sea_rule(tmp_path):
    from rasterio.warp import transform
    like = lattice()
    lon, lat = transform("EPSG:32636", "EPSG:4326", [733500.0], [3749500.0])
    lon0, lat0 = round(lon[0] - 0.1, 2), round(lat[0] + 0.1, 2)
    values = np.full((20, 20), 30.0)
    values[:, :9] = 0.0  # sea in the west part of the tile
    values[10, 12] = 55.0  # an isolated high cell must survive: no street-level filter on FABDEM
    write_tile(tmp_path / "t.tif", lat0, lon0, values)
    out = fabdem_ground_model(like, tmp_path / "t.tif")
    assert out.crs == like.crs and out.x0 == like.x0 and out.y0 == like.y0 and out.cell_m == like.cell_m
    assert out.ground.shape == like.ground.shape
    assert np.nanmax(out.ground) <= 55.0 and np.nanmin(out.ground) >= 0.1
    assert np.isclose(np.nanmedian(out.ground), 30.0, atol=1.0)
    assert "FABDEM" in out.source and "CC BY-NC-SA" in out.source
    out.save(tmp_path / "f.npz")
    assert GroundModel.load(tmp_path / "f.npz").ground.shape == like.ground.shape


def test_lattice_tiles_lists_each_degree_tile_once():
    assert lattice_tiles(lattice()) == [(33, 35)]


def test_range_file_reads_a_zip_member_without_the_whole_archive(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as z:
        z.writestr("a.tif", b"a" * 5000)
        z.writestr("b.tif", bytes(range(256)) * 40)
    data = buffer.getvalue()
    calls = []

    def fake_read_range(url, start, end, retries=4):
        calls.append((start, end))
        return data[start:end + 1]

    monkeypatch.setattr(open_sources, "read_range", fake_read_range)
    remote = open_sources.HttpRangeFile("http://x/y.zip", len(data), block_bytes=2000)
    with zipfile.ZipFile(remote) as z:
        assert z.namelist() == ["a.tif", "b.tif"]
        assert z.read("b.tif") == bytes(range(256)) * 40
    assert remote.bytes_fetched >= 10240 and calls


def test_download_ranges_reassembles_the_file_and_enforces_the_cap(monkeypatch, tmp_path):
    data = bytes(range(256)) * 100

    monkeypatch.setattr(open_sources, "head", lambda url: {"content-length": str(len(data)), "etag": '"e"'})
    monkeypatch.setattr(open_sources, "read_range", lambda url, s, e, retries=4: data[s:e + 1])
    entry = open_sources.download_ranges("http://x/f.bin", tmp_path / "raw" / "f.bin", max_bytes=100_000, workers=3, chunk_bytes=999)
    assert (tmp_path / "raw" / "f.bin").read_bytes() == data
    assert entry["bytes"] == len(data) and entry["server_etag"] == '"e"' and len(entry["sha256"]) == 64
    assert not (tmp_path / "raw" / "f.bin.part").exists()
    with pytest.raises(ValueError, match="cap"):
        open_sources.download_ranges("http://x/f.bin", tmp_path / "g.bin", max_bytes=100)


def test_record_source_merges_into_one_ledger(tmp_path):
    open_sources.record_source(tmp_path, "a", files=[], licence="L1", attribution="A1", publisher="P")
    open_sources.record_source(tmp_path, "b", files=[{"url": "u"}], licence="L2", attribution="A2", publisher="P")
    open_sources.record_source(tmp_path, "a", files=[], licence="L1b", attribution="A1", publisher="P")
    import json
    ledger = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    assert set(ledger["sources"]) == {"a", "b"} and ledger["sources"]["a"]["licence"] == "L1b"
