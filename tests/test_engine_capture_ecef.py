import hashlib
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer

from dynacity.engine_capture_ecef import prepare


def test_provisional_conversion_preserves_topology_and_source(tmp_path):
    source = tmp_path / 'capture.obj'
    ecef = Transformer.from_crs(4979, 4978, always_xy=True).transform(35.52, 33.89, 40)
    original = (f'mtllib capture.mtl\nv {ecef[0]} {ecef[1]} {ecef[2]} 0.5 0.4 0.3\n'
                'vn 1 0 0\nvt 0.1 0.2\nf 1/1/1 1/1/1 1/1/1\n')
    source.write_text(original)
    source.with_suffix('.mtl').write_text('newmtl m\nmap_Kd capture.jpg\n')
    source.with_suffix('.jpg').write_bytes(b'texture')
    with pytest.raises(ValueError, match='acknowledge'):
        prepare(source, tmp_path / 'preview', [0, 0, 0])
    result = prepare(source, tmp_path / 'preview', [0, 0, 0], assume_ecef=True)
    converted = (tmp_path / 'preview' / source.name).read_text()
    vertex = np.array([float(v) for v in next(l for l in converted.splitlines() if l.startswith('v ')).split()[1:4]])
    x, y = Transformer.from_crs(4326, 32636, always_xy=True).transform(35.52, 33.89)
    assert np.allclose(vertex, [x, y, 40], atol=1e-5)
    assert 'vt 0.1 0.2\nf 1/1/1 1/1/1 1/1/1\n' in converted
    assert source.read_text() == original
    assert result['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (tmp_path / 'preview' / 'capture.jpg').read_bytes() == b'texture'
    with pytest.raises(FileExistsError):
        prepare(source, tmp_path / 'preview', [0, 0, 0], assume_ecef=True)
