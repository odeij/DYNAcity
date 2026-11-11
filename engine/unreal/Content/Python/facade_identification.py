"""Root-only material-slot attribution diagnostic; no original asset or scene save."""
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci
import tower_infill_native_pilot as pilot
R=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration21-identification')
def actor():return next(a for a in ci.city_world._actors() if a.get_actor_label()==pilot.REFERENCE)
def stage(action):
    ci.cb._ready();R.mkdir(parents=True,exist_ok=True)
    if action=='begin':
        pre=pilot.preflight();data={'snapshot':pilot._snapshot(),'reference':ci._state(actor()),'preflight':pre}
        (R/'backup.json').write_text(json.dumps(data,indent=2));return {'static_count':len(data['snapshot'])}
    data=json.loads((R/'backup.json').read_text());a=actor();comp=ci._component(a)
    if action in ('beige','granite','restore'):
        for i,p in enumerate(data['reference']['materials']):comp.set_material(i,unreal.load_asset(p))
        changed=[]
        if action!='restore':
            diagnostic=ci._material('IdentificationMagenta',{'base_color':[1.,0.,1.,1.],'roughness':1.,'metallic':0.})
            for i,p in enumerate(data['reference']['materials']):
                if '__'+action+'_' in p:
                    comp.set_material(i,diagnostic);changed.append({'slot':i,'original':p})
            assert changed,'No unique family slots selected'
        out={'family':action,'changed_slots':changed,'scene_saved':False,'original_assets_saved':False}
    elif action=='verify':
        after=pilot._snapshot();out={'original_static_actors_exact':after==data['snapshot'],'static_actor_count':len(after),'reference_exact':ci._state(a)==data['reference'],'scene_saved':False}
        assert out['original_static_actors_exact'] and out['reference_exact'],out
    else:raise ValueError(action)
    (R/(action+'.json')).write_text(json.dumps(out,indent=2));return out
