"""Benders outer loop: envelope → subproblem → cut → repeat.

Architecture:
  1. CP-SAT envelope produces a (clinician, day, window) assignment
  2. Per-vehicle subproblem routes each (c, d) or reports a Conflict
  3. Conflicts become no-good cuts; repeat with more constraints
  4. When all vehicles are feasible, run the concrete timing pass
     (cpsat_time_vehicle_route) to produce final start/end times,
     interleaving locked visits, lunch, breaks, and calendar blocks.
  5. validate_plan is the final safety gate.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime

from models import SolverInput, SolverOutput, PlannedVisit
from solver.benders.cuts import apply_cuts, generate_cuts
from solver.benders.envelope import CutStore, Envelope, Slot, solve_envelope
from solver.benders.subproblem import (
    SubproblemResult,
    VehicleRoute,
    solve_subproblems,
)
from solver.benders.validate import ValidationError, validate_plan
from solver.context import (
    ENVELOPE_BUDGET_FRAC,
    MAX_BENDERS_ROUNDS,
    SUBPROBLEM_BUDGET_FRAC,
    SolverContext,
    build_context,
    build_default_lunch_placements,
    day_bounds,
    empty_output,
)
from solver.cpsat_timing import cpsat_time_vehicle_route
from solver.timing import TimedRoute, time_vehicle_route

logger = logging.getLogger(__name__)


def _run_concrete_timing(
    ctx: SolverContext,
    input: SolverInput,
    routes: dict[tuple[int, int], VehicleRoute],
) -> dict[int, TimedRoute]:
    """Apply the concrete timing pass to each routed vehicle."""
    timed: dict[int, TimedRoute] = {}
    for (c_idx, d_idx), route in routes.items():
        vehicle = ctx.vehicle_by_key[(c_idx, d_idx)]
        if not route.ordered:
            timed[vehicle.vehicle_idx] = TimedRoute(vehicle_idx=vehicle.vehicle_idx)
            continue
        instances = [ctx.instances_by_id[iid] for iid in route.ordered]
        try:
            tr = cpsat_time_vehicle_route(vehicle, instances, input, ctx)
        except Exception as e:
            logger.warning("cpsat timing failed on (%d,%d): %s", c_idx, d_idx, e)
            tr = time_vehicle_route(vehicle, instances, input, ctx)
        timed[vehicle.vehicle_idx] = tr
    return timed


def _build_planned_visits(
    ctx: SolverContext,
    timed: dict[int, TimedRoute],
) -> list[PlannedVisit]:
    visits: list[PlannedVisit] = []
    for v_idx, tr in timed.items():
        for v in tr.visits:
            # tr.visits entries are already PlannedVisit-shaped (from TimedRoute)
            # but need clinician_idx filled in from the vehicle
            vehicle = ctx.vehicles[v_idx]
            if hasattr(v, "clinician_idx") and v.clinician_idx == vehicle.clinician_idx:
                visits.append(v)
            else:
                # Build a fresh PlannedVisit carrying the clinician index
                visits.append(
                    PlannedVisit(
                        instance_id=v.instance_id,
                        patient_id=v.patient_id,
                        clinician_idx=vehicle.clinician_idx,
                        date=v.date,
                        starts_at=v.starts_at,
                        ends_at=v.ends_at,
                        soft_constraint_override=getattr(
                            v, "soft_constraint_override", False
                        ),
                    )
                )
    return visits


def _build_lunch_placements(
    ctx: SolverContext,
    input: SolverInput,
    timed: dict[int, TimedRoute],
) -> dict[str, dict]:
    placements: dict[str, dict] = {}
    for v_idx, tr in timed.items():
        vehicle = ctx.vehicles[v_idx]
        if tr.lunch:
            placements[vehicle.date] = tr.lunch
        elif vehicle.date not in placements:
            # Default lunch for this vehicle's clinician
            c = vehicle.clinician
            if c.lunch_duration_minutes > 0:
                ds, _ = day_bounds(c, vehicle.date)
                half_w = c.lunch_window_minutes // 2
                earliest = max(c.lunch_start_minute - half_w, ds)
                placements[vehicle.date] = {
                    "start_minute": earliest,
                    "end_minute": earliest + c.lunch_duration_minutes,
                }
    # Backfill any missing dates
    for date in input.working_days:
        if date not in placements:
            placements[date] = None
    return placements


def _envelope_from_upper_bound(
    upper_bound: SolverOutput | None,
    input: SolverInput,
    ctx: SolverContext,
) -> Envelope | None:
    """Build a warm-start Envelope from a prior SolverOutput.

    Returns None if the prior plan doesn't match the current input (instances
    renamed, clinicians changed, etc.) or if any prior visit lands on a slot
    that no longer exists under the current constraints.
    """
    if upper_bound is None or not upper_bound.planned_visits:
        return None

    instances_by_id = ctx.instances_by_id
    env = Envelope(status="WARM_START")

    for v in upper_bound.planned_visits:
        inst = instances_by_id.get(v.instance_id)
        if inst is None:
            return None
        if v.clinician_idx < 0 or v.clinician_idx >= len(input.clinicians):
            return None
        d_idx = ctx.date_to_idx.get(v.date)
        if d_idx is None:
            return None
        # Respect eligibility
        if inst.eligible_clinician_indices and v.clinician_idx not in inst.eligible_clinician_indices:
            return None
        clinician = input.clinicians[v.clinician_idx]
        ds, de = day_bounds(clinician, v.date)
        # Use the full day window as the hint — the envelope model will pick
        # an exact window_idx within its legal set.  The hint is best-effort.
        env.assignments[v.instance_id] = Slot(
            clinician_idx=v.clinician_idx,
            day_idx=d_idx,
            window_idx=0,
            window_start=ds,
            window_end=de,
        )
    return env


def _diagnose_unscheduled(
    env: Envelope,
    input: SolverInput,
    ctx: SolverContext,
) -> list[dict]:
    """Per-unscheduled-instance reason set.

    Inspects the instance's structural constraints to identify the most
    specific reason it couldn't be placed.  Reasons are ordered from most
    specific/actionable to least.
    """
    from solver.benders.envelope import _enumerate_slots  # late import to avoid cycle

    # Re-run slot enumeration to see exactly what was legal for each instance
    legal_slots = _enumerate_slots(input, ctx)

    result: list[dict] = []
    num_clinicians = len(input.clinicians)
    num_days = len(input.working_days)

    for iid in env.unscheduled:
        inst = ctx.instances_by_id.get(iid)
        if not inst:
            continue
        reasons: set[str] = set()
        patient = ctx.patients_by_id.get(inst.patient_id)

        eligible = inst.eligible_clinician_indices or list(range(num_clinicians))

        # 1. Empty eligibility set explicitly
        if inst.eligible_clinician_indices is not None and not inst.eligible_clinician_indices:
            reasons.add("no_eligible_clinician")

        # 2. Availability windows cover zero working days
        if inst.availability_windows:
            working_wdays = {str(ctx.day_wdays[d]) for d in range(num_days)}
            usable_wdays = [
                wd for wd in working_wdays if inst.availability_windows.get(wd)
            ]
            if not usable_wdays:
                reasons.add("no_window_any_working_day")

        # 3. Every window is shorter than the instance duration
        if inst.availability_windows and not reasons:
            all_windows: list[tuple[int, int]] = []
            for wd, wins in inst.availability_windows.items():
                for w in wins:
                    all_windows.append(
                        (int(w.get("start_minute", 0)), int(w.get("end_minute", 1440)))
                    )
            if all_windows and all(
                (we - ws) < inst.duration for ws, we in all_windows
            ):
                reasons.add("window_too_short")

        # 4. No legal slots at all (after eligibility + availability + blocks)
        if not legal_slots.get(iid):
            reasons.add("no_legal_slot")

        # 5. Every eligible (c, d) is already at capacity from locked visits
        all_saturated = True
        for c_idx in eligible:
            if c_idx >= num_clinicians:
                continue
            for d_idx in range(num_days):
                vehicle = ctx.vehicle_by_key.get((c_idx, d_idx))
                if vehicle is None:
                    continue
                if vehicle.capacity > 0:
                    all_saturated = False
                    break
            if not all_saturated:
                break
        if all_saturated and eligible:
            reasons.add("all_eligible_vehicles_locked_full")

        # 6. Spacing impossibility: patient requires N visits with min_gap
        #    but horizon can't fit them
        if patient and patient.min_days_between_visits > 0:
            siblings = ctx.instances_by_patient.get(inst.patient_id, [])
            n_req = len(siblings)
            if n_req >= 2:
                # Minimum horizon length: (n-1)*(min_gap+1)+1 days
                min_horizon = (n_req - 1) * (patient.min_days_between_visits + 1) + 1
                if min_horizon > num_days:
                    reasons.add("spacing_infeasible_for_horizon")

        # 7. Fallback: ran into cuts or couldn't find legal assignment
        if not reasons:
            reasons.add("envelope_infeasible_with_cuts")

        result.append(
            {
                "patient_name": patient.name if patient else "Unknown",
                "patient_id": inst.patient_id,
                "instance_id": iid,
                "reasons": sorted(reasons),
            }
        )
    return result


def solve(
    input: SolverInput,
    time_budget: float = 30.0,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Main entry: Benders hybrid solve."""
    if not input.instances:
        return empty_output(input, metadata={"optimizer_type": "benders"})

    ctx = build_context(input)
    cut_store = CutStore()
    deadline = time.monotonic() + time_budget

    warm_start_env = _envelope_from_upper_bound(upper_bound, input, ctx)
    warm_start_used = warm_start_env is not None

    best_envelope: Envelope | None = None
    best_subproblem: SubproblemResult | None = None
    benders_rounds = 0
    iteration_log: list[dict] = []
    cp_sat_status = "NOT_RUN"

    for round_idx in range(MAX_BENDERS_ROUNDS):
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            break

        env_budget = max(0.5, remaining * ENVELOPE_BUDGET_FRAC)
        # Warm-start only on the first round; after that, prefer whatever the
        # previous iteration found (not wired yet — deferred to LNS polish).
        env_warm = warm_start_env if round_idx == 0 else None
        env = solve_envelope(
            input, ctx, cut_store, time_budget=env_budget, warm_start=env_warm
        )
        cp_sat_status = env.status
        benders_rounds += 1

        if env.status not in ("OPTIMAL", "FEASIBLE"):
            iteration_log.append(
                {"round": round_idx, "envelope_status": env.status, "placed": 0}
            )
            break

        sub = solve_subproblems(env, input, ctx)
        iteration_log.append(
            {
                "round": round_idx,
                "envelope_status": env.status,
                "placed": len(env.assignments),
                "conflicts": len(sub.conflicts),
                "drive": sub.total_drive(),
            }
        )

        # Track best (feasible preferred)
        is_feasible = sub.all_feasible()
        if is_feasible and (
            best_envelope is None or not _best_was_feasible(best_subproblem)
        ):
            best_envelope = env
            best_subproblem = sub
        elif is_feasible and sub.total_drive() < (
            best_subproblem.total_drive() if best_subproblem else 10**9
        ):
            best_envelope = env
            best_subproblem = sub
        elif best_envelope is None:
            # Keep infeasible as fallback until we find something feasible
            best_envelope = env
            best_subproblem = sub

        if is_feasible:
            break

        new_cuts = generate_cuts(sub, input=input, ctx=ctx)
        added = apply_cuts(cut_store, new_cuts)
        if added == 0:
            # Couldn't generate new cuts → we're stuck, bail
            break

    if best_envelope is None or best_subproblem is None:
        return empty_output(
            input,
            metadata={
                "optimizer_type": "benders",
                "status": "INFEASIBLE",
                "cp_sat_status": cp_sat_status,
                "benders_rounds": benders_rounds,
                "iteration_log": iteration_log,
                "warm_start_used": warm_start_used,
            },
        )

    # Concrete timing pass
    timed = _run_concrete_timing(ctx, input, best_subproblem.routes)
    planned_visits = _build_planned_visits(ctx, timed)
    lunch_placements = _build_lunch_placements(ctx, input, timed)

    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Compute final drive cost from timed routes
    final_drive = sum(getattr(tr, "drive_cost", 0) or 0 for tr in timed.values())
    if final_drive == 0:
        final_drive = best_subproblem.total_drive()

    unschedulable = _diagnose_unscheduled(best_envelope, input, ctx)
    schedule_status = "FEASIBLE"
    if unschedulable:
        schedule_status = "PARTIAL"
    if not best_subproblem.all_feasible():
        schedule_status = "PARTIAL"

    output = SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch_placements,
        fitness=float(final_drive),
        metadata={
            "optimizer_type": "benders",
            "status": schedule_status,
            "cp_sat_status": cp_sat_status,
            "benders_rounds": benders_rounds,
            "cuts_generated": len(cut_store),
            "drive": final_drive,
            "placed": len(planned_visits),
            "unschedulable": unschedulable,
            "iteration_log": iteration_log,
            "warm_start_used": warm_start_used,
        },
    )

    # Final safety gate
    try:
        validate_plan(output, input)
        output.metadata["validated"] = True
    except ValidationError as e:
        logger.error("validate_plan failed: %s", e)
        output.metadata["validated"] = False
        output.metadata["validation_error"] = str(e)

    return output


def _best_was_feasible(prev: SubproblemResult | None) -> bool:
    return prev is not None and prev.all_feasible()
