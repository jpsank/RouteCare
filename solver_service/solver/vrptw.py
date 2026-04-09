"""VRPTW solver for healthcare scheduling.

Architecture:
  PyVRP (HGS-CVRP) solves the full horizon as a single VRPTW:
    - Working days are vehicles with shift-window in absolute minutes
    - Visit instances are clients with wide time windows
    - Locked visits are required clients

  LNS loop with CP-SAT repair handles constraints PyVRP can't express:
    - Min/max visit spacing across days
    - One patient per day
    - Visit frequency / priority

  Post-hoc timing produces concrete schedules:
    - 15-min slot rounding, lunch insertion, breaks
    - Calendar blocks, locked visit interleaving
    - Charting buffer, transit buffer

  Env:
    SOLVER_LNS_MAX_ITERATIONS — max LNS passes (default 30)
    SOLVER_NUM_WORKERS — OR-Tools worker threads (default min(CPU, 8))
    SOLVER_HGS_INITIAL_FRAC — fraction of time_budget for initial PyVRP solve (default 0.30)
    SOLVER_LNS_FRAC — fraction of time_budget for LNS loop (default 0.60)
    SOLVER_DESTROY_FRAC_MIN — min fraction of visits to destroy per LNS iteration (default 0.20)
    SOLVER_DESTROY_FRAC_MAX — max fraction of visits to destroy per LNS iteration (default 0.40)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime

from pyvrp.stop import MaxRuntime, MultipleCriteria, NoImprovement

from models import SolverInput, SolverOutput, PlannedVisit
from solver.context import (
    HGS_INITIAL_FRAC,
    LNS_FRAC,
    SolverContext,
    build_context,
    build_default_lunch_placements,
    empty_output,
)
from solver.fitness import (
    AdaptiveLambda,
    aggregate_tier2_violations,
    compute_total_fitness,
)
from solver.lns import lns_improve, _optimize_vehicle_order
from solver.model import (
    Solution,
    build_model,
    extract_solution,
)
from solver.cpsat_timing import cpsat_time_vehicle_route
from solver.timing import (
    TimedRoute,
    time_vehicle_route,
)

logger = logging.getLogger(__name__)


def _diagnose_unscheduled(inst, patient, input, ctx, placed_patient_dates) -> list[str]:
    """Identify which hard constraints prevented scheduling this instance."""
    reasons = set()

    for vehicle in ctx.vehicles:
        d = vehicle.day_index
        date = vehicle.date
        clinician = vehicle.clinician
        wday = str(ctx.day_wdays[d])

        # Eligibility
        if (
            inst.eligible_clinician_indices
            and vehicle.clinician_idx not in inst.eligible_clinician_indices
        ):
            continue

        if date in placed_patient_dates.get(inst.patient_id, set()):
            continue
        if d in ctx.locked_patient_days.get(inst.patient_id, set()):
            continue

        if patient and patient.min_days_between_visits >= 1:
            date_ord = datetime.fromisoformat(date).toordinal()
            too_close = False
            for pd in placed_patient_dates.get(inst.patient_id, set()):
                pd_ord = datetime.fromisoformat(pd).toordinal()
                if abs(date_ord - pd_ord) <= patient.min_days_between_visits:
                    too_close = True
                    break
            if too_close:
                reasons.add("min_days_between_visits")
                continue

        pdh = clinician.per_day_hours.get(wday, {})
        day_start = pdh.get("start", clinician.workday_start_minute)
        day_end = pdh.get("end", clinician.workday_end_minute)
        avail = max(0, day_end - day_start)
        if clinician.lunch_duration_minutes > 0:
            avail -= clinician.lunch_duration_minutes
        if inst.duration > avail:
            reasons.add("day_capacity")
            continue

        if inst.availability_windows:
            windows = inst.availability_windows.get(wday, [])
            if not windows:
                reasons.add("availability_windows")
                continue

        reasons.add("routing_or_drive_limit")

    if not reasons:
        reasons.add("no_working_days_available")

    return sorted(reasons)


def _enforce_one_per_day(solution: Solution, ctx: SolverContext):
    """Remove duplicate same-patient visits from each vehicle (day).

    Per-day client groups prevent the same *instance* appearing on multiple
    days, but two *different* instances of the same patient can still land
    on the same day.  Move extras to unassigned for LNS repair.
    """
    for v_idx, route in list(solution.routes.items()):
        seen_patients: set[int] = set()
        vehicle = ctx.vehicles[v_idx]
        for pid, days in ctx.locked_patient_days.items():
            if vehicle.day_index in days:
                seen_patients.add(pid)

        kept: list[str] = []
        for iid in route:
            inst = ctx.instances_by_id.get(iid)
            if inst and inst.patient_id in seen_patients:
                solution.unassigned.append(iid)
            else:
                if inst:
                    seen_patients.add(inst.patient_id)
                kept.append(iid)
        solution.routes[v_idx] = kept


def _time_all_routes(
    solution: Solution,
    ctx: SolverContext,
    input: SolverInput,
) -> dict[int, TimedRoute]:
    """Run concrete timing on every vehicle route."""
    timed: dict[int, TimedRoute] = {}
    for v_idx, vehicle in enumerate(ctx.vehicles):
        route_ids = solution.routes.get(v_idx, [])
        if not route_ids:
            timed[v_idx] = TimedRoute(vehicle_idx=v_idx)
            continue
        ordered = _optimize_vehicle_order(vehicle, route_ids, ctx, input)
        try:
            tr = cpsat_time_vehicle_route(vehicle, ordered, input, ctx)
        except Exception:
            tr = time_vehicle_route(vehicle, ordered, input, ctx)
        timed[v_idx] = tr
        # Update solution route order to match timing
        solution.routes[v_idx] = [v.instance_id for v in tr.visits]
    return timed


def _warm_start_solution(
    upper_bound: SolverOutput,
    ctx: SolverContext,
    input: SolverInput,
) -> Solution | None:
    """Convert a prior SolverOutput into a Solution for warm-start."""
    if not upper_bound or not upper_bound.planned_visits:
        return None

    # Validate: all instance IDs match
    expected_ids = {inst.id for inst in input.instances}
    visit_ids = {v.instance_id for v in upper_bound.planned_visits}
    if not visit_ids.issubset(expected_ids):
        return None

    # Map visits to vehicles (by date)
    routes: dict[int, list[str]] = defaultdict(list)
    for v in upper_bound.planned_visits:
        vehicle = ctx.vehicle_by_date.get(v.date)
        if vehicle is None:
            return None
        routes[vehicle.vehicle_idx].append(v.instance_id)

    unassigned = [iid for iid in expected_ids if iid not in visit_ids]

    return Solution(routes=dict(routes), unassigned=unassigned)


def solve(
    input: SolverInput,
    time_budget: int = 30,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """VRPTW solve for the given scheduling horizon."""
    if not input.instances:
        return empty_output(
            input,
            metadata={
                "optimizer_type": "vrptw",
                "upper_bound_provided": upper_bound is not None,
            },
        )

    start_time = time.monotonic()
    ctx = build_context(input)
    patients_by_id = ctx.patients_by_id
    num_instances = len(input.instances)

    # ── Phase 1: Build PyVRP model ───────────────────────────────────

    pyvrp_model = build_model(input, ctx)

    # ── Phase 2: Initial solve (PyVRP or warm-start) ─────────────────

    warm_solution = None
    if upper_bound is not None:
        warm_solution = _warm_start_solution(upper_bound, ctx, input)

    hgs_budget = max(1.0, time_budget * HGS_INITIAL_FRAC)

    # Always build NN and run HGS — even with warm-start, we pick the best
    # of all three to avoid inheriting a stale prior solution.
    nn_solution = _construct_nn_solution(input, ctx)

    try:
        hgs_stop = MultipleCriteria([MaxRuntime(hgs_budget), NoImprovement(500)])
        result = pyvrp_model.solve(stop=hgs_stop, seed=42, display=False)
        pyvrp_solution = extract_solution(result, ctx, input.instances)
        _enforce_one_per_day(pyvrp_solution, ctx)
        pyvrp_placed = sum(len(r) for r in pyvrp_solution.routes.values())
    except Exception as e:
        logger.warning("PyVRP initial solve failed: %s", e)
        pyvrp_solution = None
        pyvrp_placed = -1

    nn_placed = sum(len(r) for r in nn_solution.routes.values())

    # Pick best candidate by placement count
    candidates = [(nn_solution, nn_placed, "nn")]
    if pyvrp_solution is not None:
        candidates.append((pyvrp_solution, pyvrp_placed, "pyvrp"))
    if warm_solution is not None:
        warm_placed = sum(len(r) for r in warm_solution.routes.values())
        candidates.append((warm_solution, warm_placed, "warm"))

    candidates.sort(key=lambda c: c[1], reverse=True)
    solution = candidates[0][0]
    initial_source = candidates[0][2]
    if warm_solution is not None and initial_source != "warm":
        logger.info(
            "Warm-start (%d placed) beaten by %s (%d placed)",
            sum(len(r) for r in warm_solution.routes.values()),
            initial_source,
            candidates[0][1],
        )

    # ── Phase 2b: Time initial solution ──────────────────────────────

    timed_routes = _time_all_routes(solution, ctx, input)

    # Handle timing drops: visits that couldn't be timed get moved to unassigned
    _collect_timing_drops(solution, timed_routes)

    # Seed adaptive lambda from initial solution's violations so it
    # starts calibrated instead of cold-starting for ~5 iterations.
    lambdas = AdaptiveLambda()
    initial_viols = aggregate_tier2_violations(timed_routes, ctx)
    has_viols = (
        initial_viols.transit_excess_minutes > 0
        or initial_viols.overtime_minutes > 0
        or initial_viols.break_violations > 0
        or initial_viols.lunch_window_violation > 0
    )
    # Seed the rolling window: if initial solution has violations, start
    # lambda higher (as if we've seen mostly infeasible solutions);
    # if clean, start lower (allow exploration).
    for _ in range(5):
        lambdas.record(not has_viols)
    if has_viols:
        lambdas.adapt()  # will increase λ since infeasible_frac > target

    all_visits, total_drive = _collect_visits_and_drive(timed_routes, ctx)
    best_placed, best_cost, _ = compute_total_fitness(
        all_visits,
        total_drive,
        input,
        ctx,
        timed_routes=timed_routes,
        lambdas=lambdas,
    )

    # ── Phase 3: LNS improvement loop ────────────────────────────────

    elapsed = time.monotonic() - start_time
    lns_budget = max(
        1.0, time_budget * LNS_FRAC - max(0, elapsed - time_budget * HGS_INITIAL_FRAC)
    )

    solution, timed_routes, best_placed, best_cost, lns_log = lns_improve(
        solution,
        timed_routes,
        ctx,
        input,
        time_budget=lns_budget,
        best_placed=best_placed,
        best_cost=best_cost,
        lambdas=lambdas,
    )

    # ── Phase 4: Final timing + output assembly ──────────────────────

    # Re-collect after LNS
    all_visits, total_drive = _collect_visits_and_drive(timed_routes, ctx)
    final_placed, final_cost, breakdown = compute_total_fitness(
        all_visits,
        total_drive,
        input,
        ctx,
        timed_routes=timed_routes,
        lambdas=lambdas,
    )

    # Encode lexicographic fitness
    if final_cost == float("inf"):
        # Tier 1 violation in final output — remove offending visits
        # (duplicate patient-day) and re-evaluate.
        seen: dict[tuple[int, str], int] = {}
        clean_visits = []
        for v in all_visits:
            key = (v.patient_id, v.date)
            if key in seen:
                continue
            seen[key] = 1
            clean_visits.append(v)
        all_visits = clean_visits
        total_drive = sum(tr.drive_cost for tr in timed_routes.values())
        final_placed, final_cost, breakdown = compute_total_fitness(
            all_visits,
            total_drive,
            input,
            ctx,
            timed_routes=timed_routes,
            lambdas=lambdas,
        )
    capped_cost = min(final_cost, 999_999_999)
    visit_level = max(int(capped_cost) + 1, 1_000_000)
    fitness = (num_instances - final_placed) * visit_level + capped_cost

    # Sort visits
    all_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Lunch placements
    lunch_placements = {}
    for v_idx, tr in timed_routes.items():
        vehicle = ctx.vehicles[v_idx]
        if tr.lunch:
            lunch_placements[vehicle.date] = tr.lunch
    # Fill missing days with defaults
    default_lunch = build_default_lunch_placements(input)
    for date in input.working_days:
        if date not in lunch_placements:
            lunch_placements[date] = default_lunch.get(date)

    # Return-home per day
    return_home: dict[str, int] = {}
    for v_idx, tr in timed_routes.items():
        if tr.visits:
            vehicle = ctx.vehicles[v_idx]
            last_pid = str(tr.visits[-1].patient_id)
            home_key = f"home_{vehicle.clinician_idx}"
            return_home[vehicle.date] = ctx.travel(last_pid, home_key)

    # Day bounds
    day_bounds_used = {}
    for vehicle in ctx.vehicles:
        day_bounds_used[vehicle.date] = {
            "start": vehicle.day_start,
            "end": vehicle.day_end,
        }

    # Route winners
    route_winners = {}
    for v_idx, tr in timed_routes.items():
        if tr.visits:
            route_winners[ctx.vehicles[v_idx].date] = "vrptw"

    # Unschedulable diagnosis
    placed_ids = {v.instance_id for v in all_visits}
    placed_patient_dates: dict[int, set[str]] = defaultdict(set)
    for v in all_visits:
        placed_patient_dates[v.patient_id].add(v.date)

    unschedulable = []
    for inst in input.instances:
        if inst.id in placed_ids:
            continue
        patient = patients_by_id.get(inst.patient_id)
        reasons = _diagnose_unscheduled(inst, patient, input, ctx, placed_patient_dates)
        unschedulable.append(
            {
                "patient_name": patient.name if patient else "Unknown",
                "patient_id": inst.patient_id,
                "reasons": reasons,
            }
        )

    # Drive violations
    drive_violations = []
    for v_idx, tr in timed_routes.items():
        vehicle = ctx.vehicles[v_idx]
        if vehicle.max_drive < 999_999 and tr.drive_cost > vehicle.max_drive:
            drive_violations.append(
                {
                    "date": vehicle.date,
                    "clinician_idx": vehicle.clinician_idx,
                    "drive_minutes": tr.drive_cost,
                    "max_drive": vehicle.max_drive,
                }
            )

    schedule_status = "FEASIBLE" if not unschedulable else "PARTIAL"
    elapsed_total = time.monotonic() - start_time

    return SolverOutput(
        planned_visits=all_visits,
        lunch_placements=lunch_placements,
        fitness=fitness,
        metadata={
            "optimizer_type": "vrptw",
            "status": schedule_status,
            "iterations": len(lns_log),
            "iteration_log": lns_log,
            "route_winners": route_winners,
            "unschedulable": unschedulable,
            "drive_violations": drive_violations,
            "soft_constraint_overrides": sum(
                1 for v in all_visits if v.soft_constraint_override
            ),
            "return_home_by_day": return_home,
            "warm_start_used": warm_solution is not None,
            "upper_bound_provided": upper_bound is not None,
            "day_bounds": day_bounds_used,
            "solver_time_seconds": round(elapsed_total, 2),
            "adaptive_lambda": lambdas.to_dict(),
            **breakdown,
        },
    )


# ── Internal Helpers ─────────────────────────────────────────────────


def _construct_nn_solution(input: SolverInput, ctx: SolverContext) -> Solution:
    """Fallback: assign visits to days via nearest-neighbor heuristic."""
    travel = ctx.travel
    vehicles = ctx.vehicles
    instances = list(input.instances)

    # Simple greedy: for each instance, find the cheapest feasible vehicle
    routes: dict[int, list[str]] = defaultdict(list)
    unassigned: list[str] = []
    patient_on_day: dict[int, set[int]] = defaultdict(set)

    # Include locked visits in patient-day tracking
    for pid, days in ctx.locked_patient_days.items():
        patient_on_day[pid].update(days)

    # Sort instances by home-leg cost (closest first for better packing)
    instances.sort(key=lambda i: travel("home_0", str(i.patient_id)))

    for inst in instances:
        pid = inst.patient_id
        patient = ctx.patients_by_id.get(pid)
        best_v = None
        best_cost = float("inf")

        for v in vehicles:
            d_idx = v.day_index
            home_key = f"home_{v.clinician_idx}"

            # Clinician eligibility
            if (
                inst.eligible_clinician_indices
                and v.clinician_idx not in inst.eligible_clinician_indices
            ):
                continue

            # Capacity
            if len(routes.get(v.vehicle_idx, [])) >= v.capacity:
                continue

            # One per day
            if d_idx in patient_on_day.get(pid, set()):
                continue

            # Availability
            wday_str = str(ctx.day_wdays[d_idx])
            if inst.availability_windows:
                if not inst.availability_windows.get(wday_str, []):
                    continue

            # Min spacing
            if patient and patient.min_days_between_visits >= 1:
                too_close = False
                for placed_d in patient_on_day.get(pid, set()):
                    if abs(d_idx - placed_d) <= patient.min_days_between_visits:
                        too_close = True
                        break
                if too_close:
                    continue

            # Cost: insertion into existing route
            route = routes.get(v.vehicle_idx, [])
            if not route:
                cost = travel(home_key, str(pid)) + travel(str(pid), home_key)
            else:
                route_pids = [str(ctx.instances_by_id[iid].patient_id) for iid in route]
                cost = (
                    travel(route_pids[-1], str(pid))
                    + travel(str(pid), home_key)
                    - travel(route_pids[-1], home_key)
                )

            if cost < best_cost:
                best_cost = cost
                best_v = v

        if best_v is not None:
            routes[best_v.vehicle_idx].append(inst.id)
            patient_on_day[pid].add(best_v.day_index)
        else:
            unassigned.append(inst.id)

    return Solution(routes=dict(routes), unassigned=unassigned)


def _collect_timing_drops(solution: Solution, timed_routes: dict[int, TimedRoute]):
    """Move timing-dropped visits to solution.unassigned."""
    for v_idx, tr in timed_routes.items():
        dropped_ids = {d["instance_id"] for d in tr.dropped}
        if dropped_ids:
            route = solution.routes.get(v_idx, [])
            solution.routes[v_idx] = [iid for iid in route if iid not in dropped_ids]
            solution.unassigned.extend(dropped_ids)


def _collect_visits_and_drive(
    timed_routes: dict[int, TimedRoute],
    ctx: SolverContext,
) -> tuple[list[PlannedVisit], int]:
    """Collect all planned visits and total drive from timed routes."""
    all_visits: list[PlannedVisit] = []
    total_drive = 0
    for v_idx in range(len(ctx.vehicles)):
        tr = timed_routes.get(v_idx)
        if tr:
            all_visits.extend(tr.visits)
            total_drive += tr.drive_cost
    return all_visits, total_drive
