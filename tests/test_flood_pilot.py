from copy import deepcopy
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely.geometry import LineString, box

from dynacity.flood_pilot import (PilotAsset, PilotBundle, from_hydraulic_outputs,
                                 intersect_assets, summarize_pilot, synthetic_pilot,
                                 validate_rainfall)
from dynacity.flood_surface import HydraulicOutput


def test_demo_is_explicitly_synthetic_without_invented_exposure():
    b = synthetic_pilot()
    r = summarize_pilot(b)
    assert r['evidence'] == 'synthetic'
    assert b.assets == []
    assert r['critical_facility_accessibility']['status'] == 'unavailable'
    assert r['validation']['status'] == 'not_validated'
    assert all(f.velocity_m_s is None for s in b.scenarios for f in s.frames)
    assert validate_rainfall(b.rainfall).total_depth_mm == 30


def test_exposure_counts_threshold_intervals_and_keeps_unknown():
    data = synthetic_pilot().model_dump(mode='json')
    data['assets'] = [dict(asset_id='r1', kind='road', cells=[4,5], provenance='test geometry'),
                      dict(asset_id='b1', kind='building', cells=[0], provenance='test geometry')]
    for scenario in data['scenarios']:
        scenario['frames'][0]['depth_m'][4] = None
        scenario['frames'][1]['depth_m'][4] = None
        scenario['frames'][1]['depth_m'][5] = .4
    r = summarize_pilot(PilotBundle.model_validate(data))['results'][0]
    road, building = r['assets']
    assert road['exceedance_duration_s'] == 1200
    assert road['unknown_duration_s'] == 600
    assert building['exposed'] is False
    assert building['peak_depth_m'] == 0


@pytest.mark.parametrize('mutation', ['time','negative','size','capacity','rainfall'])
def test_rejects_incomparable_or_invalid_inputs(mutation):
    data = synthetic_pilot().model_dump(mode='json')
    if mutation == 'time': data['scenarios'][1]['frames'][0]['duration_s'] = 500
    if mutation == 'negative': data['scenarios'][0]['frames'][0]['depth_m'][0] = -1
    if mutation == 'size': data['grid']['cols'] = 4
    if mutation == 'capacity': data['scenarios'][1]['capacity_factor'] = 1
    if mutation == 'rainfall': data['rainfall']['intervals'][0]['intensity_mm_h'] = -2
    with pytest.raises((ValidationError, ValueError)):
        PilotBundle.model_validate(data)


def fixture_outputs():
    rain = validate_rainfall(synthetic_pilot().rainfall)
    meta = dict(schema='dynacity.hydraulic-grid.v1', output_kind='precomputed_hydraulic',
                crs='EPSG:32636', horizontal_units='m', depth_units='m', grid=[732800,3752300,100,100],
                vertical_datum='synthetic', terrain_id='test-terrain', terrain_provenance='fixture',
                timestamps=[rain.start.isoformat(),(rain.start+timedelta(seconds=600)).isoformat()],
                nodata=None, model=dict(name='fixture',version='1',run_id='test',source='test',evidence='synthetic'),
                forcing=dict(rainfall=dict(status='supplied',source='rain-fixture',units='m/s',description='fixture'),
                             boundary=dict(status='none',description='walls'),
                             drainage_exchange=dict(status='none',description='fixture')))
    return {k: HydraulicOutput(np.array([[[.1,.2],[.3,np.nan]],[[.2,.3],[.4,np.nan]]]),deepcopy(meta))
            for k in ('existing','blocked','improved')}, rain


def adapt(outputs, rain):
    assumptions = {s.kind: s.model_dump() for s in synthetic_pilot().scenarios}
    return from_hydraulic_outputs(outputs=outputs, rainfall=rain, rainfall_source='rain-fixture', label='fixture',
                                  source_resolution_m=100,capacity_assumptions=assumptions)


def test_surface_adapter_flips_north_first_rows_and_has_no_terminal_extrapolation():
    outputs,rain = fixture_outputs()
    outputs['existing'].velocity_m_s = np.ones((2,2,2,2))
    b = adapt(outputs,rain)
    assert len(b.scenarios[0].frames) == 2
    assert b.scenarios[0].frames[-1].duration_s == 0
    assert b.scenarios[0].frames[0].depth_m == [.3,None,.1,.2]
    assert b.scenarios[0].frames[0].velocity_m_s[0] == pytest.approx(2**.5)
    assert b.scenarios[1].frames[0].velocity_m_s is None
    assert b.grid.y_min_m == 3752100


@pytest.mark.parametrize('key',['terrain_id','grid','forcing'])
def test_surface_adapter_requires_identical_baseline_inputs(key):
    outputs,rain=fixture_outputs()
    if key == 'terrain_id': outputs['blocked'].metadata[key]='other'
    if key == 'grid': outputs['blocked'].metadata[key][0]+=1
    if key == 'forcing': outputs['blocked'].metadata[key]['rainfall']['source']='other'
    with pytest.raises(ValueError): adapt(outputs,rain)


def test_asset_intersections_exclude_partial_coverage_and_crs_mismatch():
    grid = synthetic_pilot().grid
    x,y=grid.x_min_m,grid.y_min_m
    assets=intersect_assets(grid,buildings={'a':box(x+10,y+10,x+20,y+20),'outside':box(x-1,y,x+20,y+20)},
                            roads={'b':LineString([(x+20,y+20),(x+250,y+20)])},crs=grid.crs,provenance='supplied fixture')
    assert [a.asset_id for a in assets]==['building:a','road:b']
    assert assets[1].cells==[0,1,2]
    with pytest.raises(ValueError): intersect_assets(grid,buildings={},roads={},crs='EPSG:4326',provenance='fixture')


def test_api_unavailable_and_synthetic_inputs_are_disabled():
    from dynacity.service import create_app
    class Engine: pass
    engine = Engine()
    client=TestClient(create_app(engine))
    assert client.get('/v1/flood/pilot').json()['status']=='unavailable'
    assert client.get('/v1/flood/pilot/demo').status_code==410
    demo=synthetic_pilot().model_dump(mode='json')
    assert client.post('/v1/flood/pilot/compare',json=demo).status_code==422
    bad=demo;bad['scenarios'][0]['frames'][0]['depth_m'][0]=-3
    assert client.post('/v1/flood/pilot/compare',json=bad).status_code==422
    page=client.get('/flood')
    assert page.status_code==200
    assert 'Invented data is disabled' in page.text
    assert 'id="demo"' not in page.text


def test_planning_import_keeps_original_hashes_and_budget_gate():
    from dynacity.flood_planning import (Budget, CostAssumption, Evaluation, ExperimentInputs,
                                        InterventionChoice, InterventionKind, Plan)
    from dynacity.flood_pilot import PlanningRunBundle, evaluate_planning_bundle
    inputs=ExperimentInputs.from_inputs(forcing={'source':'fixture'},baseline={'terrain':'fixture'},settings={'engine':'fixture'})
    baseline=Plan(plan_id='baseline')
    plan=Plan(plan_id='test',choices=(InterventionChoice(choice_id='test',kind=InterventionKind.DRAIN_MAINTENANCE,
              target_ids=('supplied-link',),cost=CostAssumption(amount='20',currency='USD',source='user assumption')),))
    def evaluation(p):
        return Evaluation(inputs_fingerprint=inputs.fingerprint,plan_fingerprint=p.fingerprint,evaluator_id='fixture-v1',
                          evidence='synthetic_fixture',provenance='test fixture')
    bundle=PlanningRunBundle(inputs=inputs,plans=(plan,),budget=Budget(amount='10',currency='USD'),
              evaluator_id='fixture-v1',evaluations={baseline.fingerprint:evaluation(baseline)},hypothesis='Test a budget constraint')
    result=evaluate_planning_bundle(bundle)['iteration']['report']
    assert result['results'][0]['feasible'] is False
    assert result['results'][0]['evaluation'] is None
    bundle=bundle.model_copy(update={'budget':Budget(amount='20',currency='USD')})
    with pytest.raises(ValueError,match='No original model evaluation'):evaluate_planning_bundle(bundle)
    wrong=evaluation(plan).model_copy(update={'inputs_fingerprint':'unrelated'})
    bundle=bundle.model_copy(update={'evaluations':{baseline.fingerprint:evaluation(baseline),plan.fingerprint:wrong}})
    with pytest.raises(ValueError):evaluate_planning_bundle(bundle)


def test_rainfall_drainage_adapter_preserves_precipitation_depth():
    from dynacity.flood_pilot import drainage_rainfall
    forcing=validate_rainfall(synthetic_pilot().rainfall)
    rain=drainage_rainfall(forcing)
    assert sum(rain.intensity_mm_hr)*rain.interval_s/3600 == pytest.approx(forcing.total_depth_mm)


def test_cli_outputs_require_private_destination(tmp_path):
    from dynacity.cli import main
    import json
    with pytest.raises(SystemExit):
        main(['flood-pilot','--output',str(tmp_path/'must-not-exist.json')])
    source=tmp_path/'fixture.json'
    source.write_text(synthetic_pilot().model_dump_json())
    with pytest.raises(ValueError,match='Synthetic'):
        main(['flood-pilot','--input',str(source),'--output',str(tmp_path/'must-not-exist.json')])
    assert not (tmp_path/'must-not-exist.json').exists()


def test_terminal_snapshot_counts_for_peak_without_adding_closure_duration():
    outputs,rain=fixture_outputs()
    for output in outputs.values():
        output.depth_m[0] = 0
        output.depth_m[-1] = .5
    b=adapt(outputs,rain)
    b=PilotBundle.model_validate({**b.model_dump(mode='json'),'assets':[dict(asset_id='road:r',kind='road',cells=[0],provenance='fixture')]})
    r=summarize_pilot(b)['results'][0]['assets'][0]
    assert r['peak_depth_m']==.5
    assert r['exposed'] is True
    assert r['exceedance_duration_s']==0


def test_terrain_attachment_checks_source_and_preserves_coarse_detail():
    from dynacity.flood_pilot import attach_terrain_evidence
    from dynacity.flood_terrain import TerrainGrid, TerrainProvenance
    b=synthetic_pilot()
    data=b.model_dump(mode='json');data['grid']['terrain_id']='source-sha'
    b=PilotBundle.model_validate(data)
    g=b.grid
    terrain=TerrainGrid(crs=g.crs,x0=g.x_min_m,y0=g.y_min_m+g.rows*g.cell_m,cell_m=g.cell_m,
        elevation_m=np.zeros((3,3)),provenance=TerrainProvenance(source='fixture',kind='surface',
        vertical_reference=g.vertical_datum,evidence='synthetic',source_resolution_m=300),
        source_crs=g.crs,source_resolution=(100,100),source_resolution_unit='metre',source_nodata=None,
        source_sha256='source-sha')
    result=attach_terrain_evidence(b,terrain)
    assert result.grid.source_resolution_m==300
    assert any('resampling' in w for w in result.warnings)
    terrain2=deepcopy(terrain)
    from dataclasses import replace
    with pytest.raises(ValueError):attach_terrain_evidence(b,replace(terrain2,source_sha256='wrong'))


def test_observation_http_adapter_preserves_empty_as_unavailable():
    from dynacity.service import create_app
    client=TestClient(create_app(object()))
    payload={'model':dict(grid=dict(crs='EPSG:32636',vertical_datum='synthetic-ground',x0=0,y0=10,cell_m=10,rows=1,cols=1),
        times=['2026-10-16T00:00:00Z'],depth_m=[[[0]]],valid=[[[True]]],event_id='fixture',
        model_id='fixture',provenance='synthetic fixture'),'observations':[]}
    response=client.post('/v1/flood/validation',json=payload)
    assert response.status_code==200
    assert response.json()['split_metrics']['holdout']['depth']['mae'] is None
    assert response.json()['calibrated'] is False
    assert client.post('/v1/flood/validation',json={}).status_code==422


def test_cli_does_not_promote_synthetic_hydraulic_outputs_to_city_bundle(tmp_path):
    import json
    from dynacity.cli import main
    from dynacity.flood_pilot import rainfall_record
    outputs,rain=fixture_outputs()
    for k,v in outputs.items():v.save(tmp_path/f'{k}.npz')
    manifest=dict(outputs={k:f'{k}.npz' for k in outputs},rainfall=rainfall_record(rain),rainfall_source='rain-fixture',
          label='Synthetic external fixture',source_resolution_m=100,
          capacity_assumptions={s.kind:s.model_dump(mode='json') for s in synthetic_pilot().scenarios})
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest))
    destination=tmp_path/'pilot.json'
    with pytest.raises(ValueError,match='Synthetic'):
        main(['flood-import','--manifest',str(path),'--output',str(destination)])
    assert not destination.exists()


def test_planning_journal_retains_iterations_and_requires_fixed_budget():
    from dynacity.flood_planning import Budget, Evaluation, ExperimentInputs, Plan, PlanningChallenge
    from dynacity.flood_pilot import PlanningRunBundle, evaluate_planning_bundle
    inputs=ExperimentInputs.from_inputs(forcing={'fixture':1},baseline={'fixture':1},settings={'fixture':1})
    baseline=Plan(plan_id='baseline')
    evaluation=Evaluation(inputs_fingerprint=inputs.fingerprint,plan_fingerprint=baseline.fingerprint,evaluator_id='fixture',
                          evidence='synthetic_fixture',provenance='test fixture')
    bundle=PlanningRunBundle(inputs=inputs,plans=(),budget=Budget(amount='10',currency='USD'),evaluator_id='fixture',
        evaluations={baseline.fingerprint:evaluation},hypothesis='Record this practice iteration')
    first=evaluate_planning_bundle(bundle)
    journal=PlanningChallenge.model_validate(first['challenge'])
    second=evaluate_planning_bundle(bundle.model_copy(update={'challenge':journal}))
    assert len(second['challenge']['iterations'])==2
    with pytest.raises(ValueError,match='start a new challenge'):
        evaluate_planning_bundle(bundle.model_copy(update={'challenge':journal,'budget':Budget(amount='20',currency='USD')}))


def test_map_geometry_uses_projected_cells_and_keeps_depth_values():
    from dynacity.flood_pilot import pilot_map_geometry
    from dynacity.viewer import lonlat_to_utm
    bundle=synthetic_pilot()
    geometry=pilot_map_geometry(bundle)
    assert len(geometry['polygons'])==9
    x,y=lonlat_to_utm(*geometry['polygons'][0][0],36)
    assert x==pytest.approx(bundle.grid.x_min_m,abs=.01)
    assert y==pytest.approx(bundle.grid.y_min_m,abs=.01)
    assert 'no absolute' in geometry['render_reference']
    from dynacity.service import create_app
    response=TestClient(create_app(object())).post('/v1/flood/pilot/map',json=bundle.model_dump(mode='json'))
    assert response.status_code==422
