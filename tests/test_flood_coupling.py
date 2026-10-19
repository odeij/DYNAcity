"""Numerical engine checks only. None of these values are Beirut inputs."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from dynacity.flood_coupling import Inlet, inlet_rate, exchange_volumes
from dynacity.flood_solver import load_inputs, run_surface
from test_flood_solver import benchmark, requires_engine


def coupled_case(folder, *, blocked=0., pipe_cap=.1, initial_surface=1., initial_sewer=0., step=.1, duration=20):
    folder.mkdir()
    path, manifest = benchmark(folder)
    meshpath = folder/'mesh.npz'
    with np.load(meshpath) as original: mesh={k:original[k] for k in original.files}
    mesh['initial_stage_m'][:] = initial_surface
    np.savez(meshpath,**mesh)
    with np.load(folder/'forcing.npz') as original: forcing={k:original[k] for k in original.files}
    forcing['times_s'] = np.array([0.,float(duration)])
    np.savez(folder/'forcing.npz',**forcing)
    # Explicit arbitrary mathematical network, never imported by city UI.
    (folder/'network.inp').write_text(f'''[TITLE]
Mathematical coupled-engine verification only; no Beirut measurements
[OPTIONS]
FLOW_UNITS CMS
FLOW_ROUTING DYNWAVE
START_DATE 01/01/2026
START_TIME 00:00:00
REPORT_START_DATE 01/01/2026
REPORT_START_TIME 00:00:00
END_DATE 01/01/2026
END_TIME 01:00:00
ALLOW_PONDING NO
IGNORE_RAINFALL YES
IGNORE_RDII YES
IGNORE_GROUNDWATER YES
SKIP_STEADY_STATE NO
ROUTING_STEP 0.01
VARIABLE_STEP 0
MIN_SURFAREA 0.0001
HEAD_TOLERANCE 0.000001
MAX_TRIALS 20
MINIMUM_STEP 0.001
REPORT_STEP 00:00:01
[STORAGE]
N1 -1 3 {initial_sewer} FUNCTIONAL 0 0 0.5 0 0 0
[OUTFALLS]
O1 -1.01 FREE NO
[CONDUITS]
P1 N1 O1 1 0.013 0 0 0 {pipe_cap}
[XSECTIONS]
P1 CIRCULAR 0.1 0 0 0 1
''')
    center = mesh['points_m'][mesh['triangles'][0]].mean(axis=0)
    manifest['drainage'] = dict(engine='swmm-5.2.4',network='network.inp',
        vertical_datum=manifest['vertical_datum'],crs=manifest['crs'],step_s=step,
        inlets=[dict(id='I1',node='N1',cell=0,x_m=center[0],y_m=center[1],rim_m=0,
                     opening_area_m2=.01,weir_width_m=.1,weir_coefficient_m_half_s=1.5,
                     orifice_coefficient=.6,blockage_fraction=blocked)],overflow_cells={'N1':0},
        # SWMM dynamic-wave routing is not machine-exact finite-volume storage.
        # Declare a 3-litre absolute + 0.5% relative numerical acceptance gate;
        # save actual residuals, and separately reject a failing transient below.
        mass_absolute_tolerance_m3=.003,mass_relative_tolerance=.005)
    manifest['scope']='Numerical coupled-engine check only, all physical values arbitrary test inputs; no city data.'
    path.write_text(json.dumps(manifest))
    return path, manifest


def mathematical_inlet(**kwargs):
    return Inlet(id='I',node='N',cell=0,x_m=0,y_m=0,rim_m=0,opening_area_m2=.02,
                 weir_width_m=.2,weir_coefficient_m_half_s=1.5,orifice_coefficient=.6,
                 blockage_fraction=kwargs.pop('blockage_fraction',0),**kwargs)


def test_inlet_responds_to_both_heads_and_complete_blockage():
    inlet=mathematical_inlet()
    assert inlet_rate(inlet,-.1,-1,9.8)==0
    assert inlet_rate(inlet,.2,.2,9.8)==0
    assert inlet_rate(inlet,.3,-.5,9.8)>0
    assert inlet_rate(inlet,.1,.5,9.8)<0
    assert inlet_rate(mathematical_inlet(blockage_fraction=1),.5,-1,9.8)==0
    assert inlet_rate(mathematical_inlet(blockage_fraction=1),0,1,9.8)==0
    assert inlet_rate(inlet,.3,.29,9.8)<inlet_rate(inlet,.3,-.5,9.8)


def test_shared_cell_and_node_cannot_promise_same_water_twice(tmp_path):
    from dynacity.flood_coupling import Coupling
    path, manifest=coupled_case(tmp_path/'input')
    spec=Coupling.model_validate(manifest['drainage'])
    spec.inlets.append(spec.inlets[0].model_copy(update={'id':'I2'}))
    class Sewer:
        def head(self,node): return -1
        def volume(self,node): return .001
    stage=np.array([.1]); bed=np.array([0.]); area=np.array([.001])
    volumes=exchange_volumes(spec,stage,bed,area,Sewer(),9.8)
    assert volumes.sum()==pytest.approx(.0001)
    assert volumes[0]==pytest.approx(volumes[1])
    class Full(Sewer):
        def head(self,node):return 2
    returned=exchange_volumes(spec,stage,bed,area,Full(),9.8)
    assert returned.sum()==pytest.approx(-.001)


def time_series_outfall(path, *, series=None):
    network = path.parent/'network.inp'
    network.write_text(network.read_text().replace('O1 -1.01 FREE NO', 'O1 -1.01 TIMESERIES SEA NO')+
        '\n[TIMESERIES]\n'+(series if series is not None else
        'SEA 01/01/2026 00:00:00 -1.01\nSEA 01/01/2026 00:00:20 -1.00\n'))


@pytest.mark.parametrize('series', [
    'SEA FILE /private/unrelated.txt\n',
    'SEA 01/01/2026 00:00:00 -1\nSEA 01/01/2026 00:00:01 -1\n',
    'SEA 01/01/2026 00:00:01 -1\nSEA 01/01/2026 00:00:20 -1\n',
    'SEA 01/01/2026 00:00:00 -1\nSEA 01/01/2026 00:00:00 -1\n',
    'SEA 01/01/2026 00:00:00 NaN\nSEA 01/01/2026 00:00:20 -1\n',
    'SEA 00:00:00 -1\nSEA 00:00:20 -1\n',
    'OTHER 01/01/2026 00:00:00 -1\nOTHER 01/01/2026 00:00:20 -1\n',
])
def test_outfall_series_refuses_external_files_gaps_duplicates_and_unknown_stages(tmp_path, series):
    path,_ = coupled_case(tmp_path/'input')
    time_series_outfall(path, series=series)
    with pytest.raises(ValueError, match='TIMESERIES'):
        load_inputs(path)


def test_outfall_series_requires_explicit_gate_and_no_unused_series(tmp_path):
    path,_ = coupled_case(tmp_path/'input')
    time_series_outfall(path)
    load_inputs(path)
    network=path.parent/'network.inp'
    original=network.read_text()
    network.write_text(original.replace('TIMESERIES SEA NO','TIMESERIES SEA'))
    with pytest.raises(ValueError,match='flap gate'):load_inputs(path)
    network.write_text(original.replace('TIMESERIES SEA NO','FREE NO'))
    with pytest.raises(ValueError,match='exactly the inline TIMESERIES'):load_inputs(path)


@pytest.mark.parametrize('change', ['datum','mapping','prescribed','unknown_node','unsupported_section'])
def test_refuse_unmatched_and_double_counted_inputs(tmp_path,change):
    path,m=coupled_case(tmp_path/'input')
    if change=='datum':m['drainage']['vertical_datum']='different surveyed reference'
    if change=='mapping':m['drainage']['inlets'][0]['x_m']=1e9
    if change=='unknown_node':m['drainage']['overflow_cells']={}
    if change=='unsupported_section':
        with (path.parent/'network.inp').open('a') as f:f.write('\n[FILES]\nUSE HOTSTART /private/other-file\n')
    if change=='prescribed':
        with np.load(path.parent/'forcing.npz') as data:forcing={k:data[k] for k in data.files}
        forcing['drainage_exchange_m3_s'][:]=.01
        np.savez(path.parent/'forcing.npz',**forcing)
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError):load_inputs(path)


requires_swmm=pytest.mark.skipif(not os.environ.get('DYNACITY_SWMM_LIBRARY'),reason='optional pinned SWMM bridge not configured')


@requires_engine
@requires_swmm
def test_native_time_varying_outfall_matches_constant_boundary_then_changes_drainage(tmp_path):
    volumes=[]
    for label,level in [('fixed',None),('constant',.8),('raised',1.2)]:
        path,m=coupled_case(tmp_path/label, initial_surface=.1, initial_sewer=1.8)
        m['drainage']['inlets'][0]['opening_area_m2']=.001
        path.write_text(json.dumps(m))
        network=path.parent/'network.inp'
        network.write_text(network.read_text().replace('P1 CIRCULAR 0.1','P1 CIRCULAR 0.02'))
        if level is None:
            network.write_text(network.read_text().replace('O1 -1.01 FREE NO','O1 -1.01 FIXED 0.8 NO'))
        else:
            time_series_outfall(path,series=f'SEA 01/01/2026 00:00:00 0.8\nSEA 01/01/2026 00:00:20 {level}\n')
        result=run_surface(path,tmp_path/(label+'-result'))
        with np.load(result) as data: volumes.append(float(data['volume_m3'][-1]))
        audit=json.loads((result.parent/'audit.json').read_text())
        assert audit['status']=='complete'
        assert abs(audit['max_combined_mass_residual_m3'])<.01
    assert volumes[1]==pytest.approx(volumes[0],abs=1e-8)
    assert volumes[2]>volumes[1]+.00001


@requires_engine
@requires_swmm
def test_native_engines_drain_and_blocked_inlet_retains_surface_water(tmp_path):
    results=[]
    for label,blocked in [('open',0.),('blocked',1.)]:
        path,_=coupled_case(tmp_path/label,blocked=blocked)
        result=run_surface(path,tmp_path/(label+'-run'))
        with np.load(result) as data:results.append(data['volume_m3'][-1])
        audit=json.loads((result.parent/'audit.json').read_text())
        drainage=json.loads((result.parent/'drainage.json').read_text())
        assert audit['dynamic_drainage_coupling'] is True
        assert abs(audit['max_combined_mass_residual_m3'])<.01
        assert audit['purpose']=='numerical_benchmark'
        assert drainage['frames'][-1]['captured_volume_m3']>=0
    assert results[0]<results[1]-.05
    assert results[1]==pytest.approx(12,abs=1e-7)


@requires_engine
@requires_swmm
def test_pressurised_sewer_returns_water_through_inlet(tmp_path):
    path,m=coupled_case(tmp_path/'input',pipe_cap=.0001,initial_surface=.1,initial_sewer=1.8,step=.1)
    m['drainage']['inlets'][0]['opening_area_m2']=.001
    path.write_text(json.dumps(m))
    # Start the pipe under a consistent, equal-head reservoir boundary; a
    # pressure jump at a free outfall is a different transient benchmark.
    network=path.parent/'network.inp'
    network.write_text(network.read_text().replace('O1 -1.01 FREE NO','O1 -1.01 FIXED 0.8 NO').replace('P1 CIRCULAR 0.1','P1 CIRCULAR 0.02'))
    result=run_surface(path,tmp_path/'result')
    drainage=json.loads((result.parent/'drainage.json').read_text())
    assert drainage['frames'][-1]['backflow_volume_m3']>.01
    assert any(step['inlet_transfer_m3']['I1']<0 for step in drainage['coupling_steps'])
    with np.load(result) as data:assert data['volume_m3'][-1]>data['volume_m3'][0]


@requires_engine
@requires_swmm
def test_overflow_is_returned_to_its_mapped_surface_cell(tmp_path):
    path,m=coupled_case(tmp_path/'input',pipe_cap=.1,duration=60)
    m['drainage']['overflow_cells']['N1']=23
    path.write_text(json.dumps(m))
    network=path.parent/'network.inp'
    network.write_text(network.read_text().replace('N1 -1 3 0.0','N1 -1 1 0.0').replace('P1 CIRCULAR 0.1','P1 CIRCULAR 0.02'))
    result=run_surface(path,tmp_path/'result')
    drainage=json.loads((result.parent/'drainage.json').read_text())
    frame=drainage['frames'][-1]
    assert frame['overflow_return_volume_m3']>.01
    assert frame['flooding']==pytest.approx(frame['overflow_return_volume_m3'],abs=1e-9)
    with np.load(result) as data:
        assert data['volume_m3'][-1]>data['volume_m3'][0]-frame['captured_volume_m3']+.01


@requires_engine
@requires_swmm
def test_bad_native_budget_is_rejected_without_accepted_surface_result(tmp_path):
    path,_=coupled_case(tmp_path/'input')
    network=path.parent/'network.inp'
    # This abrupt, initially dry, long/wide pipe transient violates this
    # fixture's declared volume tolerance in the pinned native engine.
    network.write_text(network.read_text().replace('P1 N1 O1 1 ','P1 N1 O1 10 ').replace('P1 CIRCULAR 0.1','P1 CIRCULAR 0.5'))
    with pytest.raises(RuntimeError,match='conservation failed'):
        run_surface(path,tmp_path/'result')
    assert not (tmp_path/'result/surface.npz').exists()
    audit=json.loads((tmp_path/'result/audit.json').read_text())
    assert audit['status']=='failed'


@requires_engine
@requires_swmm
def test_pipe_conveyance_changes_surface_flooding(tmp_path):
    values=[]
    for label,diameter in [('wider',.1),('restricted',.02)]:
        path,_=coupled_case(tmp_path/label,duration=60)
        network=path.parent/'network.inp'
        network.write_text(network.read_text().replace('N1 -1 3 0.0','N1 -1 1 0.0')
                           .replace('P1 CIRCULAR 0.1',f'P1 CIRCULAR {diameter}'))
        result=run_surface(path,tmp_path/(label+'-result'))
        with np.load(result) as data:surface=float(data['volume_m3'][-1])
        frame=json.loads((result.parent/'drainage.json').read_text())['frames'][-1]
        values.append((surface,frame['overflow_return_volume_m3'],frame['outfall_volume_m3']))
    assert values[1][0]>values[0][0]+.1
    assert values[1][1]>values[0][1]+.1
    assert values[1][2]<values[0][2]-.1


@requires_engine
@requires_swmm
def test_exchange_step_refinement_keeps_volume_result_stable(tmp_path):
    volumes=[]
    for label,step in [('coarse',.1),('fine',.05)]:
        path,_=coupled_case(tmp_path/label,step=step)
        with np.load(run_surface(path,tmp_path/(label+'-result'))) as data:
            volumes.append(float(data['volume_m3'][-1]))
    assert abs(volumes[1]-volumes[0])<.001


@requires_engine
def test_missing_native_library_never_gets_a_fake_drainage_fallback(tmp_path,monkeypatch):
    from dynacity.flood_coupling import runtime_status
    monkeypatch.delenv('DYNACITY_SWMM_LIBRARY',raising=False)
    assert runtime_status()['available'] is False
    path,_=coupled_case(tmp_path/'input')
    with pytest.raises(RuntimeError,match='DYNACITY_SWMM_LIBRARY'):
        run_surface(path,tmp_path/'result')
    assert not (tmp_path/'result/surface.npz').exists()
    assert json.loads((tmp_path/'result/audit.json').read_text())['status']=='failed'
