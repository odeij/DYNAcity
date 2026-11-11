"""Create licensed, world-projected street PBR materials through local Unreal MCP."""
import argparse
import hashlib
import json
from pathlib import Path

from verify_street_twin_import import invoke
from unreal_tool_session import UnrealToolSession

ROOT = '/Game/DynaCity/StreetTwinPilot/Finishes'


def build(url, folder, output):
    folder, output_path = Path(folder), Path(output)
    manifest = json.loads((folder / 'manifest.json').read_text())
    records = {r['id']: r for r in manifest['assets']}
    used = ['Asphalt012', 'Concrete030']
    for name in used:
        archive = folder / (name + '_1K-JPG.zip')
        if records[name]['license'] != 'CC0 1.0' or hashlib.sha256(archive.read_bytes()).hexdigest() != records[name]['sha256']:
            raise ValueError('Unverified licensed surface package: ' + name)
    # Frozen graph revision. Report/file I/O changes do not require new shaders.
    version = '2e28ef69ce'
    session = UnrealToolSession(url)
    def tool(group, name, args):
        return session.call(group, name, args)
    def properties(ref, values):
        if not tool('object.ObjectTools', 'set_properties', {'instance':ref, 'values':json.dumps(values)}):
            raise RuntimeError('Could not set material/texture properties')
    def exists(path):
        return tool('asset.AssetTools', 'exists', {'path':path})
    textures = {}
    for source in used:
        for role in ('Color', 'Roughness', 'NormalDX'):
            name = source + '_' + role
            path = ROOT + '/Textures/' + name
            if not exists(path):
                imported = tool('texture.TextureTools', 'import_file', {
                    'folder_path':ROOT+'/Textures', 'asset_name':name,
                    'source_file':str(folder/source/(source+'_1K-JPG_'+role+'.jpg'))})
                texture = imported[0]
            else:
                texture = {'refPath':path+'.'+name}
            values = {'SRGB': role == 'Color'}
            if role == 'NormalDX':
                values.update(CompressionSettings='TC_Normalmap')
            elif role == 'Roughness':
                values['CompressionSettings'] = 'TC_Masks'
            properties(texture, values)
            textures[source,role] = texture
    materials = {}
    for kind,source,size,tint in [('Asphalt','Asphalt012',400.,1.), ('Paving','Concrete030',200.,1.1), ('Paint','Concrete030',100.,1.6)]:
        name = 'M_'+kind+'PBR_'+version
        path = ROOT+'/'+name
        material = ({'refPath':path+'.'+name} if exists(path) else
                    tool('material.MaterialTools','create_material',{'folder_path':ROOT,'asset_name':name}))
        nodes = tool('material.MaterialTools','get_expressions',{'material_or_function':material})
        if not nodes:
            properties(material, {'bTangentSpaceNormal':False, 'bUsedWithInstancedStaticMeshes':True})
            def node(cls, values=None):
                result = tool('material.MaterialTools','add_expression',{
                    'material_or_function':material,'expression_class':{'refPath':'/Script/Engine.MaterialExpression'+cls},'x':-400,'y':0})
                if values: properties(result, values)
                return result
            def connect(a,b,input_name,output_name=''):
                tool('material.MaterialTools','connect_expressions',{
                    'from_expression':a,'from_output_name':output_name,'to_expression':b,'to_input_name':input_name})
            def output(a,prop,channel=''):
                tool('material.MaterialTools','connect_to_output',{'expression':a,'output_name':channel,'material_property':prop})
            position = node('WorldPosition')
            uv = node('Divide',{'ConstB':size})
            connect(position,uv,'A','XY')
            samples = {}
            for role in ('Color','Roughness','NormalDX'):
                sampler = {'Color':'SAMPLERTYPE_Color','Roughness':'SAMPLERTYPE_Masks','NormalDX':'SAMPLERTYPE_Normal'}[role]
                sample = node('TextureSample',{'Texture':textures[source,role],'SamplerType':sampler})
                connect(uv,sample,'UVs')
                samples[role] = sample
            multiply = node('Multiply',{'ConstB':tint})
            connect(samples['Color'],multiply,'A','RGB')
            output(multiply,'MP_BaseColor')
            output(samples['Roughness'],'MP_Roughness','R')
            # Planar world-space surface normals; lane grades remain geometry.
            # This is a surface finish, with no displacement or collision change.
            flat = node('Constant3Vector',{'Constant':{'r':0.,'g':0.,'b':1.,'a':1.}})
            normal = node('LinearInterpolate',{'ConstAlpha':.45})
            connect(flat,normal,'A');connect(samples['NormalDX'],normal,'B','RGB')
            output(normal,'MP_Normal')
        tool('material.MaterialTools','recompile',{'material_or_function':material})
        materials[kind] = material
    paths = sorted({r['refPath'].split('.')[0] for r in [*textures.values(),*materials.values()]})
    if not tool('asset.AssetTools','save_assets',{'asset_paths':paths}):
        raise RuntimeError('Street PBR packages did not save')
    report = {'schema':'dynacity-street-pbr/1','materials':materials,
        'textures':{s+'/'+r:t for (s,r),t in textures.items()}, 'saved_packages':paths,
        'source_archives':{s:records[s]['sha256'] for s in used},
        'license':'CC0 1.0','shader_compilation':'pass',
        'limitations':['Generic licensed asphalt/concrete; not sampled Beirut material.',
                       'Planar world-XY texture and normal projection; no displacement or collision changes.']}
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(report,indent=2))
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',required=True);p.add_argument('--output',required=True)
    p.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    args=p.parse_args()
    print(json.dumps(build(args.url,args.assets,args.output),indent=2))
