"""VRPSolverEasy (BCP) solver backend."""

from __future__ import annotations
from models import SolverInput, SolverOutput, PlannedVisit
from solvers.base import (
    MAX_VISITS_PER_DAY,
    build_lunch_placements,
    compute_return_home,
    minutes_to_datetime,
    redistribute_same_day_duplicates,
    validate_spacing,
)

try:
    import VRPSolverEasy as vrpse
    HAS_VRPSE = True
except ImportError:
    HAS_VRPSE = False


def solve(
    input: SolverInput,
    time_budget: int = 3600,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Build a VRPSolverEasy model, solve with BCP, post-process."""
    if not HAS_VRPSE:
        raise ImportError("VRPSolverEasy is not installed")

    instances = input.instances
    if not instances:
        return _empty_output(input)

    clinician = input.clinician
    working_days = input.working_days
    num_days = len(working_days)
    matrix = input.travel_matrix
    patients_by_id = {p.id: p for p in input.patients}

    # Build VRPSolverEasy model
    model = vrpse.Model()

    # Depot (home)
    home = clinician.home_location
    model.add_depot(
        id=0,
        name="home",
        tw_begin=clinician.workday_start_minute,
        tw_end=clinician.workday_end_minute,
    )

    # Customers (visit instances)
    # VRPSolverEasy uses integer IDs; map instance string IDs to ints
    id_map: dict[int, str] = {}  # vrpse_id → instance_id
    pid_map: dict[int, int] = {}  # vrpse_id → patient_id

    for i, inst in enumerate(instances):
        vrpse_id = i + 1  # 0 is depot
        id_map[vrpse_id] = inst.id
        pid_map[vrpse_id] = inst.patient_id

        # Time window: use workday bounds (availability windows handled in post-processing)
        model.add_customer(
            id=vrpse_id,
            name=inst.id,
            demand=1,
            service_time=inst.duration,
            tw_begin=clinician.workday_start_minute,
            tw_end=clinician.workday_end_minute,
        )

    # Vehicle type (one per day)
    workday_duration = clinician.workday_end_minute - clinician.workday_start_minute
    max_dist = clinician.max_drive_minutes_per_day or 999999

    model.add_vehicle_type(
        id=1,
        start_point_id=0,
        end_point_id=0,
        capacity=MAX_VISITS_PER_DAY,
        max_number=num_days,
        var_cost_dist=1.0,
        tw_begin=clinician.workday_start_minute,
        tw_end=clinician.workday_end_minute,
    )

    # Distance/time links
    n = len(instances)
    for i in range(n + 1):
        for j in range(n + 1):
            if i == j:
                continue
            from_key = "home" if i == 0 else str(pid_map.get(i, i))
            to_key = "home" if j == 0 else str(pid_map.get(j, j))
            t = matrix.get(from_key, {}).get(to_key, 0)
            model.add_link(start_point_id=i, end_point_id=j, distance=t, time=t)

    # Parameters — use HGS upper bound if provided to prune BCP search tree
    ub = int(upper_bound.fitness) if upper_bound and upper_bound.fitness > 0 else 1000000
    model.set_parameters(time_limit=time_budget, upper_bound=ub)

    # Solve
    model.solve()
    status = model.status if hasattr(model, "status") else -1
    solution = model.solution

    if not solution.is_defined or not solution.routes:
        return _empty_output(input, metadata={
            "optimizer_type": "python_bcp",
            "status": int(status),
            "proven_optimal": False,
        })

    # Map routes back
    planned_visits = []
    routes_by_day: dict[str, list[int]] = {}

    for route_idx, route in enumerate(solution.routes):
        if route_idx >= num_days:
            break
        date = working_days[route_idx]
        day_patient_ids = []

        current_time = clinician.workday_start_minute
        for node_id in route.point_ids:
            if node_id == 0:  # depot
                continue
            inst_id = id_map.get(node_id)
            patient_id = pid_map.get(node_id)
            if not inst_id or not patient_id:
                continue

            inst = next((i for i in instances if i.id == inst_id), None)
            if not inst:
                continue

            prev_key = str(day_patient_ids[-1]) if day_patient_ids else "home"
            curr_key = str(patient_id)
            transit = matrix.get(prev_key, {}).get(curr_key, 0)
            start_minute = current_time + transit
            start_minute = ((start_minute + 14) // 15) * 15

            visit_duration = inst.duration - input.clinician.charting_buffer_minutes

            planned_visits.append(PlannedVisit(
                instance_id=inst_id,
                patient_id=patient_id,
                date=date,
                starts_at=minutes_to_datetime(date, start_minute),
                ends_at=minutes_to_datetime(date, start_minute + visit_duration),
                soft_constraint_override=False,
            ))

            current_time = start_minute + inst.duration
            day_patient_ids.append(patient_id)

        routes_by_day[date] = day_patient_ids

    # Post-process: enforce one-patient-per-day by moving duplicates to other days
    planned_visits = redistribute_same_day_duplicates(planned_visits, working_days, clinician)
    planned_visits = validate_spacing(planned_visits, patients_by_id)
    lunch = build_lunch_placements(input)

    # Recompute routes_by_day after redistribution
    routes_by_day = {}
    for v in planned_visits:
        routes_by_day.setdefault(v.date, []).append(v.patient_id)
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

    # Status 0 = optimal in BaPCod
    proven_optimal = int(status) == 0

    return SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch,
        fitness=solution.value if solution.is_defined else 0.0,
        metadata={
            "optimizer_type": "python_bcp",
            "proven_optimal": proven_optimal,
            "status": int(status),
            "unschedulable": unschedulable,
            "drive_violations": [],
            "soft_constraint_overrides": sum(1 for v in planned_visits if v.soft_constraint_override),
            "return_home_by_day": return_home,
            "runtime_seconds": time_budget,
        },
    )


def _empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "python_bcp"},
    )
