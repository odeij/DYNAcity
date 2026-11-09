"""Repeat the configured district/street batch and optionally install/reload it."""
import argparse
import json
from pathlib import Path

from prepare_street_detail import run, write
from unreal_mcp_client import call


def native(url, name, filename):
    result=call(url,'tools/call',{'name':'call_tool','arguments':{
        'toolset_name':'street_tools.StreetTools','tool_name':name,
        'arguments':{'filename':str(filename)}}},timeout=180)
    if result.get('error') or result.get('result',{}).get('isError'):
        raise RuntimeError(str(result)[:2000])
    try:
        value=json.loads(result['result']['content'][0]['text'])['returnValue']
        return json.loads(value) if isinstance(value,str) else value
    except (KeyError,ValueError,IndexError) as exc:
        raise RuntimeError('Unexpected native response: '+str(result)[:2000]) from exc


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True);p.add_argument('--install',action='store_true')
    p.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    args=p.parse_args();manifest=run(args.config)
    config=json.loads(Path(args.config).read_text(encoding='utf8'));output=Path(config['output'])
    result={'changed_streets':manifest['changed_streets'],'cache_hits':manifest['cache_hits'],
            'streets':manifest['streets'],'source_geometry_modified':False}
    if args.install:
        result['native']=native(args.url,'apply_detail',output/'manifest.json')
        result['reload']=native(args.url,'verify_detail',output/'manifest.json')
        if not result['reload']['reload_verified']: raise RuntimeError('Native reload verification failed')
        if result['reload'].get('native_ground_issues'):
            raise RuntimeError('Native ground support checks failed; see Saved/DynaCity/street-detail-reload.json')
        text=(output/'exceptions.md').read_text(encoding='utf8')
        text=text.replace('Photograph comparison and native save/reload/performance remain pending until recorded.',
                          'Native save/reload verified: '+str(result['reload']['actual'])+' stable IDs. Photograph comparison and GPU performance remain pending.')
        (output/'exceptions.md').write_text(text,encoding='utf8')
    write(output/'run-report.json',result)
    if args.install:
        root=Path(config['city_checkpoint']).parent
        profile=root/'street-detail-frame-profile.json'
        lines=['# Two-street milestone','',
               f"Installed {result['native']['instances']} instances in {result['native']['groups']} shared-mesh groups.",
               f"Reload IDs and transforms verified: {result['reload']['actual']}. Native support issues: {len(result['reload'].get('native_ground_issues',[]))}.",
               f"Changed streets on this run: {result['native']['changed']}; unchanged: {result['native']['unchanged']}.",
               'Overrides remain in '+config['overrides']+'.',
               '',f"Editor update: {result['native']['editor_time_s']:.3f} s. Preparation/cache pass: {manifest['elapsed_s']:.3f} s."]
        if profile.exists():
            values=json.loads(profile.read_text(encoding='utf8'))
            if not values.get('running') and not values.get('stale') and values.get('instances')==result['native']['instances']:
                lines.extend(['','Recorded editor Slate tick comparison (90 frames/phase; not GPU or game FPS):',str(values['results'])])
        lines.extend(['','Layout exceptions remain in [exceptions.md](exceptions.md).',
                      'Do not expand districts until disconnected sections and branch junctions are reviewed.',
                      'No suitable dated street photographs were available in the cached inputs; photographic validation remains pending.',
                      'Original simulation geometry is preserved. Overture buildings are excluded.'])
        (output/'milestone.md').write_text('\n'.join(lines),encoding='utf8')
    summary=dict(result)
    if 'native' in summary:
        summary['native']={k:v for k,v in summary['native'].items() if k!='created_instance_index'}
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
