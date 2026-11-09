import numpy as np
import pytest

from dynacity.batch_patch import patch_batch
from dynacity.corridor_reconstruction import write_glb
from dynacity.engine_city_batches import Glb

MAT = {'m': {'base_color': [.5, .5, .5, 1.]}}
NODE = 'corridor_reconstruction'


def make_batch(tmp_path, quads=4):
    """A one-node, one-primitive batch of ``quads`` unit quads (2 triangles each)."""
    parts = []
    for i in range(quads):
        v = np.array([[i * 2, 0, 0], [i * 2 + 1, 0, 0], [i * 2 + 1, 1, 0], [i * 2, 1, 0]], float)
        parts.append({'name': f'q{i}', 'material': 'm', 'vertices': v, 'triangles': np.array([[0, 1, 2], [0, 2, 3]])})
    path = tmp_path / 'batch.glb'
    write_glb(path, parts, MAT)
    return path


def tri_lists(path):
    g = Glb(path)
    return [g.accessor(p['indices']).reshape(-1, 3) for p in g.doc['meshes'][0]['primitives']]


def test_omit_removes_exactly_the_listed_triangles(tmp_path):
    src = make_batch(tmp_path)
    original = tri_lists(src)[0]
    out = patch_batch(src, NODE, [2, 3], None, tmp_path / 'p.glb')
    kept = tri_lists(tmp_path / 'p.glb')
    assert len(kept) == 1 and len(kept[0]) == len(original) - 2
    assert (kept[0] == np.delete(original, [2, 3], axis=0)).all()  # every other triangle byte-for-byte
    assert out['omitted_triangles'] == 2 and out['source_modified'] is False
    # positions untouched
    assert (Glb(src).accessor(0) == Glb(tmp_path / 'p.glb').accessor(0)).all()


def test_groups_keep_their_own_primitive_and_material(tmp_path):
    src = make_batch(tmp_path)
    out = patch_batch(src, NODE, [0], {'ReferenceFacade': [4, 5]}, tmp_path / 'g.glb')
    prims = tri_lists(tmp_path / 'g.glb')
    assert [len(p) for p in prims] == [5, 2] and out['primitives']['ReferenceFacade']['triangles'] == 2
    doc = Glb(tmp_path / 'g.glb').doc
    names = [doc['materials'][p['material']]['name'] for p in doc['meshes'][0]['primitives']]
    assert names == ['SourceExisting', 'ReferenceFacade']
    assert sum(len(p) for p in prims) + 1 == 8  # nothing lost or duplicated


def test_bad_requests_raise(tmp_path):
    src = make_batch(tmp_path)
    with pytest.raises(ValueError):
        patch_batch(src, NODE, [99], None, tmp_path / 'x.glb')
    with pytest.raises(ValueError):
        patch_batch(src, NODE, [1], {'g': [1]}, tmp_path / 'x.glb')
    with pytest.raises(ValueError):
        patch_batch(src, NODE, list(range(8)), None, tmp_path / 'x.glb')
    with pytest.raises(ValueError):
        patch_batch(src, 'missing', [0], None, tmp_path / 'x.glb')
