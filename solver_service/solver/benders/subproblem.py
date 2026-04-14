"""Per-vehicle routing subproblem for the Benders hybrid.

Given a fixed envelope, route each (clinician, day) vehicle independently.
For typical day capacities (≤6 stops), exhaustive permutation is both
optimal and faster than invoking a metaheuristic per vehicle.  Larger
vehicles could fall back to PyVRP — deferred.

A vehicle is "feasible" under the envelope if some ordering of its assigned
stops fits within:
  - Each stop's chosen availability window (from envelope.Slot)
  - Vehicle shift bounds
  - Travel time between consecutive stops

Returns per-vehicle either a routed solution or a Conflict identifying the
subset of instances that cannot coexist on this (clinician, day).  The loop
turns conflicts into no-good cuts and re-solves the envelope.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

from models import SolverInput
from solver.benders.envelope import Envelope, Slot
from solver.context import SolverContext, VehicleDef

logger = logging.getLogger(__name__)


# Cap on exhaustive enumeration; beyond this we'd need a metaheuristic.
MAX_EXHAUSTIVE_STOPS = 7


@dataclass
class VehicleRoute:
    clinician_idx: int
    day_idx: int
    ordered: list[str]
    drive_cost: int
    # Absolute start minute per stop (within the day, minute-of-day)
    starts: dict[str, int] = field(default_factory=dict)


@dataclass
class Conflict:
    clinician_idx: int
    day_idx: int
    unfit_subset: list[str]
    reason: str = ""


@dataclass
class SubproblemResult:
    routes: dict[tuple[int, int], VehicleRoute] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)

    def all_feasible(self) -> bool:
        return len(self.conflicts) == 0

    def total_drive(self) -> int:
        return sum(r.drive_cost for r in self.routes.values())


# ── Per-vehicle routing ─────────────────────────────────────────────


def _route_vehicle(
    vehicle: VehicleDef,
    stops: list[tuple[str, Slot]],
    input: SolverInput,
    ctx: SolverContext,
) -> VehicleRoute | Conflict:
    """Find best ordering for one vehicle's stops, or report the infeasible subset.

    Forward-pass feasibility against each stop's envelope window and the
    vehicle shift.  Locked visits are NOT modeled here — the concrete timing
    pass handles them at the end.
    """
    if not stops:
        return VehicleRoute(
            clinician_idx=vehicle.clinician_idx,
            day_idx=vehicle.day_index,
            ordered=[],
            drive_cost=0,
        )

    c_idx = vehicle.clinician_idx
    home_key = f"home_{c_idx}"
    travel = ctx.travel
    clinician = vehicle.clinician
    day_start = vehicle.day_start
    day_end = vehicle.day_end

    # Lunch is currently unmodeled in the subproblem feasibility pass —
    # the concrete timing pass (cpsat_time_vehicle_route) enforces it later.
    # For the spike this is acceptable: subproblem proves routing feasibility
    # on travel+windows, concrete timing handles lunch/blocks/breaks.

    n = len(stops)

    # If n is small, try every permutation.  For n > MAX_EXHAUSTIVE_STOPS,
    # fall back to nearest-neighbor (quality degrades but remains feasible).
    use_exhaustive = n <= MAX_EXHAUSTIVE_STOPS

    def _evaluate(order: list[int]) -> tuple[int, dict[str, int]] | None:
        """Forward pass.  Returns (drive_cost, stop_starts) or None if infeasible."""
        ordered_stops = [stops[i] for i in order]
        pids = [str(ctx.instances_by_id[iid].patient_id) for iid, _ in ordered_stops]
        # Start time: leave home at day_start
        t = day_start + travel(home_key, pids[0])
        # But can't arrive before the first stop's window opens
        first_slot = ordered_stops[0][1]
        if t < first_slot.window_start:
            t = first_slot.window_start
        if t > first_slot.window_end - ctx.instances_by_id[ordered_stops[0][0]].duration:
            return None
        starts: dict[str, int] = {ordered_stops[0][0]: t}
        t += ctx.instances_by_id[ordered_stops[0][0]].duration

        drive = travel(home_key, pids[0])
        for k in range(1, n):
            prev_pid = pids[k - 1]
            cur_pid = pids[k]
            iid = ordered_stops[k][0]
            slot = ordered_stops[k][1]
            dur = ctx.instances_by_id[iid].duration
            leg = travel(prev_pid, cur_pid)
            drive += leg
            t += leg
            if t < slot.window_start:
                t = slot.window_start
            if t > slot.window_end - dur:
                return None
            starts[iid] = t
            t += dur

        # Return to home
        back = travel(pids[-1], home_key)
        drive += back
        t += back
        if t > day_end:
            return None
        # Drive limit check
        if drive > vehicle.max_drive:
            return None
        return drive, starts

    best: tuple[int, dict[str, int], list[int]] | None = None

    if use_exhaustive:
        for perm in itertools.permutations(range(n)):
            result = _evaluate(list(perm))
            if result is None:
                continue
            drive, starts = result
            if best is None or drive < best[0]:
                best = (drive, starts, list(perm))
    else:
        # Nearest-neighbor fallback (not used for MAX_VISITS_PER_DAY=5)
        order = _nearest_neighbor(stops, ctx, home_key)
        result = _evaluate(order)
        if result is not None:
            best = (result[0], result[1], order)

    if best is None:
        return Conflict(
            clinician_idx=c_idx,
            day_idx=vehicle.day_index,
            unfit_subset=[iid for iid, _ in stops],
            reason="no_feasible_ordering",
        )

    drive_cost, starts, order = best
    ordered_ids = [stops[i][0] for i in order]
    return VehicleRoute(
        clinician_idx=c_idx,
        day_idx=vehicle.day_index,
        ordered=ordered_ids,
        drive_cost=drive_cost,
        starts=starts,
    )


def _nearest_neighbor(
    stops: list[tuple[str, Slot]],
    ctx: SolverContext,
    home_key: str,
) -> list[int]:
    travel = ctx.travel
    n = len(stops)
    pids = [str(ctx.instances_by_id[iid].patient_id) for iid, _ in stops]
    remaining = list(range(n))
    order: list[int] = []
    cur = home_key
    while remaining:
        best_i = min(remaining, key=lambda i: travel(cur, pids[i]))
        order.append(best_i)
        remaining.remove(best_i)
        cur = pids[best_i]
    return order


def _shrink_conflict(
    vehicle: VehicleDef,
    stops: list[tuple[str, Slot]],
    input: SolverInput,
    ctx: SolverContext,
) -> list[str]:
    """Find a minimal infeasible subset (greedy shrink).

    Starts from the full set (already known infeasible) and removes one
    instance at a time; if the result is still infeasible, drop it
    permanently.  Produces a small unfit subset that makes cuts stronger.

    Memoizes routing results by frozenset(instance_ids) for this vehicle —
    conflict shrinking tests many overlapping subsets and would otherwise
    re-run the same permutation enumeration multiple times.
    """
    cache: dict[frozenset, bool] = {}

    def _is_feasible(subset: list[tuple[str, Slot]]) -> bool:
        key = frozenset(iid for iid, _ in subset)
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = _route_vehicle(vehicle, subset, input, ctx)
        feasible = not isinstance(result, Conflict)
        cache[key] = feasible
        return feasible

    remaining = list(stops)
    i = 0
    while i < len(remaining) and len(remaining) > 1:
        trial = remaining[:i] + remaining[i + 1:]
        if _is_feasible(trial):
            # Removing this one made it feasible → this one is part of the conflict
            i += 1
        else:
            # Still infeasible without this one → permanently drop it
            remaining = trial
    return [iid for iid, _ in remaining]


# ── Public API ──────────────────────────────────────────────────────


def solve_subproblems(
    envelope: Envelope,
    input: SolverInput,
    ctx: SolverContext,
) -> SubproblemResult:
    """Route every (c, d) vehicle under the fixed envelope."""
    result = SubproblemResult()

    for vehicle in ctx.vehicles:
        c_idx = vehicle.clinician_idx
        d_idx = vehicle.day_index
        stops = envelope.slots_for_vehicle(c_idx, d_idx)
        route_or_conflict = _route_vehicle(vehicle, stops, input, ctx)
        if isinstance(route_or_conflict, Conflict):
            # Try to shrink the conflict for a stronger cut
            minimal = _shrink_conflict(vehicle, stops, input, ctx)
            route_or_conflict.unfit_subset = minimal
            result.conflicts.append(route_or_conflict)
        else:
            result.routes[(c_idx, d_idx)] = route_or_conflict

    return result
