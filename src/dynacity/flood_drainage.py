"""Explicit SI drainage networks → EPA SWMM 5.2. No inferred infrastructure.

The adapter solves 1D runoff/drainage only, not a surface inundation map.
All fixture parameters are synthetic assumptions, never Beirut calibration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess


class DrainageError(RuntimeError):
    """Solver unavailable, invalid output, or unacceptable continuity."""


@dataclass(frozen=True)
class Catchment:
    id: str
    outlet: str
    area_m2: float
    impervious_fraction: float
    width_m: float
    slope_fraction: float
    suction_mm: float
    conductivity_mm_hr: float
    moisture_deficit: float
    n_impervious: float = 0.015
    n_pervious: float = 0.15
    depression_impervious_mm: float = 1.0
    depression_pervious_mm: float = 3.0


@dataclass(frozen=True)
class Node:
    id: str
    invert_m: float
    max_depth_m: float
    storage_area_m2: float = 0.0  # constant area, initially empty
    outfall: bool = False  # free boundary, no tide/backwater


@dataclass(frozen=True)
class Link:
    id: str
    upstream: str
    downstream: str
    length_m: float
    diameter_m: float
    roughness: float
    max_flow_m3_s: float  # explicit flow cap; 0 would mean unlimited in SWMM


@dataclass(frozen=True)
class DrainageNetwork:
    catchments: tuple[Catchment, ...]
    nodes: tuple[Node, ...]
    links: tuple[Link, ...]
    evidence_kind: str  # supplied_survey | synthetic_fixture
    provenance: str
    vertical_datum: str
    crs: str  # metadata only: this adapter has no geometry


@dataclass(frozen=True)
class Rainfall:
    start: datetime  # timezone aware; serialized as UTC wall time for SWMM
    interval_s: int
    intensity_mm_hr: tuple[float, ...]  # constant intensity in each interval
    provenance: str


@dataclass(frozen=True)
class Intervention:
    name: str
    capacity_factors: tuple[tuple[str, float], ...] = ()
    added_storage_m2: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class DrainageResult:
    scenario: str
    comparison_key: str
    input_sha256: str
    engine: str
    evidence_kind: str
    provenance: str
    report_path: str
    runoff_m3: dict[str, float]
    routing_m3: dict[str, float]
    runoff_continuity_error_pct: float
    routing_continuity_error_pct: float
    warnings: tuple[str, ...]


def _positive(value: float, label: str, zero: bool = False) -> None:
    if not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f'{label} must be finite and {"nonnegative" if zero else "positive"}')


def _fraction(value: float, label: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'{label} must lie in [0, 1]')


def validate(network: DrainageNetwork, rain: Rainfall) -> None:
    if network.evidence_kind not in {'supplied_survey', 'synthetic_fixture'}:
        raise ValueError('Declare supplied_survey or synthetic_fixture evidence')
    if not all(x.strip() for x in (network.provenance, network.vertical_datum, network.crs, rain.provenance)):
        raise ValueError('Provenance, CRS and vertical datum must be explicit (unknown is allowed)')
    if not network.catchments or not network.links or not any(n.outfall for n in network.nodes):
        raise ValueError('Catchments, links and an explicit outfall are required')
    for group in (network.nodes, network.links, network.catchments):
        ids = [obj.id for obj in group]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,30}', i) for i in ids):
            raise ValueError('IDs must be unique safe SWMM identifiers within each object type')
    nodes = {n.id: n for n in network.nodes}
    for n in network.nodes:
        if not math.isfinite(n.invert_m):
            raise ValueError('Invalid invert elevation')
        _positive(n.max_depth_m, 'max_depth_m', zero=n.outfall)
        _positive(n.storage_area_m2, 'storage_area_m2', zero=True)
        if n.outfall and n.storage_area_m2:
            raise ValueError('Outfall cannot be storage')
    for c in network.catchments:
        if c.outlet not in nodes or nodes[c.outlet].outfall:
            raise ValueError('Catchment must discharge to a non-outfall node')
        for key in ('area_m2', 'width_m', 'slope_fraction', 'suction_mm', 'conductivity_mm_hr', 'n_impervious', 'n_pervious'):
            _positive(getattr(c, key), key)
        for key in ('impervious_fraction', 'moisture_deficit'):
            _fraction(getattr(c, key), key)
        for key in ('depression_impervious_mm', 'depression_pervious_mm'):
            _positive(getattr(c, key), key, zero=True)
    for link in network.links:
        if link.upstream not in nodes or link.downstream not in nodes or link.upstream == link.downstream:
            raise ValueError('Invalid link endpoints')
        if nodes[link.upstream].outfall:
            raise ValueError('Outfall cannot be upstream')
        for key in ('length_m', 'diameter_m', 'roughness', 'max_flow_m3_s'):
            _positive(getattr(link, key), key)
    # Every non-outfall must have a directed path to a boundary.
    reached = {n.id for n in network.nodes if n.outfall}
    while True:
        expanded = reached | {l.upstream for l in network.links if l.downstream in reached}
        if expanded == reached:
            break
        reached = expanded
    if set(nodes) != reached:
        raise ValueError('Every node needs a directed path to an outfall')
    if rain.start.tzinfo is None or rain.start.utcoffset() is None:
        raise ValueError('Rainfall start must be timezone aware')
    if not isinstance(rain.interval_s, int) or rain.interval_s <= 0 or rain.interval_s % 60 or rain.interval_s >= 86400:
        raise ValueError('Rainfall interval must be whole minutes, between 60 and 86340 seconds')
    if not rain.intensity_mm_hr:
        raise ValueError('Rainfall cannot be empty')
    for v in rain.intensity_mm_hr:
        _positive(v, 'rain intensity', zero=True)
    if rain.start.second or rain.start.microsecond:
        raise ValueError('Rainfall start must be on a whole minute')


def _clock(seconds: int) -> str:
    return f'{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}'


def build_swmm_input(network: DrainageNetwork, rain: Rainfall,
                     intervention: Intervention = Intervention('baseline'), *,
                     drain_down_s: int = 3600, report_step_s: int = 60,
                     routing_step_s: int = 5) -> str:
    """Generate auditable SWMM INP; seconds, SI units, UTC simulation clock."""
    validate(network, rain)
    for value, label in ((drain_down_s, 'drain_down_s'), (report_step_s, 'report_step_s'), (routing_step_s, 'routing_step_s')):
        if not isinstance(value, int) or not 0 < value < 86400:
            raise ValueError(f'{label} must be an integer in [1, 86399]')
    if routing_step_s > min(report_step_s, rain.interval_s):
        raise ValueError('Routing step exceeds reporting/rainfall step')
    capacities, storage = dict(intervention.capacity_factors), dict(intervention.added_storage_m2)
    if len(capacities) != len(intervention.capacity_factors) or len(storage) != len(intervention.added_storage_m2):
        raise ValueError('Duplicate intervention target')
    if set(capacities) - {l.id for l in network.links} or set(storage) - {n.id for n in network.nodes if not n.outfall}:
        raise ValueError('Unknown or ineligible intervention target')
    for v in capacities.values():
        _positive(v, 'capacity factor (exact closure is unsupported)')
    for v in storage.values():
        _positive(v, 'added storage', zero=True)
    start = rain.start.astimezone(timezone.utc)
    end = start + timedelta(seconds=len(rain.intensity_mm_hr) * rain.interval_s + drain_down_s)
    sections: dict[str, list[str]] = {
        'TITLE': ['DYNAcity explicit drainage network; see external provenance manifest'],
        'OPTIONS': ['FLOW_UNITS CMS', 'INFILTRATION GREEN_AMPT', 'FLOW_ROUTING DYNWAVE',
                    'ALLOW_PONDING NO', 'LINK_OFFSETS DEPTH', 'START_DATE ' + start.strftime('%m/%d/%Y'),
                    'START_TIME ' + start.strftime('%H:%M:%S'), 'REPORT_START_DATE ' + start.strftime('%m/%d/%Y'),
                    'REPORT_START_TIME ' + start.strftime('%H:%M:%S'), 'END_DATE ' + end.strftime('%m/%d/%Y'),
                    'END_TIME ' + end.strftime('%H:%M:%S'), 'REPORT_STEP ' + _clock(report_step_s),
                    'WET_STEP ' + _clock(min(60, rain.interval_s)), 'DRY_STEP 00:01:00',
                    'ROUTING_STEP ' + str(routing_step_s)],
        'EVAPORATION': ['CONSTANT 0'],
        'RAINGAGES': [f'RG INTENSITY {_clock(rain.interval_s)} 1.0 TIMESERIES RAIN'],
        'SUBCATCHMENTS': [], 'SUBAREAS': [], 'INFILTRATION': [],
        'JUNCTIONS': [], 'STORAGE': [], 'OUTFALLS': [], 'CONDUITS': [], 'XSECTIONS': [],
        'TIMESERIES': [], 'REPORT': ['CONTINUITY YES', 'FLOWSTATS YES', 'SUBCATCHMENTS ALL', 'NODES ALL', 'LINKS ALL'],
    }
    for c in network.catchments:
        sections['SUBCATCHMENTS'].append(f'{c.id} RG {c.outlet} {c.area_m2 / 10000:.12g} {100*c.impervious_fraction:.12g} {c.width_m} {100*c.slope_fraction:.12g} 0')
        sections['SUBAREAS'].append(f'{c.id} {c.n_impervious} {c.n_pervious} {c.depression_impervious_mm} {c.depression_pervious_mm} 0 OUTLET')
        sections['INFILTRATION'].append(f'{c.id} {c.suction_mm} {c.conductivity_mm_hr} {c.moisture_deficit}')
    for n in network.nodes:
        area = n.storage_area_m2 + storage.get(n.id, 0)
        if n.outfall:
            sections['OUTFALLS'].append(f'{n.id} {n.invert_m} FREE NO')
        elif area:
            sections['STORAGE'].append(f'{n.id} {n.invert_m} {n.max_depth_m} 0 FUNCTIONAL 0 0 {area} 0 0 0')
        else:
            sections['JUNCTIONS'].append(f'{n.id} {n.invert_m} {n.max_depth_m} 0 0 0')
    for l in network.links:
        cap = l.max_flow_m3_s * capacities.get(l.id, 1)
        _positive(cap, 'effective max flow')
        sections['CONDUITS'].append(f'{l.id} {l.upstream} {l.downstream} {l.length_m} {l.roughness} 0 0 0 {cap:.12g}')
        sections['XSECTIONS'].append(f'{l.id} CIRCULAR {l.diameter_m} 0 0 0 1')
    for i, intensity in enumerate((*rain.intensity_mm_hr, 0.0)):
        t = start + timedelta(seconds=i * rain.interval_s)
        sections['TIMESERIES'].append(f'RAIN {t:%m/%d/%Y} {t:%H:%M} {intensity:.12g}')
    return '\n\n'.join(f'[{key}]\n' + '\n'.join(lines) for key, lines in sections.items()) + '\n'


def detect_swmm(executable: str | None = None) -> str | None:
    """Resolve explicit executable / DYNACITY_SWMM_EXECUTABLE / PATH. No fallback solver."""
    candidate = executable or os.environ.get('DYNACITY_SWMM_EXECUTABLE')
    if candidate:
        return shutil.which(candidate)
    return next((p for name in ('runswmm', 'swmm5', 'swmm') if (p := shutil.which(name))), None)


def parse_continuity(report: str) -> tuple[dict[str, float], dict[str, float], float, float]:
    """Read EPA 5.2 SI report tables. Volumes are rounded to 10 m³ by SWMM."""
    tables = []
    errors = []
    for title in ('Runoff Quantity Continuity', 'Flow Routing Continuity'):
        if title not in report:
            raise DrainageError(f'Missing {title}')
        part = report.split(title, 1)[1].split('Continuity Error (%)', 1)
        if len(part) != 2 or 'hectare-m' not in part[0]:
            raise DrainageError('Missing continuity or unsupported report units')
        rows = re.findall(r'^\s*([A-Za-z][A-Za-z ()-]+?)\s*\.{2,}\s*([-+\d.eE]+)\s+([-+\d.eE]+)', part[0], re.M)
        table = {label.strip(): float(vol) * 10000 for label, vol, _ in rows}
        error = re.match(r'\s*\.+\s*([-+\d.eE]+)', part[1])
        if not rows or not error:
            raise DrainageError('Malformed continuity table')
        e = float(error[1])
        if not all(math.isfinite(v) for v in (*table.values(), e)):
            raise DrainageError('Nonfinite continuity values')
        tables.append(table)
        errors.append(e)
    if not {'Total Precipitation', 'Infiltration Loss', 'Surface Runoff', 'Final Storage'} <= tables[0].keys() or not {'Wet Weather Inflow', 'External Outflow', 'Flooding Loss', 'Final Stored Volume'} <= tables[1].keys():
        raise DrainageError('Incomplete continuity budget')
    return tables[0], tables[1], errors[0], errors[1]


def run_drainage(network: DrainageNetwork, rain: Rainfall, work_dir: str | Path,
                 intervention: Intervention = Intervention('baseline'), *,
                 executable: str | None = None, continuity_tolerance_pct: float = 2.0,
                 timeout_s: float = 120, **options: int) -> DrainageResult:
    """Run in a NEW private directory; fail closed on solver/continuity failures.

    Report contains node/link/subcatchment time series. Flooding loss leaves this
    1D system; it is not a computed surface depth or a coupled 2D boundary.
    """
    _positive(continuity_tolerance_pct, 'continuity tolerance')
    _positive(timeout_s, 'timeout')
    inp = build_swmm_input(network, rain, intervention, **options)
    solver = detect_swmm(executable)
    if not solver:
        raise DrainageError('EPA SWMM runtime unavailable. Install/build SWMM 5.2 and set DYNACITY_SWMM_EXECUTABLE; no results computed.')
    directory = Path(work_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=False)
    input_path, report_path = directory / 'model.inp', directory / 'model.rpt'
    input_path.write_text(inp)
    common = json.dumps({'network': asdict(network), 'rain': asdict(rain), 'options': options}, default=str, sort_keys=True)
    key = hashlib.sha256(common.encode()).hexdigest()
    manifest = {'network': asdict(network), 'rain': asdict(rain), 'intervention': asdict(intervention), 'options': options, 'solver': solver, 'comparison_key': key}
    (directory / 'manifest.json').write_text(json.dumps(manifest, default=str, indent=2))
    try:
        # No binary filename: EPA writes all selected time series into the report.
        proc = subprocess.run([solver, str(input_path), str(report_path)], cwd=directory, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrainageError(f'SWMM execution failed: {exc}') from exc
    (directory / 'solver.log').write_text(proc.stdout + proc.stderr)
    report = report_path.read_text(encoding='latin-1') if report_path.exists() else ''
    if proc.returncode or re.search(r'\bERROR\s+\d+', report) or 'There are errors' in proc.stdout:
        raise DrainageError(f'SWMM failed; inspect {directory}')
    runoff, routing, re_pct, rt_pct = parse_continuity(report)
    if max(abs(re_pct), abs(rt_pct)) > continuity_tolerance_pct:
        raise DrainageError(f'Continuity exceeds {continuity_tolerance_pct}%: runoff={re_pct}%, routing={rt_pct}%; inspect {report_path}')
    warnings = tuple(line.strip() for line in report.splitlines() if 'WARNING' in line)
    warnings += ('No Beirut calibration; evidence: ' + network.evidence_kind,
                 'Flooding loss is uncoupled 1D overflow, not spatial surface inundation; velocity/depth grids unavailable.',
                 'Report volumes rounded to 10 m3; continuity percentages use internal solver precision.')
    engine = next((line.strip() for line in report.splitlines() if 'VERSION' in line.upper() and '5.' in line), 'EPA SWMM (see solver.log)')
    result = DrainageResult(intervention.name, key, hashlib.sha256(inp.encode()).hexdigest(), engine,
                            network.evidence_kind, network.provenance, str(report_path), runoff, routing, re_pct, rt_pct, warnings)
    (directory / 'result.json').write_text(json.dumps(asdict(result), indent=2))
    return result


def compare_drainage(network: DrainageNetwork, rain: Rainfall, work_dir: str | Path,
                     cases: tuple[Intervention, ...], **kwargs) -> dict[str, DrainageResult]:
    """Run baseline and named cases using identical network, forcing and options."""
    names = [c.name for c in cases]
    if len(names) != len(set(names)) or 'baseline' in names:
        raise ValueError('Unique case names required; baseline is automatic')
    return {case.name: run_drainage(network, rain, Path(work_dir) / f'case_{i}', case, **kwargs)
            for i, case in enumerate((Intervention('baseline'), *cases))}


def synthetic_fixture() -> tuple[DrainageNetwork, Rainfall]:
    """Small uncalibrated test basin. All dimensions/soil/rain are assumptions."""
    network = DrainageNetwork(
        (Catchment('C1', 'S1', 100000, 0.7, 300, 0.01, 100, 5, 0.25),),
        (Node('S1', 10, 1, 100), Node('O1', 9, 0, outfall=True)),
        (Link('P1', 'S1', 'O1', 100, 1, 0.013, 0.3),),
        'synthetic_fixture', 'Invented adapter verification basin; no real assets',
        'arbitrary synthetic metre datum', 'local synthetic; no georeferencing')
    rain = Rainfall(datetime(2026, 1, 1, tzinfo=timezone.utc), 300, (60.0,) * 6,
                    'Synthetic uniform 30-minute storm, no return period')
    return network, rain


@dataclass(frozen=True)
class NodeSample:
    timestamp: datetime
    inflow_m3_s: float
    overflow_m3_s: float
    depth_above_invert_m: float
    hydraulic_head_m: float


def read_node_timeseries(report_path: str | Path) -> dict[str, tuple[NodeSample, ...]]:
    """Read UTC, instantaneous node report samples (rounded to 0.001 SI).

    These are drainage node depths, not surface depths. Report samples must not
    be integrated as an exact overflow budget; use solver continuity totals.
    """
    report = Path(report_path).read_text(encoding='latin-1')
    result = {}
    for block in re.split(r'<<< Node ', report)[1:]:
        node, table = block.split(' >>>', 1)
        table = table.split('<<<', 1)[0]
        if not re.search(r'CMS\s+CMS\s+meters\s+meters', table):
            raise DrainageError('Unsupported node report units')
        samples = []
        for row in table.splitlines():
            parts = row.split()
            if len(parts) != 6 or not re.fullmatch(r'\d{2}/\d{2}/\d{4}', parts[0]):
                continue
            t = datetime.strptime(' '.join(parts[:2]), '%m/%d/%Y %H:%M:%S').replace(tzinfo=timezone.utc)
            values = tuple(float(x) for x in parts[2:])
            if not all(math.isfinite(v) for v in values):
                raise DrainageError('Nonfinite node output')
            samples.append(NodeSample(t, *values))
        if not samples:
            raise DrainageError(f'No time samples for node {node}')
        result[node] = tuple(samples)
    if not result:
        raise DrainageError('No node time series in report')
    return result
