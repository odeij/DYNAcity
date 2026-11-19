import json

import pytest

from dynacity.cli import _facilities_view, main
from dynacity.facilities import (
    OFFICIAL_SOURCE, build_facilities, crop_facilities, facilities_from_osm, facility_kind, overpass_query,
)


def osm():
    return {"osm3s": {"timestamp_osm_base": "2026-11-10T20:52:19Z"}, "elements": [
        {"type": "node", "id": 1, "lat": 33.89, "lon": 35.52,
         "tags": {"amenity": "hospital", "name": "Hôpital A", "name:ar": "مستشفى", "emergency": "yes", "phone": "x"}},
        {"type": "way", "id": 2, "center": {"lat": 33.891, "lon": 35.521}, "tags": {"amenity": "school", "name": "Brummana School"}},
        {"type": "node", "id": 3, "lat": 33.892, "lon": 35.522, "tags": {"healthcare": "centre"}},
        {"type": "node", "id": 4, "lat": 33.893, "lon": 35.523, "tags": {"amenity": "doctors"}},
        {"type": "node", "id": 5, "lat": 33.894, "lon": 35.524, "tags": {"amenity": "cafe", "name": "Not a facility"}},
        {"type": "way", "id": 6, "tags": {"amenity": "pharmacy"}},  # no position
    ]}


def test_osm_facilities_keep_kind_position_and_only_listed_tags():
    features = {f["properties"]["id"]: f for f in facilities_from_osm(osm())}
    assert set(features) == {"node/1", "way/2", "node/3", "node/4"}
    hospital = features["node/1"]["properties"]
    assert hospital["facility"] == "hospital" and hospital["group"] == "health"
    assert hospital["tags"] == {"emergency": "yes"}, "contact tags stay in the source extract"
    assert hospital["name_ar"] == "مستشفى" and hospital["named"]
    assert features["way/2"]["geometry"]["coordinates"] == [35.521, 33.891]
    assert features["node/3"]["properties"]["facility"] == "clinic"
    unnamed = features["node/4"]["properties"]
    assert unnamed["name"] == "Clinic or doctor" and not unnamed["named"], "a label is not presented as a name"
    assert facility_kind({"amenity": "college"}) == "university"
    assert '"amenity"~"^(' in overpass_query((35.5, 33.8, 35.6, 33.9)) and "out center" in overpass_query((35.5, 33.8, 35.6, 33.9))


def test_public_services_beyond_health_and_education():
    cases = {
        "police": {"amenity": "police"}, "fire_station": {"amenity": "fire_station"},
        "ambulance": {"emergency": "ambulance_station"}, "townhall": {"amenity": "townhall"},
        "courthouse": {"amenity": "courthouse"}, "post_office": {"amenity": "post_office"},
        "government_office": {"office": "government"}, "social_facility": {"amenity": "nursing_home"},
        "childcare": {"amenity": "childcare"}, "community_centre": {"amenity": "social_centre"},
        "library": {"amenity": "library"}, "public_building": {"building": "civic"},
    }
    for kind, tags in cases.items():
        assert facility_kind(tags) == kind, tags
    # A more specific tag wins over the bare building type.
    assert facility_kind({"building": "government", "amenity": "police"}) == "police"
    assert facility_kind({"building": "government"}) == "government_office"
    assert facility_kind({"amenity": "bank"}) is None and facility_kind({"office": "diplomatic"}) is None
    query = overpass_query((35.5, 33.8, 35.6, 33.9))
    assert 'nwr["office"="government"]' in query and 'nwr["emergency"="ambulance_station"]' in query
    assert 'wr["building"~"^(civic|government|public)$"]' in query


def test_official_schools_confirm_nearby_osm_schools_and_add_missing_ones():
    rows = [
        {"code": "10", "name": "Public Brummana School", "name_ar": None, "lat": 33.8912, "lon": 35.5211},  # ~25 m from way/2
        {"code": "11", "name": "Public School C", "name_ar": "مدرسة", "lat": 33.90, "lon": 35.53},
        {"code": "12", "name": "Far away", "name_ar": None, "lat": 34.4, "lon": 35.8},  # outside the box
        {"code": "13", "name": "No position", "name_ar": None, "lat": None, "lon": None},
        # Beside way/2 but with another name: a different school, so both stay.
        {"code": "14", "name": "Makassed Mixed Public School", "name_ar": None, "lat": 33.8911, "lon": 35.5210},
    ]
    collection = build_facilities(osm(), rows, bbox=(35.5, 33.85, 35.56, 33.92))
    by_id = {f["properties"]["id"]: f["properties"] for f in collection["features"]}
    assert by_id["way/2"]["official"]["code"] == "10" and by_id["way/2"]["source"] == "OpenStreetMap"
    assert by_id["mehe/11"]["source"] == OFFICIAL_SOURCE and by_id["mehe/11"]["facility"] == "school"
    assert by_id["mehe/14"]["source"] == OFFICIAL_SOURCE
    assert "mehe/12" not in by_id and "mehe/13" not in by_id
    merged = collection["official_schools"]
    assert (merged["confirmed"], merged["added"], merged["match_m"]) == (1, 2, 80.0)
    assert collection["counts"]["school"] == 3
    assert [s["licence"] for s in collection["sources"]] == ["ODbL", "CC0"]


def test_official_schools_match_arabic_names_but_not_generic_words():
    source = {"elements": [
        {"type": "node", "id": 7, "lat": 33.88, "lon": 35.50,
         "tags": {"amenity": "school", "name": "ثانوية العلامة صبحي المحمصاني الرسمية"}},
        {"type": "node", "id": 8, "lat": 33.87, "lon": 35.50,
         "tags": {"amenity": "school", "name": "ثانوية زهية سلمان الرسمية"}},
    ]}
    rows = [
        {"code": "20", "name": "El Alama Sobhi El Mahmasani Mixed Secondary Public School",
         "name_ar": "ثانوية العلامة صبحي المحمصاني الرسمية المختلطة", "lat": 33.8802, "lon": 35.5001},
        # Only "secondary public" in common with node/8: a different school.
        {"code": "21", "name": "Namouzajiya Mixed Public School", "name_ar": "الثانوية الرسمية النموذجية",
         "lat": 33.8701, "lon": 35.5001},
    ]
    collection = build_facilities(source, rows)
    by_id = {f["properties"]["id"]: f["properties"] for f in collection["features"]}
    assert by_id["node/7"]["official"]["code"] == "20"
    assert "official" not in by_id["node/8"] and "mehe/21" in by_id


def test_crop_and_cli_round_trip(tmp_path):
    collection = build_facilities(osm())
    cropped = crop_facilities(collection, (35.5205, 33.8905, 35.53, 33.90))
    assert {f["properties"]["id"] for f in cropped["features"]} == {"way/2", "node/3", "node/4"}
    assert cropped["counts"] == {"school": 1, "clinic": 2}

    source = tmp_path / "osm.json"
    source.write_text(json.dumps(osm()), encoding="utf-8")
    schools = tmp_path / "schools.csv"
    schools.write_text("CERD,School Name - Arabic,School Name,Latitude,longitude\n11,م,Public School C,33.90,35.53\n",
                       encoding="utf-8")
    output = tmp_path / "facilities.json"
    main(["build-facilities", "--bbox", "35.5", "33.85", "35.56", "33.92", "--osm", str(source),
          "--official-schools", str(schools), "--output", str(output)])
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["counts"]["school"] == 2 and saved["official_schools"]["added"] == 1
    assert len(_facilities_view(str(output), (35.529, 33.899, 35.531, 33.901))["features"]) >= 1
    with pytest.raises(SystemExit):
        _facilities_view(str(source))
