"""Validate a source-linked replacement before changing any Unreal actors."""
import math


def replacement_plan(manifest, covers, city_origin):
    if manifest.get('schema') != 'dynacity-mapped-barrier-placements/1':
        raise ValueError('Unsupported barrier placement manifest')
    if manifest.get('origin_utm_m') != city_origin or covers.get('origin_utm_m') != city_origin:
        raise ValueError('Barrier and city origins differ')
    if covers.get('pending') or covers.get('replaced_actors'):
        raise ValueError('Finish the existing cover import before replacing barriers')
    if manifest.get('omitted'):
        raise ValueError('Cannot replace a barrier layer with omitted source geometry')
    placements = manifest.get('placements') or []
    if not placements:
        raise ValueError('No source-linked barrier placements')
    sources = set()
    for row in placements:
        values = row.get('position_local_m', [])
        if len(values) != 3 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in values):
            raise ValueError('Invalid barrier position')
        if not isinstance(row.get('heading_degrees'), (float, int)) or not math.isfinite(row['heading_degrees']):
            raise ValueError('Invalid barrier heading')
        if not isinstance(row.get('scale_x'), (float, int)) or not 0 < row['scale_x'] <= 1.001:
            raise ValueError('Invalid barrier scaling')
        if not row.get('source_id') or not row.get('source_instance_id'):
            raise ValueError('Missing barrier source identity')
        sources.add((row['source_id'], row['source_instance_id']))
    replacements = []
    found = set()
    for package in covers.get('packages', []):
        if package.get('kind') != 'jersey_barrier':
            continue
        ids = {(o['source_id'], o['instance_id']) for o in package.get('objects', [])}
        if not ids & sources:
            continue
        if not ids <= sources:
            raise ValueError('Refusing to hide a package containing unmatched barriers')
        completed = covers.get('completed', {}).get(package['key'])
        if not completed or not completed.get('actor'):
            raise ValueError('Existing barrier package is not fully imported')
        replacements.append({'actor': completed['actor'], 'source_ids': sorted(ids)})
        found.update(ids)
    if found != sources:
        raise ValueError('Existing barrier source instances do not match replacement')
    return replacements
