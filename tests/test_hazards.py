from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dynacity.contracts import (
    ForecastRequest,
    HazardEvent,
    HazardKind,
    ScenarioSpec,
    UrbanObjectState,
    UrbanStateSnapshot,
)
from dynacity.hazards import PROFILES, combined_outcomes, hazard_shock, radius_of
from dynacity.planning import KpiObjective, PlanningRequest, plan
from dynacity.scenario_compiler import (
    DraftHazard,
    HazardLocation,
    ScenarioDraft,
    check_draft,
    resolve_draft,
)
from dynacity.service import create_app
from dynacity.snapshot import attach_centroids, snapshot_from_bbed
from dynacity.status import CanonicalState
from dynacity.viewer import hazard_focus, lonlat_to_utm, utm_to_lonlat

from test_models_engine_api import engine_fixture
from test_scenario_compiler import FakeClient
from test_planning import lever

DEMOLISHED = CanonicalState.DEMOLISHED


def building(object_id, x, state=CanonicalState.STABLE_BUILT, sector="Mar Mikhael", floors=4.0, y=0.0):
    return UrbanObjectState(
        object_id=object_id,
        state=state,
        sector=sector,
        building_use="Residential",
        footprint_area_m2=100,
        floors=floors,
        height_m=floors * 3.2,
        centroid_x_m=x,
        centroid_y_m=y,
    )


def city() -> UrbanStateSnapshot:
    """A row of buildings along x: two at ground zero, one 150 m out, one 5 km away."""

    return UrbanStateSnapshot(
        snapshot_id="row",
        as_of=date(2024, 4, 30),
        data_version="synthetic",
        objects=[
            building("zero", 0),
            building("site", 5, state=CanonicalState.ACTIVE_CONSTRUCTION),
            building("near", 150),
            building("lot", 10, state=CanonicalState.EMPTY_OR_PARKING, floors=0),
            building("far", 5000, sector="Gemmayzeh"),
            building("far-2", 5100, sector="Gemmayzeh", y=40),
        ],
    )


def point(kind, **extra) -> HazardEvent:
    return HazardEvent(hazard_id=f"{kind.value}-1", kind=kind, center_x_m=0.0, center_y_m=0.0, **extra)


def forecast(*hazards, horizon=4):
    snapshot = city()
    return engine_fixture().forecast(ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(
            scenario_id="disaster", baseline_snapshot_id="row", horizon_years=horizon, hazards=list(hazards)
        ),
        draws=200,
        seed=11,
    ))


def first_step(result, object_id):
    return next(item.probabilities for item in result.first_step_transitions if item.object_id == object_id)


@pytest.mark.parametrize("kind", list(HazardKind))
def test_every_hazard_is_a_valid_bounded_shock_that_fades_with_distance(kind):
    profile = PROFILES[kind]
    located = point(kind) if "point" in profile.footprints else None
    hazard = located or HazardEvent(hazard_id="h", kind=kind, path_m=[(-100, 0), (100, 0)])
    shock = hazard_shock(hazard, city().objects)
    assert np.all(shock.destroy >= 0) and np.all(shock.damage >= 0)
    assert np.all(shock.destroy + shock.damage <= 1 + 1e-12)
    assert shock.destroy[0] + shock.damage[0] > 0, "ground zero must be hit"
    if radius_of(hazard) < 5000:
        assert shock.destroy[4] + shock.damage[4] == 0, "5 km out is outside a local footprint"
    lo, hi = profile.magnitude_range
    assert lo <= profile.default_magnitude <= hi
    assert all(lo <= value <= hi for value in profile.severity_magnitudes.values())


def test_contract_rejects_unplaceable_or_out_of_range_hazards():
    with pytest.raises(ValidationError, match="cannot be citywide"):
        HazardEvent(hazard_id="h", kind=HazardKind.ORBITAL_STRIKE, citywide=True)
    with pytest.raises(ValidationError, match="exactly one"):
        HazardEvent(hazard_id="h", kind=HazardKind.FLOOD, citywide=True, center_x_m=0, center_y_m=0)
    with pytest.raises(ValidationError, match="exactly one"):
        HazardEvent(hazard_id="h", kind=HazardKind.FLOOD)
    with pytest.raises(ValidationError, match="magnitude must be within"):
        point(HazardKind.EARTHQUAKE, magnitude=11)
    with pytest.raises(ValidationError, match="both center"):
        HazardEvent(hazard_id="h", kind=HazardKind.FIRE, center_x_m=0)
    with pytest.raises(ValidationError, match="beyond the 2-year horizon"):
        ScenarioSpec(scenario_id="s", baseline_snapshot_id="row", horizon_years=2,
                     hazards=[point(HazardKind.FIRE, occurs_step=2)])
    with pytest.raises(ValidationError, match="unique"):
        ScenarioSpec(scenario_id="s", baseline_snapshot_id="row", hazards=[point(HazardKind.FIRE)] * 2)


def test_explosion_destroys_near_buildings_and_spares_far_ones_and_empty_lots():
    baseline = forecast()
    blast = forecast(point(HazardKind.EXPLOSION, magnitude=1000))

    assert blast.evidence_level == "assumption_based_scenario"
    assert any("uncalibrated" in warning for warning in blast.warnings)
    shock = hazard_shock(point(HazardKind.EXPLOSION, magnitude=1000), city().objects)
    zero = first_step(blast, "zero")
    assert zero[DEMOLISHED] >= shock.destroy[0] - 1e-9
    assert zero[CanonicalState.VACANT_OR_EVICTED] >= shock.damage[0] - 1e-9
    # A hit construction site stalls rather than being evicted.
    assert first_step(blast, "site")[CanonicalState.STALLED_OR_CANCELLED] > 0.3
    # Nothing to damage on an empty lot; nothing reaches 5 km.
    assert first_step(blast, "lot") == first_step(baseline, "lot")
    assert first_step(blast, "far") == first_step(baseline, "far")
    for item in blast.first_step_transitions:
        assert abs(sum(item.probabilities.values()) - 1) < 1e-9

    [impact] = blast.hazard_impacts
    assert impact.kind == HazardKind.EXPLOSION and impact.magnitude_unit == "TNT-equivalent tonnes"
    assert impact.radius_m == pytest.approx(2000)
    assert impact.exposed_buildings == 4  # zero, site, near, lot (the lot has nothing to lose)
    assert impact.destroyed.p05 <= impact.destroyed.p50 <= impact.destroyed.p95 <= 3
    demolished = {(k.year_offset, k.kpi): k.p50 for k in blast.kpis}
    assert demolished[(2, "demolished_count")] > {(k.year_offset, k.kpi): k.p50 for k in baseline.kpis}[
        (2, "demolished_count")]


def test_hazard_results_are_seeded_and_strike_on_their_own_step():
    later = point(HazardKind.ORBITAL_STRIKE, magnitude=200, occurs_step=2)
    first, second = forecast(later), forecast(later)
    assert first == second
    # Step 1 is untouched by a step-2 hazard …
    assert first_step(first, "zero") == first_step(forecast(), "zero")
    # … and the strike shows up at +4 years.
    [impact] = first.hazard_impacts
    assert impact.year_offset == 4 and impact.destroyed.p50 >= 2


def test_tornado_track_hits_buildings_along_it_not_beside_it():
    track = HazardEvent(hazard_id="t", kind=HazardKind.TORNADO, magnitude=4, path_m=[(-500, 0), (500, 0)])
    objects = [building("on", 300), building("beside", 0, y=900)]
    shock = hazard_shock(track, objects)
    assert shock.destroy[0] > 0.1 and shock.destroy[1] == 0


def test_citywide_hazard_reaches_every_building_even_without_location():
    heat = HazardEvent(hazard_id="h", kind=HazardKind.HEAT, magnitude=50, citywide=True)
    objects = [*city().objects, UrbanObjectState(object_id="nowhere", state=CanonicalState.STABLE_BUILT)]
    shock = hazard_shock(heat, objects)
    assert shock.exposed.all() and shock.unlocated == 0
    assert np.all(shock.destroy == 0)  # heat has no structural pathway


def test_unlocated_buildings_are_left_out_and_reported():
    snapshot = city().model_copy(update={"objects": [
        *city().objects, UrbanObjectState(object_id="nowhere", state=CanonicalState.STABLE_BUILT)
    ]})
    result = engine_fixture().forecast(ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(scenario_id="s", baseline_snapshot_id="row", hazards=[point(HazardKind.FIRE)]),
        draws=50,
    ))
    assert result.hazard_impacts[0].unlocated_buildings == 1
    assert any("1 buildings have no location" in warning for warning in result.warnings)


def test_overlapping_hazards_combine_independently():
    objects = city().objects[:1]
    fire, quake = point(HazardKind.FIRE, magnitude=5), point(HazardKind.EARTHQUAKE, magnitude=8)
    a, b = hazard_shock(fire, objects), hazard_shock(quake, objects)
    destroyed, damaged = combined_outcomes([a, b], np.asarray(["stable_built"]))
    assert destroyed[0] == pytest.approx(1 - (1 - a.destroy[0]) * (1 - b.destroy[0]))
    untouched = (1 - a.destroy[0] - a.damage[0]) * (1 - b.destroy[0] - b.damage[0])
    assert destroyed[0] + damaged[0] == pytest.approx(1 - untouched)


def test_snapshot_records_centroids_and_backfills_old_snapshots(bbed_collection):
    snapshot = snapshot_from_bbed(bbed_collection, snapshot_id="s", as_of=date(2024, 4, 30), data_version="v")
    assert [(o.centroid_x_m, o.centroid_y_m) for o in snapshot.objects] == [(5.0, 5.0), (25.0, 5.0)]
    stripped = snapshot.model_copy(update={"objects": [
        o.model_copy(update={"centroid_x_m": None, "centroid_y_m": None}) for o in snapshot.objects
    ]})
    assert attach_centroids(stripped, bbed_collection) == snapshot


def hazard_draft(**overrides) -> ScenarioDraft:
    hazard = {
        "hazard_id": "quake",
        "kind": HazardKind.EARTHQUAKE,
        "description": "earthquake in Mar Mikhael",
        "location": HazardLocation(sectors=["Mar Mikhael"]),
        "severity": "severe",
    } | overrides
    return ScenarioDraft(scenario_id="quake", horizon_years=6, hazards=[DraftHazard(**hazard)])


def test_compiler_places_a_hazard_on_the_named_sector():
    text = "A severe earthquake hits Mar Mikhael."
    report = check_draft(hazard_draft(), city(), text)
    assert report.ok, report.hard()
    assert any(issue.code == "hazard.assumed_damage" for issue in report.issues)
    [hazard] = resolve_draft(hazard_draft(), city()).hazards
    # Centre of the four Mar Mikhael buildings; magnitude from the severity table.
    assert (hazard.center_x_m, hazard.center_y_m) == pytest.approx((41.25, 0.0))
    assert hazard.magnitude == PROFILES[HazardKind.EARTHQUAKE].severity_magnitudes["severe"]


def test_compiler_runs_a_tornado_along_the_sector_and_grows_floods_to_cover_it():
    text = "A tornado tears through Gemmayzeh, then Gemmayzeh floods."
    tornado = hazard_draft(hazard_id="t", kind=HazardKind.TORNADO, location=HazardLocation(sectors=["Gemmayzeh"]))
    [track] = resolve_draft(tornado, city()).hazards
    assert len(track.path_m) == 2
    assert {tuple(np.round(p)) for p in track.path_m} == {(5000, 0), (5100, 40)}
    flood = hazard_draft(hazard_id="f", kind=HazardKind.FLOOD, severity="minor", explicit_radius_m=None)
    [area] = resolve_draft(flood, city()).hazards
    assert area.radius_m >= PROFILES[HazardKind.FLOOD].radius_m(0.3)


@pytest.mark.parametrize(
    ("overrides", "text", "code"),
    [
        ({"explicit_magnitude": 7.5}, "A severe earthquake hits Mar Mikhael.", "hazard.magnitude_not_in_request"),
        ({"explicit_magnitude": 9.5}, "A magnitude 9.5 quake hits Mar Mikhael.", "hazard.magnitude_out_of_range"),
        ({"location": HazardLocation(sectors=["Karantina"])}, "A quake in Karantina.", "hazard.unknown_sector"),
        ({"location": HazardLocation()}, "A quake somewhere.", "hazard.no_location"),
        ({"kind": HazardKind.EXPLOSION, "location": HazardLocation(citywide=True)}, "A blast.",
         "hazard.not_citywide"),
        ({"kind": HazardKind.FLOOD, "location": HazardLocation(citywide=True)}, "A flood.",
         "hazard.citywide_not_requested"),
        ({"occurs_step": 4}, "A severe earthquake hits Mar Mikhael.", "hazard.step_out_of_horizon"),
    ],
)
def test_compiler_rejects_hazards_the_model_made_up(overrides, text, code):
    report = check_draft(hazard_draft(**overrides), city(), text)
    assert code in {issue.code for issue in report.hard()}


def test_compiler_accepts_a_stated_magnitude_and_regional_citywide_quake():
    text = "A magnitude 7 earthquake hits Beirut."
    draft = hazard_draft(explicit_magnitude=7, location=HazardLocation(citywide=True))
    report = check_draft(draft, city(), text)
    assert report.ok, report.hard()
    [hazard] = resolve_draft(draft, city()).hazards
    assert hazard.citywide and hazard.magnitude == 7


def test_compiler_cannot_place_hazards_in_a_snapshot_without_locations():
    blind = city().model_copy(update={"objects": [
        o.model_copy(update={"centroid_x_m": None, "centroid_y_m": None}) for o in city().objects
    ]})
    report = check_draft(hazard_draft(), blind, "A severe earthquake hits Mar Mikhael.")
    assert "hazard.unlocatable" in {issue.code for issue in report.hard()}


def test_planning_faces_every_candidate_with_the_same_hazard():
    request = PlanningRequest(
        snapshot=city(),
        horizon_years=4,
        levers=[lever("finish", CanonicalState.STABLE_BUILT)],
        objectives=[KpiObjective(kpi="completed_count", year_offset=4, direction="maximize")],
        hazards=[point(HazardKind.FLOOD, magnitude=3)],
        draws=50,
    )
    result = plan(engine_fixture(), request)
    assert any("same hazards" in warning for warning in result.warnings)
    with pytest.raises(ValidationError, match="beyond the 2-year horizon"):
        PlanningRequest(**(request.model_dump() | {"horizon_years": 2, "objectives": [
            KpiObjective(kpi="completed_count", year_offset=2, direction="maximize")],
            "hazards": [point(HazardKind.FLOOD, occurs_step=2)]}))


def test_viewer_flies_to_the_buildings_a_hazard_hits_hardest():
    fire = point(HazardKind.FIRE, radius_m=200)
    [focus] = hazard_focus([fire], city())
    assert focus["hazard_id"] == fire.hazard_id and focus["kind"] == "fire"
    assert focus["ids"] and not {"far", "far-2"} & set(focus["ids"])
    [quake] = hazard_focus([HazardEvent(hazard_id="q", kind=HazardKind.EARTHQUAKE, citywide=True)], city())
    assert {"far", "far-2"} <= set(quake["ids"])


def test_viewer_scenario_endpoint_returns_where_the_hazard_struck():
    client = TestClient(create_app(
        engine_fixture(), scenario_client=FakeClient(hazard_draft()), viewer_snapshot=city()
    ))
    body = client.post("/v1/viewer/scenario", json={"text": "A severe earthquake hits Mar Mikhael."}).json()
    assert body["forecast"] is not None
    [focus] = body["hazards"]
    assert focus["hazard_id"] == "quake" and focus["ids"]


def lonlat(x, y):
    return list(utm_to_lonlat(x, y, 36))


def test_forward_utm_inverts_the_viewer_projection():
    for x, y in [(732346.89, 3752967.64), (820000.0, 3800000.0), (0.0, 0.0), (5100.0, 40.0)]:
        assert lonlat_to_utm(*utm_to_lonlat(x, y, 36), 36) == pytest.approx((x, y), abs=0.01)


def test_dropped_disasters_run_without_a_language_model():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=city()))
    blast = {"kind": "explosion", "points": [lonlat(0, 0)], "severity": "severe"}
    tornado = {"kind": "tornado", "points": [lonlat(4900, 0), lonlat(5200, 40)]}
    body = client.post("/v1/viewer/hazard", json={"hazards": [blast, tornado]}).json()
    assert [h["kind"] for h in body["scenario"]["hazards"]] == ["explosion", "tornado"]
    assert body["forecast"]["evidence_level"] == "assumption_based_scenario"
    assert {i["hazard_id"] for i in body["forecast"]["hazard_impacts"]} == {"explosion-1", "tornado-2"}
    boom, twister = body["hazards"]
    assert "zero" in boom["ids"] and not {"far", "far-2"} & set(boom["ids"])
    assert boom["center"] == pytest.approx(lonlat(0, 0)) and boom["radius_m"] > 0
    assert len(twister["path"]) == 2 and {"far", "far-2"} <= set(twister["ids"])


def test_dropped_disasters_are_checked():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=city()))
    fire_track = {"kind": "fire", "points": [lonlat(0, 0), lonlat(100, 0)]}
    assert client.post("/v1/viewer/hazard", json={"hazards": [fire_track]}).status_code == 422
    unlocated = city().model_copy(update={"objects": [
        o.model_copy(update={"centroid_x_m": None, "centroid_y_m": None}) for o in city().objects
    ]})
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=unlocated))
    drop = {"kind": "explosion", "points": [lonlat(0, 0)]}
    response = client.post("/v1/viewer/hazard", json={"hazards": [drop]})
    assert response.status_code == 422 and "centroids" in response.json()["detail"]
