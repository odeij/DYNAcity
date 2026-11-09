"""Losslessly batch city GLBs by existing production group and material.

Reduces Unreal mesh asset count while preserving source-object bounds and
triangle ranges in a companion index. These are authoring groups, not new tiles.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import LineString, shape
from shapely.ops import transform


DTYPES={5126:'<f4',5125:'<u4',5123:'<u2',5121:'u1'}
WIDTHS={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}


class Glb:
    def __init__(self,path):
        self.path=Path(path)
        raw=self.path.read_bytes()
        magic,version,total=struct.unpack_from('<4sII',raw)
        if magic!=b'glTF' or version!=2 or total!=len(raw): raise ValueError('Invalid GLB')
        size,kind=struct.unpack_from('<I4s',raw,12)
        if kind!=b'JSON':raise ValueError('Expected GLB JSON')
        self.doc=json.loads(raw[20:20+size]); offset=20+size
        length,kind=struct.unpack_from('<I4s',raw,offset)
        if kind!=b'BIN\0':raise ValueError('Expected GLB binary')
        self.binary=memoryview(raw)[offset+8:offset+8+length]

    def accessor(self,index):
        accessor=self.doc['accessors'][index]; view=self.doc['bufferViews'][accessor['bufferView']]
        if accessor.get('sparse') or view.get('byteStride'):raise ValueError('Sparse/interleaved GLB not supported')
        if view.get('buffer',0)!=0:raise ValueError('External GLB buffer not supported')
        width=WIDTHS[accessor['type']]; dtype=DTYPES[accessor['componentType']]
        array=np.frombuffer(self.binary,dtype=dtype,count=accessor['count']*width,
                            offset=view.get('byteOffset',0)+accessor.get('byteOffset',0))
        return array.reshape(-1,width) if width>1 else array

    def objects(self):
        for node in self.doc['nodes']:
            if 'mesh' not in node:continue
            if any(k in node for k in ('translation','rotation','scale','matrix')):
                raise ValueError('Batch only untransformed source exporter nodes')
            mesh=self.doc['meshes'][node['mesh']]
            if len(mesh['primitives'])!=1:raise ValueError('Source exporter requires one primitive per object')
            primitive=mesh['primitives'][0]
            if primitive.get('mode',4)!=4:raise ValueError('Only triangle meshes supported')
            yield node,primitive,self.accessor(primitive['attributes']['POSITION']),self.accessor(primitive['attributes']['NORMAL']),self.accessor(primitive['indices']).reshape(-1,3)


def batch_glb(source,assign_group,output,*,kind):
    """Preserve exported positions/normals/indices/materials exactly; concatenate only."""
    glb=Glb(source); groups=defaultdict(lambda:defaultdict(list)); seen=set()
    for node,primitive,positions,normals,faces in glb.objects():
        extras=node.get('extras',{}); identifier=extras.get('source_id',node['name'])
        if identifier in seen:raise ValueError(f'Duplicate source object: {identifier}')
        seen.add(identifier)
        group,ownership=assign_group(identifier,extras)
        groups[group][primitive['material']].append((identifier,extras,positions,normals,faces,ownership))
    result=[];output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for group,materials in sorted(groups.items()):
        doc={'asset':{'version':'2.0','generator':'DynaCITY lossless district material batcher'},
            'scene':0,'scenes':[{'nodes':[]}],'nodes':[],'meshes':[],'materials':glb.doc['materials'],
            'bufferViews':[],'accessors':[],'extras':dict(glb.doc.get('extras',{}),production_group=group,
                batching='Whole source objects concatenated per material; see companion object index')}
        binary=bytearray();objects=[]
        def accessor(array,typ,component):
            array=np.ascontiguousarray(array)
            while len(binary)%4:binary.append(0)
            vi=len(doc['bufferViews']);doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':array.nbytes})
            binary.extend(array.tobytes());ai=len(doc['accessors'])
            record={'bufferView':vi,'componentType':component,'count':len(array),'type':typ}
            if typ=='VEC3':record.update(min=array.min(axis=0).astype(float).tolist(),max=array.max(axis=0).astype(float).tolist())
            doc['accessors'].append(record);return ai
        for material,items in sorted(materials.items()):
            positions=[];normals=[];faces=[];vertex_offset=triangle_offset=0
            batch_name=f'{kind}_{re.sub(r"[^a-z0-9]+","_",group.lower())}_material_{material}'
            for identifier,extras,pos,norm,tri,ownership in items:
                local=pos[:,[0,2,1]].astype(float);local[:,1]*=-1
                objects.append({'source_id':identifier,'batch':batch_name,'material':material,
                    'first_triangle':triangle_offset,'triangle_count':len(tri),
                    'first_vertex':vertex_offset,'vertex_count':len(pos),
                    'local_bounds_m':[local.min(axis=0).tolist(),local.max(axis=0).tolist()],
                    'ownership':ownership,'metadata':extras})
                positions.append(pos);normals.append(norm);faces.append(tri.astype('<u4')+vertex_offset)
                vertex_offset+=len(pos);triangle_offset+=len(tri)
            attributes={'POSITION':accessor(np.concatenate(positions).astype('<f4'),'VEC3',5126),
                        'NORMAL':accessor(np.concatenate(normals).astype('<f4'),'VEC3',5126)}
            primitive={'attributes':attributes,'indices':accessor(np.concatenate(faces).reshape(-1).astype('<u4'),'SCALAR',5125),'material':material}
            mi=len(doc['meshes']);doc['meshes'].append({'name':batch_name,'primitives':[primitive]})
            doc['scenes'][0]['nodes'].append(len(doc['nodes']))
            doc['nodes'].append({'name':batch_name,'mesh':mi,'extras':{'kind':kind,'production_group':group,
                'source_object_count':len(items),'batch':batch_name}})
        doc['buffers']=[{'byteLength':len(binary)}]
        js=json.dumps(doc,separators=(',',':'),allow_nan=False).encode();js+=b' '*((-len(js))%4)
        binary.extend(b'\0'*((-len(binary))%4));total=12+8+len(js)+8+len(binary)
        slug=re.sub(r'[^a-z0-9]+','-',group.lower()).strip('-')
        digest=hashlib.sha256(js+binary).hexdigest()[:20]
        asset=output/f'{kind}-{slug}-{digest}.glb';metadata=asset.with_suffix('.objects.json')
        if not asset.exists():
            temporary=asset.with_suffix('.partial')
            with temporary.open('wb') as stream:
                stream.write(struct.pack('<4sII',b'glTF',2,total));stream.write(struct.pack('<I4s',len(js),b'JSON'))
                stream.write(js);stream.write(struct.pack('<I4s',len(binary),b'BIN\0'));stream.write(binary)
            temporary.replace(asset)
        index={'schema':'dynacity-batched-objects/1','asset':str(asset),'kind':kind,'production_group':group,
               'coordinates':glb.doc.get('extras',{}).get('coordinates'),
               'bounds_frame':'metres relative to common UTM origin; X east, Y north, Z up',
               'triangle_range':'first_triangle and triangle_count within named batch mesh; zero based',
               'objects':objects}
        metadata.write_text(json.dumps(index,separators=(',',':')),encoding='utf-8')
        result.append({'name':kind+'-'+slug,'production_group':group,'kind':kind,'asset':str(asset),'metadata':str(metadata),
                       'mesh_count':len(doc['meshes']),'object_count':len(objects),'bytes':total})
    return result


def build_city_batches(buildings_path,surface_path,districts_path,output):
    buildings=json.loads(Path(buildings_path).read_text());surface=json.loads(Path(surface_path).read_text())
    districts=json.loads(Path(districts_path).read_text());output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if buildings['origin_utm_m']!=surface['origin_utm_m']:raise ValueError('City layers have different origins')
    jobs=[]
    for package in buildings['packages']:
        name=package['name']
        jobs.extend(batch_glb(package['asset'],lambda identifier,extras,n=name:(n,'existing whole-building owner'),output,kind='buildings'))
    projector=Transformer.from_crs(4326,32636,always_xy=True).transform
    features=districts['groups']['features'];names=[f['properties']['name'] for f in features]
    polygons=[shapely.make_valid(transform(projector,shape(f['geometry']))) for f in features]
    road_data=json.loads(Path(surface['roads_metadata']).read_text());origin=np.asarray(surface['origin_utm_m'])
    owners={}
    tree=shapely.STRtree(polygons)
    for road in road_data['roads']:
        path=np.asarray(road['path'])+origin;line=LineString(path[:,:2])
        candidates=tree.query(line,predicate='intersects')
        overlaps=[(line.intersection(polygons[int(i)]).length,names[int(i)]) for i in candidates]
        overlaps=[(length,name) for length,name in overlaps if length>0]
        if overlaps:owner=min(overlaps,key=lambda item:(-item[0],item[1]))[1];reason='largest whole-road length overlap with source sector'
        else:
            owner=min(zip(polygons,names),key=lambda item:(line.distance(item[0]),item[1]))[1]
            reason='nearest source sector; road outside named polygon coverage'
        owners[f'road:{road["id"]}']=(owner,reason)
    roads_asset=next(layer['asset'] for layer in surface['layers'] if layer['name']=='roads')
    # A road's pavement goes to the same area as its asphalt.
    jobs.extend(batch_glb(roads_asset,lambda identifier,extras:owners[f'road:{extras["road_id"]}'],output,kind='roads'))
    result={'schema':'dynacity-city-batches/1','origin_utm_m':buildings['origin_utm_m'],'jobs':jobs,
        'mesh_count':sum(j['mesh_count'] for j in jobs),'source_objects':sum(j['object_count'] for j in jobs),
        'bytes':sum(j['bytes'] for j in jobs),
        'shared_layers':[layer for layer in surface['layers'] if layer['name']!='roads'],
        'limitations':['Source geometry and materials preserved; Unreal runtime performance not measured.',
            'Bounds/triangle ranges support source inspection; imported triangle reorder requires bounds lookup or rebuilt mappings.',
            'District material batches trade object selection granularity for fewer engine assets.',
            'Nearest-sector road ownership is for authoring only, explicitly marked in the object index.',
            'Original terrain and structures remain shared layers; no duplicate pilot import.']}
    (output/'city-batches.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--buildings',required=True);parser.add_argument('--surface',required=True)
    parser.add_argument('--districts',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=build_city_batches(args.buildings,args.surface,args.districts,args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('jobs','limitations','shared_layers')},indent=2))


if __name__=='__main__':main()
