import json
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

from dynacity.bbed import BBEDClient, resolved_object_ids
from dynacity.panel import PanelBuilder, assert_no_temporal_leakage
from dynacity.transitions import summarize_interval

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


def test_panel_includes_direct_2018_to_2024_analysis_interval(bbed_collection):
    panel = PanelBuilder().build(bbed_collection)
    direct = panel[(panel.start_year == 2018) & (panel.target_year == 2024)]

    assert len(panel) == 6
    assert len(direct) == 2
    assert set(direct.interval_years) == {6}
    assert set(direct.current_state) == {"active_construction"}
    assert set(direct.target_state) == {"stable_built"}

    summary = summarize_interval(panel, start_year=2018, target_year=2024)
    assert summary["total_rows"] == 2
    assert summary["canonical_changed"] == 2
    assert summary["canonical_changed_rate"] == 1.0
    assert summary["canonical_transitions"][0] == {
        "from_state": "active_construction",
        "to_state": "stable_built",
        "count": 2,
        "share": 1.0,
    }


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
