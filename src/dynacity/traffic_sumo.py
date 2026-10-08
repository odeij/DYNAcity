"""Optional SUMO microscopic traffic, using the same fleet for replay and metrics.

Generated OD trips are assumptions calibrated to historical density, not GPS tracks.
All working XML/net files live in temporary directories outside the repository.
"""
from __future__ import annotations

import importlib
import math
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

from .contracts import HazardEvent
from .traffic import TrafficModel, RoadDisruption, background_flow, lanes_of
from .viewer import _utm_zone, utm_to_lonlat

ASSUMPTIONS = (
    "SUMO simulated trajectories, not measured GPS trips. Historical 2014–2019 hourly speeds "
    "set desired speeds; 2016–17 road-class counts estimate initial density. OD destinations "
    "and turning demand are synthetic and not calibrated. Lane counts use OSM or class defaults; "
    "junction priority and signal timing are generated assumptions, not surveyed controls. "
    "The finite represented fleet drains as trips finish; it is not a continuous demand forecast."
)


def runtime():
    try:
        traci = importlib.import_module('traci')
        sumolib = importlib.import_module('sumolib')
        binaries = [sumolib.checkBinary(name) for name in ('sumo', 'netconvert')]
        if not all(shutil.which(binary) or Path(binary).is_file() for binary in binaries):
            raise RuntimeError('SUMO binaries missing')
        return traci, *binaries
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError('SUMO unavailable: install eclipse-sumo and traci and put SUMO binaries on PATH or set SUMO_HOME') from exc


def export_network(model: TrafficModel, directory: Path, netconvert: str, hour: int | None = None) -> Path:
    """Preserve directed road identities and OSM connectivity, including parallel arcs."""
    nodes, edges = ET.Element('nodes'), ET.Element('edges')
    coordinates, degree = {}, {}
    for i, points in enumerate(model.geometry):
        for node, point in ((int(model.u[i]), points[0]), (int(model.v[i]), points[-1])):
            coordinates[node] = point
            degree[node] = degree.get(node, 0) + 1
    # Split-way model does not retain OSM traffic_signals tags. Guess only large
    # junction controls, and record this assumption explicitly in every response.
    for node, (x, y) in coordinates.items():
        ET.SubElement(nodes, 'node', id=str(node), x=str(x), y=str(y),
                      type='traffic_light' if degree[node] >= 4 else 'priority')
    lanes = lanes_of(model)
    for i, points in enumerate(model.geometry):
        for direction in (1, -1):
            if model.oneway[i] == -direction:
                continue
            shape = points if direction == 1 else list(reversed(points))
            ET.SubElement(edges, 'edge', id=f'{i}:{direction}',
                          **{'from': str(int(model.u[i] if direction == 1 else model.v[i])),
                             'to': str(int(model.v[i] if direction == 1 else model.u[i]))},
                          numLanes=str(max(1, round(float(lanes[i])))),
                          speed=str(max(1, float(model.free_kmh[i] if hour is None else model.speed_kmh[i, hour]) / 3.6)),
                          shape=' '.join(f'{x},{y}' for x, y in shape), allow='passenger',
                          spreadType='right')
    for name, root in (('nodes', nodes), ('edges', edges)):
        ET.ElementTree(root).write(directory / f'{name}.xml')
    net = directory / 'network.net.xml'
    result = subprocess.run([netconvert, '--node-files', str(directory / 'nodes.xml'),
                             '--edge-files', str(directory / 'edges.xml'), '-o', str(net),
                             '--no-turnarounds', 'true', '--junctions.corner-detail', '5'],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'SUMO network conversion failed: {result.stderr[-2000:]}')
    return net


@dataclass(frozen=True)
class Trip:
    id: str
    route: tuple[str, ...]
    lane: int
    position: float


def demand(model, net, hour, seed=42):
    """Full estimated initial fleet, with fixed reachable destinations.

    Reachable edge walks choose an OD destination; SUMO then computes a legal
    shortest route. Separate route per trip also allows parallel streets.
    """
    rng = random.Random(seed)
    flow = background_flow(model)[:, hour]
    trips = []
    for edge in sorted(net.getEdges(), key=lambda e: e.getID()):
        i = int(edge.getID().split(':')[0])
        expected = float(flow[i]) * float(model.length_m[i]) / 1000 / max(3, float(model.speed_kmh[i, hour]))
        count = int(expected) + (rng.random() < expected % 1)
        for k in range(count):
            target = edge
            for _ in range(rng.randint(4, 12)):
                exits = list(target.getOutgoing())
                if not exits:
                    break
                target = rng.choice(sorted(exits, key=lambda e: e.getID()))
            route, _ = net.getShortestPath(edge, target)
            if route is None:
                route = [edge]
            lane = k % edge.getLaneNumber()
            # Ordered spatial seeding, safety insertion queues surplus demand.
            row = k // edge.getLaneNumber()
            rows = math.ceil(count / edge.getLaneNumber())
            position = min(edge.getLength() - 0.1, (row + 0.5) * edge.getLength() / rows)
            trips.append(Trip(f'vehicle-{len(trips)}', tuple(e.getID() for e in route), lane, max(0, position)))
    return trips


class Simulation:
    def __init__(self, model: TrafficModel, hour=8, *, trips=None, seed=42):
        self.model, self.hour = model, hour
        self.lock = threading.RLock()
        self.last_used = time.monotonic()
        self.directory = tempfile.TemporaryDirectory(prefix='dynacity-sumo-')
        self.connections = []
        self.closed = set()
        self.last_factors = None
        self.seen = set()
        self.retry = set()
        self.blocked = set()
        self.rerouted = set()
        self.arrivals = [{}, {}]
        self.traci, binary, converter = runtime()
        try:
            path = export_network(model, Path(self.directory.name), converter, hour)
            import sumolib
            self.net = sumolib.net.readNet(str(path))
            represented = {int(e.getID().split(':')[0]) for e in self.net.getEdges()}
            self.network_quality = {'edges': len(self.net.getEdges()),
                                    'assumed_signals': len(self.net.getTrafficLights()),
                                    'excluded_roads': sorted(set(range(len(model))) - represented)}
            self.trips = demand(model, self.net, hour, seed) if trips is None else trips
            self.trip_ids = {t.id for t in self.trips}
            self.routes = {t.id: t.route for t in self.trips}
            self.held = set()
            routes_xml = ET.Element('routes')
            for trip in self.trips:
                vehicle = ET.SubElement(routes_xml, 'vehicle', id=trip.id, depart='0',
                                        departLane=str(trip.lane), departPos=str(trip.position), departSpeed='0')
                ET.SubElement(vehicle, 'route', edges=' '.join(trip.route))
            routes_path = Path(self.directory.name) / 'trips.rou.xml'
            ET.ElementTree(routes_xml).write(routes_path)
            self.offset = self.net.getLocationOffset()
            self.zone, self.north = _utm_zone(model.crs)
            self.context_node = self.net.getNodes()[0].getID()
            bounds = self.net.getBBoxXY()
            self.context_radius = math.hypot(bounds[1][0] - bounds[0][0], bounds[1][1] - bounds[0][1]) + 100
            constants = self.traci.constants
            variables = (constants.VAR_POSITION, constants.VAR_ANGLE, constants.VAR_SPEED,
                         constants.VAR_ROAD_ID, constants.VAR_LANE_INDEX, constants.VAR_ROUTE_INDEX)
            self.variables = variables
            for kind in ('scenario', 'baseline'):
                label = f'dynacity-{uuid.uuid4().hex}'
                self.traci.start([binary, '-n', str(path), '-r', str(routes_path), '--step-length', '0.2', '--seed', str(seed),
                                  '--time-to-teleport', '-1', '--time-to-teleport.disconnected', '-1',
                                  '--collision.action', 'warn', '--no-step-log', 'true',
                                  '--duration-log.disable', 'true', '--no-warnings', 'true'], label=label,
                                 stdout=subprocess.DEVNULL)
                c = self.traci.getConnection(label)
                self.connections.append(c)
                c.simulationStep()
                if kind == 'scenario':
                    c.junction.subscribeContext(self.context_node, constants.CMD_GET_VEHICLE_VARIABLE,
                                                self.context_radius, variables)
            self.last_factors = np.ones(len(model))
            self.seen = set(self.connections[0].vehicle.getIDList())
            # Exported edges permit passenger only; preserve their generated permissions.
            self.permissions = {lane.getID(): tuple(sumolib.net.lane.SUMO_VEHICLE_CLASSES - lane.getPermissions())
                                for edge in self.net.getEdges() for lane in edge.getLanes()}
        except Exception:
            self.close()
            raise

    def close(self):
        for c in self.connections:
            try:
                c.close()
            except Exception:
                pass
        self.connections = []
        self.directory.cleanup()

    def apply(self, disruption: RoadDisruption):
        c = self.connections[0]
        closed = {edge.getID() for edge in self.net.getEdges()
                  if disruption.closed[int(edge.getID().split(':')[0])]}
        changed = closed != self.closed or self.last_factors is None or not np.array_equal(self.last_factors, disruption.speed_factor)
        if not changed:
            candidates = set(c.vehicle.getIDList()) - self.seen | self.retry
        else:
            candidates = set(c.vehicle.getIDList())
        for edge in self.net.getEdges() if changed else ():
            eid = edge.getID()
            i = int(eid.split(':')[0])
            if self.last_factors is None or disruption.speed_factor[i] != self.last_factors[i]:
                c.edge.setMaxSpeed(eid, max(0.1, float(self.model.speed_kmh[i, self.hour]) * float(disruption.speed_factor[i]) / 3.6))
            if (eid in closed) != (eid in self.closed):
                for lane in edge.getLanes():
                    original = self.permissions[lane.getID()]
                    c.lane.setDisallowed(lane.getID(), list(set(original) | {'passenger'}) if eid in closed else original)
        previous_closed = self.closed
        self.closed = closed
        self.last_factors = disruption.speed_factor.copy()
        # Retry after every chunk, including cars which were in internal junctions
        # during the last change and cars newly released from insertion queues.
        active = set(c.vehicle.getIDList())
        self.blocked.intersection_update(active)
        self.seen = active
        self.retry = set()
        if changed:
            self.blocked.clear()
        states = c.junction.getContextSubscriptionResults(self.context_node) or {}
        for vid in candidates:
            state = states.get(vid, {})
            road = state.get(self.traci.constants.VAR_ROAD_ID)
            index = state.get(self.traci.constants.VAR_ROUTE_INDEX)
            before = self.routes[vid]
            if index is None:
                index = c.vehicle.getRouteIndex(vid)
            needs_route = bool(closed.intersection(before[index + 1:])) or (bool(previous_closed - closed) and vid in self.rerouted)
            if not needs_route and road not in closed and vid not in self.held:
                continue
            if road is None:
                road = c.vehicle.getRoadID(vid)
            if road.startswith(':'):
                self.retry.add(vid)
                continue
            if needs_route:
                try:
                    c.vehicle.rerouteTraveltime(vid, currentTravelTimes=False)
                except self.traci.TraCIException:
                    pass
                after = c.vehicle.getRoute(vid)
            else:
                after = before
            self.routes[vid] = after
            if after != before:
                self.rerouted.add(vid)
            after_index = c.vehicle.getRouteIndex(vid) if after != before else index
            if closed.intersection(after[after_index + 1:]):
                self.blocked.add(vid)
            # Cars already on a closed edge queue in place and resume on reopen.
            if road in closed:
                c.vehicle.setSpeed(vid, 0)
                self.held.add(vid)
            elif vid in self.held:
                c.vehicle.setSpeed(vid, -1)
                self.held.discard(vid)

    def frame(self):
        c = self.connections[0]
        vehicles = []
        # One junction context subscription returns the full fleet without
        # thousands of per-vehicle socket round trips.
        constants = self.traci.constants
        states = c.junction.getContextSubscriptionResults(self.context_node) or {}
        for vid, state in states.items():
            x, y = state[constants.VAR_POSITION]
            lon, lat = utm_to_lonlat(x - self.offset[0], y - self.offset[1], self.zone, self.north)
            vehicles.append([vid, round(lon, 7), round(lat, 7),
                             state[constants.VAR_ANGLE], state[constants.VAR_SPEED],
                             state[constants.VAR_ROAD_ID], state[constants.VAR_LANE_INDEX]])
        active_ids = set(c.vehicle.getIDList())
        pending_ids = set(c.simulation.getPendingVehicles())
        active = len(active_ids)
        arrived = len(self.arrivals[0])
        pending = len(pending_ids)
        if self.trip_ids != active_ids | pending_ids | self.arrivals[0].keys():
            raise RuntimeError('SUMO lost a represented vehicle')
        paired = self.arrivals[0].keys() & self.arrivals[1].keys()
        delays = [self.arrivals[0][v] - self.arrivals[1][v] for v in paired]
        now = c.simulation.getTime()
        overdue = sum(v not in self.arrivals[0] for v in self.arrivals[1])
        detour_counts = {}
        for vehicle in vehicles:
            if vehicle[0] in self.rerouted and not vehicle[5].startswith(':'):
                road = int(vehicle[5].split(':')[0])
                detour_counts[road] = detour_counts.get(road, 0) + 1
        metrics = {'detour': sorted(detour_counts.items()), 'total': len(self.trips), 'active': active, 'pending': pending,
                   'arrived': arrived, 'stopped_on_closed': len(self.held & active_ids), 'no_detour': len(self.blocked), 'rerouted': len(self.rerouted),
                   'paired_arrivals': len(paired), 'overdue': overdue,
                   'mean_completed_delay_s': float(np.mean(delays)) if delays else None}
        if {v[0] for v in vehicles} != active_ids:
            raise RuntimeError('SUMO replay is missing an active vehicle')
        if pending < 0 or c.simulation.getStartingTeleportNumber():
            raise RuntimeError('SUMO vehicle accounting invariant failed')
        return {'time_s': now, 'vehicles': vehicles, 'metrics': metrics}

    def advance(self, disruption, seconds=2, *, include_initial=True):
        with self.lock:
            self.last_used = time.monotonic()
            self.apply(disruption)
            frames = [self.frame()] if include_initial else []
            remaining = round(seconds * 5)
            while remaining:
                ticks = min(5, remaining)
                for index, c in enumerate(self.connections):
                    # SUMO still advances internally at 0.2 s. One TraCI call
                    # returns the one-second sample and all arrival IDs since
                    # the previous call, avoiding full-fleet decoding at 5 Hz.
                    if index == 0:
                        c.junction.unsubscribeContext(self.context_node, self.traci.constants.CMD_GET_VEHICLE_VARIABLE,
                                                      self.context_radius)
                    c.simulationStep(c.simulation.getTime() + ticks * 0.2)
                    if index == 0:
                        c.junction.subscribeContext(self.context_node, self.traci.constants.CMD_GET_VEHICLE_VARIABLE,
                                                    self.context_radius, self.variables)
                    self.arrivals[index].update({v: c.simulation.getTime() for v in c.simulation.getArrivedIDList()})
                frames.append(self.frame())
                remaining -= ticks
            return {'frames': frames, 'assumptions': ASSUMPTIONS, 'network': self.network_quality}


def install_routes(app, model, snapshot, ground=None):
    """Traffic-only API registration; sessions own independent TraCI connections."""
    from fastapi import HTTPException
    from pydantic import BaseModel, Field, ConfigDict
    from .contracts import HazardEvent
    from .traffic import road_events, road_disruption, combine_disruptions

    class Request(BaseModel):
        model_config = ConfigDict(extra='forbid')
        hour: int = Field(default=8, ge=0, le=23)
        seconds: float = Field(default=2, ge=0.2, le=10)
        closed: list[int] = Field(default_factory=list, max_length=1000)
        crashes: list[int] = Field(default_factory=list, max_length=20)
        hazards: list[HazardEvent] = Field(default_factory=list, max_length=8)

    # FastAPI evaluates annotations in module globals (Request is local).
    sessions = {}
    registry_lock = threading.RLock()

    def disruption(request):
        parts = [road_events(model, closed=request.closed, crashes=request.crashes)]
        if request.hazards:
            parts.append(road_disruption(request.hazards, snapshot, model, ground=ground))
        return combine_disruptions(*parts)

    def create(request):
        try:
            changes = disruption(request)
            with registry_lock:
                for key, sim in list(sessions.items()):
                    if time.monotonic() - sim.last_used > 120:
                        with sim.lock:
                            sim.close()
                        del sessions[key]
                if len(sessions) >= 4:
                    raise HTTPException(429, 'Four traffic sessions are active; close a viewer or retry after two minutes')
                sim = Simulation(model, request.hour)
                key = uuid.uuid4().hex
                sessions[key] = sim
            try:
                return {'session': key, **sim.advance(changes, request.seconds)}
            except Exception:
                with registry_lock:
                    sessions.pop(key, None)
                with sim.lock:
                    sim.close()
                raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, str(exc)) from exc

    def step(key: str, request):
        with registry_lock:
            sim = sessions.get(key)
            if sim is not None:
                sim.lock.acquire()
        if sim is None:
            raise HTTPException(404, 'Traffic session expired')
        try:
            if sim.hour != request.hour:
                raise HTTPException(409, 'Create a new session when changing the hour')
            return sim.advance(disruption(request), request.seconds, include_initial=False)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, str(exc)) from exc
        finally:
            sim.lock.release()

    def delete(key: str):
        with registry_lock:
            sim = sessions.pop(key, None)
        if sim:
            with sim.lock:
                sim.close()
        return {'closed': True}

    create.__annotations__['request'] = Request
    step.__annotations__['request'] = Request
    app.post('/v1/viewer/traffic/simulation')(create)
    app.post('/v1/viewer/traffic/simulation/{key}')(step)
    app.delete('/v1/viewer/traffic/simulation/{key}')(delete)

    @app.on_event('shutdown')
    def cleanup():
        for sim in sessions.values():
            sim.close()
        sessions.clear()
