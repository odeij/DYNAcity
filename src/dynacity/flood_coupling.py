"""Conservative, explicit surface/SWMM exchange. No inferred inlet parameters.

One process owns one SWMM instance. Product jobs already run in subprocesses.
Only a configured local, pinned runtime is loaded; input packages cannot supply
shared libraries. Rain and infiltration belong to ANUGA, never counted twice.
"""
from __future__ import annotations

import ctypes as ct
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import threading
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class Inlet(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1)
    node: str = Field(min_length=1)
    cell: int = Field(ge=0, strict=True)
    x_m: float
    y_m: float
    rim_m: float
    opening_area_m2: float = Field(gt=0)
    weir_width_m: float = Field(gt=0)
    weir_coefficient_m_half_s: float = Field(gt=0)
    orifice_coefficient: float = Field(gt=0, le=1)
    blockage_fraction: float = Field(ge=0, le=1)


class Coupling(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    engine: Literal['swmm-5.2.4']
    network: str = Field(min_length=1)
    vertical_datum: str = Field(min_length=1)
    crs: str = Field(min_length=1)
    step_s: float = Field(ge=.001, le=60)
    inlets: list[Inlet] = Field(min_length=1, max_length=3000)
    # Every non-outfall node must have one explicit surface re-entry cell.
    overflow_cells: dict[str, int]
    mass_absolute_tolerance_m3: float = Field(gt=0)
    mass_relative_tolerance: float = Field(gt=0, lt=1)


def inlet_rate(inlet: Inlet, surface_head: float, sewer_head: float, gravity: float) -> float:
    """Positive into sewer; negative onto street. Quasi-steady dual-head law.

    The smaller weir/orifice capacity controls each direction. Coefficients,
    crest, opening and obstruction are supplied data/declared derivations.
    """
    if not all(math.isfinite(v) for v in (surface_head, sewer_head, gravity)) or gravity <= 0:
        raise ValueError('finite heads and positive gravity required')
    high, low = max(surface_head, sewer_head), min(surface_head, sewer_head)
    head = max(high - inlet.rim_m, 0.)
    drop = max(high - max(low, inlet.rim_m), 0.)
    open_fraction = 1 - inlet.blockage_fraction
    q_weir = inlet.weir_coefficient_m_half_s * inlet.weir_width_m * head**1.5
    q_orifice = inlet.orifice_coefficient * inlet.opening_area_m2 * math.sqrt(2*gravity*drop)
    q = open_fraction * min(q_weir, q_orifice)
    return q if surface_head >= sewer_head else -q


def validate_coupling(spec, manifest, mesh, forcing, path):
    """Check mapping/evidence domain and a self-contained routing-only INP."""
    from pyproj import CRS
    from shapely.geometry import Point, Polygon
    if spec.vertical_datum != manifest.vertical_datum or CRS(spec.crs) != CRS(manifest.crs):
        raise ValueError('surface and sewer must share an explicitly matched CRS and vertical datum')
    if len({i.id for i in spec.inlets}) != len(spec.inlets):
        raise ValueError('duplicate inlet IDs')
    for inlet in spec.inlets:
        if inlet.cell >= len(mesh['triangles']) or not Polygon(mesh['points_m'][mesh['triangles'][inlet.cell]]).covers(Point(inlet.x_m, inlet.y_m)):
            raise ValueError(f'inlet {inlet.id} must lie in its supplied surface cell')
    if any(type(c) is not int or c < 0 or c >= len(mesh['triangles']) for c in spec.overflow_cells.values()):
        raise ValueError('invalid overflow surface cell')
    if np.any(forcing['drainage_exchange_m3_s'] != 0):
        raise ValueError('dynamic coupling forbids simultaneous prescribed drainage (double counting)')
    if abs(spec.step_s*1000-round(spec.step_s*1000))>1e-7:
        raise ValueError('coupling step must use the native SWMM millisecond clock')
    if not np.allclose(forcing['times_s']/spec.step_s, np.rint(forcing['times_s']/spec.step_s), rtol=0, atol=1e-7):
        raise ValueError('forcing endpoints must align to the explicit coupling step')
    if forcing['times_s'][-1]/spec.step_s * (len(spec.inlets)+len(spec.overflow_cells)+16) > 2_000_000:
        raise ValueError('coupling history exceeds the supported two-million-record limit')
    sections, section = {}, None
    allowed = {'TITLE','OPTIONS','JUNCTIONS','STORAGE','OUTFALLS','CONDUITS','XSECTIONS','COORDINATES','VERTICES','TIMESERIES'}
    for raw in Path(path).read_text().splitlines():
        line = raw.split(';',1)[0].strip()
        if not line: continue
        if line.startswith('['):
            section = line.strip('[]').upper()
            if section not in allowed or section in sections:
                raise ValueError('routing-only SWMM file: unsupported or duplicate section ' + section)
            sections[section] = []
        elif section: sections[section].append(line.split())
        else: raise ValueError('SWMM values must be inside sections')
    option_lines=sections.get('OPTIONS',[])
    if any(len(p)!=2 for p in option_lines) or len({p[0].upper() for p in option_lines})!=len(option_lines):
        raise ValueError('SWMM options must be unique key/value pairs')
    options = {parts[0].upper():parts[1].upper() for parts in option_lines}
    if 'TEMPDIR' in options or options.get('IGNORE_ROUTING','NO')!='NO':
        raise ValueError('SWMM coupling forbids external temp paths and disabled routing')
    required = {'FLOW_UNITS':'CMS','FLOW_ROUTING':'DYNWAVE','ALLOW_PONDING':'NO',
                'IGNORE_RAINFALL':'YES','IGNORE_RDII':'YES','IGNORE_GROUNDWATER':'YES',
                'SKIP_STEADY_STATE':'NO', 'VARIABLE_STEP':'0'}
    if any(options.get(k) != v for k,v in required.items()):
        raise ValueError('SWMM coupling requires explicit SI dynamic-wave routing and no duplicate runoff/ponding')
    start = datetime.strptime(options['START_DATE']+' '+options['START_TIME'], '%m/%d/%Y %H:%M:%S').replace(tzinfo=timezone.utc)
    end = datetime.strptime(options['END_DATE']+' '+options['END_TIME'], '%m/%d/%Y %H:%M:%S').replace(tzinfo=timezone.utc)
    report = datetime.strptime(options['REPORT_START_DATE']+' '+options['REPORT_START_TIME'], '%m/%d/%Y %H:%M:%S').replace(tzinfo=timezone.utc)
    if start != manifest.start.astimezone(timezone.utc) or report != start or (end-start).total_seconds() <= forcing['times_s'][-1]:
        raise ValueError('SWMM start/report time must match surface UTC start; end must exceed run duration')
    nodes = [p[0] for key in ('JUNCTIONS','STORAGE') for p in sections.get(key,[])]
    outfalls = [p[0] for p in sections.get('OUTFALLS',[])]
    if len(nodes) > 5000 or len(set(nodes+outfalls)) != len(nodes+outfalls) or not outfalls or not sections.get('CONDUITS'):
        raise ValueError('SWMM network needs unique nodes, conduits and explicit outfalls within supported limits')
    if set(spec.overflow_cells) != set(nodes) or any(i.node not in nodes for i in spec.inlets):
        raise ValueError('map every non-outfall sewer node to a surface overflow cell; inlet nodes must exist')
    for parts in sections.get('OUTFALLS',[]):
        if len(parts)<3 or parts[2].upper() not in {'FREE','NORMAL','FIXED','TIMESERIES'}:
            raise ValueError('this coupling supports explicitly supplied FREE/NORMAL/FIXED/TIMESERIES outfalls only')
    _validate_outfall_series(sections, start, float(forcing['times_s'][-1]))
    return {'nodes':nodes, 'outfalls':outfalls}


def _validate_outfall_series(sections, start, duration_s):
    """Self-contained dated boundary observations, no implicit extrapolation.

    SWMM normally accepts external FILE series and abbreviated dates. Require
    one full UTC-aligned date/time/value per row so package coverage can be
    checked before launching native code. Levels use the declared shared datum.
    """
    required = set()
    for parts in sections.get('OUTFALLS', []):
        if parts[2].upper() == 'TIMESERIES':
            if len(parts) != 5 or parts[4].upper() not in {'YES','NO'}:
                raise ValueError('TIMESERIES outfalls require series ID and explicit YES/NO flap gate; runoff routing is unsupported')
            required.add(parts[3])
    series = {}
    for parts in sections.get('TIMESERIES', []):
        if len(parts) != 4 or parts[1].upper() == 'FILE':
            raise ValueError('outfall TIMESERIES requires inline ID MM/DD/YYYY HH:MM:SS level_m; external files are forbidden')
        try:
            stamp = datetime.strptime(parts[1]+' '+parts[2], '%m/%d/%Y %H:%M:%S').replace(tzinfo=timezone.utc)
            stage = float(parts[3])
        except ValueError as exc:
            raise ValueError('outfall TIMESERIES needs an explicit dated UTC time and finite stage') from exc
        if not math.isfinite(stage):
            raise ValueError('outfall TIMESERIES stage must be finite')
        entries = series.setdefault(parts[0], [])
        if entries and stamp <= entries[-1]:
            raise ValueError('outfall TIMESERIES times must increase strictly without duplicates')
        entries.append(stamp)
    if series.keys() != required:
        raise ValueError('declare exactly the inline TIMESERIES referenced by outfalls')
    end = start+timedelta(seconds=duration_s)
    if any(len(entries)<2 or entries[0]>start or entries[-1]<end for entries in series.values()):
        raise ValueError('outfall TIMESERIES must cover the complete surface simulation; no endpoint extrapolation')


_swmm_lock = threading.Lock()


def runtime_path():
    path = os.environ.get('DYNACITY_SWMM_LIBRARY')
    if not path or not Path(path).is_file():
        raise RuntimeError('Dynamic drainage requires DYNACITY_SWMM_LIBRARY pointing to the pinned local SWMM 5.2.4 bridge library')
    return Path(path).resolve()


def load_runtime():
    lib=ct.CDLL(str(runtime_path()))
    if lib.swmm_getVersion()!=52004 or lib.dynacity_bridge_version()!=1:
        raise RuntimeError('Pinned SWMM 5.2.4 bridge required')
    return lib


def runtime_status():
    try:
        load_runtime()
        return {'available':True,'engine':'EPA SWMM 5.2.4','coupling':'two-way surface/sewer exchange'}
    except (OSError,RuntimeError,AttributeError):
        return {'available':False,'engine':'EPA SWMM 5.2.4',
                'reason':'Pinned local SWMM coupling library is not configured or could not be verified.'}


class Sewer:
    def __init__(self, network, work_dir):
        self.lib = load_runtime()
        for name in ('swmm_getValue','dynacity_storage','dynacity_external','dynacity_outflow','dynacity_losses','dynacity_flooding','dynacity_node_flooding'):
            getattr(self.lib,name).restype = ct.c_double
        self.lib.swmm_getValue.argtypes = [ct.c_int,ct.c_int]
        self.lib.swmm_setValue.argtypes = [ct.c_int,ct.c_int,ct.c_double]
        self.lib.swmm_getIndex.argtypes = [ct.c_int,ct.c_char_p]
        self.lib.swmm_open.argtypes = [ct.c_char_p,ct.c_char_p,ct.c_char_p]
        self.lib.swmm_stride.argtypes = [ct.c_int,ct.POINTER(ct.c_double)]
        self.lib.swmm_step.argtypes = [ct.POINTER(ct.c_double)]
        if self.lib.swmm_getVersion()!=52004 or self.lib.dynacity_bridge_version()!=1:
            raise RuntimeError('Pinned SWMM 5.2.4 bridge required')
        self.acquired = _swmm_lock.acquire(blocking=False)
        if not self.acquired: raise RuntimeError('SWMM is process-global; run coupled jobs in separate processes')
        self.started = False
        try:
            self.check(self.lib.swmm_open(str(network).encode(),str(Path(work_dir)/'sewer.rpt').encode(),b''))
            self.check(self.lib.swmm_start(0))
            self.started = True
            if self.lib.swmm_getValue(8,0) != 3: raise RuntimeError('SWMM must use CMS units')
            self.initial_storage = self.storage()
            self.routing_step = self.lib.swmm_getValue(3,0)
            self.flood_totals = {}
            self.previous_overflow = {}
            self.library_sha256 = hashlib.sha256(runtime_path().read_bytes()).hexdigest()
        except Exception:
            self.close(); raise

    def check(self, code):
        if code:
            message = ct.create_string_buffer(1024)
            self.lib.swmm_getError(message,1024)
            raise RuntimeError(f'SWMM error {code}: {message.value.decode(errors="replace")}')

    def index(self, name):
        result = self.lib.swmm_getIndex(2,name.encode())
        if result<0: raise ValueError('unknown sewer node '+name)
        return result

    def head(self, name): return self.lib.swmm_getValue(304,self.index(name))
    def volume(self, name): return self.lib.swmm_getValue(305,self.index(name))
    def storage(self): return self.lib.dynacity_storage()
    def flood(self, name):
        if name not in self.flood_totals:
            if self.lib.swmm_getValue(2,0)>0: raise RuntimeError('register every overflow node before advancing SWMM')
            self.flood_totals[name]=0.
            self.previous_overflow[name]=0.
        return self.flood_totals[name]
    def totals(self):
        return {k: getattr(self.lib,'dynacity_'+k)() for k in ('storage','external','outflow','losses','flooding')}

    def advance(self, rates, seconds):
        for name, q in rates.items(): self.lib.swmm_setValue(306,self.index(name),float(q))
        clock = self.lib.swmm_getValue(2,0)*86400
        target = clock+seconds
        while clock < target-1e-8:
            dt = min(self.routing_step,target-clock)
            # Official API permits subsecond fixed routing steps. Refuse a
            # native minimum-step clamp rather than letting the clocks drift.
            self.lib.swmm_setValue(3,0,dt)
            if abs(self.lib.swmm_getValue(3,0)-dt)>1e-8:
                raise RuntimeError('SWMM MINIMUM_STEP exceeds coupling interval')
            elapsed = ct.c_double()
            self.check(self.lib.swmm_step(ct.byref(elapsed)))
            now = elapsed.value*86400
            if now <= clock or now > target+1e-6: raise RuntimeError('SWMM clock exceeded coupling interval')
            # Native FlowTotals uses a half-old/half-new routing flux budget.
            # NodeStats.volFlooded uses endpoint rectangles and is NOT that
            # budget. Integrate each node using native internal steps instead.
            for name in self.flood_totals:
                q = max(self.lib.swmm_getValue(308,self.index(name)),0.)
                self.flood_totals[name] += .5*(self.previous_overflow[name]+q)*(now-clock)
                self.previous_overflow[name] = q
            clock=now
        return clock

    def close(self):
        if self.acquired:
            try:
                if self.started: self.lib.swmm_end(); self.lib.swmm_report()
                self.lib.swmm_close()
            finally:
                self.acquired=False; _swmm_lock.release()


def exchange_volumes(spec, stage, bed, areas, sewer, gravity):
    """Jointly cap shared surface/node withdrawals; order independent allocation."""
    dt = spec.step_s
    volumes = np.array([inlet_rate(i,float(stage[i.cell]),sewer.head(i.node),gravity)*dt for i in spec.inlets])
    for cell in {i.cell for i in spec.inlets}:
        mask = np.array([i.cell==cell for i in spec.inlets]) & (volumes>0)
        request = volumes[mask].sum()
        available = max(float(stage[cell]-bed[cell]),0)*areas[cell]
        if request>available: volumes[mask] *= available/request
    for node in {i.node for i in spec.inlets}:
        mask = np.array([i.node==node for i in spec.inlets]) & (volumes<0)
        request = -volumes[mask].sum()
        available = max(sewer.volume(node),0)
        if request>available: volumes[mask] *= available/request
    return volumes


def apply_surface_volumes(domain, volumes):
    """Instant conservative split-step; withdrawal removes proportional momentum."""
    stage = domain.quantities['stage'].centroid_values
    bed = domain.quantities['elevation'].centroid_values
    depth = np.maximum(stage-bed,0)
    new = depth + volumes/domain.areas
    if np.any(new < -1e-10): raise RuntimeError('exchange would withdraw unavailable surface water')
    factor = np.ones_like(depth)
    np.divide(np.maximum(new,0),depth,out=factor,where=(volumes<0)&(depth>0))
    for name in ('xmomentum','ymomentum'):
        domain.quantities[name].centroid_values[:] *= factor
    stage[:] += volumes/domain.areas
    # ANUGA's discontinuous-elevation get_water_volume() reads its cached
    # height quantity after evolution. Surface re-entry happens BETWEEN evolve
    # calls, so keep that cache consistent before inspecting any mass budget.
    domain.quantities['height'].centroid_values[:] = stage-bed
    domain.fractional_step_volume_integral += float(volumes.sum())
