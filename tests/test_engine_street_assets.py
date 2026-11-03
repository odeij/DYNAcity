import json
import struct

from dynacity.engine_street_assets import build, COLOURS


def test_cover_batches_preserve_source_faces_and_material(tmp_path):
    # An already triangulated ring around a courtyard: do not fill the hole.
    vertices=[[0,0,0],[4,0,0],[4,4,0],[0,4,0],[1,1,0],[3,1,0],[3,3,0],[1,3,0]]
    triangles=[[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]
    source=tmp_path/'source.json'
    source.write_text(json.dumps({'schema':'dynacity-engine-street-context/1','origin_utm_m':[1,2,3],
        'areas':[{'id':'way/123','kind':'garden','production_area':'Remeil',
                  'vertices_local_m':vertices,'triangles':triangles}]}))
    target=build(source,tmp_path/'output');manifest=json.loads(target.read_text())
    package=manifest['packages'][0]
    assert package['objects']==[{'source_id':'way/123','triangle_start':0,'triangle_count':8}]
    raw=__import__('pathlib').Path(package['asset']).read_bytes()
    magic,version,length=struct.unpack_from('<4sII',raw)
    assert (magic,version,length)==(b'glTF',2,len(raw))
    size=struct.unpack_from('<I',raw,12)[0];doc=json.loads(raw[20:20+size])
    assert doc['materials'][0]['pbrMetallicRoughness']['baseColorFactor']==[*COLOURS['garden'],1]
    node=next(n for n in doc['nodes'] if n['name']==package['name'])
    primitive=doc['meshes'][node['mesh']]['primitives'][0]
    accessor=doc['accessors'][primitive['indices']]
    view=doc['bufferViews'][accessor['bufferView']]
    indices=struct.unpack_from('<24I',raw,28+size+view.get('byteOffset',0))
    assert list(indices)==[i for face in triangles for i in face]
    assert build(source,tmp_path/'output')==target
