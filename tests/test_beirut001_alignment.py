import json

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import box, mapping
from shapely.strtree import STRtree

from dynacity import beirut001_alignment as A
from dynacity.beirut001_pack import Beirut001Pack, viewer_payload
from dynacity.terrain import GroundModel
from dynacity.viewer import utm_to_lonlat

ORIGIN = (731000.0, 3752000.0)
# How the model is really related to the reference: its placement is off by this much.
OFF_DEG, OFF_SHIFT = 0.4, (2.0, -3.0)


def _city(n=700, extent=3000.0, seed=5):
    rng = np.random.default_rng(seed)
    keep = []
    for x, y in rng.uniform(0, extent, size=(n, 2)):
        w, d = rng.uniform(10, 30, size=2)
        b = box(ORIGIN[0] + x, ORIGIN[1] + y, ORIGIN[0] + x + w, ORIGIN[1] + y + d)
        if all(not b.buffer(4).intersects(k) for k in keep):
            keep.append(b)
    return keep


def _misplace(geom):
    """The truth as the model currently draws it: turned about the city centre and shifted."""
    turned = affinity.rotate(geom, OFF_DEG, origin=(ORIGIN[0] + 1500, ORIGIN[1] + 1500))
    return affinity.translate(turned, *OFF_SHIFT)


@pytest.fixture(scope="module")
def city():
    return _city()


def test_rigid_correction_is_recovered_and_survives_rebuilt_buildings(city):
    reference = np.array(city, dtype=object)
    placed = np.array([_misplace(g) for g in city], dtype=object)
    # A tenth of the reference was rebuilt since the model: it must not drag the fit.
    rng = np.random.default_rng(1)
    for i in rng.choice(len(reference), len(reference) // 10, replace=False):
        reference[i] = affinity.translate(reference[i], *rng.uniform(-6, 6, 2))
    tree = STRtree(reference)
    correction, pairs = A.fit_icp(placed, reference, tree, "rigid")
    assert len(pairs) > 0.8 * len(city)
    moved = np.array([correction.transform_geometry(g) for g in placed], dtype=object)
    clean = [i for i in range(len(city)) if reference[i].equals(city[i])]
    error = np.linalg.norm(A.centroids(moved[clean]) - A.centroids(np.array(city, dtype=object)[clean]), axis=1)
    assert np.median(error) < 0.05 and error.max() < 0.3
    summary = correction.similarity_summary()
    assert summary["rotation_degrees"] == pytest.approx(-OFF_DEG, abs=0.01)
    assert summary["scale"] == pytest.approx(1.0, abs=1e-9)


def test_similarity_finds_a_real_scale_and_the_test_keeps_scale_one_otherwise(city):
    reference = np.array(city, dtype=object)
    placed = np.array([affinity.scale(_misplace(g), 1.004, 1.004, origin=(ORIGIN[0] + 1500, ORIGIN[1] + 1500)) for g in city], dtype=object)
    tree = STRtree(reference)
    _, pairs = A.fit_icp(placed, reference, tree, "similarity")
    boot = A.similarity_bootstrap(pairs, draws=60)
    assert boot["scale"]["one_inside_ci"] is False and boot["scale"]["value"] == pytest.approx(1 / 1.004, abs=2e-4)
    _, clean_pairs = A.fit_icp(np.array([_misplace(g) for g in city], dtype=object), reference, tree, "similarity")
    assert A.similarity_bootstrap(clean_pairs, draws=60)["scale"]["one_inside_ci"] is True


def test_spatial_folds_keep_whole_tiles_together_and_balanced():
    rng = np.random.default_rng(0)
    xy = rng.uniform(0, 4000, size=(5000, 2)) + ORIGIN
    fold = A.spatial_folds(xy, tile_m=500, folds=5)
    tiles = A._tile_key(xy, 500)
    for tile in np.unique(tiles):
        assert len(set(fold[tiles == tile])) == 1  # no tile is split between train and test
    counts = np.bincount(fold, minlength=5)
    assert counts.min() > 0.7 * counts.max()
    # Neighbouring points mostly share a fold, unlike a random split.
    near = np.linalg.norm(xy[:, None, :] - xy[None, :200, :], axis=2) < 40
    same = fold[:, None] == fold[None, :200]
    assert (same & near).sum() / near.sum() > 0.85


def test_held_out_blocks_improve_and_a_richer_model_is_not_adopted_without_evidence(city):
    reference = np.array(city, dtype=object)
    placed = np.array([_misplace(g) for g in city], dtype=object)
    tree = STRtree(reference)
    cv = A.cross_validate(placed, reference, tree, models=("shift", "rigid", "similarity", "affine", "quadratic"))
    assert cv["current"]["median_m"] > 3 and cv["rigid"]["median_m"] < 0.1
    assert cv["folds"]["count"] == 5 and sum(cv["folds"]["buildings_per_fold"]) == len(city)
    boot = {"scale": {"one_inside_ci": True}, "rotation_degrees": {"zero_inside_ci": False}}
    assert A.choose_model(cv, boot)[0] == "rigid"
    # A fabricated clear win on every fold is adopted; a one-fold win is not.
    fake = json_copy(cv)
    fake["affine"].update(median_m=fake["rigid"]["median_m"] - 1, fold_medians_m=[v - 1 for v in fake["rigid"]["fold_medians_m"]])
    fake["rigid"]["median_m"] = 3.0
    fake["affine"]["median_m"] = 2.0
    assert A.choose_model(fake, boot)[0] == "affine"
    fake["affine"]["fold_medians_m"] = [v - 1 if i == 0 else v + 1 for i, v in enumerate(fake["rigid"]["fold_medians_m"])]
    assert A.choose_model(fake, boot)[0] == "rigid"


def json_copy(x):
    return json.loads(json.dumps(x))


def test_correction_inverse_and_json_round_trip():
    c = A.fit_polynomial(np.array([[0., 0], [100, 0], [0, 100], [100, 100], [50, 20]]) + ORIGIN,
                         np.array([[1., -2], [101, -2], [1, 98], [101, 98], [51, -1.5]]) + ORIGIN, 2)
    p = np.array([[10., 20.], [90, 40]]) + ORIGIN
    np.testing.assert_allclose(c.inverse(c(p)), p, atol=1e-6)
    again = A.Correction.from_json(json.loads(json.dumps(c.to_json())))
    np.testing.assert_allclose(again(p), c(p))


def test_reference_is_projected_from_lonlat_and_non_polygons_dropped(tmp_path, city):
    def lonlat(geom):
        ring = [list(utm_to_lonlat(x, y, 36, True)) for x, y in geom.exterior.coords]
        return {"type": "Polygon", "coordinates": [ring]}

    features = [{"type": "Feature", "geometry": lonlat(g), "properties": {"sources": [{"dataset": "OpenStreetMap"}]}}
                for g in city[:5]]
    features.append({"type": "Feature", "geometry": None, "properties": {}})
    path = tmp_path / "ref.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    shapes, datasets = A.load_reference(path)
    assert len(shapes) == 5 and datasets == ["OpenStreetMap"] * 5
    assert shapes[0].symmetric_difference(city[0]).area < 1.0  # sub-metre round trip


def _synthetic_pack(city, with_ground=True):
    placed = [_misplace(g) for g in city]
    n = len(placed)
    ground = GroundModel("EPSG:32636", ORIGIN[0] - 100, ORIGIN[1] + 3200, 10.0, np.full((340, 330), 12.0))
    ground.ground[:, :20] = np.nan  # a sea edge
    bridge = _misplace(box(ORIGIN[0] + 500, ORIGIN[1] + 500, ORIGIN[0] + 540, ORIGIN[1] + 520))
    triangles = np.array([[[ORIGIN[0] + 500, ORIGIN[1] + 500, 15.], [ORIGIN[0] + 540, ORIGIN[1] + 500, 15.],
                           [ORIGIN[0] + 540, ORIGIN[1] + 520, 16.]]])
    meta = {"schema": "dynacity-beirut001-pack/1", "crs": "EPSG:32636",
            "origin_xy": [float(v) for v in ORIGIN], "source": {"name": "x"}, "attribution": "Beirut 001",
            "registration": {"method": "rigid centroid-pattern registration, inferred not surveyed", "rotation_degrees": 1.34,
                             "rotation_matrix": [[1, 0], [0, 1]], "translation_xy_m": [0., 0.],
                             "bbed_to_model_percentiles_m": [1, 2, 3, 4, 5]},
            "matching": "Provisional links.", "vertical": "v",
            "counts": {"buildings": n, "linked": 0, "unlinked": n, "bridges": 1}}
    return Beirut001Pack(
        meta=meta, building_ids=np.array([f"b{i}" for i in range(n)]), buildings=placed,
        base_z=np.full(n, 5.), top_z=np.full(n, 30.), ground_z=np.full(n, 12.), roof_levels=np.ones(n, np.uint8),
        bbed_objectid=np.full(n, -1, np.int64), link_iou=np.zeros(n), link_distance_m=np.full(n, np.nan),
        bridges=[bridge], bridge_z_min=np.array([14.]), bridge_z_max=np.array([16.]),
        ground=ground if with_ground else None, bridge_triangles=triangles)


def _bbed_file(tmp_path, city):
    features = [{"type": "Feature", "geometry": mapping(g), "properties": {"OBJECTID": 1000 + i}} for i, g in enumerate(city)]
    path = tmp_path / "bbed.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features,
                                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32636"}}}))
    return path


def test_apply_moves_everything_keeps_heights_and_rematches_links(tmp_path, city):
    pack = _synthetic_pack(city)
    bbed = _bbed_file(tmp_path, city)
    reference = np.array(city, dtype=object)
    correction, _ = A.fit_icp(np.array(pack.buildings, dtype=object), reference, STRtree(reference), "rigid")
    refined = A.apply_correction(pack, correction, bbed)
    # Outlines land on the truth, so links appear where there were none (the 2 m / 0.4 deg error broke them).
    assert refined.buildings[3].symmetric_difference(city[3]).area < 2.0
    assert refined.meta["counts"]["linked"] > pack.meta["counts"]["linked"] + 0.8 * len(city)
    assert refined.meta["counts"]["linked_before_refinement"] == 0
    assert (refined.top_z == pack.top_z).all() and (refined.ground_z == pack.ground_z).all()
    assert refined.bridge_triangles[..., 2].tolist() == pack.bridge_triangles[..., 2].tolist()
    assert not np.allclose(refined.bridge_triangles[..., :2], pack.bridge_triangles[..., :2])
    # The grid is re-gridded, not shifted blindly: sea stays sea, land keeps its level.
    assert np.isnan(refined.ground.sample([ORIGIN[0] - 80], [ORIGIN[1] + 1000])[0])
    assert refined.ground.sample([ORIGIN[0] + 1500], [ORIGIN[1] + 1500])[0] == pytest.approx(12.0)
    reg = refined.meta["registration"]
    assert reg["initial"]["rotation_degrees"] == 1.34 and reg["rotation_degrees"] == pytest.approx(correction.similarity_summary()["rotation_degrees"], abs=1e-6)
    assert refined.meta["alignment"]["bbed_links"] == refined.meta["counts"]["linked"]
    # Round trip through a file, and the viewer sees the alignment block.
    path = tmp_path / "refined.npz"
    refined.save(path)
    again = Beirut001Pack.load(path)
    assert again.meta["alignment"]["bbed_links"] == refined.meta["counts"]["linked"]
    assert viewer_payload(again)["alignment"]["correction"]["kind"] == "rigid"
    assert viewer_payload(pack)["alignment"] is None
    # The original pack object is untouched.
    assert pack.meta["counts"]["linked"] == 0 and "alignment" not in pack.meta


def test_refine_pipeline_writes_outputs_never_overwrites_and_reports(tmp_path, city):
    pack = _synthetic_pack(city)
    bbed = _bbed_file(tmp_path, city)
    pack_path = tmp_path / "pack.npz"
    pack.save(pack_path)
    ref_path = tmp_path / "ref.geojson"

    def lonlat(geom):
        return {"type": "Polygon", "coordinates": [[list(utm_to_lonlat(x, y, 36, True)) for x, y in geom.exterior.coords]]}

    ref_path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": lonlat(g), "properties": {"sources": [{"dataset": "OpenStreetMap"}]}} for g in city]}))
    with pytest.raises(ValueError, match="overwrite"):
        A.refine_beirut001(pack_path, ref_path, pack_path, tmp_path / "r.json")
    original = pack_path.read_bytes()
    out, report_path = tmp_path / "refined.npz", tmp_path / "report.json"
    report = A.refine_beirut001(pack_path, ref_path, out, report_path, bbed_path=bbed, terrain_path=None)
    assert pack_path.read_bytes() == original and out.exists() and report_path.exists()
    assert report["selected_model"] in ("shift", "rigid", "similarity")
    cv = report["cross_validation"]
    assert cv["rigid"]["median_m"] < cv["current"]["median_m"] / 10
    assert report["bbed_links"]["after"] > report["bbed_links"]["before"]
    assert json.loads(report_path.read_text())["reference"]["datasets"] == {"OpenStreetMap": len(city)}
    with pytest.raises(ValueError, match="already refined"):
        A.refine_beirut001(out, ref_path, tmp_path / "again.npz", tmp_path / "again.json")


def test_vertical_check_reports_a_known_offset_and_viewer_wording_comes_from_metadata():
    base = GroundModel("EPSG:32636", 0., 1000., 10., np.tile(np.linspace(0, 50, 100), (100, 1)))
    shifted = GroundModel("EPSG:32636", 0., 1000., 10., base.ground + 0.4)
    check = A.vertical_check(shifted, base)
    assert check["median_diff_m"] == pytest.approx(0.4, abs=1e-6) and check["cells"] == 10000
    html = open(__import__("dynacity").__path__[0] + "/viewer_template.html", encoding="utf-8").read()
    assert "beirutAlignmentText(data.alignment)" in html
    assert "held-out median error" in html and "inferred map alignment" in html  # new wording, old fallback
