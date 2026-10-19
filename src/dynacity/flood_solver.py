"""ANUGA shallow-water execution with explicit inputs and native mesh accounting.

No default terrain, rainfall, drains, roughness, initial water or boundary data.
Run via ``python -m dynacity.flood_solver --manifest ... --output-dir ...``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .flood_coupling import Coupling


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Record):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: Literal["measured", "derived", "analytical_benchmark"]
    description: str = Field(min_length=1)


class Boundary(Record):
    kind: Literal["reflective", "transmissive", "stage_momentum"]
    # [stage m in the terrain datum, x momentum m2/s, y momentum m2/s]
    values: list[tuple[float, float, float]] | None = None


class Numerics(Record):
    algorithm: Literal["DE0", "DE1", "DE2"]
    cfl: float = Field(gt=0, le=1)
    max_timestep_s: float = Field(gt=0)
    minimum_depth_m: float = Field(gt=0)
    mass_absolute_tolerance_m3: float = Field(gt=0)
    mass_relative_tolerance: float = Field(gt=0, lt=1)


class RunManifest(Record):
    schema_version: Literal[1]
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    purpose: Literal["real_data", "numerical_benchmark"]
    start: datetime
    crs: str = Field(min_length=1)
    vertical_datum: str = Field(min_length=1)
    mesh: str = Field(min_length=1)
    forcing: str = Field(min_length=1)
    # Exactly every external triangle edge: [triangle index, local edge, tag].
    boundary_edges: list[tuple[int, int, str]] = Field(min_length=1)
    boundaries: dict[str, Boundary]
    sources: dict[str, Source]
    # Explicitly records exclusions, data limitations and structure treatment.
    scope: str = Field(min_length=1)
    numerics: Numerics
    drainage: Coupling | None = None

    @model_validator(mode="after")
    def complete(self):
        if self.start.utcoffset() is None:
            raise ValueError("start requires a timezone")
        if self.vertical_datum.strip().lower() in {"unknown", "missing", "unspecified"}:
            raise ValueError("a documented vertical datum is required")
        required = {"terrain", "mesh", "roughness", "initial_conditions", "rainfall",
                    "infiltration", "drainage_exchange", "boundaries", "structures"}
        if self.sources.keys() != required:
            raise ValueError(f"sources must declare exactly {sorted(required)}")
        if self.purpose == "real_data" and any(s.evidence == "analytical_benchmark" for s in self.sources.values()):
            raise ValueError("analytical benchmarks cannot be used as real-data inputs")
        if {t for _, _, t in self.boundary_edges} != self.boundaries.keys():
            raise ValueError("every boundary tag requires an explicit boundary condition")
        return self


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _array(data, name, shape, *, nonnegative=False):
    a = np.array(data[name], dtype=float, copy=True)
    if a.shape != shape or not np.isfinite(a).all():
        raise ValueError(f"{name} must have finite shape {shape}; missing values are not zero")
    if nonnegative and (a < 0).any():
        raise ValueError(f"{name} must be nonnegative")
    return a


def load_inputs(path: str | Path):
    """Validate all input files before importing or launching the engine."""
    path = Path(path).resolve()
    manifest = RunManifest.model_validate_json(path.read_text())
    from pyproj import CRS
    crs = CRS.from_user_input(manifest.crs)
    if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-12 for a in crs.axis_info[:2]):
        raise ValueError("projected metre CRS required")
    hashes = {}
    for name, source in manifest.sources.items():
        file = (path.parent / source.path).resolve()
        if not file.is_file() or _hash(file) != source.sha256:
            raise ValueError(f"missing or checksum-mismatched {name} source: {file}")
        hashes[name] = source.sha256
    mesh_path = (path.parent / manifest.mesh).resolve()
    forcing_path = (path.parent / manifest.forcing).resolve()
    # Hash the actual numerical arrays as well as upstream evidence files.
    hashes.update(mesh_file=_hash(mesh_path), forcing_file=_hash(forcing_path), manifest=_hash(path))
    with np.load(mesh_path, allow_pickle=False) as data:
        points = np.array(data["points_m"], dtype=float, copy=True)
        raw = data["triangles"]
        if raw.dtype.kind not in "iu" or raw.ndim != 2 or raw.shape[1] != 3 or not len(raw):
            raise ValueError("triangles must be a nonempty integer [triangle,3] array")
        triangles = raw.astype(np.int64, copy=True)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError("points_m requires finite [vertex,2] coordinates")
        if (triangles < 0).any() or (triangles >= len(points)).any():
            raise ValueError("triangle vertex index outside points_m")
        if len(np.unique(points, axis=0)) != len(points):
            raise ValueError("duplicate mesh vertices")
        if len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
            raise ValueError("duplicate triangles")
        vertices = points[triangles]
        a, b = vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
        twice_area = a[:, 0]*b[:, 1] - a[:, 1]*b[:, 0]
        if (twice_area <= 0).any():
            raise ValueError("triangles must be nondegenerate and counterclockwise")
        n = len(triangles)
        mesh = {"points_m": points, "triangles": triangles, "areas_m2": twice_area/2,
                "elevation_m": _array(data, "elevation_m", (n, 3)),
                "friction": _array(data, "friction", (n,), nonnegative=True),
                "initial_stage_m": _array(data, "initial_stage_m", (n,)),
                "initial_momentum_m2_s": _array(data, "initial_momentum_m2_s", (n, 2))}
        if (mesh["initial_stage_m"] < mesh["elevation_m"].mean(axis=1)).any():
            raise ValueError("initial stage below triangle mean bed")
        dry = mesh["initial_stage_m"] == mesh["elevation_m"].mean(axis=1)
        if np.any(mesh["initial_momentum_m2_s"][dry] != 0):
            raise ValueError("dry initial cells cannot carry momentum")
    edges = {}
    for ti, triangle in enumerate(triangles):
        for ei in range(3):
            key = tuple(sorted((int(triangle[(ei+1) % 3]), int(triangle[(ei+2) % 3]))))
            edges.setdefault(key, []).append((ti, ei))
    if any(len(v) > 2 for v in edges.values()):
        raise ValueError("nonmanifold mesh edge")
    exterior = {v[0] for v in edges.values() if len(v) == 1}
    supplied = {(ti, ei): tag for ti, ei, tag in manifest.boundary_edges}
    if len(supplied) != len(manifest.boundary_edges) or supplied.keys() != exterior:
        raise ValueError("boundary_edges must identify every external edge exactly once")
    # Check overlap/crossing topology; no implicit mesh repair or guessed holes.
    from matplotlib.tri import Triangulation
    Triangulation(points[:, 0], points[:, 1], triangles).get_trifinder()
    with np.load(forcing_path, allow_pickle=False) as data:
        times = np.array(data["times_s"], dtype=float, copy=True)
        if times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all() or times[0] != 0 or (np.diff(times) <= 0).any():
            raise ValueError("times_s must start at 0 and increase strictly")
        k = len(times)-1
        forcing = {"times_s": times,
                   "rainfall_m_s": _array(data, "rainfall_m_s", (k,), nonnegative=True),
                   "infiltration_capacity_m_s": _array(data, "infiltration_capacity_m_s", (k, n), nonnegative=True),
                   "drainage_exchange_m3_s": _array(data, "drainage_exchange_m3_s", (k, n))}
    for tag, condition in manifest.boundaries.items():
        if condition.kind == "stage_momentum":
            if condition.values is None or len(condition.values) != len(times):
                raise ValueError(f"boundary {tag} needs one stage/momentum triple per times_s")
        elif condition.values is not None:
            raise ValueError(f"boundary {tag} cannot silently ignore supplied values")
    if manifest.drainage is not None:
        from .flood_coupling import validate_coupling
        network_path = (path.parent / manifest.drainage.network).resolve()
        validate_coupling(manifest.drainage,manifest,mesh,forcing,network_path)
        hashes['drainage_network_file'] = _hash(network_path)
    return manifest, mesh, forcing, hashes


def run_surface(manifest_path: str | Path, output_dir: str | Path) -> Path:
    """Run native ANUGA, fail on numerical errors, save audited mesh snapshots.

    All forcing intervals are [t[i],t[i+1]); evolve ends at each discontinuity.
    Infiltration is supplied capacity capped by available water, not a soil model.
    Optional SWMM two-way coupling replaces prescribed exchange when supplied.
    """
    m, mesh, forcing, hashes = load_inputs(manifest_path)
    output_dir = Path(output_dir).resolve()
    repo = Path(__file__).resolve().parents[2]
    if output_dir == repo or repo in output_dir.parents:
        raise ValueError("hydraulic run outputs must be outside the repository")
    import anuga
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "manifest.json").write_text(m.model_dump_json(indent=2))
    audit = {"status": "running", "engine": "ANUGA", "version": importlib.metadata.version("anuga"),
             "input_sha256": hashes, "purpose": m.purpose, "calibration": "not_established",
             "dynamic_drainage_coupling": m.drainage is not None, "scope": m.scope,
             "warnings": ["Input provenance is supplied by the producer; checksums do not authenticate measurements.",
                          "Native mesh results; no independently validated Beirut forecast is established.",
                          "Explicit SWMM/surface splitting; timestep and inlet-law calibration require review." if m.drainage else "Prescribed drainage fluxes; no dynamic sewer/surface feedback."]}
    sewer = None
    try:
        # Local coordinates avoid large-coordinate cancellation. Preserve absolute vertices in output.
        origin = mesh["points_m"].min(axis=0)
        domain = anuga.Domain(mesh["points_m"]-origin, mesh["triangles"], use_inscribed_circle=True,
                              boundary={(t,e): tag for t,e,tag in m.boundary_edges})
        audit["timestep_radius"] = "triangle inscribed circle"
        domain.set_flow_algorithm(m.numerics.algorithm)
        domain.set_cfl(m.numerics.cfl)
        domain.set_evolve_max_timestep(m.numerics.max_timestep_s)
        domain.set_minimum_allowed_height(m.numerics.minimum_depth_m)
        domain.set_store(False)
        domain.set_quantity("elevation", mesh["elevation_m"], location="vertices")
        for name, values in (("friction", mesh["friction"]), ("stage", mesh["initial_stage_m"]),
                             ("xmomentum", mesh["initial_momentum_m2_s"][:,0]),
                             ("ymomentum", mesh["initial_momentum_m2_s"][:,1])):
            domain.set_quantity(name, values, location="centroids")
        conditions = {}
        times = forcing["times_s"]
        for tag, bc in m.boundaries.items():
            if bc.kind == "reflective":
                conditions[tag] = anuga.Reflective_boundary(domain)
            elif bc.kind == "transmissive":
                conditions[tag] = anuga.Transmissive_boundary(domain)
            else:
                vals = np.asarray(bc.values)
                def interpolate(t, values=vals):
                    if t < times[0] or t > times[-1]+1e-8:
                        raise ValueError("boundary time outside supplied observations")
                    return [float(np.interp(t, times, values[:,j])) for j in range(3)]
                conditions[tag] = anuga.Time_boundary(domain, function=interpolate)
        domain.set_boundary(conditions)
        rain = anuga.Rate_operator(domain, rate=0.0)
        infiltration = anuga.Rate_operator(domain, rate=0.0)
        class PrescribedDrainage(anuga.Rate_operator):
            def __call__(self):
                # Reject impossible individual withdrawals before ANUGA caps them.
                # A net volume check alone could conceal cancelling inlet/outlet errors.
                depth = self.stage_c-self.elev_c
                request = np.asarray(self.rate)*self.domain.get_timestep()
                if np.any(request < -np.maximum(depth, 0)-1e-12):
                    raise RuntimeError("prescribed drainage withdrawal exceeds available cell water; coupling is required")
                super().__call__()
        drainage = PrescribedDrainage(domain, rate=0.0)
        sewer_frames, sewer_steps = [], []
        captured = backflow = overflow_return = 0.0
        if m.drainage:
            from .flood_coupling import Sewer
            sewer = Sewer((Path(manifest_path).resolve().parent / m.drainage.network).resolve(), output_dir)
            audit.update(drainage_engine='EPA SWMM 5.2.4', drainage_library_sha256=sewer.library_sha256,
                         coupling_step_s=m.drainage.step_s,
                         inlet_law='minimum of supplied weir/orifice capacities, bidirectional head difference, explicit blockage',
                         exchange_momentum='withdrawal removes proportional surface momentum; return is vertical with zero added horizontal momentum')
            previous_flood = {node:sewer.flood(node) for node in m.drainage.overflow_cells}
        stages, momenta, volumes, boundary_volumes, operator_volumes, residuals = [], [], [], [], [], []
        rain_volumes, infiltration_volumes, drainage_volumes = [], [], []
        initial_volume = float(domain.get_water_volume())

        def capture():
            stage = domain.quantities["stage"].centroid_values.copy()
            momentum = np.column_stack([domain.quantities[q].centroid_values for q in ("xmomentum", "ymomentum")])
            volume = float(domain.get_water_volume())
            boundary_volume = float(domain.get_boundary_flux_integral())
            operator_volume = float(domain.get_fractional_step_volume_integral())
            residual = volume-initial_volume-boundary_volume-operator_volume
            if not np.isfinite([volume, boundary_volume, operator_volume, residual]).all() or not np.isfinite(stage).all() or not np.isfinite(momentum).all():
                raise RuntimeError("nonfinite hydraulic state")
            depth = stage-mesh["elevation_m"].mean(axis=1)
            if depth.min() < -m.numerics.minimum_depth_m:
                raise RuntimeError("negative hydraulic depth exceeds numerical tolerance")
            scale = max(initial_volume, volume, abs(boundary_volume),
                        abs(rain.cumulative_influx)+abs(infiltration.cumulative_influx)+abs(drainage.cumulative_influx))
            if abs(residual) > m.numerics.mass_absolute_tolerance_m3 + m.numerics.mass_relative_tolerance*scale:
                raise RuntimeError(f"native water conservation failed: residual {residual} m3")
            stages.append(stage); momenta.append(momentum); volumes.append(volume)
            boundary_volumes.append(boundary_volume); operator_volumes.append(operator_volume); residuals.append(residual)
            rain_volumes.append(rain.cumulative_influx)
            infiltration_volumes.append(infiltration.cumulative_influx)
            drainage_volumes.append(drainage.cumulative_influx)
            if sewer:
                totals = sewer.totals()
                outfall = totals['outflow'] - backflow
                combined = (volume+totals['storage']-initial_volume-sewer.initial_storage
                            -boundary_volume-rain.cumulative_influx-infiltration.cumulative_influx
                            +outfall+totals['losses'])
                sewer_frames.append({**totals, 'outfall_volume_m3':outfall,
                                     'captured_volume_m3':captured,'backflow_volume_m3':backflow,
                                     'overflow_return_volume_m3':overflow_return,'combined_residual_m3':combined,
                                     'heads_m':{node:sewer.head(node) for node in m.drainage.overflow_cells}})
            progress = {"status": "running", "simulation_time_s": float(domain.get_time()),
                        "duration_s": float(times[-1]), "completed_frames": len(stages),
                        "total_frames": len(times), "volume_m3": volume, "mass_residual_m3": residual}
            pending = output_dir / "progress.pending.json"
            pending.write_text(json.dumps(progress, allow_nan=False))
            pending.replace(output_dir / "progress.json")

        capture()
        last_progress_update = time.monotonic()
        for i, end in enumerate(times[1:]):
            rain.set_rate(float(forcing["rainfall_m_s"][i]))
            infiltration.set_rate(-forcing["infiltration_capacity_m_s"][i])
            if sewer is None:
                drainage.set_rate(forcing["drainage_exchange_m3_s"][i]/mesh["areas_m2"])
                for _ in domain.evolve(yieldstep=float(end-times[i]), finaltime=float(end), skip_initial_step=True):
                    pass
            else:
                from .flood_coupling import exchange_volumes, apply_surface_volumes
                spec = m.drainage
                while domain.get_time() < end-1e-8:
                    start = float(domain.get_time())
                    stop = start + spec.step_s
                    values = exchange_volumes(spec,domain.quantities['stage'].centroid_values,
                                              mesh['elevation_m'].mean(axis=1),mesh['areas_m2'],sewer,anuga.g)
                    surface = np.zeros(len(mesh['triangles']))
                    rates = {node:0. for node in spec.overflow_cells}
                    for inlet, value in zip(spec.inlets,values):
                        surface[inlet.cell] -= value
                        rates[inlet.node] += value/spec.step_s
                    apply_surface_volumes(domain,surface)
                    drainage.cumulative_influx += float(surface.sum())
                    # SWMM sees the same signed, water-limited exchange once.
                    clock = sewer.advance(rates,spec.step_s)
                    if abs(clock-stop)>1e-6: raise RuntimeError('surface/sewer clocks diverged')
                    for _ in domain.evolve(yieldstep=spec.step_s,finaltime=stop,skip_initial_step=True): pass
                    # Multiple inlets at a shared sewer node can oppose each
                    # other. SWMM receives their NET rate; budget that same rate.
                    net_values = np.array(list(rates.values()))*spec.step_s
                    captured += float(np.maximum(net_values,0).sum())
                    backflow += float(-np.minimum(net_values,0).sum())
                    returned = np.zeros(len(mesh['triangles']))
                    for node, cell in spec.overflow_cells.items():
                        flooded = sewer.flood(node)
                        delta = flooded-previous_flood[node]
                        if delta < -1e-10: raise RuntimeError('sewer flooding total decreased')
                        returned[cell] += max(delta,0)
                        previous_flood[node] = flooded
                    apply_surface_volumes(domain,returned)
                    drainage.cumulative_influx += float(returned.sum())
                    overflow_return += float(returned.sum())
                    total = sewer.totals()
                    if not np.isfinite(list(total.values())).all() or total['storage'] < -1e-10:
                        raise RuntimeError('nonfinite or negative native sewer hydraulic storage')
                    # Read native integrated volumes, never endpoint flow * interval.
                    sewer_residual = (total['storage']-sewer.initial_storage-total['external']
                                      +total['outflow']+total['losses']+total['flooding'])
                    delivered_residual = total['external']-captured
                    flooding_residual = total['flooding']-overflow_return
                    outfall = total['outflow']-backflow
                    combined = (domain.get_water_volume()+total['storage']-initial_volume-sewer.initial_storage
                                -domain.get_boundary_flux_integral()-rain.cumulative_influx-infiltration.cumulative_influx
                                +outfall+total['losses'])
                    scale = max(initial_volume+sewer.initial_storage,captured,backflow,overflow_return,
                                abs(outfall),total['storage'],domain.get_water_volume(),1e-30)
                    tolerance = spec.mass_absolute_tolerance_m3+spec.mass_relative_tolerance*scale
                    sewer_scale = max(sewer.initial_storage,total['storage'],captured,backflow,
                                      abs(outfall),overflow_return,1e-30)
                    sewer_tolerance = spec.mass_absolute_tolerance_m3+spec.mass_relative_tolerance*sewer_scale
                    # A large surface lake must not hide a bad small-pipe budget.
                    if max(abs(sewer_residual),abs(delivered_residual),abs(flooding_residual))>sewer_tolerance or abs(combined)>tolerance:
                        raise RuntimeError(f'coupled water conservation failed at {stop}s: sewer={sewer_residual}, delivered={delivered_residual}, overflow={flooding_residual}, combined={combined} m3')
                    sewer_steps.append({'time_s':stop,'captured_volume_m3':captured,'backflow_volume_m3':backflow,
                                        'inlet_transfer_m3':{inlet.id:float(v) for inlet,v in zip(spec.inlets,values)},
                                        'overflow_return_volume_m3':overflow_return,'outfall_volume_m3':outfall,
                                        'sewer_storage_m3':total['storage'],'sewer_residual_m3':sewer_residual,
                                        'exchange_delivery_residual_m3':delivered_residual,
                                        'overflow_delivery_residual_m3':flooding_residual,
                                        'combined_residual_m3':combined})
                    if time.monotonic()-last_progress_update >= 1:
                        pending = output_dir / 'progress.pending.json'
                        pending.write_text(json.dumps({'status':'running','simulation_time_s':stop,
                            'duration_s':float(times[-1]),'completed_frames':len(stages),'total_frames':len(times),
                            'volume_m3':float(domain.get_water_volume()),'combined_mass_residual_m3':combined,
                            'mass_residual_m3':float(domain.get_water_volume()-initial_volume-
                                domain.get_boundary_flux_integral()-domain.get_fractional_step_volume_integral())},allow_nan=False))
                        pending.replace(output_dir/'progress.json')
                        last_progress_update=time.monotonic()
            capture()
        # A prescribed withdrawal must be delivered, not silently reduced when a cell dries.
        requested_exchange = np.concatenate(([0.0], np.cumsum(np.sum(forcing["drainage_exchange_m3_s"],axis=1)*np.diff(times))))
        if not sewer and not np.allclose(drainage_volumes, requested_exchange, rtol=1e-9, atol=m.numerics.mass_absolute_tolerance_m3):
            raise RuntimeError("prescribed drainage withdrawals exceed available surface water; coupling is required")
        depth = np.maximum(np.asarray(stages)-mesh["elevation_m"].mean(axis=1), 0)
        velocity = np.full((*depth.shape, 2), np.nan)
        np.divide(np.asarray(momenta), depth[...,None], out=velocity,
                  where=(depth > m.numerics.minimum_depth_m)[...,None])
        audit.update(status="complete", timestamps=[(m.start+timedelta(seconds=float(t))).isoformat() for t in times],
                     crs=m.crs, vertical_datum=m.vertical_datum, run_id=m.run_id,
                     numerics=m.numerics.model_dump(),
                     velocity_definition="centroid momentum/depth; dry and numerically shallow cells have undefined (NaN) velocity",
                     max_mass_residual_m3=float(np.max(np.abs(residuals))))
        artifact = output_dir / "surface.npz"
        if sewer:
            coupling_result = output_dir / 'drainage.json'
            coupling_result.write_text(json.dumps({'engine':'EPA SWMM 5.2.4', 'purpose':m.purpose,
                'frames':sewer_frames,'coupling_steps':sewer_steps},indent=2,allow_nan=False))
            audit.update(drainage_output_sha256=_hash(coupling_result),
                         max_sewer_mass_residual_m3=max(abs(s['sewer_residual_m3']) for s in sewer_steps),
                         max_exchange_delivery_residual_m3=max(abs(s['exchange_delivery_residual_m3']) for s in sewer_steps),
                         max_combined_mass_residual_m3=max(abs(s['combined_residual_m3']) for s in sewer_steps))
        np.savez_compressed(artifact, points_m=mesh["points_m"], triangles=mesh["triangles"],
                            elevation_m=mesh["elevation_m"], areas_m2=mesh["areas_m2"], times_s=times,
                            stage_m=np.asarray(stages), depth_m=depth, momentum_m2_s=np.asarray(momenta), velocity_m_s=velocity,
                            volume_m3=volumes, boundary_volume_m3=boundary_volumes,
                            operator_volume_m3=operator_volumes, mass_residual_m3=residuals,
                            rainfall_volume_m3=rain_volumes, infiltration_volume_m3=infiltration_volumes,
                            drainage_volume_m3=drainage_volumes, metadata_json=np.array(json.dumps(audit)))
        audit["output_sha256"] = _hash(artifact)
        return artifact
    except Exception as exc:
        audit.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            if sewer: sewer.close()
        finally:
            (output_dir / "audit.json").write_text(json.dumps(audit, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True, help="new directory outside Git")
    args = parser.parse_args()
    print(run_surface(args.manifest, args.output_dir))


if __name__ == "__main__":
    main()
