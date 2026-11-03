"""Fetch the city bbox through bounded HTTPS range reads (no full-world downloads)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import threading
import urllib.request

import pyarrow.compute as pc
import pyarrow.parquet as pq
from shapely import from_wkb
from shapely.geometry import mapping

BBOX = (35.4693, 33.8288, 35.572, 33.9076)
TYPES = ('place', 'water', 'land_use', 'land_cover', 'infrastructure',
         'segment', 'connector', 'division_area', 'land')


class RangeFile(io.RawIOBase):
    def __init__(self, url):
        super().__init__()
        self.url, self.pos = url, 0
        self.lock = threading.RLock()
        self.transferred = 0
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=-8'}), timeout=30) as r:
            self.size = int(r.headers['Content-Range'].split('/')[-1])
            r.read()

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, size=-1):
        with self.lock:
            size = self.size - self.pos if size < 0 else min(size, self.size - self.pos)
            if size <= 0: return b''
            start, end = self.pos, self.pos + size - 1
            request = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}'})
            with urllib.request.urlopen(request, timeout=60) as r:
                if r.status != 206: raise RuntimeError('Server did not honor range request')
                expected = f'bytes {start}-{end}/{self.size}'
                if r.headers.get('Content-Range') != expected:
                    raise IOError('Server returned a different byte range')
                data = r.read(size)
            if len(data) != size: raise IOError('Incomplete range response')
            self.pos += len(data)
            self.transferred += len(data)
            return data


def intersects(group):
    stats = {group.column(i).path_in_schema: group.column(i).statistics for i in range(group.num_columns)}
    tests = [('bbox.xmin', 'min', BBOX[2], 'lt'), ('bbox.xmax', 'max', BBOX[0], 'gt'),
             ('bbox.ymin', 'min', BBOX[3], 'lt'), ('bbox.ymax', 'max', BBOX[1], 'gt')]
    for name, bound, value, op in tests:
        stat = stats.get(name)
        if stat is None or not stat.has_min_max: continue
        observed = getattr(stat, bound)
        if (op == 'lt' and observed >= value) or (op == 'gt' and observed <= value): return False
    return True


def fetch_type(kind, rows, root):
    target = root / (kind + '.geojson')
    if target.exists() and target.stat().st_size:
        existing = json.loads(target.read_text(encoding='utf-8'))
        if existing.get('release') != '2026-10-21.0' or tuple(existing.get('bbox', ())) != BBOX:
            raise ValueError('Cached data has a different release or bbox')
        return {'type': kind, 'features': len(existing['features']), 'cached': True}
    features = {}
    transferred = groups = 0
    for row in rows:
        if row['collection'] != kind: continue
        with RangeFile(row['assets']['aws']['href']) as stream:
            parquet = pq.ParquetFile(stream, pre_buffer=False)
            selected = [i for i in range(parquet.num_row_groups) if intersects(parquet.metadata.row_group(i))]
            print(json.dumps({'type': kind, 'selected_groups': len(selected), 'total_groups': parquet.num_row_groups}), flush=True)
            columns = [c for c in ('id', 'geometry', 'names', 'subtype', 'class', 'sources',
                                   'categories', 'bbox', 'road_surface', 'road_flags', 'routes',
                                   'prohibited_transitions', 'connectors', 'subclass', 'level')
                       if c in parquet.schema_arrow.names]
            for i in selected:
                table = parquet.read_row_group(i, columns=columns, use_threads=False)
                bbox = table['bbox']
                keep = pc.and_(pc.and_(pc.less(pc.struct_field(bbox, 'xmin'), BBOX[2]),
                                      pc.greater(pc.struct_field(bbox, 'xmax'), BBOX[0])),
                               pc.and_(pc.less(pc.struct_field(bbox, 'ymin'), BBOX[3]),
                                       pc.greater(pc.struct_field(bbox, 'ymax'), BBOX[1])))
                for item in table.filter(keep).to_pylist():
                    geometry = from_wkb(item.pop('geometry'))
                    features[item['id']] = {'type': 'Feature', 'geometry': mapping(geometry), 'properties': item}
                groups += 1
            transferred += stream.transferred
    result = {'type': 'FeatureCollection', 'release': '2026-10-21.0', 'bbox': BBOX,
              'overture_type': kind, 'features': list(features.values())}
    temporary = target.with_suffix('.partial')
    temporary.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
    temporary.replace(target)
    return {'type': kind, 'features': len(features), 'row_groups': groups, 'bytes_transferred': transferred}


def enrich_places(rows, root):
    path = root/'place.geojson'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('category_fields_downloaded'): return {'type': 'place_enrichment', 'cached': True}
    wanted = {f['properties']['id']: f['properties'] for f in data['features']}
    for row in rows:
        if row['collection'] != 'place': continue
        with RangeFile(row['assets']['aws']['href']) as stream:
            parquet = pq.ParquetFile(stream, pre_buffer=False)
            columns = [c for c in ('id', 'basic_category', 'taxonomy', 'confidence', 'operating_status', 'addresses')
                       if c in parquet.schema_arrow.names]
            for i in range(parquet.num_row_groups):
                if not intersects(parquet.metadata.row_group(i)): continue
                for item in parquet.read_row_group(i, columns=columns, use_threads=False).to_pylist():
                    if item['id'] in wanted: wanted[item['id']].update(item)
    data['category_fields_downloaded'] = True
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
    return {'type': 'place_enrichment', 'features': len(wanted)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--types', nargs='+', choices=(*TYPES, 'place_enrichment'), default=TYPES)
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    index = root/'stac-selection.json'
    if not index.exists():
        with urllib.request.urlopen('https://stac.overturemaps.org/2026-10-21.0/collections.parquet', timeout=30) as response:
            table = pq.read_table(io.BytesIO(response.read()))
        candidates = table.to_pylist()
        rows = [r for r in candidates if r['type'] == 'Feature' and
                r['bbox']['xmin'] < BBOX[2] and r['bbox']['xmax'] > BBOX[0] and
                r['bbox']['ymin'] < BBOX[3] and r['bbox']['ymax'] > BBOX[1]]
        index.write_text(json.dumps(rows, default=str), encoding='utf-8')
    rows = json.loads(index.read_text(encoding='utf-8'))
    report = []
    def run(kind):
        try: result = enrich_places(rows, root) if kind == 'place_enrichment' else fetch_type(kind, rows, root)
        except Exception as error: result = {'type': kind, 'error': repr(error)}
        print(json.dumps(result), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        report = list(pool.map(run, args.types))
    suffix = '' if tuple(args.types) == TYPES else '-extra'
    (root / ('download-report'+suffix+'.json')).write_text(json.dumps(report, indent=2))


if __name__ == '__main__': main()
