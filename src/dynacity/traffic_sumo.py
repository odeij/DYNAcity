"""Optional SUMO microscopic traffic, using the same fleet for replay and metrics.

Generated OD trips are assumptions calibrated to historical density, not GPS tracks.
All working XML/net files live in temporary directories outside the repository.
"""
from __future__ import annotations

import importlib
import heapq
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
from .traffic import TrafficModel, RoadDisruption, background_flow, lane_counts, LANE_WIDTH_M
from .viewer import _utm_zone, utm_to_lonlat

ASSUMPTIONS = (
    "SUMO simulated trajectories, not measured GPS trips. Speed limits are each road's historical "
    "2014–2019 free-flow speed; the hourly medians are the check on simulated speeds, not an input. "
    "2016–17 road-class counts estimate initial density. OD destinations "
    "and turning demand are synthetic and not calibrated. Lane counts use OSM or class defaults; "
    "signal locations and turn bans come from OSM (guessed at four-way junctions for older road files); "
    "unsurveyed junction priority and signal timing are generated assumptions; imported survey programs "
    "are identified separately. Vehicle dimensions and driving behaviour use SUMO class defaults, not local measurements. "
    "Optional boundary inflow uses estimated historical directional volumes and synthetic destinations; "
    "it is not measured origin-destination demand or a current demand forecast. "
    "Junction blockers remain obstacles; gridlocked vehicles are not relocated and queues can persist."
)


# The speed check ignores the first minute (the fleet starts at rest) and
# roads with fewer vehicle-second samples than this.
SPEED_CHECK_WARMUP_S = 60
SPEED_CHECK_MIN_SAMPLES = 20

# Preserve junction right-of-way even during gridlock: never ignore blockers
# or increase impatience with waiting time. Queues may persist on this assumed
# network; hiding them by driving through occupied junctions is misleading.
JUNCTION_BLOCKER_S = -1
IMPATIENCE_S = 0
UNSTICK_AFTER_S = 120
# Free road needed to place an unstuck car (SUMO's default car length plus
# gap), searched at most UNSTICK_REACH roads ahead along its route.
UNSTICK_ROOM_M = 7.5
UNSTICK_REACH = 5

# Clipping the network at the map edge leaves stub roads, some only a few
# metres long, where a car can never be inserted: half the 08:00 boundary
# inflow was scheduled onto entries shorter than this and piled up unentered.
# Inflow trips start on the first road of their route at least this long.
MIN_ENTRY_M = 60.0

# Trips that start inside the modelled area. The initial fleet stands for
# cars already mid-trip: their remaining journeys average under a kilometre,
# so on its own it drains within minutes and boundary entries alone cannot
# replace it (08:00 fell from about 27,000 active cars to 3,500). New interior
# trips start on roads in proportion to their estimated vehicles and go to a
# destination drawn the same way at least INTERIOR_MIN_TRIP_M away in a
# straight line, at the rate that replaces the initial fleet over one mean
# trip time. The distance is a modelling assumption; no Beirut trip-length
# data are loaded.
INTERIOR_MIN_TRIP_M = 1000.0
INTERIOR_SAMPLE_TRIPS = 150


def trim_entry(route, length_of, min_length=MIN_ENTRY_M):
    """Drop leading stub roads so the route starts where a car fits; unchanged if none is long enough."""
    for i, edge in enumerate(route[:-1]):
        if length_of(edge) >= min_length:
            return tuple(route[i:])
    return tuple(route)


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


def export_network(model: TrafficModel, directory: Path, netconvert: str, *, mixed=False) -> Path:
    """Preserve directed road identities and OSM connectivity, including parallel arcs.

    Speed limits are each road's free-flow speed (its fastest hourly median).
    The hourly medians already contain that hour's congestion, so using them
    as limits would slow the cars twice once SUMO queues them; they are the
    check on the simulation instead (`Simulation.speed_check`).
    """
    nodes, edges = ET.Element('nodes'), ET.Element('edges')
    coordinates, degree = {}, {}
    for i, points in enumerate(model.geometry):
        for node, point in ((int(model.u[i]), points[0]), (int(model.v[i]), points[-1])):
            coordinates[node] = point
            degree[node] = degree.get(node, 0) + 1
    # Signals come from OSM when the model kept them; an older model without
    # signal nodes falls back to guessing a light at every four-way junction.
    signals = set(model.signals) if model.signals is not None else {n for n, d in degree.items() if d >= 4}
    for node, (x, y) in coordinates.items():
        ET.SubElement(nodes, 'node', id=str(node), x=str(x), y=str(y),
                      type='traffic_light' if node in signals else 'priority')
    mobility = model.mobility
    if mobility:
        required = {s.node for s in mobility.signals}
        if required - coordinates.keys():
            raise ValueError('Mobility signal references an unknown network node')
        for n in nodes:
            if int(n.attrib['id']) in required:
                n.set('type', 'traffic_light')
    allowed = sorted({'passenger'} | ({t.vclass for t in mobility.types} if mixed and mobility else set()))
    lanes = lane_counts(model)
    for i, points in enumerate(model.geometry):
        for direction in (1, -1):
            if model.oneway[i] == -direction:
                continue
            shape = points if direction == 1 else list(reversed(points))
            ET.SubElement(edges, 'edge', id=f'{i}:{direction}',
                          **{'from': str(int(model.u[i] if direction == 1 else model.v[i])),
                             'to': str(int(model.v[i] if direction == 1 else model.u[i]))},
                          numLanes=str(int(lanes[i])), width=str(LANE_WIDTH_M),
                          speed=str(max(1, float(model.free_kmh[i]) / 3.6)),
                          shape=' '.join(f'{x},{y}' for x, y in shape), allow=' '.join(allowed),
                          # An undivided two-way way describes the shared centre
                          # line: each direction belongs on its right. A one-way
                          # way already describes its whole carriageway centre.
                          spreadType='right' if model.oneway[i] == 0 else 'center')
    # OSM turn restrictions: delete the banned movement at its junction.
    connections = ET.Element('connections')
    for source, node, target in model.turn_bans:
        into = 1 if int(model.v[source]) == node and model.oneway[source] >= 0 else -1
        out = 1 if int(model.u[target]) == node and model.oneway[target] >= 0 else -1
        ET.SubElement(connections, 'delete', **{'from': f'{source}:{into}', 'to': f'{target}:{out}'})
    for name, root in (('nodes', nodes), ('edges', edges), ('connections', connections)):
        ET.ElementTree(root).write(directory / f'{name}.xml')
    net = directory / 'network.net.xml'
    result = subprocess.run([netconvert, '--node-files', str(directory / 'nodes.xml'),
                             '--edge-files', str(directory / 'edges.xml'),
                             '--connection-files', str(directory / 'connections.xml'), '-o', str(net),
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


class BoundaryInflow:
    """Seeded Poisson entries on inward-facing network-fringe roads, plus
    interior trips that keep the represented fleet from draining.

    This is a transparent demand assumption, not an observed OD matrix. The
    schedule is shared by both runs and never responds to scenario arrivals.
    """
    def __init__(self, model, net, hour, seed, *, interior=True):
        self.net = net
        self.rng = random.Random(seed + 104729)
        self.interior_rng = random.Random(seed + 7919)
        self.heap, self.pool, self.exits = [], {}, []
        self.interior_pool = {}
        # Routes new interior trips; sumolib until the simulation hands over
        # SUMO's own (much faster) router.
        self.router = None
        self.generated, self.expected_vph, self.interior_vph = 0, 0.0, 0.0
        (west, south), (east, north) = net.getBBoxXY()
        margin = max(25, min(east-west, north-south)*.03)
        flow = background_flow(model)[:, hour]
        for edge in sorted(net.getEdges(), key=lambda e:e.getID()):
            x,y = edge.getFromNode().getCoord()
            xx,yy = edge.getToNode().getCoord()
            inward = ((x <= west+margin and xx>x+1) or (x >= east-margin and xx<x-1)
                      or (y <= south+margin and yy>y+1) or (y >= north-margin and yy<y-1))
            outward = ((xx <= west+margin and xx<x-1) or (xx >= east-margin and xx>x+1)
                       or (yy <= south+margin and yy<y-1) or (yy >= north-margin and yy>y+1))
            if outward:
                self.exits.append(edge)
            rate = float(flow[int(edge.getID().split(':')[0])])/3600
            if inward and math.isfinite(rate) and rate>0:
                self.expected_vph += rate*3600
                heapq.heappush(self.heap,(self.rng.expovariate(rate),'boundary',edge.getID(),rate))
        self.origins = len(self.heap)
        if interior:
            self._schedule_interior(model, hour)

    def _schedule_interior(self, model, hour):
        speed = np.maximum(3, model.speed_kmh[:, hour])
        flow = background_flow(model)[:, hour]
        edges = [e for e in sorted(self.net.getEdges(), key=lambda e: e.getID())]
        index = [int(e.getID().split(':')[0]) for e in edges]
        # Vehicles each road holds at this hour, as in `demand`.
        on_road = np.nan_to_num(flow[index] * model.length_m[index] / 1000 / speed[index])
        keep = on_road > 0
        self.places = [e for e, k in zip(edges, keep) if k]
        self.weights = on_road[keep].tolist()
        self.centres = {e.getID(): e.getShape()[len(e.getShape()) // 2] for e in self.places}
        fleet = float(on_road.sum())
        if not self.places or fleet <= 0:
            return
        sample = self.interior_rng.choices(self.places, weights=self.weights, k=INTERIOR_SAMPLE_TRIPS)
        seconds = []
        for origin in sample:
            route = self.interior_routes(origin.getID())[0]
            i = [int(e.split(':')[0]) for e in route]
            seconds.append(float((model.length_m[i] / (speed[i] / 3.6)).sum()))
        # Replace the fleet once per mean trip time; never faster than once a minute.
        total = fleet / max(60.0, float(np.mean(seconds)))
        self.interior_vph = total * 3600
        for edge, weight in zip(self.places, self.weights):
            rate = total * weight / fleet
            heapq.heappush(self.heap, (self.interior_rng.expovariate(rate), 'interior', edge.getID(), rate))

    def interior_routes(self, edge_id):
        """A few legal routes from this road to destinations at least `INTERIOR_MIN_TRIP_M` away."""
        if edge_id not in self.interior_pool:
            origin = self.net.getEdge(edge_id)
            ox, oy = self.centres[edge_id]
            paths = []
            for _ in range(3):
                best, best_d = None, -1.0
                for target in self.interior_rng.choices(self.places, weights=self.weights, k=20):
                    tx, ty = self.centres[target.getID()]
                    d = math.hypot(tx - ox, ty - oy)
                    if d > best_d:
                        best, best_d = target, d
                    if d >= INTERIOR_MIN_TRIP_M:
                        break
                if self.router:
                    route = self.router(edge_id, best.getID())
                else:
                    found, _ = self.net.getShortestPath(origin, best)
                    route = tuple(e.getID() for e in found) if found else ()
                if len(route) > 1:
                    paths.append(route)
            self.interior_pool[edge_id] = paths or [(edge_id,)]
        return self.interior_pool[edge_id]

    def routes(self, edge_id):
        if edge_id not in self.pool:
            origin = self.net.getEdge(edge_id)
            candidates = [e for e in self.exits if e.getID()!=edge_id]
            paths=[]
            for target in self.rng.sample(candidates,min(12,len(candidates))):
                route,_ = self.net.getShortestPath(origin,target)
                if route and len(route)>1:
                    paths.append(tuple(e.getID() for e in route))
                    if len(paths)==4:
                        break
            # A disconnected fringe still gets accountable local trips.
            if not paths:
                route=[origin]
                for _ in range(12):
                    exits=sorted(route[-1].getOutgoing(),key=lambda e:e.getID())
                    if not exits:
                        break
                    route.append(self.rng.choice(exits))
                paths=[tuple(e.getID() for e in route)]
            length=lambda e:self.net.getEdge(e).getLength()
            self.pool[edge_id]=[trim_entry(path,length) for path in paths]
        return self.pool[edge_id]

    def until(self, time_s):
        while self.heap and self.heap[0][0] <= time_s:
            depart,kind,edge_id,rate=heapq.heappop(self.heap)
            if kind=='interior':
                route=self.interior_rng.choice(self.interior_routes(edge_id))
                gap=self.interior_rng.expovariate(rate)
            else:
                route=self.rng.choice(self.routes(edge_id))
                gap=self.rng.expovariate(rate)
            self.generated+=1
            yield depart,Trip(f'inflow-{self.generated}',route,0,0)
            heapq.heappush(self.heap,(depart+gap,kind,edge_id,rate))


class Simulation:
    def __init__(self, model: TrafficModel, hour=8, *, trips=None, seed=42, continuous=False, mixed=False):
        self.model, self.hour = model, hour
        self.mixed = mixed
        self.mobility = model.mobility
        self.seed = seed
        self.vehicle_type_ids = {}
        if mixed and (not self.mobility or not self.mobility.fleet):
            raise ValueError('Mixed traffic requires a sourced vehicle-count profile')
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
        # Cars moved past a gridlock, per run (`unstick`).
        self.unstuck = [0, 0]
        # Simulated speed per road (km/h sums and samples) while nothing is disrupted.
        self.speed_sum = np.zeros(len(model))
        self.speed_samples = np.zeros(len(model), dtype=np.int64)
        self.continuous = continuous
        self.inflow = None
        self.future_entries = []
        self.entry_queue = {}
        self.requeued = 0
        self.entry_route_cache = {}
        self.traci, binary, converter = runtime()
        try:
            path = export_network(model, Path(self.directory.name), converter, mixed=mixed)
            import sumolib
            self.net = sumolib.net.readNet(str(path))
            represented = {int(e.getID().split(':')[0]) for e in self.net.getEdges()}
            self.network_quality = {'edges': len(self.net.getEdges()),
                                    'signals': len(self.net.getTrafficLights()),
                                    'signal_source': 'osm' if model.signals is not None else 'guessed',
                                    'turn_bans': len(model.turn_bans),
                                    'excluded_roads': sorted(set(range(len(model))) - represented)}
            programs = {str(s.node): s for s in self.mobility.signals} if self.mobility else {}
            for node, program in programs.items():
                tls = self.net.getTLS(node)
                links = max(connection[2] for connection in tls.getConnections()) + 1
                if any(len(p.state) != links for p in program.phases):
                    raise ValueError(f'Signal {node}: phase state must cover all {links} controlled links')
            self.network_quality['surveyed_signals'] = len(programs)
            self.network_quality['vehicle_types'] = {}
            self.network_quality['vehicle_mix'] = self.mobility.fleet.model_dump() if mixed else None
            self.trips = demand(model, self.net, hour, seed) if trips is None else list(trips)
            self.initial_trips = len(self.trips)
            self.inflow = BoundaryInflow(model,self.net,hour,seed) if continuous else None
            self.trip_ids = {t.id for t in self.trips}
            self.routes = {t.id: t.route for t in self.trips}
            self.held = set()
            routes_xml = ET.Element('routes')
            if mixed:
                for t in self.mobility.types:
                    ET.SubElement(routes_xml, 'vType', id=t.id, vClass=t.vclass)
            stops = self.mobility.stops if self.mobility else []
            if {s.vehicle for s in stops} - self.trip_ids:
                raise ValueError('Observed stop references a vehicle absent from the initial fleet')
            stops_by_vehicle = {}
            for stop in stops:
                stops_by_vehicle.setdefault(stop.vehicle, []).append(stop)
            for trip in self.trips:
                vehicle = ET.SubElement(routes_xml, 'vehicle', id=trip.id, depart='0',
                                        departLane=str(trip.lane), departPos=str(trip.position), departSpeed='0',
                                        type=self.type_for(trip.id))
                ET.SubElement(vehicle, 'route', edges=' '.join(trip.route))
                for stop in stops_by_vehicle.get(trip.id, []):
                    if stop.edge not in trip.route or not self.net.hasEdge(stop.edge):
                        raise ValueError('Observed stop must lie on the vehicle route')
                    edge = self.net.getEdge(stop.edge)
                    if stop.lane >= edge.getLaneNumber() or stop.position_m >= edge.getLength():
                        raise ValueError('Observed stop lies outside its lane')
                    ET.SubElement(vehicle, 'stop', lane=f'{stop.edge}_{stop.lane}', endPos=str(stop.position_m),
                                  duration=str(stop.duration_s), parking='true' if stop.parking else 'false')
            routes_path = Path(self.directory.name) / 'trips.rou.xml'
            ET.ElementTree(routes_xml).write(routes_path)
            self.offset = self.net.getLocationOffset()
            self.zone, self.north = _utm_zone(model.crs)
            self.context_node = self.net.getNodes()[0].getID()
            bounds = self.net.getBBoxXY()
            self.context_radius = math.hypot(bounds[1][0] - bounds[0][0], bounds[1][1] - bounds[0][1]) + 100
            constants = self.traci.constants
            variables = (constants.VAR_POSITION, constants.VAR_ANGLE, constants.VAR_SPEED,
                         constants.VAR_ROAD_ID, constants.VAR_LANE_INDEX, constants.VAR_ROUTE_INDEX,
                         constants.VAR_WAITING_TIME, constants.VAR_SIGNALS)
            self.variables = variables
            self.baseline_variables = (constants.VAR_SPEED, constants.VAR_WAITING_TIME)
            for kind in ('scenario', 'baseline'):
                label = f'dynacity-{uuid.uuid4().hex}'
                self.traci.start([binary, '-n', str(path), '-r', str(routes_path), '--step-length', '0.2', '--seed', str(seed),
                                  '--time-to-teleport', '-1', '--time-to-teleport.disconnected', '-1',
                                  '--ignore-junction-blocker', str(JUNCTION_BLOCKER_S),
                                  '--time-to-impatience', str(IMPATIENCE_S),
                                  '--collision.check-junctions', 'true',
                                  '--collision.action', 'warn', '--no-step-log', 'true',
                                  '--duration-log.disable', 'true', '--no-warnings', 'true'], label=label,
                                 stdout=subprocess.DEVNULL)
                c = self.traci.getConnection(label)
                self.connections.append(c)
                for node, program in programs.items():
                    logic = self.traci.trafficlight.Logic('observed', 0, 0,
                        [self.traci.trafficlight.Phase(p.duration_s, p.state) for p in program.phases])
                    c.trafficlight.setProgramLogic(node, logic)
                    c.trafficlight.setProgram(node, 'observed')
                    # Determine phase at this local-hour start, including its cycle offset.
                    cycle = sum(p.duration_s for p in program.phases)
                    elapsed = (hour * 3600 - program.offset_s) % cycle
                    for index, phase in enumerate(program.phases):
                        if elapsed < phase.duration_s:
                            c.trafficlight.setPhase(node, index)
                            c.trafficlight.setPhaseDuration(node, phase.duration_s - elapsed)
                            break
                        elapsed -= phase.duration_s
                c.simulationStep()
                c.junction.subscribeContext(self.context_node, constants.CMD_GET_VEHICLE_VARIABLE,
                                            self.context_radius, variables if kind == 'scenario' else self.baseline_variables)
            if self.inflow:
                # Route interior trips with SUMO's router on the baseline run, which is never
                # closed, so both runs keep the same schedule.
                baseline = self.connections[1]
                def route(origin, target):
                    try:
                        return tuple(baseline.simulation.findRoute(origin, target).edges)
                    except self.traci.TraCIException:
                        return ()
                self.inflow.router = route
            c = self.connections[0]
            type_ids = [t.id for t in self.mobility.types] if mixed else ['DEFAULT_VEHTYPE']
            declared = {t.id: t for t in self.mobility.types} if self.mobility else {}
            for tid in type_ids:
                self.network_quality['vehicle_types'][tid] = {
                    'class': c.vehicletype.getVehicleClass(tid),
                    'label': declared[tid].label if tid in declared else 'Passenger car',
                    'length_m': c.vehicletype.getLength(tid), 'width_m': c.vehicletype.getWidth(tid),
                    'height_m': c.vehicletype.getHeight(tid),
                    'parameters': 'SUMO class defaults; not measured Beirut vehicle dimensions or behaviour',
                }
            self.signal_links = []
            for node in c.trafficlight.getIDList():
                for index, connections in enumerate(c.trafficlight.getControlledLinks(node)):
                    if not connections:
                        continue
                    lane = self.net.getLane(connections[0][0])
                    # Replay a marker at the incoming stop line, not a claimed physical signal pole.
                    x, y = lane.getShape()[-1]
                    lon, lat = utm_to_lonlat(x-self.offset[0], y-self.offset[1], self.zone, self.north)
                    bounds = self.mobility.bounds if self.mobility else None
                    if bounds and bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]:
                        self.signal_links.append((node, index, round(lon, 7), round(lat, 7), node in programs))
            for node in {link[0] for link in self.signal_links}:
                c.trafficlight.subscribe(node, [constants.TL_RED_YELLOW_GREEN_STATE])
            self.last_factors = np.ones(len(model))
            self.seen = set(self.connections[0].vehicle.getIDList())
            # Preserve positive allowed classes; computing a negative complement includes
            # aliases such as public_transport, which would prohibit buses on reopening.
            self.permissions = {lane.getID(): tuple(sorted(lane.getPermissions()))
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

    def type_for(self, vehicle_id):
        if vehicle_id not in self.vehicle_type_ids:
            self.vehicle_type_ids[vehicle_id] = self.mobility.vehicle_type(vehicle_id, self.seed) if self.mixed else 'DEFAULT_VEHTYPE'
        return self.vehicle_type_ids[vehicle_id]

    def entry_route(self, trip):
        if trip.route[0] in self.closed or trip.route[-1] in self.closed:
            return None
        if not self.closed.intersection(trip.route):
            return trip.route
        key=(trip.route[0],trip.route[-1], self.type_for(trip.id))
        if key not in self.entry_route_cache:
            try:
                route=tuple(self.connections[0].simulation.findRoute(key[0], key[1], vType=key[2]).edges)
            except self.traci.TraCIException:
                route=()
            self.entry_route_cache[key]=route if route and not self.closed.intersection(route) else None
        return self.entry_route_cache[key]

    def apply(self, disruption: RoadDisruption):
        c = self.connections[0]
        closed = {edge.getID() for edge in self.net.getEdges()
                  if disruption.closed[int(edge.getID().split(':')[0])]}
        changed = closed != self.closed or self.last_factors is None or not np.array_equal(self.last_factors, disruption.speed_factor)
        if changed:
            self.entry_route_cache.clear()
        if not changed:
            candidates = set(c.vehicle.getIDList()) - self.seen | self.retry
        else:
            candidates = set(c.vehicle.getIDList())
        for edge in self.net.getEdges() if changed else ():
            eid = edge.getID()
            i = int(eid.split(':')[0])
            if self.last_factors is None or disruption.speed_factor[i] != self.last_factors[i]:
                c.edge.setMaxSpeed(eid, max(0.1, float(self.model.free_kmh[i]) * float(disruption.speed_factor[i]) / 3.6))
            if (eid in closed) != (eid in self.closed):
                for lane in edge.getLanes():
                    original = self.permissions[lane.getID()]
                    if eid in closed:
                        c.lane.setDisallowed(lane.getID(), ['all'])
                    else:
                        c.lane.setAllowed(lane.getID(), original)
        previous_closed = self.closed
        self.closed = closed
        if closed - previous_closed:
            self.withhold(c, closed - previous_closed)
        self.last_factors = disruption.speed_factor.copy()
        # Retry after every chunk, including cars which were in internal junctions
        # during the last change and cars newly released from insertion queues.
        active = set(c.vehicle.getIDList())
        previously_blocked = self.blocked.copy()
        self.blocked.intersection_update(active)
        self.seen = active
        self.retry = set()
        if changed:
            self.blocked.clear()
        states = c.junction.getContextSubscriptionResults(self.context_node) or {}
        for vid in candidates:
            if changed:
                c.vehicle.updateBestLanes(vid)
            state = states.get(vid, {})
            road = state.get(self.traci.constants.VAR_ROAD_ID)
            index = state.get(self.traci.constants.VAR_ROUTE_INDEX)
            before = self.routes[vid]
            if index is None:
                index = c.vehicle.getRouteIndex(vid)
            needs_route = bool(closed.intersection(before[index + 1:])) or (bool(previous_closed - closed) and vid in (self.rerouted | previously_blocked))
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
                             state[constants.VAR_ROAD_ID], state[constants.VAR_LANE_INDEX],
                             self.type_for(vid) if hasattr(self, 'mixed') else 'DEFAULT_VEHTYPE',
                             # SUMO's own light bits (1 right, 2 left indicator, 8 brake),
                             # set by its turn and lane-change rules: drawn, not observed.
                             int(state.get(constants.VAR_SIGNALS, 0))])
        active_ids = set(c.vehicle.getIDList())
        now = c.simulation.getTime()
        pending_ids = set(c.simulation.getPendingVehicles())
        pending_ids.update(vid for vid,(depart,_) in getattr(self,'entry_queue',{}).items()
                           if depart < now-1e-7)
        active = len(active_ids)
        arrived = len(self.arrivals[0])
        pending = len(pending_ids)
        if self.trip_ids != active_ids | pending_ids | self.arrivals[0].keys():
            raise RuntimeError('SUMO lost a represented vehicle')
        paired = self.arrivals[0].keys() & self.arrivals[1].keys()
        delays = [self.arrivals[0][v] - self.arrivals[1][v] for v in paired]
        overdue = sum(v not in self.arrivals[0] for v in self.arrivals[1])
        detour_counts = {}
        stopped_roads = {}
        for vehicle in vehicles:
            if vehicle[4] < 0.1 and not vehicle[5].startswith(':'):
                road = int(vehicle[5].split(':')[0])
                stopped_roads[road] = stopped_roads.get(road, 0) + 1
            if vehicle[0] in self.rerouted and not vehicle[5].startswith(':'):
                road = int(vehicle[5].split(':')[0])
                detour_counts[road] = detour_counts.get(road, 0) + 1
        factors = getattr(self, 'last_factors', None)
        undisturbed = not getattr(self, 'closed', None) and (factors is None or bool(np.all(factors == 1)))
        if undisturbed and now >= SPEED_CHECK_WARMUP_S and hasattr(self, 'speed_sum'):
            for vehicle in vehicles:
                if not vehicle[5].startswith(':'):
                    road = int(vehicle[5].split(':')[0])
                    self.speed_sum[road] += vehicle[4] * 3.6
                    self.speed_samples[road] += 1
        metrics = {'detour': sorted(detour_counts.items()), 'total': len(self.trips), 'active': active, 'pending': pending,
                   'arrived': arrived, 'stopped_on_closed': len(self.held & active_ids), 'no_detour': len(self.blocked), 'rerouted': len(self.rerouted),
                   'paired_arrivals': len(paired), 'overdue': overdue,
                   'mean_completed_delay_s': float(np.mean(delays)) if delays else None,
                   'speed_check': self.speed_check(), 'unstuck': getattr(self, 'unstuck', [0])[0]}
        baseline = self.connections[1]
        baseline_states = baseline.junction.getContextSubscriptionResults(self.context_node) or {}
        metrics['baseline'] = {
            'active': baseline.vehicle.getIDCount(),
            'pending': len(baseline.simulation.getPendingVehicles()),
            'arrived': len(self.arrivals[1]),
            'stopped': sum(s[constants.VAR_SPEED] < 0.1 for s in baseline_states.values()),
        }
        metrics['stopped'] = sum(v[4] < 0.1 for v in vehicles)
        metrics['stopped_roads'] = sorted(stopped_roads.items())
        metrics['vehicle_types'] = {tid: sum(v[7] == tid for v in vehicles) for tid in {v[7] for v in vehicles}}
        inflow = getattr(self,'inflow',None)
        metrics['demand'] = {
            'mode': 'boundary_inflow' if inflow and inflow.origins else 'finite',
            'initial_trips': getattr(self,'initial_trips',len(self.trips)),
            'added_trips': len(self.trips)-getattr(self,'initial_trips',len(self.trips)),
            'entry_roads': inflow.origins if inflow else 0,
            'expected_entry_vph': round(inflow.expected_vph,1) if inflow else 0,
            'interior_vph': round(inflow.interior_vph,1) if inflow else 0,
        }
        if {v[0] for v in vehicles} != active_ids:
            raise RuntimeError('SUMO replay is missing an active vehicle')
        if pending < 0 or c.simulation.getStartingTeleportNumber():
            raise RuntimeError('SUMO vehicle accounting invariant failed')
        tls = c.trafficlight.getAllSubscriptionResults() if getattr(self, 'signal_links', []) else {}
        signals = []
        for node, index, lon, lat, observed in getattr(self, 'signal_links', []):
            state = tls.get(node, {}).get(constants.TL_RED_YELLOW_GREEN_STATE)
            if state is not None:
                signals.append([node, index, lon, lat, state[index], observed])
        return {'time_s': now, 'vehicles': vehicles, 'signals': signals, 'metrics': metrics}

    def withhold(self, c, newly_closed):
        """Take cars still waiting to enter on a newly closed road out of SUMO.

        SUMO aborts the whole run when a queued car's departure road is
        closed under it. These cars wait in `entry_queue` instead, counted as
        pending, and enter once a route from their origin is open again.
        """
        trips = {t.id: t for t in self.trips}
        departs = {}
        for depart, trip in self.future_entries:
            trips[trip.id] = trip
            departs[trip.id] = depart
        waiting = set(c.vehicle.getLoadedIDList()) - set(c.vehicle.getIDList())
        for vid in sorted(waiting):
            route = self.routes.get(vid)
            if not route or route[0] not in newly_closed or vid not in trips:
                continue
            c.vehicle.remove(vid)
            # Cars not in `future_entries` were already due: queue them as overdue.
            self.entry_queue[vid] = (departs.get(vid, -1.0), trips[vid])

    def unstick(self, index, c):
        """Move gridlocked front-of-queue cars further along their route.

        A car standing `UNSTICK_AFTER_S` while first on its lane is placed at
        the start of the nearest of its next `UNSTICK_REACH` roads with
        `UNSTICK_ROOM_M` free, as SUMO's teleport would. Cars a closure holds
        or blocks are left alone, and the search stops at a closed road, so
        closures stay closed. The car stays on the network and in the
        accounting; `unstuck` counts the moves.
        """
        constants = self.traci.constants
        states = c.junction.getContextSubscriptionResults(self.context_node) or {}
        protected = (self.held | self.blocked) if index == 0 else set()
        for vid, state in states.items():
            if state.get(constants.VAR_WAITING_TIME, 0) < UNSTICK_AFTER_S or vid in protected:
                continue
            # Only the first car on its lane; the ones behind follow once it moves.
            here = c.vehicle.getLanePosition(vid)
            if any(c.vehicle.getLanePosition(v) > here for v in c.lane.getLastStepVehicleIDs(c.vehicle.getLaneID(vid)) if v != vid):
                continue
            route, at = c.vehicle.getRoute(vid), c.vehicle.getRouteIndex(vid)
            target = None
            for ahead in route[at + 1:at + 1 + UNSTICK_REACH]:
                if index == 0 and ahead in self.closed:
                    break
                for lane in range(c.edge.getLaneNumber(ahead)):
                    lane_id = f'{ahead}_{lane}'
                    if min((c.vehicle.getLanePosition(v) for v in c.lane.getLastStepVehicleIDs(lane_id)), default=math.inf) >= UNSTICK_ROOM_M:
                        target = lane_id
                        break
                if target:
                    break
            if target is None:
                continue
            try:
                c.vehicle.moveTo(vid, target, 0.0)
            except self.traci.TraCIException:
                continue
            self.unstuck[index] += 1

    def speed_check(self):
        """Simulated against observed (Tari'ak) speed at this hour, over roads with enough samples.

        Vehicle-second samples are gathered only while nothing is closed or
        slowed, after the warm-up in which the fleet starts from rest. A ratio
        below 1 means SUMO runs slower than the historical median, above 1 faster.
        None until some road has `SPEED_CHECK_MIN_SAMPLES`.
        """
        if not hasattr(self, 'speed_samples'):
            return None
        roads = np.flatnonzero(self.speed_samples >= SPEED_CHECK_MIN_SAMPLES)
        if not len(roads):
            return None
        simulated = self.speed_sum[roads] / self.speed_samples[roads]
        observed = self.model.speed_kmh[roads, self.hour]
        return {'roads': int(len(roads)),
                'simulated_kmh': round(float(np.median(simulated)), 1),
                'observed_kmh': round(float(np.median(observed)), 1),
                'ratio_p50': round(float(np.median(simulated / np.maximum(observed, 1.0))), 2),
                'mae_kmh': round(float(np.mean(np.abs(simulated - observed))), 1)}

    def advance(self, disruption, seconds=2, *, include_initial=True):
        with self.lock:
            self.last_used = time.monotonic()
            self.apply(disruption)
            frames = [self.frame()] if include_initial else []
            remaining = round(seconds * 5)
            while remaining:
                ticks = 1
                for vid,(_,trip) in list(self.entry_queue.items()):
                    route=self.entry_route(trip)
                    if route:
                        # A withheld car's first route id is still taken in SUMO.
                        self.requeued+=1
                        route_id=f'{vid}#{self.requeued}'
                        self.connections[0].route.add(route_id,route)
                        self.connections[0].vehicle.add(vid,route_id,depart='now',departLane='best',
                                                        departPos='base',departSpeed='max', typeID=self.type_for(vid))
                        self.routes[vid]=route
                        if route!=trip.route:
                            self.rerouted.add(vid)
                        del self.entry_queue[vid]
                if self.inflow:
                    target = self.connections[0].simulation.getTime()+ticks*.2
                    for depart,trip in self.inflow.until(target):
                        depart=math.ceil(depart*5-1e-9)/5
                        if trip.id in self.routes:
                            raise RuntimeError('Duplicate inflow vehicle identity')
                        self.routes[trip.id]=trip.route
                        for index,c in enumerate(self.connections):
                            route=self.entry_route(trip) if index==0 else trip.route
                            if not route:
                                self.entry_queue[trip.id]=(depart,trip)
                            else:
                                c.route.add(trip.id,route)
                                c.vehicle.add(trip.id,trip.id,depart=str(depart),departLane='best',
                                              departPos='base',departSpeed='max', typeID=self.type_for(trip.id))
                                if index==0:
                                    self.routes[trip.id]=route
                                    if route!=trip.route:
                                        self.rerouted.add(trip.id)
                        self.future_entries.append((depart,trip))
                for index, c in enumerate(self.connections):
                    # Replay each 0.2 s integration step so interpolation cannot
                    # shortcut a whole second of turning through a junction.
                    c.junction.unsubscribeContext(self.context_node, self.traci.constants.CMD_GET_VEHICLE_VARIABLE,
                                                  self.context_radius)
                    c.simulationStep(c.simulation.getTime() + ticks * 0.2)
                    variables = self.variables if index == 0 else self.baseline_variables
                    c.junction.subscribeContext(self.context_node, self.traci.constants.CMD_GET_VEHICLE_VARIABLE,
                                                self.context_radius, variables)
                    self.arrivals[index].update({v: c.simulation.getTime() for v in c.simulation.getArrivedIDList()})
                    # Keep queues in place; relocating a gridlocked car to a
                    # later road creates visible jumps through other vehicles.
                now=self.connections[0].simulation.getTime()
                # SUMO can retain a departure exactly at the sample boundary
                # for its next integration step. Do not count future demand as
                # an active/pending trip before SUMO makes it eligible.
                still_future=[]
                for depart,trip in self.future_entries:
                    if depart < now-1e-7:
                        self.trips.append(trip)
                        self.trip_ids.add(trip.id)
                    else:
                        still_future.append((depart,trip))
                self.future_entries=still_future
                frames.append(self.frame())
                remaining -= ticks
            return {'frames': frames, 'assumptions': ASSUMPTIONS, 'network': self.network_quality}


def install_routes(app, model, snapshot, ground=None):
    """Traffic-only API registration; sessions own independent TraCI connections."""
    from fastapi import HTTPException
    from pydantic import BaseModel, Field, ConfigDict
    from .contracts import HazardEvent
    from .traffic import road_events, road_disruption, combine_disruptions

    # sumolib imports pyproj, and pyproj (3.8) empties the process environment
    # when the thread that first imported it exits. Requests run on worker
    # threads that do exit, after which netconvert starts with no environment
    # and segfaults, so every session after the first failed. Importing here,
    # on the thread that builds the app, keeps those imports off the workers.
    try:
        runtime()
    except RuntimeError:
        pass  # SUMO missing: each request reports it.

    class Request(BaseModel):
        model_config = ConfigDict(extra='forbid')
        hour: int = Field(default=8, ge=0, le=23)
        seconds: float = Field(default=2, ge=0.2, le=10)
        continuous: bool = False
        mixed: bool = False
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
                sim = Simulation(model, request.hour, continuous=request.continuous, mixed=request.mixed)
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
            if sim.continuous != request.continuous:
                raise HTTPException(409, 'Create a new session when changing traffic demand')
            if sim.mixed != request.mixed:
                raise HTTPException(409, 'Create a new session when changing vehicle mix')
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
