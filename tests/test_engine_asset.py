import json
import struct
import numpy as np
import pytest

from dynacity.engine_asset import export_glb


def test_glb_structure_coordinate_conversion_and_source_identity(tmp_path):
    scene={'coordinates':{'units':'metres'},'limitations':[],'bounds_lonlat':[35,33,36,34],
           'trees':[],'meshes':[{'id':'beirut001:abc','kind':'building',
           'vertices':[[0,0,0],[10,0,0],[0,20,30]],'triangles':[[0,1,2]],
           'metadata':{'bbed_objectid':123}}]}
    target=tmp_path/'scene.glb';result=export_glb(scene,target);raw=target.read_bytes()
    magic,version,length=struct.unpack_from('<4sII',raw)
    assert (magic,version,length)==(b'glTF',2,len(raw))
    json_length,chunk_type=struct.unpack_from('<I4s',raw,12)
    assert chunk_type==b'JSON'
    doc=json.loads(raw[20:20+json_length])
    assert doc['nodes'][0]['extras']['source_id']=='beirut001:abc'
    assert doc['nodes'][0]['extras']['bbed_objectid']==123
    bin_length,bin_type=struct.unpack_from('<I4s',raw,20+json_length)
    assert bin_type==b'BIN\0'
    binary=raw[28+json_length:]
    assert bin_length==len(binary)
    view=doc['bufferViews'][0]
    p=np.frombuffer(binary,dtype='<f4',offset=view['byteOffset'],count=9).reshape(-1,3)
    assert np.array_equal(p,[[0,0,0],[10,0,0],[0,30,-20]])
    for view in doc['bufferViews']:
        assert view['byteOffset']%4==0
        assert view['byteOffset']+view['byteLength']<=len(binary)
    assert result['meshes']==3  # Building plus two shared tree meshes.
    names={node['name']:node for node in doc['nodes']}
    assert names['DYNACITY_BASIS_NORTH']['translation']==[0,0,-10]
    assert names['DYNACITY_BASIS_UP']['translation']==[0,10,0]
    with pytest.raises(FileExistsError):export_glb(scene,target)
