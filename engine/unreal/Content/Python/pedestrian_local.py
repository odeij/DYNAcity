"""Walkers on pavements near the player, at modelled weekday PM-peak densities; standard library only.

Densities come from `scripts/prepare_pedestrian_walkers.py` (UNA flows with Sevtsuk et al. 2024
coefficients and proxy inputs: modelled, not observed). Each segment near the player holds about its
modelled number of people, walking both ways on both pavements at 1.1-1.6 m/s. Frames use the traffic
frame shape (`id`, `position` in local metres, compass `heading_deg`) so a renderer can interpolate them.
"""
import json
import math
import random

STEP_S = 0.1
BATCH_S = 4.0
RADIUS_M = 200.0


class LocalPedestrians:
    def __init__(self, walkers_path, centre, seed=23, limit=160):
        data = json.loads(open(walkers_path, encoding='utf-8').read())
        self.status = data['status']
        self.segments = []
        for s in data['segments']:
            path = s['path']   # [x, y, z]: model road vertices with the pavement height
            lengths = [0.0]
            for a, b in zip(path, path[1:]):
                lengths.append(lengths[-1] + math.dist(a[:2], b[:2]))
            xs, ys = [p[0] for p in path], [p[1] for p in path]
            self.segments.append({**s, 'lengths': lengths, 'bounds': (min(xs), min(ys), max(xs), max(ys))})
        self.random = random.Random(seed)
        self.limit = limit
        self.centre = centre
        self.time_s = 0.0
        self.walkers = []
        self.next_id = 0

    def _near(self, seg):
        x0, y0, x1, y1 = seg['bounds']
        cx, cy = self.centre
        return x0 - RADIUS_M <= cx <= x1 + RADIUS_M and y0 - RADIUS_M <= cy <= y1 + RADIUS_M

    def _populate(self):
        nearby = [s for s in self.segments if self._near(s)]
        have = {}
        for w in self.walkers:
            have[id(w['seg'])] = have.get(id(w['seg']), 0) + 1
        for seg in nearby:
            # Fractional people become a probability, so the expected count matches the model.
            target = int(seg['people']) + (1 if self.random.random() < seg['people'] % 1 else 0)
            for _ in range(max(0, target - have.get(id(seg), 0))):
                if len(self.walkers) >= self.limit:
                    return
                self.walkers.append({'id': f'ped{self.next_id}', 'seg': seg, 'dir': self.random.choice((1, -1)),
                                     'side': self.random.choice((1, -1)), 'lane': self.random.uniform(-.3, .3),
                                     's': self.random.uniform(0, seg['lengths'][-1]),
                                     'speed': self.random.uniform(1.1, 1.6)})
                self.next_id += 1

    def _sample(self, w):
        seg, s = w['seg'], w['s']
        lengths, path = seg['lengths'], seg['path']
        i = 1
        while i < len(lengths) - 1 and lengths[i] < s:
            i += 1
        a, b = path[i - 1], path[i]
        t = (s - lengths[i - 1]) / max(lengths[i] - lengths[i - 1], 1e-6)
        x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        z = a[2] + (b[2] - a[2]) * t   # pavement top: road height + kerb, per vertex
        dx, dy = (b[0] - a[0]), (b[1] - a[1])
        n = math.hypot(dx, dy) or 1
        dx, dy = dx / n, dy / n
        # Stay inside the pavement: its middle, plus up to +-30 % of its width.
        off = w['side'] * (seg['offset_m'] + w['lane'] * seg.get('pavement_m', 2.0))
        x, y = x + dy * off, y - dx * off
        hx, hy = dx * w['dir'], dy * w['dir']
        return [x, y, z], (90 - math.degrees(math.atan2(hy, hx))) % 360

    def batch(self, centre=None):
        if centre is not None:
            self.centre = centre
        self.walkers = [w for w in self.walkers if self._near(w['seg'])]
        self._populate()
        frames = []
        for _ in range(int(BATCH_S / STEP_S)):
            self.time_s += STEP_S
            people = []
            for w in self.walkers:
                w['s'] += w['speed'] * STEP_S * w['dir']
                end = w['seg']['lengths'][-1]
                if w['s'] > end or w['s'] < 0:   # turn back at the segment end; keeps densities per segment
                    w['dir'] *= -1
                    w['s'] = min(end, max(0.0, w['s']))
                position, heading = self._sample(w)
                people.append({'id': w['id'], 'position': position, 'heading_deg': heading, 'type_id': 'pedestrian'})
            frames.append({'time_s': round(self.time_s, 3), 'pedestrians': people})
        return json.dumps({'frames': frames, 'status': self.status}, separators=(',', ':'))
