"""PyVRP model construction for the VRPTW solver.

Builds a single VRPTW where:
  - Each working day is a vehicle with shift-window in absolute minutes
  - Each visit instance is a client with time window spanning eligible days
  - Locked visits are required clients with tight time windows
  - Travel/duration matrix from the input travel_matrix
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pyvrp import Model as VRPModel

from models import SolverInput, VisitInstanceData
from solver.context import (
    MINUTES_PER_DAY,
    SolverContext,
    datetime_to_minute,
)

logger = logging.getLogger(__name__)

# Prize for optional clients — must exceed any realistic routing cost
# so HGS only drops visits when hard constraints force it.
OPTIONAL_PRIZE = 1_000_000


@dataclass
class Solution:
    """Internal solution representation before/after concrete timing."""

    # vehicle_idx → ordered list of instance_ids
    routes: dict[int, list[str]] = field(default_factory=dict)
    unassigned: list[str] = field(default_factory=list)
    total_distance: int = 0


def _eligible_day_windows(
    inst: VisitInstanceData,
    ctx: SolverContext,
) -> list[tuple[int, int]]:
    """Compute per-eligible-day time windows in absolute minutes for a visit instance.

    Returns list of (tw_early, tw_late) pairs, one per eligible day.
    A day is eligible if the patient has availability windows on that weekday
    (or has no availability constraints at all).
    """
    windows: list[tuple[int, int]] = []

    for vehicle in ctx.vehicles:
        d_idx = vehicle.day_index
        wday_str = str(ctx.day_wdays[d_idx])

        # Check clinician eligibility
        if (
            inst.eligible_clinician_indices
            and vehicle.clinician_idx not in inst.eligible_clinician_indices
        ):
            continue

        # Check if patient already has a locked visit on this day
        if d_idx in ctx.locked_patient_days.get(inst.patient_id, set()):
            continue

        # Check availability windows
        if inst.availability_windows:
            day_windows = inst.availability_windows.get(wday_str, [])
            if not day_windows:
                continue  # No availability on this weekday
            # Use the widest window for HGS flexibility
            best_w = max(
                day_windows,
                key=lambda w: w.get("end_minute", 1440) - w.get("start_minute", 0),
            )
            tw_e = max(vehicle.day_start, best_w.get("start_minute", vehicle.day_start))
            tw_l = min(vehicle.day_end, best_w.get("end_minute", vehicle.day_end))
            if tw_l <= tw_e:
                continue
        else:
            # No availability constraints — full day is eligible
            tw_e = vehicle.day_start
            tw_l = vehicle.day_end

        # Convert to absolute minutes
        offset = d_idx * MINUTES_PER_DAY
        windows.append((offset + tw_e, offset + tw_l))

    return windows


def build_model(
    input: SolverInput,
    ctx: SolverContext,
    locked_as_required: bool = True,
) -> VRPModel:
    """Construct a PyVRP Model for the full VRPTW.

    Populates ctx.node_to_instance_id and ctx.instance_id_to_node as a side effect.
    """
    travel = ctx.travel
    instances = input.instances
    vehicles = ctx.vehicles

    model = VRPModel()

    # ── Depots: one per clinician home ──────────────────────────────
    depots = []
    for c_idx in range(len(ctx.clinicians)):
        depots.append(model.add_depot(x=0, y=0))

    # ── Client nodes: one per visit instance ─────────────────────────
    clients = []
    instance_ids_in_model: list[str] = []  # parallel to clients list

    unassignable_ids: list[str] = []

    for inst in instances:
        eligible_windows = _eligible_day_windows(inst, ctx)
        if not eligible_windows:
            unassignable_ids.append(inst.id)
            continue

        # Create one client per eligible day in a mutually-exclusive group.
        # This forces PyVRP to pick exactly one day per visit instance,
        # preventing cross-day mis-placement that the old wide-window
        # approach suffered from.
        group = model.add_client_group(required=False)

        for tw_early, tw_late in eligible_windows:
            client = model.add_client(
                x=0,
                y=0,
                delivery=[1],  # counts toward vehicle capacity
                service_duration=inst.duration,
                tw_early=tw_early,
                tw_late=tw_late,
                required=False,
                prize=OPTIONAL_PRIZE,
                group=group,
            )
            clients.append(client)
            instance_ids_in_model.append(inst.id)

    # ── Locked visits as required clients ────────────────────────────
    locked_client_map: dict[str, int] = {}  # "patient_id:date" → client index
    if locked_as_required:
        for lv in input.locked_visits:
            d_idx = ctx.date_to_idx.get(lv.date)
            if d_idx is None:
                continue
            lv_start = datetime_to_minute(lv.starts_at)
            offset = d_idx * MINUTES_PER_DAY
            # Tight time window: must start at exactly this time
            tw_e = offset + lv_start
            tw_l = offset + lv_start + 1  # 1-min window for "exactly here"

            client = model.add_client(
                x=0,
                y=0,
                delivery=[1],
                service_duration=lv.duration_minutes,
                tw_early=tw_e,
                tw_late=tw_l,
                required=True,
                prize=0,
            )
            clients.append(client)
            key = f"locked_{lv.patient_id}_{lv.date}"
            locked_client_map[key] = len(clients) - 1
            instance_ids_in_model.append(key)

    n_clients = len(clients)

    # ── Build node mapping ───────────────────────────────────────────
    ctx.node_to_instance_id = {}
    ctx.instance_id_to_node = {}
    for i, inst_id in enumerate(instance_ids_in_model):
        node_idx = i  # 0-based client index
        ctx.node_to_instance_id[node_idx] = inst_id
        ctx.instance_id_to_node[inst_id] = node_idx

    # ── Edges: travel times between all nodes ────────────────────────
    # PyVRP needs edges between depot and all clients, and between all client pairs.
    # Travel times are day-independent (same drive regardless of day).

    def _pid_for_node(i: int) -> str:
        """Get patient_id string for a client node."""
        inst_id = instance_ids_in_model[i]
        if inst_id.startswith("locked_"):
            # "locked_{patient_id}_{date}"
            parts = inst_id.split("_")
            return parts[1]
        inst = ctx.instances_by_id.get(inst_id)
        return str(inst.patient_id) if inst else "home_0"

    for i in range(n_clients):
        pid_i = _pid_for_node(i)
        # Edges from/to each depot
        for c_idx, depot in enumerate(depots):
            home_key = f"home_{c_idx}"
            t_out = travel(home_key, pid_i)
            t_back = travel(pid_i, home_key)
            model.add_edge(depot, clients[i], distance=t_out, duration=t_out)
            model.add_edge(clients[i], depot, distance=t_back, duration=t_back)

        for j in range(n_clients):
            if i == j:
                continue
            pid_j = _pid_for_node(j)
            t = travel(pid_i, pid_j)
            model.add_edge(clients[i], clients[j], distance=t, duration=t)

    # ── Vehicles: one per clinician per working day ──────────────────
    for v in vehicles:
        model.add_vehicle_type(
            num_available=1,
            capacity=[v.capacity],
            start_depot=depots[v.depot_idx],
            end_depot=depots[v.depot_idx],
            shift_duration=v.shift_duration,
            max_distance=v.max_drive,
            tw_early=v.tw_early,
            tw_late=v.tw_late,
        )

    return model


def extract_solution(
    result,
    ctx: SolverContext,
    instances: list[VisitInstanceData],
) -> Solution:
    """Convert a PyVRP Result into a Solution.

    Maps PyVRP route indices back to instance_ids and vehicle indices.
    """
    solution = Solution()
    instance_ids_in_model = list(ctx.node_to_instance_id.values())
    all_instance_ids = {inst.id for inst in instances}
    placed_ids: set[str] = set()

    if not result.is_feasible():
        solution.unassigned = list(all_instance_ids)
        return solution

    for route in result.best.routes():
        vehicle_idx = route.vehicle_type()
        route_instance_ids: list[str] = []

        for visit_idx in route.visits():
            node = visit_idx - 1  # PyVRP: depot=0, clients 1-indexed in visits
            if node < 0 or node >= len(instance_ids_in_model):
                continue
            inst_id = instance_ids_in_model[node]
            if inst_id.startswith("locked_"):
                continue  # locked visits handled separately in timing
            if inst_id in placed_ids:
                continue  # mutually-exclusive group: already placed on another day
            route_instance_ids.append(inst_id)
            placed_ids.add(inst_id)

        if route_instance_ids:
            solution.routes[vehicle_idx] = route_instance_ids

    solution.unassigned = [iid for iid in all_instance_ids if iid not in placed_ids]
    solution.total_distance = result.best.distance() if result.is_feasible() else 0

    return solution
