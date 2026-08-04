from datetime import date

import pytest
from pydantic import ValidationError

from dynacity.contracts import ForecastRequest, ScenarioSpec, UrbanStateSnapshot
from dynacity.status import CanonicalState, canonicalize_status


def test_status_crosswalk_handles_all_major_groups():
    assert canonicalize_status("Complete Building") == CanonicalState.STABLE_BUILT
    assert canonicalize_status("Construction on-Hold") == CanonicalState.STALLED_OR_CANCELLED
    assert canonicalize_status("Old-Bldg-Uninhabited") == CanonicalState.VACANT_OR_EVICTED
    assert canonicalize_status("new future label") == CanonicalState.UNKNOWN


def test_forecast_contract_rejects_mismatched_snapshot():
    snapshot = UrbanStateSnapshot(
        snapshot_id="one", as_of=date(2024, 1, 1), objects=[], data_version="test"
    )
    with pytest.raises(ValidationError):
        ForecastRequest(
            snapshot=snapshot,
            scenario=ScenarioSpec(
                scenario_id="s", baseline_snapshot_id="two", horizon_years=2
            ),
        )

