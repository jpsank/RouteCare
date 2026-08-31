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
from solver.benders.lns import LNS_BUDGET_FRAC
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
from solver.cpsat_timing import TimedRoute, cpsat_time_vehicle_route

logger = logging.getLogger(__name__)


def _run_concrete_timing(
    ctx: SolverContext,
    input: SolverInput,
    routes: dict[tuple[int, int], VehicleRoute],
) -> dict[int, TimedRoute]:
    """Apply the concrete timing pass to each routed vehicle.

    Uses the CP-SAT timing model as the single source of truth.  There
    is no greedy fallback — CP-SAT timing is deterministic and robust
    enough under the subproblem's feasibility contract.  If it ever
    raises, the exception propagates and the benders loop reports
    failure; there's no silent fallback masking a real bug.
    """
    timed: dict[int, TimedRoute] = {}
    for (c_idx, d_idx), route in routes.items():
        vehicle = ctx.vehicle_by_key[(c_idx, d_idx)]
        # Always go through cpsat_time_vehicle_route, even with zero routed
        # instances — it still needs to place lunch around this vehicle's
        # locked visits and calendar blocks. A bare empty TimedRoute() skips
        # that and lets _build_lunch_placements's naive per-date default
        # stamp lunch on top of whatever's already on the schedule.
        instances = [ctx.instances_by_id[iid] for iid in route.ordered]
        timed[vehicle.vehicle_idx] = cpsat_time_vehicle_route(
            vehicle, instances, input, ctx
        )
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
            if v.clinician_idx == vehicle.clinician_idx:
                visits.append(v)
            else:
                visits.append(
                    PlannedVisit(
                        instance_id=v.instance_id,
                        patient_id=v.patient_id,
                        clinician_idx=vehicle.clinician_idx,
                        date=v.date,
                        starts_at=v.starts_at,
                        ends_at=v.ends_at,
                    )
                )
    return visits


def _build_lunch_placements(
    ctx: SolverContext,
    input: SolverInput,
    timed: dict[int, TimedRoute],
) -> dict[str, dict]:
    """Return per-working-day lunch placement.

    Every working day maps to a dict (never None — the SolverOutput
    schema requires it).  Dates with visits use the concrete timing
    pass's chosen lunch.  Dates with no routed visits get the first
    clinician's default lunch window, or an empty dict if the clinician
    has no lunch configured.
    """
    placements: dict[str, dict] = {}

    def _default_lunch(c, date: str) -> dict:
        if c is None or c.lunch_duration_minutes <= 0:
            return {}
        ds, _ = day_bounds(c, date)
        half_w = c.lunch_window_minutes // 2
        earliest = max(c.lunch_start_minute - half_w, ds)
        return {
            "start_minute": earliest,
            "end_minute": earliest + c.lunch_duration_minutes,
        }

    for v_idx, tr in timed.items():
        vehicle = ctx.vehicles[v_idx]
        if tr.lunch:
            placements[vehicle.date] = tr.lunch
        elif vehicle.date not in placements:
            placements[vehicle.date] = _default_lunch(vehicle.clinician, vehicle.date)

    default_clinician = input.clinicians[0] if input.clinicians else None
    for date in input.working_days:
        if date not in placements:
            placements[date] = _default_lunch(default_clinician, date)
    return placements


def _envelope_from_upper_bound(
    upper_bound: SolverOutput | None,
    input: SolverInput,
    ctx: SolverContext,
) -> Envelope | None:
    """Build a warm-start Envelope from a prior SolverOutput.

    Instances in the prior plan that no longer exist in the current input
    (e.g. cancelled patients) are silently skipped — the warm-start covers
    whatever overlap exists.  Returns None only if the prior plan is
    structurally unusable (bad clinician indices, missing dates).
    """
    if upper_bound is None or not upper_bound.planned_visits:
        return None

    instances_by_id = ctx.instances_by_id
    env = Envelope(status="WARM_START")

    for v in upper_bound.planned_visits:
        inst = instances_by_id.get(v.instance_id)
        if inst is None:
            continue  # instance removed from current input — skip
        if v.clinician_idx < 0 or v.clinician_idx >= len(input.clinicians):
            continue  # clinician no longer exists — skip
        d_idx = ctx.date_to_idx.get(v.date)
        if d_idx is None:
            continue  # date out of current horizon — skip
        if inst.eligible_clinician_indices and v.clinician_idx not in inst.eligible_clinician_indices:
            continue  # eligibility changed — skip
        clinician = input.clinicians[v.clinician_idx]
        ds, de = day_bounds(clinician, v.date)
        env.assignments[v.instance_id] = Slot(
            clinician_idx=v.clinician_idx,
            day_idx=d_idx,
            window_idx=0,
            window_start=ds,
            window_end=de,
        )

    # If literally nothing from the prior plan maps to the current input,
    # there's no warm start to provide.
    if not env.assignments:
        return None
    return env


def _diagnose_unscheduled(
    env: Envelope,
    input: SolverInput,
    ctx: SolverContext,
    legal_slots: dict | None = None,
) -> list[dict]:
    """Per-unscheduled-instance reason set.

    Inspects the instance's structural constraints to identify the most
    specific reason it couldn't be placed.  Reasons are ordered from most
    specific/actionable to least.
    """
    if legal_slots is None:
        from solver.benders.envelope import _enumerate_slots  # late import
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

        # 3b. Unavailable (blackout) windows fully cover every availability
        #     window (or the whole day, when no availability windows exist)
        if inst.unavailability_windows and not reasons:
            from solver.benders.envelope import _block_kills_window  # late import

            working_wdays = {ctx.day_wdays[d] for d in range(num_days)}
            fully_blacked_out = True
            for wd in working_wdays:
                wd_str = str(wd)
                avail = (
                    inst.availability_windows.get(wd_str, [])
                    if inst.availability_windows
                    else [{"start_minute": 0, "end_minute": 1440}]
                )
                unavail = inst.unavailability_windows.get(wd_str, [])
                if not unavail:
                    fully_blacked_out = False
                    break
                unavail_ranges = [
                    (int(w.get("start_minute", 0)), int(w.get("end_minute", 1440)))
                    for w in unavail
                ]
                if any(
                    not _block_kills_window(
                        int(a.get("start_minute", 0)),
                        int(a.get("end_minute", 1440)),
                        unavail_ranges,
                        inst.duration,
                    )
                    for a in avail
                ):
                    fully_blacked_out = False
                    break
            if fully_blacked_out:
                reasons.add("fully_blacked_out")

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

    # Precompute invariants across Benders rounds.  Slot enumeration and
    # home-leg approximation both depend only on (input, ctx), which
    # don't change across rounds — only the cut store and marginals do.
    # Computing these once instead of per-round is ~30% of the envelope
    # model-build time on multi-round scenarios.
    from solver.benders.envelope import _compute_approx_costs, _enumerate_slots
    precomputed_slots = _enumerate_slots(input, ctx)
    precomputed_approx_costs = _compute_approx_costs(input, ctx)

    best_envelope: Envelope | None = None
    best_subproblem: SubproblemResult | None = None
    benders_rounds = 0
    iteration_log: list[dict] = []
    cp_sat_status = "NOT_RUN"
    # Per-(instance, clinician, day) detour marginals, accumulated from
    # subproblem routes and fed back to the envelope's cost model on
    # each subsequent round.  Empty on round 0 (envelope uses the
    # home-leg approximation); populated whenever a route is produced.
    marginal_costs: dict[tuple[str, int, int], int] = {}

    logger.info(
        "benders.solve start instances=%d clinicians=%d days=%d warm=%s budget=%.1fs",
        len(input.instances), len(input.clinicians), len(input.working_days),
        warm_start_used, time_budget,
    )

    for round_idx in range(MAX_BENDERS_ROUNDS):
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            break

        env_budget = max(0.5, remaining * ENVELOPE_BUDGET_FRAC)
        env_warm = warm_start_env if round_idx == 0 else None

        env_t0 = time.monotonic()
        env = solve_envelope(
            input, ctx, cut_store,
            time_budget=env_budget,
            warm_start=env_warm,
            marginal_costs=marginal_costs if round_idx > 0 else None,
            precomputed_slots=precomputed_slots,
            precomputed_approx_costs=precomputed_approx_costs,
        )
        env_dt = time.monotonic() - env_t0
        cp_sat_status = env.status
        benders_rounds += 1

        if env.status not in ("OPTIMAL", "FEASIBLE"):
            logger.info(
                "benders.round %d envelope=%s elapsed=%.3fs cuts=%d — aborting",
                round_idx, env.status, env_dt, len(cut_store),
            )
            iteration_log.append(
                {"round": round_idx, "envelope_status": env.status, "placed": 0}
            )
            break

        sub_t0 = time.monotonic()
        sub = solve_subproblems(env, input, ctx)
        sub_dt = time.monotonic() - sub_t0

        # Collect per-instance detour marginals from multi-stop routes
        # only.  Single-stop routes have `detour == full_round_trip`,
        # which is just 2× the home-leg approximation and adds no
        # information — feeding that back would destabilize cost
        # comparisons without improving the envelope's decisions.
        for (c_idx, d_idx), route in sub.routes.items():
            if len(route.ordered) < 2:
                continue
            for iid, detour in route.marginals.items():
                marginal_costs[(iid, c_idx, d_idx)] = detour

        logger.info(
            "benders.round %d envelope=%s (%.3fs) placed=%d subproblem=(%.3fs) "
            "conflicts=%d drive=%d cuts_before=%d marginals=%d",
            round_idx, env.status, env_dt,
            len(env.assignments), sub_dt,
            len(sub.conflicts), sub.total_drive(), len(cut_store),
            len(marginal_costs),
        )

        iteration_log.append(
            {
                "round": round_idx,
                "envelope_status": env.status,
                "envelope_time": round(env_dt, 3),
                "subproblem_time": round(sub_dt, 3),
                "placed": len(env.assignments),
                "conflicts": len(sub.conflicts),
                "drive": sub.total_drive(),
            }
        )

        # Track best:
        #   1. feasible beats any infeasible
        #   2. among feasible, lower drive cost wins
        #   3. among infeasible, more routed visits wins (tiebreak by drive)
        is_feasible = sub.all_feasible()
        cur_placed = sum(len(r.ordered) for r in sub.routes.values())

        if best_subproblem is None:
            best_envelope = env
            best_subproblem = sub
        elif is_feasible:
            if not _best_was_feasible(best_subproblem):
                best_envelope = env
                best_subproblem = sub
            elif sub.total_drive() < best_subproblem.total_drive():
                best_envelope = env
                best_subproblem = sub
        else:
            # Current round is infeasible — only replace a previous
            # infeasible best if we've routed more visits.
            if not _best_was_feasible(best_subproblem):
                best_placed = sum(
                    len(r.ordered) for r in best_subproblem.routes.values()
                )
                if cur_placed > best_placed or (
                    cur_placed == best_placed
                    and sub.total_drive() < best_subproblem.total_drive()
                ):
                    best_envelope = env
                    best_subproblem = sub

        if is_feasible:
            break

        new_cuts = generate_cuts(sub, input=input, ctx=ctx)
        added = apply_cuts(cut_store, new_cuts)
        logger.info(
            "benders.round %d generated %d cuts (added %d, total %d)",
            round_idx, len(new_cuts), added, len(cut_store),
        )
        if added == 0:
            logger.warning(
                "benders.round %d no new cuts — stuck, bailing out", round_idx
            )
            break

    if best_envelope is None or best_subproblem is None:
        logger.warning(
            "benders.solve INFEASIBLE after %d rounds cp_sat_status=%s",
            benders_rounds, cp_sat_status,
        )
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

    # ── LNS polish (post-convergence, pre-timing) ────────────────
    # Hill-climbing destroy/repair on the feasible incumbent.  Runs
    # only when it's likely to help AND when it's compatible with
    # what the caller asked for:
    #   - incumbent is feasible
    #   - budget fraction is positive
    #   - problem is big enough (placed ≥ MIN_LNS_PLACED)
    #   - NOT a warm-started re-solve: warm-start implies the caller
    #     wants continuity with the prior plan.  LNS would undo that
    #     continuity to chase a few percent of drive savings — the
    #     wrong tradeoff.  Week-to-week re-solves should preserve
    #     assignments; LNS re-runs from scratch on new plans only.
    #
    # LNS itself has further short-circuits (no multi-stop routes,
    # too few placements).  Never worsens the incumbent.
    MIN_LNS_PLACED = 15
    lns_metadata: dict = {}
    should_polish = (
        best_subproblem.all_feasible()
        and LNS_BUDGET_FRAC > 0
        and len(best_envelope.assignments) >= MIN_LNS_PLACED
        and not warm_start_used
    )
    if should_polish:
        remaining = deadline - time.monotonic()
        # Scale LNS cap by problem size.  Small problems (≤50 placed)
        # get 3s — enough for ~10-30 iterations, plenty.  Large problems
        # (>100 placed) get up to 10s because each envelope re-solve
        # is slower and we need more absolute time to see any iterations
        # at all.  The `LNS_BUDGET_FRAC` env var still gates the overall
        # fraction of remaining time.
        placed = len(best_envelope.assignments)
        if placed <= 50:
            cap = 3.0
        elif placed <= 100:
            cap = 5.0
        else:
            cap = 10.0
        lns_budget = min(cap, remaining * LNS_BUDGET_FRAC)
        if lns_budget > 0.5:
            from solver.benders.lns import polish as lns_polish  # late import
            lns_result = lns_polish(
                best_envelope, best_subproblem,
                input, ctx, cut_store,
                precomputed_slots=precomputed_slots,
                precomputed_approx_costs=precomputed_approx_costs,
                time_budget=lns_budget,
            )

            if lns_result.final_cost < lns_result.initial_cost:
                # Verify via concrete timing — the subproblem's forward
                # pass is lunch-aware but edge-case interactions can
                # still cause cpsat_timing to drop a visit.  If timing
                # drops anything, fall back to the pre-LNS incumbent.
                trial_timed = _run_concrete_timing(
                    ctx, input, lns_result.subproblem.routes
                )
                trial_dropped = sum(
                    len(getattr(tr, "dropped", []) or [])
                    for tr in trial_timed.values()
                )
                if trial_dropped == 0:
                    best_envelope = lns_result.envelope
                    best_subproblem = lns_result.subproblem
                else:
                    logger.info(
                        "lns.polish output dropped %d visits in concrete "
                        "timing — reverting to pre-LNS incumbent",
                        trial_dropped,
                    )

            lns_metadata = {
                "lns_iterations": lns_result.iterations,
                "lns_improvements": lns_result.improvements,
                "lns_rejected": lns_result.rejected,
                "lns_initial_cost": lns_result.initial_cost,
                "lns_final_cost": lns_result.final_cost,
                "lns_delta": lns_result.final_cost - lns_result.initial_cost,
                "lns_stopped_reason": lns_result.stopped_reason,
            }

    # Concrete timing pass
    timed = _run_concrete_timing(ctx, input, best_subproblem.routes)
    planned_visits = _build_planned_visits(ctx, timed)
    lunch_placements = _build_lunch_placements(ctx, input, timed)

    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Compute final drive cost from timed routes
    final_drive = sum(getattr(tr, "drive_cost", 0) or 0 for tr in timed.values())
    if final_drive == 0:
        final_drive = best_subproblem.total_drive()

    unschedulable = _diagnose_unscheduled(
        best_envelope, input, ctx, legal_slots=precomputed_slots
    )
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
            **lns_metadata,
        },
    )

    # Final safety gate
    try:
        validate_plan(output, input)
        output.metadata["validated"] = True
    except ValidationError as e:
        logger.error("benders.solve validate_plan FAILED: %s", e)
        output.metadata["validated"] = False
        output.metadata["validation_error"] = str(e)

    logger.info(
        "benders.solve done status=%s placed=%d/%d drive=%d rounds=%d cuts=%d validated=%s",
        schedule_status, len(planned_visits), len(input.instances),
        final_drive, benders_rounds, len(cut_store),
        output.metadata.get("validated"),
    )
    return output


def _best_was_feasible(prev: SubproblemResult | None) -> bool:
    return prev is not None and prev.all_feasible()
