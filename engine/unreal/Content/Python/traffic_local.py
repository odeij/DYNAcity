"""Default ambient traffic when no SUMO server is connected; standard library only, no Unreal import.

Drop-in for `traffic_client.TrafficClient` (same `batch(view)` / `close()` contract and frame JSON):
vehicles follow modeled road centrelines near the player, offset to the right-hand lane, and turn
onto a connected road at each end. This is illustrative ambient motion, not simulated demand.
"""
import hashlib
import json
import math
import random

import location_core

STEP_S = 0.1
BATCH_S = 4.0
RADIUS_M = 420.0
JOIN_M = 4.0


class LocalTrafficClient:
    def __init__(self, index, view, count=70, seed=17):
        self.origin = index['origin_utm_m']
        self.roads = {str(r['id']): r for r in index['roads'] if len(r['path']) > 1}
        self.session = 'local'
        self.request = {}
        self.appearance = None
        self.count = count
        self.random = random.Random(seed)
        self.time_s = 0.0
        self.agents = []
        self.next_id = 0
        self.last_batch_received = self.last_batch_placed = 0
        self.last_batch_stats = {}
        self.lengths = {k: self._lengths(r['path']) for k, r in self.roads.items()}
        self.ends = {}
        for key, road in self.roads.items():
            for end, point in ((0, road['path'][0]), (1, road['path'][-1])):
                self.ends.setdefault((round(point[0] / JOIN_M), round(point[1] / JOIN_M)), []).append((key, end))
        self.centre = self._centre(view)

    @staticmethod
    def _lengths(path):
        out = [0.0]
        for a, b in zip(path, path[1:]):
            out.append(out[-1] + math.dist(a[:2], b[:2]))
        return out

    def _centre(self, view):
        lon, lat = (view[0] + view[2]) / 2, (view[1] + view[3]) / 2
        x, y = location_core.lonlat_to_utm(lon, lat)
        return x - self.origin[0], y - self.origin[1]

    def _near(self, key):
        b = self.roads[key].get('bounds')
        cx, cy = self.centre
        if b:
            return b[0] - RADIUS_M <= cx <= b[2] + RADIUS_M and b[1] - RADIUS_M <= cy <= b[3] + RADIUS_M
        return any(math.dist(p[:2], self.centre) < RADIUS_M for p in self.roads[key]['path'])

    def _spawn(self):
        candidates = [k for k in self.roads if self._near(k) and self.lengths[k][-1] > 20]
        if not candidates:
            return None
        key = self.random.choice(candidates)
        direction = self.random.choice((1, -1))
        u = self.random.random()
        # Type ids map to the installed fleet's mesh groups (mixed traffic draws only typed vehicles).
        type_id = 'van' if u < .08 else ('taxi' if u < .18 else ('car:bmw' if u < .59 else 'car:toyota'))
        agent = {'id': f'local{self.next_id}', 'road': key, 'dir': direction, 'type': type_id,
                 's': self.random.uniform(0, self.lengths[key][-1]), 'speed': self.random.uniform(7, 13)}
        self.next_id += 1
        return agent

    def _sample(self, key, s, direction):
        road, lengths = self.roads[key], self.lengths[key]
        s = max(0.0, min(lengths[-1], s))
        i = 1
        while i < len(lengths) - 1 and lengths[i] < s:
            i += 1
        a, b = road['path'][i - 1], road['path'][i]
        t = (s - lengths[i - 1]) / max(lengths[i] - lengths[i - 1], 1e-6)
        x, y, z = (a[k] + (b[k] - a[k]) * t for k in range(3))
        dx, dy = (b[0] - a[0]) * direction, (b[1] - a[1]) * direction
        norm = math.hypot(dx, dy) or 1
        dx, dy = dx / norm, dy / norm
        lane = max(1.4, (road.get('width_m') or 6) / 4)
        # Right-hand traffic: the right of travel direction (dx, dy) is (dy, -dx).
        x, y = x + dy * lane, y - dx * lane
        heading = (90 - math.degrees(math.atan2(dy, dx))) % 360   # compass, as SUMO reports it
        return [x, y, z], heading

    def _advance(self, agent, dt):
        agent['s'] += agent['speed'] * dt * agent['dir']
        length = self.lengths[agent['road']][-1]
        if 0 <= agent['s'] <= length:
            return True
        end = 1 if agent['s'] > length else 0
        point = self.roads[agent['road']]['path'][-1 if end else 0]
        options = [(k, e) for k, e in self.ends.get((round(point[0] / JOIN_M), round(point[1] / JOIN_M)), [])
                   if k != agent['road']]
        if not options:
            return False
        key, joined_end = self.random.choice(options)
        agent['road'], agent['dir'] = key, (1 if joined_end == 0 else -1)
        agent['s'] = 0.0 if joined_end == 0 else self.lengths[key][-1]
        return self._near(key)

    def batch(self, view=None):
        if view is not None:
            self.centre = self._centre(view)
        self.agents = [a for a in self.agents if self._near(a['road'])]
        while len(self.agents) < self.count:
            agent = self._spawn()
            if agent is None:
                break
            self.agents.append(agent)
        frames = []
        for _ in range(int(BATCH_S / STEP_S)):
            self.time_s += STEP_S
            survivors, vehicles = [], []
            for agent in self.agents:
                if not self._advance(agent, STEP_S):
                    continue
                survivors.append(agent)
                position, heading = self._sample(agent['road'], agent['s'], agent['dir'])
                vehicle = {'id': agent['id'], 'position': position, 'heading_deg': heading,
                           'type_id': agent['type']}
                if self.appearance:
                    from traffic_appearance import apply
                    apply(vehicle, self.appearance)
                vehicles.append(vehicle)
            self.agents = survivors
            frames.append({'time_s': round(self.time_s, 3), 'vehicles': vehicles})
        placed = sum(len(f['vehicles']) for f in frames)
        self.last_batch_received = self.last_batch_placed = placed
        return json.dumps({'frames': frames}, allow_nan=False, separators=(',', ':'))

    def close(self):
        self.session = None
