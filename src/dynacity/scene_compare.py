"""Compare two scene snapshots and report every difference outside an explicit allowlist (stdlib only).

A snapshot is ``{'actors': {path: {'label', 'visible', 'mesh', 'materials': [...], 'collision', 'transform': [...], ...}}}``.
Nothing here asserts success: ``unexpected`` is computed from the two snapshots, and an empty or mismatched
snapshot is itself an error, never a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = ('label', 'visible', 'hidden_in_game', 'mesh', 'materials', 'collision', 'transform')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _same(a, b, tol=1e-3):
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return False
    return a == b


def compare(pre, post, allowed=None, created_label_prefixes=()):
    """Differences between snapshots.

    ``allowed``: {actor path: {field: (expected_pre, expected_post)}}; a change is allowed only when it is exactly that.
    Actors present only in ``post`` are allowed only if their label starts with one of ``created_label_prefixes``.
    Actors missing from ``post`` are always unexpected.
    """
    if not pre.get('actors'):
        raise ValueError('empty pre-install snapshot: nothing to compare against')
    allowed = allowed or {}
    expected, unexpected = [], []
    for path, before in pre['actors'].items():
        after = post['actors'].get(path)
        if after is None:
            unexpected.append({'actor': path, 'label': before.get('label'), 'change': 'missing after'})
            continue
        for f in FIELDS:
            if f in before and not _same(before[f], after.get(f)):
                row = {'actor': path, 'label': before.get('label'), 'field': f, 'pre': before[f], 'post': after.get(f)}
                want = allowed.get(path, {}).get(f)
                if want is not None and _same(want[0], before[f]) and _same(want[1], after.get(f)):
                    expected.append(row)
                else:
                    unexpected.append(row)
    for path, after in post['actors'].items():
        if path not in pre['actors']:
            row = {'actor': path, 'label': after.get('label'), 'change': 'new actor'}
            (expected if str(after.get('label', '')).startswith(tuple(created_label_prefixes)) and created_label_prefixes else unexpected).append(row)
    return {'compared_actors': len(pre['actors']), 'expected_changes': expected, 'unexpected': unexpected,
            'ok': not unexpected and len(pre['actors']) > 0}
