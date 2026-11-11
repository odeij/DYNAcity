import json

import numpy as np
from PIL import Image

from dynacity import photo_facade_library as lib


def make_scene(path, wall=(214, 196, 160), size=(640, 480)):
    """Sky on top, a cream wall with dark windows on the flank, road at the bottom."""
    width, height = size
    image = np.zeros((height, width, 3), np.uint8)
    image[:] = (105, 110, 112)
    image[: int(height * 0.18)] = (120, 170, 235)
    image[int(height * 0.12): int(height * 0.7), : int(width * 0.55)] = wall
    for y in range(int(height * 0.18), int(height * 0.6), 60):
        for x in range(30, int(width * 0.5), 70):
            image[y:y + 30, x:x + 36] = (40, 48, 56)
    rng = np.random.default_rng(1)
    image = np.clip(image + rng.normal(0, 4, image.shape), 0, 255).astype(np.uint8)
    Image.fromarray(image).save(path, quality=92)


def record_for(path, photo_id="900001"):
    return {"id": photo_id, "file": path.name, "path": str(path), "sha256": lib.sha256_file(path),
            "bytes": path.stat().st_size, "capture_date": "2018-07-18 10:00:00.000"}


def test_analyse_photo_reports_checksum_and_clean_tiles(tmp_path):
    path = tmp_path / "a.jpg"
    make_scene(path)
    report = lib.analyse_photo(record_for(path))
    assert report["exists"] and report["decode_ok"] and report["checksum_ok"]
    assert report["clean_tiles"] > 0
    assert any(t["family"] == "cream_limestone_balcony_midrise" for t in report["_tiles"])
    # sky rows and the road region must never become wall tiles
    height = report["analysis_size_px"][1]
    assert all(t["ty"] + lib.TILE <= height * lib.ROW_RANGE[1] + lib.TILE for t in report["_tiles"])


def test_checksum_mismatch_and_missing_and_corrupt_are_rejected(tmp_path):
    path = tmp_path / "a.jpg"
    make_scene(path)
    wrong = {**record_for(path), "sha256": "0" * 64}
    report = lib.analyse_photo(wrong)
    assert not report["usable"] and "checksum_mismatch" in report["reject_reasons"]
    missing = lib.analyse_photo({**record_for(path), "path": str(tmp_path / "gone.jpg"), "file": "gone.jpg"})
    assert missing["reject_reasons"] == ["missing_file"]
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a jpeg")
    corrupt = lib.analyse_photo(record_for(bad, "900002"))
    assert not corrupt["decode_ok"] and "decode_failed" in corrupt["reject_reasons"]


def test_sky_only_photo_has_no_wall_tiles(tmp_path):
    path = tmp_path / "sky.jpg"
    Image.fromarray(np.full((480, 640, 3), (120, 170, 235), np.uint8)).save(path)
    report = lib.analyse_photo(record_for(path))
    assert report["clean_tiles"] == 0 and not report["usable"]


def test_classify_tile_rules():
    assert lib.classify_tile([0.82, 0.74, 0.60]) == "cream_limestone_balcony_midrise"
    assert lib.classify_tile([0.45, 0.45, 0.46]) == "weathered_grey_concrete_frame"
    assert lib.classify_tile([0.85, 0.85, 0.85]) == "white_panel_commercial"
    assert lib.classify_tile([0.20, 0.32, 0.45]) == "blue_grey_glazed_tower"
    assert lib.classify_tile([0.45, 0.20, 0.15]) == "reddish_brown_framed_residential"
    assert lib.classify_tile([0.80, 0.55, 0.30]) == "ochre_terracotta_masonry_lowrise"
    assert lib.classify_tile([0.2, 0.7, 0.2]) is None


def test_conservative_albedo_limits_brightness_and_saturation():
    bright = lib.conservative_albedo([1.0, 0.95, 0.8])
    assert max(bright) <= 0.74 + 1e-6
    dark = lib.conservative_albedo([0.02, 0.02, 0.02])
    assert min(dark) >= 0.34 - 1e-6


def test_synthesised_maps_are_tileable_and_deterministic():
    family = lib.FAMILIES[0]
    params = lib.material_parameters(family, {"apparent_median_rgb": [0.8, 0.74, 0.6],
                                              "texture_luma_std_median": 0.04})
    a = lib.synthesise_maps(params, size=128, seed="x")
    b = lib.synthesise_maps(params, size=128, seed="x")
    assert np.array_equal(a["albedo"], b["albedo"])
    for key in ("albedo", "roughness", "normal"):
        assert a[key].shape[:2] == (128, 128) and a[key].dtype == np.uint8
    # seams: wrap-around difference is not larger than an interior neighbour difference band
    albedo = a["albedo"].astype(float)
    seam = np.abs(albedo[:, 0] - albedo[:, -1]).mean()
    interior = np.abs(albedo[:, 1:] - albedo[:, :-1]).mean()
    assert seam < interior * 4 + 5
    assert a["tile_size_m"] == [round(4 * params["bay_width_m"], 2), round(4 * params["floor_height_m"], 2)]


def test_height_class_boundaries():
    assert [lib.height_class(h) for h in (5, 12, 27.9, 28, 59, 60, 120)] == [
        "low", "mid", "mid", "high", "high", "tower", "tower"]


def fake_inventory(tmp_path, count=60):
    index = {"coordinates": {"origin_utm_m": [730000.0, 3750000.0, 0]}, "objects": []}
    records = []
    for i in range(count):
        sid = f"beirut001:{i:08d}"
        index["objects"].append({"source_id": sid, "local_bounds_m": [[i * 30, 0, 0], [i * 30 + 20, 20, 10]]})
        records.append({
            "source_id": sid, "district": "Test", "owner_tile": "0:0", "source_index": str(tmp_path / "idx.json"),
            "facade": {"status": "estimated", "template": "grey_concrete"},
            "source_metadata": {"height_m": 6.0 + 2.0 * i, "production_group": "Test", "kind": "building"},
            "street_candidates": []})
    (tmp_path / "idx.json").write_text(json.dumps(index))
    return records


def jobs_for(tmp_path, records, excluded=None):
    geometry = lib.load_building_geometry(records)
    assets = {f["id"]: {"reference_paths": [f"{f['id']}/albedo.png"]} for f in lib.FAMILIES}
    return lib.build_jobs(records, [], geometry, {}, {}, assets, excluded or {}, set(), set(), set())


def test_jobs_keep_every_source_id_and_are_deterministic(tmp_path):
    records = fake_inventory(tmp_path)
    jobs = jobs_for(tmp_path, records)
    again = jobs_for(tmp_path, records)
    assert [j["source_id"] for j in jobs] == sorted(r["source_id"] for r in records)
    assert jobs == again
    assert {j["evidence_class"] for j in jobs} <= {"confirmed_historical", "reference_informed_estimate", "unchanged"}
    families = {j["proposed_family"] for j in jobs if j["proposed_family"]}
    assert len(families) >= 3  # variety, not one family everywhere
    for job in jobs:
        if job["evidence_class"] == "reference_informed_estimate":
            assert job["proposed_family"] in lib.FAMILY_IDS and job["reference_paths"]
            assert job["family_basis"] == "typology_prior_only"
            assert job["variant"]["floors"] >= 1


def test_exclusions_and_small_buildings_stay_unchanged(tmp_path):
    records = fake_inventory(tmp_path, 10)
    records[0]["source_metadata"]["height_m"] = 2.0
    lgb = records[1]["source_id"]
    jobs = {j["source_id"]: j for j in jobs_for(
        tmp_path, records, {lgb: ("confirmed_historical", "lgb_photo_authored_preserved")})}
    assert jobs[records[0]["source_id"]]["unchanged_reason"] == "too_small_for_facade_treatment"
    assert jobs[lgb]["evidence_class"] == "confirmed_historical" and jobs[lgb]["proposed_family"] is None


def test_observed_historical_record_is_never_overwritten(tmp_path):
    records = fake_inventory(tmp_path, 5)
    records[2]["facade"] = {"status": "observed_historical_visible_portion", "template": "reference_review_required"}
    jobs = {j["source_id"]: j for j in jobs_for(tmp_path, records)}
    assert jobs[records[2]["source_id"]]["evidence_class"] == "confirmed_historical"


def test_candidate_photos_are_not_identity_and_rejected_pairs_dropped(tmp_path):
    records = fake_inventory(tmp_path, 3)
    records[0]["street_candidates"] = [{"photo_id": "p1"}, {"photo_id": "p2"}, {"photo_id": "bad"}]
    geometry = lib.load_building_geometry(records)
    assets = {f["id"]: {"reference_paths": []} for f in lib.FAMILIES}
    jobs = lib.build_jobs(records, [], geometry, {}, {}, assets, {}, {(records[0]["source_id"], "p2")}, {"bad"}, set())
    job = next(j for j in jobs if j["source_id"] == records[0]["source_id"])
    assert job["candidate_photo_ids_unconfirmed"] == ["p1"]


def test_nearby_photo_evidence_shifts_family_odds(tmp_path):
    records = fake_inventory(tmp_path, 400)
    for r in records:
        r["source_metadata"]["height_m"] = 20.0
    geometry = lib.load_building_geometry(records)
    assets = {f["id"]: {"reference_paths": []} for f in lib.FAMILIES}
    centre = geometry[records[0]["source_id"]]["centre_utm_m"]
    shares = {"p": {"ochre_terracotta_masonry_lowrise": 1.0}}
    near = lib.build_jobs(records[:1], [], geometry, shares, {"p": centre}, assets, {}, set(), set(), set())
    assert near[0]["family_basis"] == "typology_plus_nearby_photos"
    base = lib.build_jobs(records, [], geometry, {}, {}, assets, {}, set(), set(), set())
    boosted = lib.build_jobs(records, [], geometry, shares, {"p": centre}, assets, {}, set(), set(), set())
    count = lambda jobs: sum(j["proposed_family"] == "ochre_terracotta_masonry_lowrise" for j in jobs)
    assert count(boosted) >= count(base)


def test_stable_unit_is_stable_and_in_range():
    values = [lib.stable_unit("a", i) for i in range(50)]
    assert values == [lib.stable_unit("a", i) for i in range(50)]
    assert all(0 <= v < 1 for v in values) and len(set(values)) == 50


def noisy(array, seed=3, sigma=5):
    rng = np.random.default_rng(seed)
    return np.clip(array + rng.normal(0, sigma, array.shape), 0, 255).astype(np.uint8)


def test_overcast_white_sky_and_street_lamp_produce_no_wall_tiles(tmp_path):
    """Regression: overcast sky and a lamp were once accepted as 'white panel' wall."""
    image = np.zeros((480, 640, 3), np.float32)
    image[:] = (105, 110, 112)  # road
    sky = np.zeros((300, 640, 3), np.float32)
    sky[:] = (196, 200, 204)  # pale grey overcast, below the haze rule
    yy = np.linspace(0, 1, 300)[:, None, None]
    sky = sky + 12 * np.sin(yy * 9) + 6  # soft cloud banding
    image[:300] = sky
    image[200:290, 300:306] = (60, 60, 60)  # lamp post
    path = tmp_path / "overcast.jpg"
    Image.fromarray(noisy(image, sigma=4)).save(path, quality=92)
    report = lib.analyse_photo(record_for(path, "900010"))
    assert report["clean_tiles"] == 0, report["clean_tiles"]
    assert not report["usable"]


def test_tunnel_underside_is_not_ochre_wall_when_curation_rejects_it(tmp_path):
    path = tmp_path / "tunnel.jpg"
    make_scene(path)
    report = lib.analyse_photo(record_for(path, "900011"))
    curated = {"accepted": [{"family": "ochre_terracotta_masonry_lowrise", "photo_id": "900011", "ty": 48, "tx": 48}],
               "rejected_photos": {"900011": "underpass underside"}}
    try:
        lib.validate_curated(curated, {"900011": report}, {})
    except ValueError as error:
        assert "photo rejected" in str(error)
    else:
        raise AssertionError("rejected photo must not provide accepted patches")


def test_curated_validation_catches_bad_patches(tmp_path):
    path = tmp_path / "a.jpg"
    make_scene(path)
    report = lib.analyse_photo(record_for(path, "900012"))
    by_id = {"900012": report}
    good = {"accepted": [{"family": "cream_limestone_balcony_midrise", "photo_id": "900012", "ty": 48, "tx": 48}]}
    assert lib.validate_curated(good, by_id, {}) is good
    bad_cases = [
        ({"family": "nope", "photo_id": "900012", "ty": 48, "tx": 48}, "unknown family"),
        ({"family": "white_panel_commercial", "photo_id": "missing", "ty": 48, "tx": 48}, "missing"),
        ({"family": "white_panel_commercial", "photo_id": "900012", "ty": 4000, "tx": 48}, "outside"),
        ({"family": "white_panel_commercial", "photo_id": "900012", "ty": 48, "tx": 5000}, "outside"),
    ]
    for entry, text in bad_cases:
        try:
            lib.validate_curated({"accepted": [entry]}, by_id, {})
        except ValueError as error:
            assert text in str(error)
        else:
            raise AssertionError(entry)
    rejected_patch = {"accepted": good["accepted"], "rejected_patches": [{"photo_id": "900012", "ty": 48, "tx": 48}]}
    for payload, review in ((rejected_patch, {}), (good, {"900012": {"status": "rejected_non_facade"}})):
        try:
            lib.validate_curated(payload, by_id, review)
        except ValueError:
            continue
        raise AssertionError("expected rejection")


def test_curated_tiles_come_from_pixels_not_algorithmic_classes(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    make_scene(path)
    report = lib.analyse_photo(record_for(path, "900013"))
    report["road_neutral_gains"] = report["road_neutral_gains"]
    curated = {"accepted": [{"family": "cream_limestone_balcony_midrise", "photo_id": "900013", "ty": 48, "tx": 48,
                             "note": "x"}]}
    groups = lib.curated_family_tiles(curated, {"900013": report})
    tiles = groups["cream_limestone_balcony_midrise"]["tiles"]
    assert len(tiles) == lib.BLOCK ** 2 and groups["white_panel_commercial"]["tiles"] == []
    assert all(t["photo_id"] == "900013" for t in tiles)


def test_candidate_quality_and_support_are_recorded_without_identity(tmp_path):
    records = fake_inventory(tmp_path, 3)
    geometry = lib.load_building_geometry(records)
    assets = {f["id"]: {"reference_paths": []} for f in lib.FAMILIES}
    sid = records[0]["source_id"]
    cands = {sid: [{"photo_id": "p1", "rank_cost": 5.0, "wall_sample": {"distance_m": 30.0, "heading_offset_deg": 4.0}},
                   {"photo_id": "p1", "rank_cost": 20.0},
                   {"photo_id": "p2", "rank_cost": 12.0}]}
    shares = {"p1": {"ochre_terracotta_masonry_lowrise": 1.0}}
    jobs = lib.build_jobs(records, [], geometry, shares, {}, assets, {}, set(), set(), set(), candidate_map=cands)
    job = next(j for j in jobs if j["source_id"] == sid)
    assert job["candidate_photo_ids_unconfirmed"] == ["p1", "p2"]  # duplicates collapsed to the best pair
    support = {c["photo_id"]: c for c in job["candidate_support"]}
    assert support["p1"]["used_for_style"] and not support["p2"]["used_for_style"]
    assert support["p1"]["rank_cost"] == 5.0 and job["candidate_status"] == "geometric_candidate_unverified"
    assert job["evidence_class"] == "reference_informed_estimate"
    assert lib.candidate_quality({"rank_cost": 0}) == 1.0 and lib.candidate_quality({"rank_cost": 99}) == 0.2


def test_grey_catch_all_counts_less_in_photo_shares():
    photo = {"photo_id": "z", "usable": True, "camera_xy_m": [0, 0],
             "_tiles": [{"family": "weathered_grey_concrete_frame"}] * 5 + [{"family": "cream_limestone_balcony_midrise"}] * 2}
    share = lib.photo_family_shares([photo])["z"]
    assert share["cream_limestone_balcony_midrise"] > 2 / 7
    assert lib.photo_family_shares([photo], excluded_photos={"z"}) == {}
    only_cream = lib.photo_family_shares([photo], allowed_families={"cream_limestone_balcony_midrise"})["z"]
    assert only_cream == {"cream_limestone_balcony_midrise": 1.0}
