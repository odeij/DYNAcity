"""Build district/kind meshes for source-draped mapped street cover."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct

from .engine_asset import export_glb


COLOURS={'grass':(.17,.25,.095),'park':(.22,.27,.13),'garden':(.19,.27,.11),
    'wood':(.105,.19,.075),'scrub':(.24,.28,.13),'forest':(.105,.19,.075),
    'sand':(.58,.49,.34),'water':(.045,.20,.27),'wetland':(.12,.22,.17),
    'parking':(.20,.21,.20),'pitch':(.18,.30,.16),'playground':(.35,.26,.17),
    'cemetery':(.29,.31,.22),'allotments':(.28,.29,.14),'farmland':(.32,.29,.15),
    'orchard':(.22,.29,.12),'brownfield':(.31,.27,.20),'construction':(.40,.34,.25),
    'footway':(.43,.40,.33),'pedestrian':(.43,.40,.33),'path':(.37,.32,.23),
    'cycleway':(.27,.28,.25),'steps':(.43,.42,.38)}
COLOURS.update(greenery=(.19,.25,.12),village_green=(.19,.25,.12),greenfield=(.34,.32,.21),
    shrubbery=(.12,.23,.08),heath=(.28,.29,.16),reedbed=(.30,.32,.15),moss=(.20,.26,.12),
    flowerbed=(.28,.23,.13),vineyard=(.25,.29,.13),plant_nursery=(.22,.29,.12),
    greenhouse_horticulture=(.47,.51,.43),green_roof=(.23,.29,.12),grass_paver=(.32,.34,.23),
    hedge=(.12,.23,.08),planter=(.29,.25,.18))


def _colour_glb(path,colour):
    raw=path.read_bytes();size,_=struct.unpack_from('<I4s',raw,12)
    doc=json.loads(raw[20:20+size]);binary=raw[20+size:]
    doc['materials'][0]['pbrMetallicRoughness']['baseColorFactor']=[*colour,1]
    doc['materials'][0]['name']='Mapped area, illustrative surface finish'
    data=json.dumps(doc,separators=(',',':')).encode()
    data+=b' '*((-len(data))%4)
    path.write_bytes(struct.pack('<4sII',b'glTF',2,12+8+len(data)+len(binary))+
                     struct.pack('<I4s',len(data),b'JSON')+data+binary)


def build(manifest,output):
    source=Path(manifest);data=json.loads(source.read_text(encoding='utf-8'))
    if data['schema']!='dynacity-engine-street-context/1':raise ValueError('Unsupported street manifest')
    signature=hashlib.sha256(source.read_bytes()).hexdigest()
    output=Path(output)/('cover-'+signature[:16]);output.mkdir(parents=True,exist_ok=True)
    groups=defaultdict(list)
    for area in data['areas']:
        if area.get('triangles'):
            groups[(area.get('production_area','Unassigned'),area['kind'])].append(area)
    packages=[]
    for (group,kind),areas in sorted(groups.items()):
        key=re.sub('[^a-z0-9]+','_',group.lower()).strip('_')+'_'+re.sub('[^a-z0-9]+','_',kind.lower())
        name='StreetCover_'+key+'_'+signature[:8];vertices=[];faces=[];objects=[]
        for area in areas:
            offset=len(vertices);start=len(faces)
            vertices.extend(area['vertices_local_m'])
            faces.extend([[i+offset for i in face] for face in area['triangles']])
            objects.append({'source_id':area['id'],'triangle_start':start,'triangle_count':len(area['triangles'])})
        scene={'coordinates':{'origin_utm_m':data['origin_utm_m'],'frame':'Local east/north/up metres'},
            'limitations':['Mapped OSM cover; source-ground drape. Material appearance is illustrative.'],
            'bounds_lonlat':None,'trees':[],'roads':[],
            'meshes':[{'id':name,'kind':'terrain','vertices':vertices,'triangles':faces,
                       'metadata':{'source_manifest':str(source),'production_area':group,'mapped_kind':kind}}]}
        path=output/(name+'.glb')
        if not path.exists():
            export_glb(scene,path,include_calibration=False)
            _colour_glb(path,COLOURS.get(kind,(.31,.29,.22)))
        packages.append({'key':key,'name':name,'kind':kind,'production_area':group,'asset':str(path),
                         'objects':objects,'triangles':len(faces)})
    result={'schema':'dynacity-street-cover-assets/1','origin_utm_m':data['origin_utm_m'],
            'source_manifest':str(source),'source_sha256':signature,'packages':packages}
    target=output/'cover-assets.json';target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return target


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(build(args.manifest,args.output))
