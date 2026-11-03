"""Focus the viewer on one corridor, such as Al Masar al Akhdar along the old railway.

A corridor is a line (GeoJSON LineStrings in longitude/latitude) and a buffer
in metres. Every serve-time input is cut to that buffer, so the page loads and
simulates only the corridor and its neighbouring streets. Cropping selects
whole objects; nothing is clipped, moved or re-estimated, and the forecast
bands are recomputed for the buildings kept, not rescaled from the city.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .bbed import resolved_object_ids
from .contracts import UrbanStateSnapshot
from .viewer import _utm_zone, lonlat_to_utm, utm_to_lonlat


@dataclass(frozen=True)
class Corridor:
    name: str
    lines: list[list[tuple[float, float]]]  # longitude/latitude
    buffer_m: float
    crs: str
    area: Polygon  # buffered lines, in the snapshot's UTM metres
    source: str = ''
    # Named places along the line, in order, for the page's camera stops.
    stops: tuple[dict, ...] = ()

    @classmethod
    def load(cls, path: str | Path, crs: str, buffer_m: float) -> Corridor:
        collection = json.loads(Path(path).read_text(encoding='utf-8'))
        features = collection.get('features', [collection])
        lines = []
        for feature in features:
            geometry = feature.get('geometry') or {}
            parts = ([geometry['coordinates']] if geometry.get('type') == 'LineString' else
                     geometry['coordinates'] if geometry.get('type') == 'MultiLineString' else [])
            lines += [[(float(lon), float(lat)) for lon, lat, *_ in part] for part in parts if len(part) > 1]
        if not lines:
            raise ValueError(f'{path} holds no LineString to follow')
        if buffer_m <= 0:
            raise ValueError('corridor buffer must be positive')
        zone, north = _utm_zone(crs)
        metric = [LineString([lonlat_to_utm(lon, lat, zone, north) for lon, lat in line]) for line in lines]
        area = unary_union(metric).buffer(buffer_m)
        area = max(area.geoms, key=lambda g: g.area) if area.geom_type == 'MultiPolygon' else area
        return cls(name=collection.get('name') or features[0].get('properties', {}).get('name') or Path(path).stem,
                   lines=lines, buffer_m=buffer_m, crs=crs, area=area, source=collection.get('source', ''),
                   stops=tuple({'name': str(s['name']), 'at': [float(s['at'][0]), float(s['at'][1])],
                                'note': str(s.get('note', ''))} for s in collection.get('stops', [])))

    def outline(self) -> list[list[float]]:
        zone, north = _utm_zone(self.crs)
        return [[round(v, 7) for v in utm_to_lonlat(x, y, zone, north)] for x, y in self.area.exterior.coords]

    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in longitude/latitude around the buffer."""
        ring = self.outline()
        lons, lats = [p[0] for p in ring], [p[1] for p in ring]
        return min(lons), min(lats), max(lons), max(lats)

    def payload(self, counts: dict[str, int]) -> dict[str, Any]:
        return {'name': self.name, 'buffer_m': self.buffer_m, 'source': self.source,
                'lines': [[[round(v, 7) for v in p] for p in line] for line in self.lines],
                'outline': self.outline(), 'bounds': list(self.bounds()), 'stops': list(self.stops), 'counts': counts}


def crop_snapshot(snapshot: UrbanStateSnapshot, corridor: Corridor) -> UrbanStateSnapshot:
    """Buildings whose centroid lies inside the buffer; ones without a centroid are left out."""
    if snapshot.crs != corridor.crs:
        raise ValueError('corridor and snapshot use different CRS')
    from shapely import contains_xy
    kept = [o for o in snapshot.objects if o.centroid_x_m is not None and o.centroid_y_m is not None
            and contains_xy(corridor.area, o.centroid_x_m, o.centroid_y_m)]
    if not kept:
        raise ValueError('no snapshot buildings with a centroid lie inside the corridor')
    return snapshot.model_copy(update={'objects': kept})


def crop_bbed(collection: dict, snapshot: UrbanStateSnapshot) -> dict:
    keep = {o.object_id for o in snapshot.objects}
    features = collection.get('features', [])
    return {**collection, 'features': [f for f, i in zip(features, resolved_object_ids(features), strict=True) if i in keep]}


def _overlaps(bounds: tuple[float, float, float, float], w: float, s: float, e: float, n: float) -> bool:
    west, south, east, north = bounds
    return w <= east and e >= west and s <= north and n >= south


def crop_osm(raw: dict, bounds: tuple[float, float, float, float]) -> dict:
    """Overpass elements (`out geom` with bounds) touching the corridor's box."""
    def touches(e: dict) -> bool:
        if 'lat' in e:
            return _overlaps(bounds, e['lon'], e['lat'], e['lon'], e['lat'])
        b = e.get('bounds')
        if b:
            return _overlaps(bounds, b['minlon'], b['minlat'], b['maxlon'], b['maxlat'])
        points = e.get('geometry') or []
        return any(_overlaps(bounds, p['lon'], p['lat'], p['lon'], p['lat']) for p in points)
    return {**raw, 'elements': [e for e in raw.get('elements', []) if touches(e)]}


def _ring_box(ring) -> tuple[float, float, float, float]:
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def crop_context_buildings(companion: dict, bounds: tuple[float, float, float, float]) -> dict:
    def inside(feature: dict) -> bool:
        geometry = feature.get('geometry') or {}
        rings = (geometry.get('coordinates') or [[]])
        ring = rings[0][0] if geometry.get('type') == 'MultiPolygon' else rings[0]
        return bool(ring) and _overlaps(bounds, *_ring_box(ring))
    return {**companion, 'features': [f for f in companion.get('features', []) if inside(f)]}


def crop_beirut001(payload: dict, bounds: tuple[float, float, float, float]) -> dict:
    buildings = [b for b in payload['buildings'] if b['parts'] and _overlaps(bounds, *_ring_box(b['parts'][0][0]))]
    linked = {str(b['objectid']) for b in buildings if b['objectid'] is not None}
    return {**payload, 'buildings': buildings, 'bbed': {k: v for k, v in payload['bbed'].items() if k in linked}}

