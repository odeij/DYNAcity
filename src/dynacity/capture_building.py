"""Source-preserving preparation for one real building capture replacement."""
import copy
import hashlib
import json
from pathlib import Path
import struct

import numpy as np


def fit_xy(source,target):
    a=np.asarray(source,dtype=float);b=np.asarray(target,dtype=float)
    if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=2 or len(a)<3 or not np.isfinite([a,b]).all():
        raise ValueError('At least three finite matching XY controls required')
    ca=a.mean(0);cb=b.mean(0)
    if np.linalg.matrix_rank(a-ca)<2:raise ValueError('Control points must span an area')
    u,_,vt=np.linalg.svd((a-ca).T@(b-cb));r=vt.T@u.T
    if np.linalg.det(r)<0:vt[-1]*=-1;r=vt.T@u.T
    t=cb-ca@r.T;errors=np.linalg.norm(a@r.T+t-b,axis=1)
    return r,t,errors


def omit_source_triangles(asset,index,source_ids,output,explicit=None):
    """Copy affected nodes and omit exactly indexed triangles, preserving all others.

    ``explicit`` optionally maps a source ID to absolute batch triangle indices; only those triangles are omitted
    (the rest of that building, e.g. roof and rear walls, stays). Indices must lie inside the object's own range.

    Original bytes, positions, normals and materials remain untouched. New index
    accessors are appended to the private copy; affected nodes retain identity.
    """
    from .engine_city_batches import Glb
    original=Glb(Path(asset));metadata=json.loads(Path(index).read_text())
    selected=[row for row in metadata['objects'] if row['source_id'] in set(source_ids)]
    if {row['source_id'] for row in selected}!=set(source_ids):raise ValueError('Missing selected source ID')
    batches={row['batch'] for row in selected};doc=copy.deepcopy(original.doc);binary=bytearray(original.binary)
    kept_nodes=[n for n in doc['nodes'] if n.get('name') in batches]
    if len(kept_nodes)!=len(batches):raise ValueError('Batch node missing or duplicated')
    removed=[];retained=[]
    for node in kept_nodes:
        primitives=doc['meshes'][node['mesh']]['primitives']
        if len(primitives)!=1:raise ValueError('Expected one indexed batch primitive')
        primitive=primitives[0];indices=original.accessor(primitive['indices']).reshape(-1,3)
        keep=np.ones(len(indices),dtype=bool)
        for row in selected:
            if row['batch']!=node['name']:continue
            start,count=row['first_triangle'],row['triangle_count']
            if start<0 or count<1 or start+count>len(indices):raise ValueError('Invalid source triangle range')
            if explicit and row['source_id'] in explicit:
                chosen=np.unique(np.asarray(explicit[row['source_id']],dtype=np.int64))
                if not len(chosen) or chosen.min()<start or chosen.max()>=start+count:raise ValueError('Explicit triangle outside its source object')
                if not keep[chosen].all():raise ValueError('Overlapping source ranges')
                keep[chosen]=False;removed.append(dict(source_id=row['source_id'],triangles=int(len(chosen)),batch=node['name'],mode='explicit',of=count))
                continue
            if not keep[start:start+count].all():raise ValueError('Overlapping source ranges')
            keep[start:start+count]=False;removed.append(dict(source_id=row['source_id'],triangles=count,batch=node['name']))
        array=np.ascontiguousarray(indices[keep].reshape(-1).astype('<u4'))
        if not len(array):raise ValueError('A whole batch cannot be removed by this targeted patch')
        while len(binary)%4:binary.append(0)
        view=len(doc['bufferViews']);doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':array.nbytes})
        binary.extend(array.tobytes());ai=len(doc['accessors'])
        doc['accessors'].append({'bufferView':view,'componentType':5125,'count':len(array),'type':'SCALAR'})
        primitive['indices']=ai
        retained.append({'batch':node['name'],'triangles':int(keep.sum()),
                         'indices_sha256':hashlib.sha256(array.tobytes()).hexdigest()})
    kept_meshes=[copy.deepcopy(doc['meshes'][n['mesh']]) for n in kept_nodes]
    for i,node in enumerate(kept_nodes):node['mesh']=i
    doc['meshes']=kept_meshes;doc['nodes']=kept_nodes
    doc['scenes']=[{'nodes':list(range(len(kept_nodes)))}];doc['scene']=0
    doc['buffers']=[{'byteLength':len(binary)}]
    js=json.dumps(doc,separators=(',',':')).encode();js+=b' '*((-len(js))%4);binary.extend(b'\0'*((-len(binary))%4))
    raw=struct.pack('<4sII',b'glTF',2,12+8+len(js)+8+len(binary))+struct.pack('<I4s',len(js),b'JSON')+js+struct.pack('<I4s',len(binary),b'BIN\0')+binary
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(raw)
    # Re-open the generated copy and compare the complete remaining index arrays.
    verify=Glb(output)
    for node in verify.doc['nodes']:
        primitive=verify.doc['meshes'][node['mesh']]['primitives'][0]
        actual=verify.accessor(primitive['indices']).astype('<u4').tobytes()
        expected=next(r['indices_sha256'] for r in retained if r['batch']==node['name'])
        if hashlib.sha256(actual).hexdigest()!=expected:raise RuntimeError('Patched triangle verification failed')
    return {'source_asset':str(asset),'source_sha256':hashlib.sha256(Path(asset).read_bytes()).hexdigest(),
            'asset':str(output),'sha256':hashlib.sha256(raw).hexdigest(),'removed':removed,'retained':retained,
            'positions_normals_materials':'original buffers retained unchanged','source_modified':False}
