import json
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

from dynacity.bbed import BBEDClient, resolved_object_ids
from dynacity.panel import PanelBuilder, assert_no_temporal_leakage

from conftest import bbed_feature


def test_bbed_client_paginates_and_enforces_allowlist():
    features = [bbed_feature(index) for index in range(1, 4)]

    def transport(url: str) -> bytes:
        query = parse_qs(urlparse(url).query)
        offset = int(query.get("resultOffset", [0])[0])
        page = features[offset : offset + 2]
        return json.dumps({"type": "FeatureCollection", "features": page}).encode()

    client = BBEDClient(transport=transport, page_size=2)
    result = client.fetch_features()
    assert len(result["features"]) == 3
    with pytest.raises(ValueError, match="privacy allowlist"):
        client.fetch_features(fields=["OBJECTID", "F31__Contact_person"])


def test_panel_uses_2020_lidar_only_after_acquisition(bbed_collection):
    object_id = "BULBuildingID:1|OBJECTID:1"
    lidar = pd.DataFrame(
        [{"object_id": object_id, "height_p95_m": 21.0, "point_count": 100}]
    )
    panel = PanelBuilder(lidar_acquisition_year=2020).build(bbed_collection, lidar)
    early = panel[(panel.object_id == object_id) & (panel.start_year == 2018)].iloc[0]
    late = panel[(panel.object_id == object_id) & (panel.start_year == 2022)].iloc[0]
    assert early.lidar_available == 0
    assert np.isnan(early.lidar_height_p95_m)
    assert late.lidar_available == 1
    assert late.lidar_height_p95_m == 21.0
    assert_no_temporal_leakage(panel)


def test_object_ids_are_stable_composites_across_query_subsets():
    first = bbed_feature(1)
    second = bbed_feature(2)
    second["properties"]["BULBuildingID"] = 1
    assert resolved_object_ids([first, second]) == [
        "BULBuildingID:1|OBJECTID:1",
        "BULBuildingID:1|OBJECTID:2",
    ]
    assert resolved_object_ids([second]) == ["BULBuildingID:1|OBJECTID:2"]


def test_leakage_assertion_rejects_future_features():
    bad = pd.DataFrame(
        [{"start_year": 2018, "lidar_available": 1, "lidar_acquisition_year": 2020}]
    )
    with pytest.raises(ValueError, match="leakage"):
        assert_no_temporal_leakage(bad)
