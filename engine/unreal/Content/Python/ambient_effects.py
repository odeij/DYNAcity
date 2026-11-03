"""Prepare small local presentation materials; no downloads or weather feeds.

Assets are versioned and reused. Runtime effects are spawned only in the Play
world and never modify the saved city map or traffic demand.
"""
import unreal

ROOT = '/Game/DynaCity/AmbientV1'
CLOUD_VERSION = '7'


def prepare_assets():
    # EditorAssetLibrary metadata/existence helpers reject PIE. An empty metadata
    # result must never trigger material graph edits or shader compilation in Play.
    game = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if game is not None:
        prepared = [unreal.load_asset(ROOT + '/' + name)
                    for name in ('Clouds', 'Rear', 'Brake', 'Front', 'Indicator')]
        if any(not isinstance(material, unreal.MaterialInterface) for material in prepared):
            raise RuntimeError('Prepare ambient materials outside Play before starting driving.')
        return prepared[0], prepared[1:]

    lib = unreal.MaterialEditingLibrary
    assets = unreal.AssetToolsHelpers.get_asset_tools()

    def material(name, colour, clouds=False):
        path = ROOT + '/' + name
        # Load first: during Play does_asset_exist can report False for saved assets, and creating one then fails.
        mat = unreal.load_asset(path)
        if mat is not None:
            if not clouds or unreal.EditorAssetLibrary.get_metadata_tag(mat, 'DynaCityCloudVersion') == CLOUD_VERSION:
                return mat
            # Migrate only this generated cloud graph; keep the asset reference stable.
            lib.delete_all_material_expressions(mat)
        else:
            mat = assets.create_asset(name, ROOT, unreal.Material, unreal.MaterialFactoryNew())
        if mat is None:
            raise RuntimeError('Could not prepare ambient material: ' + path)
        mat.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
        mat.set_editor_property('used_with_instanced_static_meshes', True)
        tint = lib.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -300, 0)
        tint.set_editor_property('parameter_name', 'CloudColor' if clouds else 'LampColor')
        tint.set_editor_property('default_value', unreal.LinearColor(*colour, 1))
        lib.connect_material_property(tint, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        if clouds:
            mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
            mat.set_editor_property('two_sided', True)
            position = lib.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -950, 200)
            local = lib.create_material_expression(mat, unreal.MaterialExpressionTransformPosition, -700, 200)
            local.set_editor_property('transform_source_type', unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_WORLD)
            local.set_editor_property('transform_type', unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_LOCAL)
            if not lib.connect_material_expressions(position, '', local, ''):
                raise RuntimeError('Could not connect cloud projection coordinates.')
            clock = lib.create_material_expression(mat, unreal.MaterialExpressionTime, -700, 400)
            opacity = lib.create_material_expression(mat, unreal.MaterialExpressionCustom, -200, 300)
            opacity.set_editor_property('output_type', unreal.CustomMaterialOutputType.CMOT_FLOAT1)
            inputs = []
            for name in ('Position', 'Clock'):
                pin = unreal.CustomInput(); pin.set_editor_property('input_name', name); inputs.append(pin)
            opacity.set_editor_property('inputs', inputs)
            opacity.set_editor_property('code', '''
// Smooth value noise instead of the high-contrast ground macro texture.
struct CloudNoise {
    float hash(float2 p) {
        float3 q=frac(float3(p.xyx)*.1031);
        q+=dot(q,q.yzx+33.33);
        return frac((q.x+q.y)*q.z);
    }
    float sample(float2 p) {
        float2 i=floor(p), f=frac(p);
        f=f*f*f*(f*(f*6.-15.)+10.);
        return lerp(lerp(hash(i),hash(i+float2(1,0)),f.x),
                    lerp(hash(i+float2(0,1)),hash(i+1.),f.x),f.y);
    }
};
CloudNoise noise;
// Intersect the viewing direction with a horizontal cloud layer. This avoids
// sphere UV seams/poles and lets clouds recede naturally towards the horizon.
float3 direction=normalize(Position);
float2 p=direction.xy/max(direction.z,.12)*2.4+float2(3.7,8.2);
p+=Clock*float2(.003,.0008);
float density=0., amplitude=.57;
for(int octave=0;octave<5;octave++) {
    density+=noise.sample(p)*amplitude;
    p=mul(float2x2(.8,-.6,.6,.8),p)*2.03+float2(7.1,3.8);
    amplitude*=.47;
}
float cloud=smoothstep(.48,.73,density);
return cloud*smoothstep(.08,.24,direction.z)*.78;
''')
            for node, pin in ((local, 'Position'), (clock, 'Clock')):
                if not lib.connect_material_expressions(node, '', opacity, pin):
                    raise RuntimeError('Could not connect cloud input: ' + pin)
            lib.connect_material_property(opacity, '', unreal.MaterialProperty.MP_OPACITY)
        errors = lib.recompile_material(mat)
        if errors:
            raise RuntimeError('Ambient material compile failed: ' + path + ': ' + '; '.join(errors))
        if clouds:
            unreal.EditorAssetLibrary.set_metadata_tag(mat, 'DynaCityCloudVersion', CLOUD_VERSION)
        if not unreal.EditorAssetLibrary.save_loaded_asset(mat):
            raise RuntimeError('Could not save ambient material: ' + path)
        return mat

    lamps = [material(name, colour) for name, colour in (
        ('Rear', (.35, .006, .003)), ('Brake', (4., .015, .005)),
        ('Front', (2., 1.9, 1.6)), ('Indicator', (4., .7, .008)))]
    return material('Clouds', (.85, .89, .94), clouds=True), lamps


def configure(pawn, up):
    if not hasattr(pawn, 'configure_ambient_effects'):
        raise RuntimeError('Rebuild DynaCityDrive to enable clouds and vehicle signals.')
    clouds, lamps = prepare_assets()
    pawn.configure_ambient_effects(clouds, lamps, up)
    configure_cloud_layer(pawn)


def configure_cloud_layer(pawn):
    """Tune the native cloud component without modifying the engine material.

    The stock component spans 5–15 km, giving the street view towering clouds.
    Keep a shallow fair-weather layer, with sufficient samples for soft edges.
    This also applies to already running native plugin builds.
    """
    sky_class = unreal.load_class(None, '/Script/DynaCityDrive.DynaAmbientSky')
    for sky in unreal.GameplayStatics.get_all_actors_of_class(pawn.get_world(), sky_class):
        if sky.get_owner() != pawn:
            continue
        volume = sky.get_component_by_class(unreal.VolumetricCloudComponent)
        if volume is None:
            continue
        volume.set_absolute(False, False, True)
        volume.set_world_scale3d(unreal.Vector(1, 1, 1))
        volume.set_layer_bottom_altitude(2.0)
        volume.set_layer_height(1.8)
        volume.set_view_sample_count_scale(1.5)
        volume.set_sky_light_cloud_bottom_occlusion(.25)
        stock = unreal.load_asset('/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst')
        if stock is not None:
            material = unreal.MaterialLibrary.create_dynamic_material_instance(pawn, stock)
            material.set_scalar_parameter_value('Cloud_GlobalCoverage', -.12)
            material.set_scalar_parameter_value('Cloud_GlobalDensity', .012)
            volume.set_material(material)
