"""Validate a finished model and its LHD presentation before importing it."""
import hashlib
import json
import math
from pathlib import Path
import re

def texture_source_index(texture):
    """Resolve core glTF or explicitly supported alternate image sources."""
    source=texture.get('source')
    if source is None:
        source=texture.get('extensions',{}).get('EXT_texture_webp',{}).get('source')
    if not isinstance(source,int) or isinstance(source,bool) or source<0:
        raise ValueError('Texture has no supported image source')
    return source


def material_definition(slot_name, materials):
    """Resolve an FBX material slot without guessing between source materials.

    Blender can truncate an imported material's name to 63 bytes before the
    FBX export. Restrict the fallback to that length and reject ambiguous
    prefixes; material definitions and their source indices stay unchanged.
    """
    normalize=lambda name: re.sub('[^A-Za-z0-9_]', '_', name)
    slot=normalize(slot_name)
    names=[normalize(m.get('name', 'Material_'+str(i))) for i,m in enumerate(materials)]
    def unique(matches):
        if len(matches)>1:raise ValueError('Ambiguous source material for '+slot_name)
        return matches[0] if matches else None
    exact=unique([m for name,m in zip(names,materials) if name==slot])
    if exact is not None:return exact
    stripped=re.sub(r'[_\.]\d{3}$', '', slot)
    if stripped!=slot:
        exact=unique([m for name,m in zip(names,materials) if name==stripped])
        if exact is not None:return exact
    for candidate in (slot,stripped):
        if len(candidate)==63:
            match=unique([m for name,m in zip(names,materials) if len(name)>63 and name.startswith(candidate)])
            if match is not None:return match
    raise ValueError('No source PBR definition for '+slot_name)


def validate_vehicle(profile):
    for key in ('steering_ground_cm','eye_chassis_cm'):
        value=profile.get(key,[])
        if len(value)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
            raise ValueError('Finite three-dimensional vehicle presentation coordinates are required.')
    if (profile.get('handedness')!='left' or profile.get('mirror_cabin') is not False or
            profile['steering_ground_cm'][1]>=-15 or profile['eye_chassis_cm'][1]>=0 or
            abs(profile['steering_ground_cm'][1]-profile['eye_chassis_cm'][1])>10):
        raise ValueError('An unmirrored, authored left-hand-drive wheel and aligned driver seat are required.')


def read_prepared(folder):
    folder=Path(folder);data=json.loads((folder/'prepared.json').read_text(encoding='utf-8'))
    for key,digest in (('source','source_sha256'),('fbx','fbx_sha256')):
        if hashlib.sha256(Path(data[key]).read_bytes()).hexdigest()!=data[digest]:
            raise ValueError('Prepared asset content changed: '+key)
    provenance=folder/'source.json'
    if not provenance.is_file():provenance=Path(data['source']).parent/'source.json'
    source=json.loads(provenance.read_text(encoding='utf-8'))
    if source.get('license') not in ('CC0 1.0','CC BY 4.0'):
        raise ValueError('A recorded, approved finished-asset licence is required.')
    if data.get('vehicle'):validate_vehicle(data['vehicle'])
    data['provenance']=source
    return data
