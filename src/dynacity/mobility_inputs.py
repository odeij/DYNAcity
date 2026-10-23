"""Small, explicit mobility evidence packages; never fetch data on app startup."""
from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Source(Record):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(pattern=r'^https://')
    as_of: str = Field(min_length=1)
    kind: Literal['mapped', 'survey', 'published', 'assumption']
    limitations: str = Field(min_length=1)


class VehicleType(Record):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]*$')
    label: str
    vclass: Literal['passenger', 'taxi', 'motorcycle', 'delivery', 'bus', 'truck']
    # Unspecified dimensions and behaviour use SUMO's class defaults, not local measurements.


class Fleet(Record):
    source: str
    label: str
    scope: str = Field(min_length=1)
    weights: dict[str, float]
    missing_classes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_weights(self):
        if not self.weights or any(not math.isfinite(w) or w <= 0 for w in self.weights.values()):
            raise ValueError('fleet weights must be finite and positive')
        return self


class MapFeature(Record):
    id: str
    kind: Literal['bus_stop', 'parking', 'crossing', 'signal']
    label: str
    source: str
    path: list[tuple[float, float]] = Field(min_length=1, max_length=2000)
    capacity: int | None = Field(default=None, ge=0)
    source_url: str = Field(pattern=r'^https://')


class Phase(Record):
    duration_s: float = Field(gt=0, le=3600)
    state: str = Field(pattern=r'^[rRyYgGoOuUs]+$')


class Signal(Record):
    node: int = Field(ge=0)
    source: str
    phases: list[Phase] = Field(min_length=1, max_length=100)
    offset_s: float = Field(default=0, ge=0)


class Stop(Record):
    vehicle: str
    edge: str = Field(pattern=r'^\d+:(1|-1)$')
    lane: int = Field(default=0, ge=0)
    position_m: float = Field(gt=0)
    duration_s: float = Field(gt=0, le=86400)
    parking: bool = False
    source: str


class MobilityInputs(Record):
    schema_version: Literal['dynacity-mobility/1'] = 'dynacity-mobility/1'
    title: str
    bounds: tuple[float, float, float, float]
    sources: list[Source]
    features: list[MapFeature] = Field(default_factory=list, max_length=5000)
    types: list[VehicleType] = Field(default_factory=list, max_length=30)
    fleet: Fleet | None = None
    signals: list[Signal] = Field(default_factory=list, max_length=500)
    stops: list[Stop] = Field(default_factory=list, max_length=5000)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def references(self):
        west, south, east, north = self.bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('invalid mobility bounds')
        sources = {s.id: s for s in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError('duplicate source identifiers')
        records = [*self.features, *self.signals, *self.stops, *([self.fleet] if self.fleet else [])]
        if any(r.source not in sources for r in records):
            raise ValueError('unknown mobility evidence source')
        if any(sources[r.source].kind != 'survey' for r in [*self.signals, *self.stops]):
            raise ValueError('signal programs and stop events require survey evidence')
        types = {t.id for t in self.types}
        if len(types) != len(self.types):
            raise ValueError('duplicate vehicle types')
        if self.fleet and (set(self.fleet.weights) - types or sources[self.fleet.source].kind not in ('survey', 'published')):
            raise ValueError('fleet requires declared types and a count source')
        if self.fleet and any(t.id in self.fleet.weights and t.vclass in self.fleet.missing_classes for t in self.types):
            raise ValueError('fleet cannot assign demand to an explicitly unmeasured class')
        if len({s.node for s in self.signals}) != len(self.signals):
            raise ValueError('duplicate signal programs')
        if len({f.id for f in self.features}) != len(self.features):
            raise ValueError('duplicate mapped features')
        for f in self.features:
            if any(not (west <= lon <= east and south <= lat <= north) for lon, lat in f.path):
                raise ValueError('mapped feature outside pilot bounds')
            if f.kind == 'parking' and len(f.path) > 1 and (len(f.path) < 4 or f.path[0] != f.path[-1]):
                raise ValueError('parking areas must be closed rings')
        return self

    @classmethod
    def load(cls, path):
        return cls.model_validate_json(Path(path).read_text(encoding='utf-8'))

    def vehicle_type(self, vehicle_id: str, seed: int) -> str:
        if not self.fleet:
            return 'DEFAULT_VEHTYPE'
        # Stable across request chunking, both paired runs and initial/incoming trips.
        q = int.from_bytes(hashlib.sha256(f'{seed}:{vehicle_id}'.encode()).digest()[:8], 'big') / 2**64
        total = sum(self.fleet.weights.values())
        cumulative = 0.0
        for type_id, weight in sorted(self.fleet.weights.items()):
            cumulative += weight / total
            if q < cumulative:
                return type_id
        return sorted(self.fleet.weights)[-1]

    def public(self):
        return self.model_dump(mode='json', exclude={'signals', 'stops'}) | {
            'surveyed_signal_programs': len(self.signals), 'observed_stop_events': len(self.stops),
            'pedestrian_demand': 'unavailable',
        }


def pilot_network(model, bounds):
    """Select whole measured road segments touching the pilot, preserving node identity.

    The pilot is a separate network, not a reduction of the represented citywide
    fleet in an existing session. Roads are not geometrically clipped or moved.
    """
    from .traffic import class_peak_volumes
    from .viewer import _utm_zone, utm_to_lonlat
    zone, north = _utm_zone(model.crs)
    west, south, east, north_lat = bounds

    def touches(a, b):
        # Liang–Barsky segment/rectangle intersection, including boundary contacts.
        dx, dy = b[0]-a[0], b[1]-a[1]
        lo, hi = 0.0, 1.0
        for p, q in ((-dx, a[0]-west), (dx, east-a[0]), (-dy, a[1]-south), (dy, north_lat-a[1])):
            if p == 0:
                if q < 0:
                    return False
            elif p < 0:
                lo = max(lo, q/p)
            else:
                hi = min(hi, q/p)
        return lo <= hi

    keep = []
    for i, geometry in enumerate(model.geometry):
        points = [utm_to_lonlat(x, y, zone, north) for x, y in geometry]
        if any(touches(a, b) for a, b in zip(points, points[1:])):
            keep.append(i)
    if not keep:
        raise ValueError('No traffic roads intersect the mobility pilot')
    arrays = {name: getattr(model, name)[keep].copy() for name in
              ('u', 'v', 'length_m', 'oneway', 'speed_kmh', 'free_kmh', 'readings', 'estimated')}
    lists = {name: [getattr(model, name)[i] for i in keep] for name in ('geometry', 'highway', 'way_id')}
    ways = set(lists['way_id'])
    buses = [dict(r, ways=[w for w in r['ways'] if w in ways]) for r in model.bus_routes if ways.intersection(r['ways'])]
    return replace(model, **arrays, **lists,
                   lanes=model.lanes[keep].copy() if model.lanes is not None else None,
                   name=[model.name[i] for i in keep] if model.name else [], bus_routes=buses,
                   volume_reference=class_peak_volumes(model),
                   source=model.source+' Whole-road subset for the Mar Mikhael mobility pilot; not a citywide run.',
                   _graph_cache={}), keep
