import json

import laspy
import numpy as np
from pyproj import CRS

from dynacity_bbed.pipeline import run_pipeline


def test_pipeline_enriches_las_and_writes_reports(tmp_path):
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.add_crs(CRS.from_epsg(32636))
    cloud = laspy.LasData(header)
    cloud.x = np.array([1.0, 2.0, 11.0, 20.0])
    cloud.y = np.array([1.0, 2.0, 1.0, 20.0])
    cloud.z = np.array([3.0, 9.0, 7.0, 0.0])
    cloud.red = np.array([10, 20, 30, 40], dtype=np.uint16)
    cloud.green = np.array([11, 21, 31, 41], dtype=np.uint16)
    cloud.blue = np.array([12, 22, 32, 42], dtype=np.uint16)
    input_path = tmp_path / "tile.las"
    cloud.write(input_path)

    footprints = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:32636"}},
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]],
                },
                "properties": {
                    "OBJECTID": 7,
                    "BULBuildingID": 70,
                    "ParcelID": "A-1",
                    "NoofFloor": 3,
                    "Building_Hight_m": 9.0,
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 0], [15, 0], [15, 5], [10, 5], [10, 0]]],
                },
                "properties": {"OBJECTID": 8, "ParcelID": "A-2"},
            },
        ],
    }
    bbed_path = tmp_path / "bbed.geojson"
    bbed_path.write_text(json.dumps(footprints), encoding="utf-8")

    output_dir = tmp_path / "outputs"
    report = run_pipeline(
        input_path,
        str(bbed_path),
        output_dir,
        point_crs_value="EPSG:32636",
        chunk_size=2,
    )

    enriched = laspy.read(output_dir / "enriched.las")
    assert np.asarray(enriched.bbed_id).tolist() == [0, 0, 1, -1]
    assert np.asarray(enriched.bbed_oid).tolist() == [7, 7, 8, -1]
    assert np.asarray(enriched.bbed_bldg).tolist() == [70, 70, -1, -1]
    assert report["matching"]["matched_points"] == 3
    assert report["matching"]["footprints_with_points"] == 2
    assert (output_dir / "registered_bbed.geojson").exists()
    assert (output_dir / "building_lookup.csv").exists()
    assert (output_dir / "quality_report.md").exists()

