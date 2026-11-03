"""Apply reversible PBR overrides and illustrative lane paint to pilot street batches."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from unreal_tool_session import UnrealToolSession

TAG = 'DYNACITY_STREET_TWIN_FINISH_2932_15014'
VIEW = {'location':{'x':223000.,'y':-205500.,'z':10000.},
        'rotation':{'pitch':-51.1,'yaw':45.,'roll':0.},'scale':{'x':1,'y':1,'z':1}}
BOUNDS = {'min':{'x':220000,'y':-220000,'z':-10000},
          'max':{'x':250000,'y':-190000,'z':20000},'isValid':True}


def install(pbr_report, import_report, output, screenshots, url):
    pbr = json.loads(Path(pbr_report).read_text())
    imported = json.loads(Path(import_report).read_text())
    if imported['status'] != 'pass' or not imported['triangle_match'] or not imported['bounds_match']:
        raise ValueError('Pilot finish mesh import must pass geometry verification')
    mesh = imported['meshes'][0]['asset']
    output, screenshots = Path(output), Path(screenshots)
    screenshots.mkdir(parents=True,exist_ok=True)
    session = UnrealToolSession(url)
    call = session.call
    camera = call('EditorToolset.EditorAppToolset','GetCameraTransform',{})
    backup_path = output.with_name('pilot-finish-original-overrides.json')
    backup = json.loads(backup_path.read_text()) if backup_path.exists() else {}
    def find(name='',tag='',bounds=None):
        return call('scene.SceneTools','find_actors',{'root':None,'name':name,'actor_type':None,
                    'tag':tag,'bounds':bounds,'collision_channels':[]})
    def capture(name):
        result = call('EditorToolset.EditorAppToolset','CaptureViewport',
                      {'captureTransform':VIEW,'annotations':None,'bShowUI':False})
        path = screenshots/(name+'.png')
        path.write_bytes(base64.b64decode(result['image']['data']))
        return str(path)
    def signature(actor, component):
        return {'transform':call('actor.ActorTools','get_actor_transform',{'actor':actor}),
                'component':json.loads(call('object.ObjectTools','get_properties',
                    {'instance':component,'properties':['StaticMesh','BodyInstance']}))}
    report = {'schema':'dynacity-street-twin-pilot-finish/1','status':'building','changes':[],
              'surveyed_markings':False,'limits':['Illustrative marking layout; not a local survey.',
                 'Material assignment covers the existing batches that intersect the pilot bounds.',
                 'No collision, source geometry, traffic demand or driving settings changed.']}
    for record in backup.values():
        call('object.ObjectTools','set_properties',{'instance':record['component'],
             'values':json.dumps(record['properties'])})
    for paint in find(tag=TAG):
        component=call('actor.ActorTools','get_root_component',{'actor':paint})
        call('object.ObjectTools','set_properties',{'instance':component,'values':json.dumps({'bVisible':False})})
    call('EditorToolset.EditorAppToolset','SetCameraTransform',{'transform':VIEW})
    report['before_image'] = capture('street-finish-before')
    try:
        for actor in find(name='roads_',bounds=BOUNDS):
            label = call('actor.ActorTools','get_label',{'actor':actor})
            kind = ('Asphalt' if label.endswith('_material_1') else
                    'Paving' if label.endswith('_material_2') else
                    'Paint' if label.endswith('_material_10') else None)
            if kind is None:
                continue
            component = call('actor.ActorTools','get_root_component',{'actor':actor})
            before = signature(actor,component)
            old = json.loads(call('object.ObjectTools','get_properties',
                                 {'instance':component,'properties':['OverrideMaterials']}))
            if actor['refPath'] not in backup:
                backup[actor['refPath']] = {'component':component,'properties':old,'label':label}
                backup_path.write_text(json.dumps(backup,indent=2))
            if not call('object.ObjectTools','set_properties',{'instance':component,
                        'values':json.dumps({'OverrideMaterials':[pbr['materials'][kind]]})}):
                raise RuntimeError('Could not apply pilot material override')
            after = signature(actor,component)
            if before != after:
                raise RuntimeError('Existing street geometry or collision changed')
            actual = json.loads(call('object.ObjectTools','get_properties',
                                {'instance':component,'properties':['OverrideMaterials']}))
            if actual['OverrideMaterials'] != [pbr['materials'][kind]]:
                raise RuntimeError('Pilot material readback differs from requested assignment')
            report['changes'].append({'actor':actor,'label':label,'kind':kind,
                                     'material':pbr['materials'][kind],'geometry_and_collision_unchanged':True})
        slots = call('static_mesh.StaticMeshTools','get_material_slots',{'mesh':{'refPath':mesh}})
        for slot in slots:
            call('static_mesh.StaticMeshTools','set_material',{'mesh':{'refPath':mesh},
                 'slot_name':slot,'material':pbr['materials']['Paint']})
        call('asset.AssetTools','save_assets',{'asset_paths':[mesh.split('.')[0]]})
        existing = find(tag=TAG)
        if len(existing)>1:
            raise RuntimeError('Duplicate pilot finish actors need inspection')
        actor = existing[0] if existing else call('scene.SceneTools','add_to_scene_from_asset',{
            'asset_path':mesh,'name':'StreetTwin illustrative lane paint 2932_15014',
            'xform':{'location':{'x':0,'y':0,'z':0},'rotation':{'pitch':0,'yaw':0,'roll':0},
                     'scale':{'x':1,'y':1,'z':1}},'parent':None,'snap_to_ground':False})
        if not existing:
            call('actor.ActorTools','add_tag',{'actor':actor,'tag':TAG})
            call('actor.ActorTools','add_tag',{'actor':actor,'tag':'ILLUSTRATIVE_MARKINGS'})
        component = call('actor.ActorTools','get_root_component',{'actor':actor})
        # A repeated installation must replace the previous mesh as well as
        # its visibility. Paint vertices already include their surface lift.
        call('actor.ActorTools','set_actor_transform',{'actor':actor,
             'xform':{'location':{'x':0,'y':0,'z':0},'rotation':{'pitch':0,'yaw':0,'roll':0},
                          'scale':{'x':1,'y':1,'z':1}}})
        call('object.ObjectTools','set_properties',{'instance':component,
             'values':json.dumps({'StaticMesh':{'refPath':mesh},'bVisible':True,'bUseDefaultCollision':False,
                 'BodyInstance':{'CollisionEnabled':'NoCollision','CollisionProfileName':'NoCollision'}})})
        report['paint_actor'] = actor
        report['paint_actor_count'] = len(find(tag=TAG))
        report['paint_mesh'] = mesh
        report['after_image'] = capture('street-finish-after')
        report['status'] = 'awaiting_scoped_save'
        report['saved'] = False
    except Exception as exc:
        report.update(status='failed',error=str(exc))
        raise
    finally:
        output.write_text(json.dumps(report,indent=2))
        call('EditorToolset.EditorAppToolset','SetCameraTransform',{'transform':camera})
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pbr',required=True);p.add_argument('--import-report',required=True)
    p.add_argument('--output',required=True);p.add_argument('--screenshots',required=True)
    p.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    a=p.parse_args()
    print(json.dumps(install(a.pbr,a.import_report,a.output,a.screenshots,a.url),indent=2))
