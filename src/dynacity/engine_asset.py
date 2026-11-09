"""Dependency-light GLB export of the private neighbourhood interchange scene."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import struct

import numpy as np


PALETTE=[(0.70,0.65,0.54),(0.62,0.60,0.54),(0.77,0.73,0.65),(0.62,0.56,0.46),(0.73,0.69,0.61)]


def sphere_mesh(rings=6,segments=10):
    vertices=[[math.sin(math.pi*r/rings)*math.cos(2*math.pi*s/segments),
               math.sin(math.pi*r/rings)*math.sin(2*math.pi*s/segments),
               math.cos(math.pi*r/rings)] for r in range(rings+1) for s in range(segments)]
    faces=[]
    for r in range(rings):
        for s in range(segments):
            a=r*segments+s;b=r*segments+(s+1)%segments;c=a+segments;d=b+segments
            if r>0:faces.append([a,c,b])
            if r<rings-1:faces.append([b,c,d])
    return vertices,faces


def export_glb(scene, output, *, include_calibration=True):
    """GLB standard axes: X east, Y up, Z south; metres; retain IDs in extras."""
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    binary=bytearray()
    doc={'asset':{'version':'2.0','generator':'DynaCITY private scene exporter'},
         'scene':0,'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':[],
         'bufferViews':[],'accessors':[],'extras':{'coordinates':scene['coordinates'],
            'limitations':scene['limitations'],'bounds_lonlat':scene['bounds_lonlat']}}

    def accessor(array,kind,component):
        array=np.ascontiguousarray(array)
        while len(binary)%4:binary.append(0)
        view=len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':array.nbytes})
        binary.extend(array.tobytes())
        result={'bufferView':view,'componentType':component,'count':len(array),'type':kind}
        if kind=='VEC3':result.update(min=array.min(axis=0).astype(float).tolist(),max=array.max(axis=0).astype(float).tolist())
        doc['accessors'].append(result)
        return len(doc['accessors'])-1

    colours=[('Ground',(0.38,0.35,0.29)),('Asphalt',(0.055,0.065,0.075)),
             ('Concrete',(0.56,0.55,0.50)),('Foliage',(0.12,0.25,0.10)),('Bark',(0.19,0.12,0.065))]
    colours.extend((f'Illustrative facade {i}',c) for i,c in enumerate(PALETTE))
    colours.append(('Lane paint',(0.82,0.82,0.78)))  # index 5+len(PALETTE); after the facades so their indices stay put
    for name,colour in colours:
        doc['materials'].append({'name':name,'pbrMetallicRoughness':{'baseColorFactor':[*colour,1],
                                  'metallicFactor':0,'roughnessFactor':0.85},'doubleSided':False})

    def mesh(name,vertices,faces,material):
        positions=np.asarray(vertices,dtype='<f4')[:,[0,2,1]].copy();positions[:,2]*=-1
        indices=np.asarray(faces,dtype='<u4')
        if not len(indices):return None
        cross=np.cross(positions[indices[:,1]]-positions[indices[:,0]],positions[indices[:,2]]-positions[indices[:,0]])
        indices=indices[np.linalg.norm(cross,axis=1)>1e-10]
        if not len(indices):return None
        used,inverse=np.unique(indices,return_inverse=True)
        positions=positions[used];indices=inverse.reshape(-1,3).astype('<u4')
        normals=np.zeros_like(positions)
        cross=np.cross(positions[indices[:,1]]-positions[indices[:,0]],positions[indices[:,2]]-positions[indices[:,0]])
        for i in range(3):np.add.at(normals,indices[:,i],cross)
        norms=np.linalg.norm(normals,axis=1)
        normals/=np.maximum(norms[:,None],1e-12)
        if not np.isfinite(positions).all() or not np.isfinite(normals).all():
            raise ValueError('Non-finite exported geometry')
        if not np.allclose(np.linalg.norm(normals,axis=1),1,atol=1e-4):
            raise ValueError('Cannot create unit vertex normals')
        index=len(doc['meshes'])
        doc['meshes'].append({'name':name,'primitives':[{'attributes':{
            'POSITION':accessor(positions,'VEC3',5126),'NORMAL':accessor(normals.astype('<f4'),'VEC3',5126)},
            'indices':accessor(indices.reshape(-1),'SCALAR',5125),'material':material}]})
        return index

    def node(name,index,extras=None,**properties):
        if index is None:return
        doc['scenes'][0]['nodes'].append(len(doc['nodes']))
        doc['nodes'].append({'name':name,'mesh':index,'extras':extras or {},**properties})

    roads=defaultdict(list)
    for item in scene['meshes']:
        if item['kind']=='road':roads[item['metadata']['road_id']].append(item);continue
        material={'terrain':0,'concrete':2,'marking':5+len(PALETTE)}.get(item['kind'],2)
        if item['kind']=='building':material=5+int.from_bytes(hashlib.sha256(item['id'].encode()).digest()[:2],'little')%len(PALETTE)
        name=item['id'].replace(':','_')
        node(name,mesh(name,item['vertices'],item['triangles'],material),{'source_id':item['id'],'kind':item['kind'],**item['metadata']})
    for identifier,items in roads.items():
        vertices,faces=[],[]
        for item in items:
            offset=len(vertices);vertices.extend(item['vertices'])
            faces.extend([[i+offset for i in f] for f in item['triangles']])
        node(f'road_{identifier}',mesh(f'road_{identifier}',vertices,faces,1),
             {'source_id':f'road:{identifier}','kind':'road',**items[0]['metadata']})
    # Reuse a single crown mesh across every source tree.
    vertices,faces=sphere_mesh()
    crown=mesh('Illustrative tree crown',vertices,faces,3)
    trunk=mesh('Illustrative tree trunk',vertices,faces,4)
    # Calibration nodes let an engine client discover the actual importer basis
    # instead of guessing a handedness/axis conversion for moving objects.
    for name,position in [('ORIGIN',[0,0,0]),('EAST',[10,0,0]),('NORTH',[0,0,-10]),('UP',[0,10,0])]:
        if not include_calibration:break
        node('DYNACITY_BASIS_'+name,crown,{'kind':'coordinate calibration'},
             translation=position,scale=[0.2,0.2,0.2])
    for tree in scene['trees']:
        x,y,z=tree['position'];height=tree['height_m']
        extras={'source_id':tree['id'],'kind':'tree','shape_source':'illustrative',**tree['metadata']}
        node(tree['id'].replace('/','_')+'_crown',crown,extras,
             translation=[x,z+height*0.70,-y],scale=[height*0.30,height*0.34,height*0.30])
        node(tree['id'].replace('/','_')+'_trunk',trunk,{'source_id':tree['id'],'kind':'tree_trunk'},
             translation=[x,z+height*0.25,-y],scale=[0.16,height*0.25,0.16])
    doc['buffers']=[{'byteLength':len(binary)}]
    js=json.dumps(doc,allow_nan=False,separators=(',',':')).encode('utf-8')
    js+=b' '*((-len(js))%4);binary.extend(b'\0'*((-len(binary))%4))
    total=12+8+len(js)+8+len(binary)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('wb') as f:
        f.write(struct.pack('<4sII',b'glTF',2,total))
        f.write(struct.pack('<I4s',len(js),b'JSON'));f.write(js)
        f.write(struct.pack('<I4s',len(binary),b'BIN\0'));f.write(binary)
    return {'nodes':len(doc['nodes']),'meshes':len(doc['meshes']),'bytes':total}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(export_glb(json.loads(Path(args.scene).read_text(encoding='utf-8')),args.output)))


if __name__=='__main__':main()
