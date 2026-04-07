"""PyVRP (HGS) solver backend."""

from __future__ import annotations
from pyvrp import Model
from pyvrp.stop import MaxRuntime
from models import SolverInput, SolverOutput, PlannedVisit
from solvers.base import (
    MAX_VISITS_PER_DAY,
    build_lunch_placements,
    compute_return_home,
    minutes_to_datetime,
    validate_spacing,
)


def solve(input: SolverInput, time_budget: int = 30) -> SolverOutput:
    """Build a PyVRP model from SolverInput, solve with HGS, post-process."""
    instances = input.instances
    if not instances:
        return _empty_output(input)

    working_days = input.working_days
    num_days = len(working_days)
    clinician = input.clinician
    matrix = input.travel_matrix

    n = len(instances)
    patient_ids = [inst.patient_id for inst in instances]

    # Build PyVRP model
    model = Model()
    depot = model.add_depot(x=0, y=0)

    # Build capacity dimensions: one per unique patient (enforces at most 1 per patient per route)
    # plus one global dimension for MAX_VISITS_PER_DAY
    unique_pids = sorted(set(inst.patient_id for inst in instances))
    pid_to_dim = {pid: idx for idx, pid in enumerate(unique_pids)}
    num_dims = len(unique_pids) + 1  # +1 for global visit count

    # Add clients (one per visit instance)
    clients = []
    for inst in instances:
        # Demand vector: 1 in the patient's dimension, 1 in the global dimension
        demand = [0] * num_dims
        demand[pid_to_dim[inst.patient_id]] = 1  # patient-specific
        demand[-1] = 1  # global visit count

        client = model.add_client(
            x=0, y=0,
            delivery=demand,
            service_duration=inst.duration,
            tw_early=clinician.workday_start_minute,
            tw_late=clinician.workday_end_minute,
            required=True,
        )
        clients.append(client)

    # Add edges (depot↔clients and client↔client)
    workday_duration = clinician.workday_end_minute - clinician.workday_start_minute

    for i, inst_i in enumerate(instances):
        pid_i = str(inst_i.patient_id)
        home_to_i = matrix.get("home", {}).get(pid_i, 0)
        model.add_edge(depot, clients[i], distance=home_to_i, duration=home_to_i)
        i_to_home = matrix.get(pid_i, {}).get("home", 0)
        model.add_edge(clients[i], depot, distance=i_to_home, duration=i_to_home)

        for j, inst_j in enumerate(instances):
            if i == j:
                continue
            pid_j = str(inst_j.patient_id)
            t = matrix.get(pid_i, {}).get(pid_j, 0)
            model.add_edge(clients[i], clients[j], distance=t, duration=t)

    # Vehicle type (one per working day)
    max_dist = clinician.max_drive_minutes_per_day or 999999

    # Capacity: 1 per patient dimension (at most 1 visit per patient per day)
    # + MAX_VISITS_PER_DAY for the global dimension
    capacity = [1] * len(unique_pids) + [MAX_VISITS_PER_DAY]

    model.add_vehicle_type(
        num_available=num_days,
        capacity=capacity,
        shift_duration=workday_duration,
        max_distance=max_dist,
        tw_early=clinician.workday_start_minute,
        tw_late=clinician.workday_end_minute,
    )

    # Solve
    result = model.solve(stop=MaxRuntime(time_budget), seed=42, display=False)

    if not result.is_feasible():
        return _empty_output(input, metadata={"optimizer_type": "python_hgs", "error": "infeasible"})

    # Map PyVRP routes back to our format
    planned_visits = []
    routes_by_day: dict[str, list[int]] = {}
    best = result.best

    for route_idx, route in enumerate(best.routes()):
        if route_idx >= num_days:
            break
        date = working_days[route_idx]
        day_patient_ids: list[int] = []

        current_time = clinician.workday_start_minute
        for visit_idx in route.visits():
            # PyVRP client indices are 1-based (0 = depot)
            node_idx = visit_idx - 1
            if node_idx < 0 or node_idx >= n:
                continue

            inst = instances[node_idx]
            pid = inst.patient_id

            # Compute start time from travel
            prev_key = str(day_patient_ids[-1]) if day_patient_ids else "home"
            curr_key = str(pid)
            transit = matrix.get(prev_key, {}).get(curr_key, 0)
            start_minute = current_time + transit
            # Round to 15-min intervals
            start_minute = ((start_minute + 14) // 15) * 15

            visit_duration = inst.duration - clinician.charting_buffer_minutes

            planned_visits.append(PlannedVisit(
                instance_id=inst.id,
                patient_id=pid,
                date=date,
                starts_at=minutes_to_datetime(date, start_minute),
                ends_at=minutes_to_datetime(date, start_minute + visit_duration),
                soft_constraint_override=False,
            ))

            current_time = start_minute + inst.duration  # includes charting buffer for gap
            day_patient_ids.append(pid)

        routes_by_day[date] = day_patient_ids

    # Post-process
    patients_by_id = {p.id: p for p in input.patients}
    planned_visits = validate_spacing(planned_visits, patients_by_id)
    lunch = build_lunch_placements(input)
    return_home = compute_return_home(routes_by_day, matrix)

    unschedulable = []
    placed_ids = {v.instance_id for v in planned_visits}
    for inst in instances:
        if inst.id not in placed_ids:
            name = patients_by_id.get(inst.patient_id, None)
            unschedulable.append({
                "patient_name": name.name if name else "Unknown",
                "patient_id": inst.patient_id,
            })

    return SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch,
        fitness=best.distance() if best else 0.0,
        metadata={
            "optimizer_type": "python_hgs",
            "unschedulable": unschedulable,
            "drive_violations": [],
            "soft_constraint_overrides": sum(1 for v in planned_visits if v.soft_constraint_override),
            "return_home_by_day": return_home,
            "cost": best.distance() if best else 0,
            "num_routes": best.num_routes() if best else 0,
            "runtime_seconds": time_budget,
        },
    )


def _empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "python_hgs", "unschedulable": []},
    )
