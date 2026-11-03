"""Owned SUMO session and source-road height adapter. No Unreal calls on workers."""
import json
import hashlib
import math
import urllib.request
import location_core


class TrafficClient:
    def __init__(self, index, url, view, hour=8, road_mapping=None):
        if url not in ('http://127.0.0.1:8012', 'http://127.0.0.1:8014'):
            raise ValueError('Use a configured local DynaCITY server on port 8012 or 8014.')
        self.endpoint = url + '/v1/viewer/traffic/simulation'
        self.session = None
        self.origin = index['origin_utm_m']
        self.roads = {str(r['id']): r for r in index['roads']}
        self.road_mapping = road_mapping
        if road_mapping is not None:
            digest = hashlib.sha256(json.dumps(index, sort_keys=True, separators=(',', ':'),
                                                allow_nan=False).encode()).hexdigest()
            if (not isinstance(road_mapping, dict) or road_mapping.get('schema') != 'dynacity-traffic-road-map/1'
                    or road_mapping.get('city_index_identity') != digest
                    or not isinstance(road_mapping.get('roads'), dict)
                    or not isinstance(road_mapping.get('source_road_identity'), str)):
                raise ValueError('Traffic road mapping does not match this city index.')
        self.request = {'hour': hour, 'seconds': 2, 'view': view,
                        'max_vehicles': 1000, 'continuous': True, 'mixed': False}
        self.omitted = 0
        self.last_batch_received = 0
        self.last_batch_placed = 0
        self.last_batch_stats = {}
        self._last_roads = {}

    def _position(self, lon, lat, road_id):
        road = self.roads.get(str(road_id).split(':')[0])
        if road is None: return None
        x, y = location_core.lonlat_to_utm(lon, lat)
        x -= self.origin[0]; y -= self.origin[1]
        best = None
        for a, b in zip(road['path'], road['path'][1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            t = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/max(dx*dx+dy*dy, 1e-12)))
            distance = math.hypot(a[0]+t*dx-x, a[1]+t*dy-y)
            if best is None or distance < best[0]: best = distance, a[2]+t*(b[2]-a[2])
        if best is None or best[0] > 25: return None
        return [x, y, best[1]]

    def batch(self, view=None):
        # The backend simulates one continuous network. Its view is a response
        # filter and can follow the player without restarting that simulation.
        request=dict(self.request)
        if view is not None:
            if (len(view)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in view)
                    or not -180<=view[0]<view[2]<=180 or not -90<=view[1]<view[3]<=90):
                raise ValueError('Invalid traffic view bounds.')
            request['view']=list(view)
        address = self.endpoint + ('/'+self.session if self.session else '')
        req = urllib.request.Request(address, data=json.dumps(request).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=45) as response: result = json.load(response)
        response_session=result.get('session')
        if self.session is None:
            if not isinstance(response_session,str) or not response_session:
                raise ValueError('SUMO did not return a session ID when connecting.')
            self.session=response_session
        elif response_session is not None and response_session!=self.session:
            raise ValueError('SUMO changed the owned session ID unexpectedly.')
        if self.road_mapping is not None:
            network = result.get('network') or {}
            if network.get('road_identity_sha256') != self.road_mapping['source_road_identity']:
                raise ValueError('Traffic road mapping does not match the SUMO road model.')
        frames = []
        received = placed = 0
        for frame in result['frames']:
            vehicles = []
            current_roads = {}
            for v in frame['vehicles']:
                received += 1
                road = str(v[5])
                if road.startswith(':'): road = self._last_roads.get(str(v[0]), road)
                elif self.road_mapping is not None:
                    road_id, _, lane_direction = road.partition(':')
                    mapped = self.road_mapping['roads'].get(road_id)
                    road = str(mapped) + ':' + lane_direction if mapped is not None else ':unmapped'
                current_roads[str(v[0])] = road
                position = self._position(v[1], v[2], road)
                if position is None: self.omitted += 1; continue
                vehicle = {'id': str(v[0]), 'position': position, 'heading_deg': v[3]}
                # Preserve the authoritative SUMO bitmask, including an explicit zero.
                # Missing older data stays missing; do not infer turn intent from yaw.
                if len(v) > 8:
                    bits = v[8]
                    if isinstance(bits, bool) or not isinstance(bits, int) or not 0 <= bits <= 16383:
                        raise ValueError('Invalid SUMO vehicle signal bitmask.')
                    vehicle['light_bits'] = bits
                # Older frame producers omit the type; do not invent one.
                if len(v) > 7 and isinstance(v[7], str) and v[7]:
                    vehicle['type_id'] = v[7]
                if getattr(self, 'appearance', None):
                    from traffic_appearance import apply
                    apply(vehicle, self.appearance)
                vehicles.append(vehicle)
                placed += 1
            frames.append({'time_s': frame['time_s'], 'vehicles': vehicles})
            self._last_roads = current_roads
        self.last_batch_received, self.last_batch_placed = received, placed
        from traffic_evidence import summarize_frames
        self.last_batch_stats = summarize_frames(frames)
        self.last_batch_stats.update(received_vehicle_samples=received,
                                     omitted_vehicle_samples=received-placed)
        return json.dumps({'frames': frames}, allow_nan=False, separators=(',', ':'))

    def close(self):
        if self.session:
            session, self.session = self.session, None
            req = urllib.request.Request(self.endpoint+'/'+session, method='DELETE')
            with urllib.request.urlopen(req, timeout=15): pass
