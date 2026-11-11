"""Convert existing SUMO API frames into the exported scene's local metre frame."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import urllib.request

import numpy as np
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .viewer import lonlat_to_utm


class SceneTraffic:
    def __init__(self,scene):
        self.origin=scene['coordinates']['origin_utm_m']
        self.roads={r['id']:r for r in scene['roads']}
        self.paths=list(self.roads.values())
        self.lines=[LineString([p[:2] for p in r['path']]) for r in self.paths]
        self.index=STRtree(self.lines) if self.lines else None

    def position(self,lon,lat,road_id=None):
        x,y=lonlat_to_utm(lon,lat,36,True)
        x-=self.origin[0];y-=self.origin[1]
        point=Point(x,y)
        try:road=self.roads.get(int(str(road_id).split(':')[0]))
        except ValueError:road=None
        if road is None:
            if self.index is None:return None
            index=int(self.index.nearest(point))
            if self.lines[index].distance(point)>25:return None
            road=self.paths[index]
        best=None
        for a,b in zip(road['path'],road['path'][1:]):
            a,b=np.asarray(a),np.asarray(b)
            delta=b[:2]-a[:2]
            t=float(np.clip(np.dot(np.array([x,y])-a[:2],delta)/max(np.dot(delta,delta),1e-12),0,1))
            distance=float(np.linalg.norm(a[:2]+t*delta-[x,y]))
            if best is None or distance<best[0]:best=(distance,float(a[2]+t*(b[2]-a[2])))
        if best is None or best[0]>25:return None
        return [round(x,4),round(y,4),round(best[1],4)]

    def frame(self,frame):
        vehicles=[];signals=[];omitted=0
        for v in frame.get('vehicles',[]):
            position=self.position(v[1],v[2],v[5])
            if position is None:omitted+=1;continue
            vehicles.append({'id':v[0],'position':position,'heading_deg':v[3],'speed_mps':v[4],
                             'road_id':v[5],'lane':v[6],'type':v[7],'light_bits':v[8] if len(v)>8 else 0})
        for s in frame.get('signals',[]):
            position=self.position(s[2],s[3],s[9] if len(s)>9 else None)
            if position is not None:
                signals.append({'junction':s[0],'movement':s[1],'position':position,'state':s[4],
                                'observed':s[5],'geometry':s[6:]})
        return {'time_s':frame['time_s'],'vehicles':vehicles,'signals':signals,
                'metrics':frame.get('metrics',{}),'omitted_without_scene_road':omitted}


def record(scene,url,*,seconds=20,hour=8):
    """Own one independent API session, always release it, preserve source timesteps."""
    converter=SceneTraffic(scene)
    endpoint=url.rstrip('/')+'/v1/viewer/traffic/simulation'
    request={'hour':hour,'seconds':2,'view':scene['bounds_lonlat'],
             'max_vehicles':2000,'continuous':True,'mixed':False}
    session=None;frames=[];assumptions=None
    def post(address):
        req=urllib.request.Request(address,data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as response:return json.load(response)
    try:
        result=post(endpoint);session=result['session']
        for batch in range(math.ceil(seconds/2)):
            if batch:result=post(endpoint+'/'+session)
            frames.extend(converter.frame(f) for f in result['frames'])
            assumptions=result.get('assumptions',assumptions)
        return {'schema':'dynacity-engine-replay/1','coordinates':scene['coordinates'],
                'source':'SUMO through existing DynaCITY API; simulated trajectories, not observed trips',
                'hour':hour,'assumptions':assumptions,'frames':frames}
    finally:
        if session:
            req=urllib.request.Request(endpoint+'/'+session,method='DELETE')
            with urllib.request.urlopen(req,timeout=30):pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8014')
    parser.add_argument('--seconds',type=int,default=20);parser.add_argument('--hour',type=int,default=8)
    args=parser.parse_args();output=Path(args.output)
    if output.exists():raise FileExistsError(output)
    if not 2<=args.seconds<=300:raise ValueError('Record between 2 and 300 seconds')
    scene=json.loads(Path(args.scene).read_text(encoding='utf-8'))
    replay=record(scene,args.url,seconds=args.seconds,hour=args.hour)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(replay,allow_nan=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'frames':len(replay['frames']),'max_visible_vehicles':max(len(f['vehicles']) for f in replay['frames']),
                      'signal_movements':max(len(f['signals']) for f in replay['frames']),'output':str(output)}))


if __name__=='__main__':main()
