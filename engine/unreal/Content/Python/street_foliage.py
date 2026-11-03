"""UV-mapped PBR bark and masked leaf materials for local CC0 tree assets."""
import hashlib
from pathlib import Path
import unreal

ROOT='/Game/Beirut/Streets/Foliage'


def texture(filename,role,green_flip=False):
    source=Path(filename);digest=hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    name=role+'_'+digest;path=ROOT+'/Textures/'+name
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        task=unreal.AssetImportTask();task.filename=str(source);task.destination_path=ROOT+'/Textures'
        task.destination_name=name;task.automated=True;task.save=True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    tex=unreal.load_asset(path)
    if tex is None:raise RuntimeError('Missing foliage map '+str(source))
    tex.modify();tex.set_editor_property('srgb',role=='base_color')
    if role=='normal':tex.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_NORMALMAP)
    if role=='normal':tex.set_editor_property('flip_green_channel',green_flip)
    if role in ('opacity','roughness'):tex.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_MASKS)
    unreal.EditorAssetLibrary.save_loaded_asset(tex);return tex


def apply(mesh,records):
    lib=unreal.MaterialEditingLibrary;slots=mesh.get_editor_property('static_materials')
    for record in records:
        signature=hashlib.sha256(('masked-sampler-v2'+repr(sorted(record.items()))).encode()).hexdigest()[:12]
        name='M_'+record['slot_name']+'_'+signature;path=ROOT+'/'+name
        mat=unreal.load_asset(path)
        if mat is None:
            mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT,unreal.Material,unreal.MaterialFactoryNew())
            mat.set_editor_property('two_sided',record.get('two_sided',False))
            if record.get('masked'):
                mat.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
                mat.set_editor_property('opacity_mask_clip_value',.35)
                mat.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
            for role,prop,channel in [('base_color',unreal.MaterialProperty.MP_BASE_COLOR,'RGB'),
                ('normal',unreal.MaterialProperty.MP_NORMAL,'RGB'),
                ('roughness',unreal.MaterialProperty.MP_ROUGHNESS,'R'),
                ('opacity',unreal.MaterialProperty.MP_OPACITY_MASK,'R')]:
                if not record.get(role):continue
                node=lib.create_material_expression(mat,unreal.MaterialExpressionTextureSample,-400,0)
                node.set_editor_property('texture',texture(record[role],role,record.get('normal_green_flip',False)))
                if role=='normal':node.set_editor_property('sampler_type',unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
                elif role!='base_color':node.set_editor_property('sampler_type',unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
                if not lib.connect_material_property(node,channel,prop):raise RuntimeError('Could not connect foliage '+role)
                if role=='base_color' and record.get('masked'):
                    lib.connect_material_property(node,'RGB',unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
                    if not record.get('opacity'):lib.connect_material_property(node,'A',unreal.MaterialProperty.MP_OPACITY_MASK)
            if not record.get('roughness'):
                node=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-400,400)
                node.set_editor_property('r',.8);lib.connect_material_property(node,'',unreal.MaterialProperty.MP_ROUGHNESS)
            if record.get('masked'):
                transmission=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-400,600)
                transmission.set_editor_property('r',.35)
                lib.connect_material_property(transmission,'',unreal.MaterialProperty.MP_OPACITY)
            lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat)
        indices=[i for i,s in enumerate(slots) if str(s.material_slot_name)==record['slot_name'] or str(getattr(s,'imported_material_slot_name',''))==record['slot_name']]
        if not indices:raise RuntimeError('Missing tree material slot '+record['slot_name']+' in '+str([s.material_slot_name for s in slots]))
        mesh.modify()
        for index in indices:mesh.set_material(index,mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
