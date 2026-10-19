from dataclasses import replace
from datetime import timezone
from pathlib import Path

import pytest

from dynacity.flood_drainage import (
    DrainageError, Intervention, build_swmm_input, compare_drainage,
    detect_swmm, parse_continuity, read_node_timeseries, run_drainage,
    synthetic_fixture,
)


def test_si_conversion_rain_end_and_utc():
    n, r = synthetic_fixture()
    text = build_swmm_input(n, r)
    assert 'C1 RG S1 10 70 300 1 0' in text
    assert 'RG INTENSITY 00:05:00 1.0 TIMESERIES RAIN' in text
    assert 'RAIN 01/01/2026 00:30 0' in text
    assert 'END_TIME 01:30:00' in text
    assert 'S1 10 1 0 FUNCTIONAL 0 0 100' in text
    assert 'P1 S1 O1 100 0.013 0 0 0 0.3' in text
    local = replace(r, start=r.start.astimezone(timezone(__import__('datetime').timedelta(hours=3))))
    assert build_swmm_input(n, local) == text


@pytest.mark.parametrize('value', [-0.1, 1.1, float('nan')])
def test_invalid_imperviousness(value):
    n, r = synthetic_fixture()
    with pytest.raises(ValueError):
        build_swmm_input(replace(n, catchments=(replace(n.catchments[0], impervious_fraction=value),)), r)


def test_explicit_evidence_and_connectivity():
    n, r = synthetic_fixture()
    for bad in (replace(n, evidence_kind='osm_inferred'), replace(n, provenance=''),
                replace(n, links=(replace(n.links[0], downstream='missing'),)),
                replace(n, nodes=n.nodes + (replace(n.nodes[0], id='ISOLATED'),))):
        with pytest.raises(ValueError):
            build_swmm_input(bad, r)
    with pytest.raises(ValueError):
        build_swmm_input(n, replace(r, start=r.start.replace(tzinfo=None)))
    with pytest.raises(ValueError):
        build_swmm_input(n, replace(r, intensity_mm_hr=(float('inf'),)))


def test_interventions_are_explicit_and_do_not_mutate_baseline():
    n, r = synthetic_fixture()
    original = build_swmm_input(n, r)
    text = build_swmm_input(n, r, Intervention('blocked', (('P1', 0.1),)))
    assert '0 0 0 0.03' in text
    assert build_swmm_input(n, r) == original
    for case in (Intervention('zero', (('P1', 0),)), Intervention('unknown', (('road', 2),)),
                 Intervention('duplicate', (('P1', 1), ('P1', 2))),
                 Intervention('bad storage', added_storage_m2=(('O1', 2),))):
        with pytest.raises(ValueError):
            build_swmm_input(n, r, case)


def test_unavailable_solver_does_not_compute_or_write(tmp_path):
    n, r = synthetic_fixture()
    assert detect_swmm('/does/not/exist') is None
    with pytest.raises(DrainageError, match='unavailable'):
        run_drainage(n, r, tmp_path / 'no-output', executable='/does/not/exist')
    assert not (tmp_path / 'no-output').exists()


def test_incomplete_report_rejected():
    with pytest.raises(DrainageError):
        parse_continuity('EPA SWMM completed successfully')


solver = detect_swmm()
requires_swmm = pytest.mark.skipif(solver is None, reason='Optional EPA SWMM runtime absent')


@requires_swmm
def test_real_swmm_mass_balance_scenarios_and_node_samples(tmp_path):
    n, r = synthetic_fixture()
    results = compare_drainage(n, r, tmp_path / 'comparison', (
        Intervention('blocked', (('P1', 0.1),)),
        Intervention('improved', (('P1', 4),), (('S1', 500),)),
    ), executable=solver)
    base, blocked, improved = (results[x] for x in ('baseline', 'blocked', 'improved'))
    assert len({x.comparison_key for x in results.values()}) == 1
    assert len({x.input_sha256 for x in results.values()}) == 3
    assert base.runoff_m3 == blocked.runoff_m3 == improved.runoff_m3
    assert blocked.routing_m3['Flooding Loss'] > base.routing_m3['Flooding Loss'] > improved.routing_m3['Flooding Loss']
    assert base.runoff_m3['Total Precipitation'] == pytest.approx(3000, abs=10)
    for result in results.values():
        assert abs(result.runoff_continuity_error_pct) < 0.5
        assert abs(result.routing_continuity_error_pct) < 0.5
        runoff = result.runoff_m3
        assert runoff['Total Precipitation'] == pytest.approx(sum(runoff[k] for k in ('Infiltration Loss', 'Surface Runoff', 'Final Storage')), abs=30)
        route = result.routing_m3
        assert route['Wet Weather Inflow'] == pytest.approx(sum(route[k] for k in ('External Outflow', 'Flooding Loss', 'Final Stored Volume')), abs=30)
        series = read_node_timeseries(result.report_path)
        assert set(series) == {'S1', 'O1'}
        assert len(series['S1']) == 90
        assert series['S1'][0].timestamp.utcoffset().total_seconds() == 0
        assert series['S1'][-1].timestamp > series['S1'][0].timestamp
    assert max(x.overflow_m3_s for x in read_node_timeseries(improved.report_path)['S1']) == 0
    # Existing directories are never overwritten, even for an identical run.
    with pytest.raises(FileExistsError):
        run_drainage(n, r, Path(base.report_path).parent, executable=solver)


@requires_swmm
def test_zero_rain_and_pervious_infiltration(tmp_path):
    n, r = synthetic_fixture()
    dry = run_drainage(n, replace(r, intensity_mm_hr=(0.0,) * 6), tmp_path / 'dry', executable=solver)
    assert all(v == 0 for v in dry.runoff_m3.values())
    pervious = replace(n, catchments=(replace(n.catchments[0], impervious_fraction=0),))
    a = run_drainage(n, r, tmp_path / 'mixed', executable=solver)
    b = run_drainage(pervious, r, tmp_path / 'pervious', executable=solver)
    assert b.runoff_m3['Infiltration Loss'] > a.runoff_m3['Infiltration Loss']
    assert b.runoff_m3['Surface Runoff'] < a.runoff_m3['Surface Runoff']


@requires_swmm
def test_continuity_gate_and_timestep_sensitivity(tmp_path):
    n, r = synthetic_fixture()
    with pytest.raises(DrainageError, match='Continuity exceeds'):
        run_drainage(n, r, tmp_path / 'strict', executable=solver, continuity_tolerance_pct=0.001)
    a = run_drainage(n, r, tmp_path / 'step5', executable=solver)
    b = run_drainage(n, r, tmp_path / 'step2', executable=solver, routing_step_s=2)
    assert a.comparison_key != b.comparison_key
    assert b.routing_m3['Flooding Loss'] == pytest.approx(a.routing_m3['Flooding Loss'], rel=0.05)


def test_solver_report_error_rejected_even_with_zero_process_status(tmp_path, monkeypatch):
    import subprocess
    import sys
    from dynacity import flood_drainage

    def failed_run(args, **kwargs):
        Path(args[2]).write_text('ERROR 200: invalid model')
        return subprocess.CompletedProcess(args, 0, '', '')

    monkeypatch.setattr(flood_drainage.subprocess, 'run', failed_run)
    n, r = synthetic_fixture()
    with pytest.raises(DrainageError, match='SWMM failed'):
        run_drainage(n, r, tmp_path / 'failed', executable=sys.executable)
    assert not (tmp_path / 'failed' / 'result.json').exists()


def test_solver_timeout_is_explicit(tmp_path, monkeypatch):
    import subprocess
    import sys
    from dynacity import flood_drainage

    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    monkeypatch.setattr(flood_drainage.subprocess, 'run', timed_out)
    n, r = synthetic_fixture()
    with pytest.raises(DrainageError, match='execution failed'):
        run_drainage(n, r, tmp_path / 'timeout', executable=sys.executable)
    assert not (tmp_path / 'timeout' / 'result.json').exists()
