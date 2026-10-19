"""Mathematical solver benchmarks, never Beirut data or public demo assets."""
import hashlib
import importlib.util
import json

import numpy as np
import pytest

from dynacity.flood_solver import load_inputs, run_surface


def benchmark(tmp_path, *, rain=(0.0,), bed_slope=0.0, dam=False, boundary="reflective", loss=0.0, nx=6):
    # Small analytical rectangular domain; every value is confined to this test.
    ny = 2
    points = np.array([(x*6/nx, y) for y in range(ny+1) for x in range(nx+1)], dtype=float)
    triangles = []
    for y in range(ny):
        for x in range(nx):
            a = y*(nx+1)+x
            triangles.extend([(a, a+1, a+nx+2), (a, a+nx+2, a+nx+1)])
    triangles = np.array(triangles)
    bed = points[triangles, 0]*bed_slope
    stage = np.ones(len(triangles)) if not dam else np.where(points[triangles, 0].mean(axis=1)<3, 1.0, 0.0)
    if any(rain) or loss:
        stage = bed.mean(axis=1).copy()  # initially dry rainfall benchmark
    mesh_path = tmp_path / "mesh.npz"
    np.savez(mesh_path, points_m=points, triangles=triangles, elevation_m=bed,
             friction=np.zeros(len(triangles)), initial_stage_m=stage,
             initial_momentum_m2_s=np.zeros((len(triangles),2)))
    forcing_path = tmp_path / "forcing.npz"
    np.savez(forcing_path, times_s=np.arange(len(rain)+1)*0.1,
             rainfall_m_s=rain, infiltration_capacity_m_s=np.full((len(rain),len(triangles)),loss),
             drainage_exchange_m3_s=np.zeros((len(rain),len(triangles))))
    edges = {}
    for t, triangle in enumerate(triangles):
        for e in range(3):
            key = tuple(sorted((int(triangle[(e+1)%3]),int(triangle[(e+2)%3]))))
            edges.setdefault(key,[]).append((t,e))
    evidence = tmp_path / "analytical-reference.txt"
    evidence.write_text("Analytical numerical benchmark only: closed lake, mass balance and dam release; no Beirut measurements.")
    source = dict(path=str(evidence),sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                  evidence="analytical_benchmark",description="Mathematical numerical benchmark; never city evidence")
    manifest = dict(schema_version=1,run_id="numerical-check",purpose="numerical_benchmark",
                    start="2026-01-01T00:00:00Z",crs="EPSG:32636",vertical_datum="analytical local reference",
                    mesh=str(mesh_path),forcing=str(forcing_path),
                    boundary_edges=[(t,e,"exterior") for pairs in edges.values() if len(pairs)==1 for t,e in pairs],
                    boundaries={"exterior":{"kind":boundary}},
                    sources={k:source for k in ("terrain","mesh","roughness","initial_conditions","rainfall",
                                                "infiltration","drainage_exchange","boundaries","structures")},
                    scope="Numerical benchmark, no city data; no structures or drainage.",
                    numerics=dict(algorithm="DE0",cfl=0.8,max_timestep_s=0.01,minimum_depth_m=1e-6,
                                  mass_absolute_tolerance_m3=1e-8,mass_relative_tolerance=1e-7))
    path = tmp_path / "run.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


requires_engine = pytest.mark.skipif(importlib.util.find_spec("anuga") is None, reason="optional ANUGA runtime absent")


@requires_engine
def test_lake_at_rest_sloping_bed_preserves_stage_and_momentum(tmp_path):
    path,_ = benchmark(tmp_path,bed_slope=0.05)
    result=run_surface(path,tmp_path / "result")
    with np.load(result,allow_pickle=False) as d:
        np.testing.assert_allclose(d["stage_m"],1.0,atol=1e-10)
        np.testing.assert_allclose(d["momentum_m2_s"],0.0,atol=1e-10)
        np.testing.assert_allclose(d["mass_residual_m3"],0.0,atol=1e-10)
    audit=json.loads((result.parent / "audit.json").read_text())
    assert audit["purpose"]=="numerical_benchmark"
    assert audit["status"]=="complete"
    assert audit["dynamic_drainage_coupling"] is False
    assert audit["output_sha256"]==hashlib.sha256(result.read_bytes()).hexdigest()


@requires_engine
def test_rainfall_discontinuities_are_integrated_exactly_on_dry_bed(tmp_path):
    path,_=benchmark(tmp_path,rain=(0.01,0.0,0.02))
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        expected=np.array([0,.001,.001,.003])
        np.testing.assert_allclose(d["depth_m"],np.repeat(expected[:,None],24,axis=1),atol=1e-10)
        np.testing.assert_allclose(d["volume_m3"],expected*12,atol=1e-10)
        np.testing.assert_allclose(d["rainfall_volume_m3"],expected*12,atol=1e-10)


@requires_engine
def test_dam_release_wets_dry_cells_and_conserves_water(tmp_path):
    path,_=benchmark(tmp_path,dam=True)
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        dry=d["depth_m"][0]==0
        assert np.max(d["depth_m"][-1,dry])>0
        assert np.max(np.abs(d["momentum_m2_s"][-1]))>0
        np.testing.assert_allclose(d["volume_m3"],d["volume_m3"][0],rtol=1e-7)


@requires_engine
def test_infiltration_cannot_remove_unavailable_water(tmp_path):
    path,_=benchmark(tmp_path,rain=(0.01,),loss=0.02)
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        np.testing.assert_allclose(d["depth_m"],0,atol=1e-10)
        assert d["infiltration_volume_m3"][-1]==pytest.approx(-.012)


@requires_engine
def test_open_boundary_flux_is_in_native_water_budget(tmp_path):
    path,_=benchmark(tmp_path,dam=True,boundary="transmissive")
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        assert abs(d["boundary_volume_m3"][-1])>0
        np.testing.assert_allclose(d["volume_m3"]-d["volume_m3"][0],
                                   d["boundary_volume_m3"]+d["operator_volume_m3"],atol=1e-8)


@pytest.mark.parametrize("mutation",["missing_source","benchmark_as_real","checksum","unknown_datum","missing_boundary","missing_array","negative_rain","geographic_crs"])
def test_missing_or_invalid_inputs_never_launch_solver(tmp_path,mutation):
    path,m=benchmark(tmp_path)
    if mutation=="missing_source": del m["sources"]["drainage_exchange"]
    if mutation=="benchmark_as_real": m["purpose"]="real_data"
    if mutation=="checksum": m["sources"]["terrain"]["sha256"]="a"*64
    if mutation=="unknown_datum": m["vertical_datum"]="unknown"
    if mutation=="missing_boundary": m["boundary_edges"].pop()
    if mutation=="geographic_crs": m["crs"]="EPSG:4326"
    if mutation in {"missing_array","negative_rain"}:
        with np.load(m["forcing"]) as d: arrays=dict(d)
        if mutation=="missing_array": del arrays["drainage_exchange_m3_s"]
        else: arrays["rainfall_m_s"]=np.array([-1])
        np.savez(m["forcing"],**arrays)
    path.write_text(json.dumps(m))
    with pytest.raises((ValueError,KeyError)):
        run_surface(path,tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


@requires_engine
def test_output_directory_is_never_overwritten(tmp_path):
    path,_=benchmark(tmp_path)
    output=tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError): run_surface(path,output)


def test_stage_boundary_requires_full_observation_coverage(tmp_path):
    path,m=benchmark(tmp_path)
    m["boundaries"]["exterior"]={"kind":"stage_momentum","values":[[1,0,0]]}
    path.write_text(json.dumps(m))
    with pytest.raises(ValueError,match="one stage/momentum"): load_inputs(path)


@requires_engine
def test_dry_dam_release_matches_ritter_depth_profile(tmp_path):
    path,_=benchmark(tmp_path,dam=True,nx=120)
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        x=d["points_m"][d["triangles"],0].mean(axis=1)-3
        t=d["times_s"][-1]
        g=9.8  # ANUGA's documented gravitational constant for this benchmark
        c=np.sqrt(g)
        exact=np.where(x<=-c*t,1.0,np.where(x>=2*c*t,0.0,(2*c-x/t)**2/(9*g)))
        error=np.sum(np.abs(d["depth_m"][-1]-exact)*d["areas_m2"])/12
        assert error<0.02


@requires_engine
def test_impossible_drainage_withdrawal_fails_with_audit_and_no_results(tmp_path):
    path,m=benchmark(tmp_path,rain=(0.01,))
    with np.load(m["forcing"]) as d: arrays=dict(d)
    arrays["drainage_exchange_m3_s"][:]=-1
    np.savez(m["forcing"],**arrays)
    out=tmp_path / "failed-run"
    with pytest.raises(RuntimeError,match="withdrawal"):
        run_surface(path,out)
    assert not (out / "surface.npz").exists()
    assert json.loads((out / "audit.json").read_text())["status"]=="failed"


@requires_engine
def test_time_varying_stage_boundary_runs_with_accounted_flux(tmp_path):
    path,m=benchmark(tmp_path)
    m["boundaries"]["exterior"]={"kind":"stage_momentum","values":[[1,0,0],[1.01,0,0]]}
    path.write_text(json.dumps(m))
    with np.load(run_surface(path,tmp_path / "result"),allow_pickle=False) as d:
        assert d["volume_m3"][-1]>d["volume_m3"][0]
        np.testing.assert_allclose(d["mass_residual_m3"],0,atol=1e-8)
