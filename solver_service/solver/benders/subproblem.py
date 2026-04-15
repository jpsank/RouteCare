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


# Cap on exhaustive enumeration; beyond this we fall back to NN + 2-opt
# local search.  n ≤ 7 is 7! × 8 = 40k evaluations (~30ms).  At n = 8 it
# becomes 362k (~250ms), too slow per vehicle.
MAX_EXHAUSTIVE_STOPS = 7

# Upper bound on 2-opt sweeps for the NN-fallback path.  Each sweep is
# O(n² × (n+1)) evaluations.  In practice 2-opt converges in 3-5 sweeps;
# the cap prevents pathological oscillation or slow convergence.
MAX_TWO_OPT_ITERATIONS = 50


@dataclass
class VehicleRoute:
    clinician_idx: int
    day_idx: int
    ordered: list[str]
    drive_cost: int
    # Absolute start minute per stop (within the day, minute-of-day)
    starts: dict[str, int] = field(default_factory=dict)
    # Per-instance detour cost: travel(prev, i) + travel(i, next) -
    # travel(prev, next).  Fed back to the envelope as the cost
    # marginal for (instance, clinician_idx, day_idx) on the next
    # Benders round.  Empty for vehicles with no stops.
    marginals: dict[str, int] = field(default_factory=dict)
    # Planned lunch start minute if lunch was inserted into the route,
    # else None.  Included so the concrete timing pass can honor the
    # subproblem's lunch choice directly.
    lunch_start: int | None = None


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

    Forward pass with explicit lunch insertion: enumerates both the stop
    permutation AND the lunch position (before stop k, for k in 0..n) so
    the subproblem matches the concrete timing pass's lunch behavior.
    Required breaks (max_continuous_work overflow) remain capacity-
    reserved — they're rarer than lunch and the conservative approx is
    sound for them.

    Locked visits are NOT modeled here — the concrete timing pass handles
    them at the end.
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

    # Lunch flex window [earliest, latest] — lunch can start anywhere in
    # this range.  If lunch_duration_minutes == 0 the whole lunch phase
    # is skipped.
    lunch_dur = clinician.lunch_duration_minutes
    has_lunch = lunch_dur > 0
    half_w = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_w, day_start)
    lunch_latest = min(
        clinician.lunch_start_minute + half_w,
        day_end - lunch_dur,
    )
    if has_lunch and lunch_latest < lunch_earliest:
        # Degenerate config: lunch window starts after it can end.
        # Fall back to a conservative reservation.
        lunch_latest = lunch_earliest

    # Mandatory break reservation (long-shift rule) — still approximated
    # as capacity subtraction.  Break insertion happens in the concrete
    # timing pass via CP-SAT.
    shift_duration = day_end - day_start
    break_reserved = 0
    if (
        clinician.max_continuous_work_minutes > 0
        and shift_duration > clinician.max_continuous_work_minutes
        and clinician.required_break_minutes > 0
    ):
        break_reserved = clinician.required_break_minutes
    effective_day_end = day_end - break_reserved

    n = len(stops)

    def _evaluate(
        order: list[int], lunch_pos: int
    ) -> tuple[int, dict[str, int], int | None] | None:
        """Forward pass for a (permutation, lunch_position) pair.

        lunch_pos in [0, n]:
          0   → lunch before the first stop
          k   → lunch between stops k-1 and k
          n   → lunch after the last stop (still before return-home)

        If has_lunch is False, lunch_pos is ignored.

        Returns (drive_cost, stop_start_minutes, lunch_start_minute)
        or None if infeasible.  lunch_start_minute is None when no
        lunch was inserted.
        """
        ordered_stops = [stops[i] for i in order]
        pids = [str(ctx.instances_by_id[iid].patient_id) for iid, _ in ordered_stops]
        durs = [ctx.instances_by_id[iid].duration for iid, _ in ordered_stops]

        t = day_start
        prev_key = home_key
        starts: dict[str, int] = {}
        drive = 0
        lunch_start_out: int | None = None

        def _take_lunch(current_t: int) -> int | None:
            """Try to take lunch at or after current_t.  Returns new time
            after lunch, or None if the lunch window is already closed."""
            nonlocal lunch_start_out
            ls = max(current_t, lunch_earliest)
            if ls > lunch_latest:
                return None
            lunch_start_out = ls
            return ls + lunch_dur

        for k in range(n):
            # Take lunch at this position if requested
            if has_lunch and lunch_pos == k and lunch_start_out is None:
                new_t = _take_lunch(t)
                if new_t is None:
                    return None
                t = new_t

            pid = pids[k]
            iid = ordered_stops[k][0]
            slot = ordered_stops[k][1]
            dur = durs[k]

            leg = travel(prev_key, pid)
            drive += leg
            t += leg

            # Wait until window opens
            if t < slot.window_start:
                t = slot.window_start
            # Window end check
            if t > slot.window_end - dur:
                return None

            starts[iid] = t
            t += dur
            prev_key = pid

        # Take lunch after last stop if requested
        if has_lunch and lunch_pos == n and lunch_start_out is None:
            new_t = _take_lunch(t)
            if new_t is None:
                return None
            t = new_t

        # Sanity: if has_lunch, lunch must have been placed by now
        if has_lunch and lunch_start_out is None:
            return None

        # Return to home
        back = travel(prev_key, home_key)
        drive += back
        t += back

        if t > effective_day_end:
            return None
        if drive > vehicle.max_drive:
            return None
        return drive, starts, lunch_start_out

    # Enumerate (permutation, lunch_position).
    # For n ≤ MAX_EXHAUSTIVE_STOPS: all permutations × (n+1) lunch positions.
    # For n > MAX_EXHAUSTIVE_STOPS: nearest-neighbor order × all lunch positions.
    use_exhaustive = n <= MAX_EXHAUSTIVE_STOPS
    lunch_positions = range(n + 1) if has_lunch else [0]  # 0 is a no-op sentinel when no lunch

    best: tuple[int, dict[str, int], list[int], int | None] | None = None

    if use_exhaustive:
        for perm in itertools.permutations(range(n)):
            perm_list = list(perm)
            for lp in lunch_positions:
                result = _evaluate(perm_list, lp)
                if result is None:
                    continue
                drive, starts, lunch_s = result
                if best is None or drive < best[0]:
                    best = (drive, starts, perm_list, lunch_s)
    else:
        # NN + 2-opt local search.  NN gives a fast initial ordering;
        # 2-opt iteratively reverses sub-sequences that reduce total
        # drive while keeping all window constraints satisfied.
        # First-improvement strategy: accept the first improving swap
        # each sweep and restart.  Terminates when a full sweep finds
        # no improvement, or after MAX_TWO_OPT_ITERATIONS bumps.
        initial_order = _nearest_neighbor(stops, ctx, home_key)
        best = _best_over_lunch_positions(initial_order, lunch_positions, _evaluate)
        if best is not None:
            best = _two_opt_improve(best, lunch_positions, _evaluate, n)

    if best is None:
        return Conflict(
            clinician_idx=c_idx,
            day_idx=vehicle.day_index,
            unfit_subset=[iid for iid, _ in stops],
            reason="no_feasible_ordering",
        )

    drive_cost, starts, order, lunch_s = best
    ordered_ids = [stops[i][0] for i in order]
    marginals = _compute_marginals(order, stops, ctx, home_key)
    return VehicleRoute(
        clinician_idx=c_idx,
        day_idx=vehicle.day_index,
        ordered=ordered_ids,
        drive_cost=drive_cost,
        starts=starts,
        marginals=marginals,
        lunch_start=lunch_s,
    )


def _best_over_lunch_positions(
    order: list[int],
    lunch_positions,
    evaluate_fn,
) -> tuple[int, dict[str, int], list[int], int | None] | None:
    """Run `evaluate_fn(order, lp)` over every lunch position, pick the
    cheapest feasible result.  Returns the `best` tuple shape used in
    `_route_vehicle` or None if no lunch position produces a feasible
    plan."""
    best: tuple[int, dict[str, int], list[int], int | None] | None = None
    for lp in lunch_positions:
        result = evaluate_fn(order, lp)
        if result is None:
            continue
        drive, starts, lunch_s = result
        if best is None or drive < best[0]:
            best = (drive, starts, list(order), lunch_s)
    return best


def _two_opt_improve(
    current: tuple[int, dict[str, int], list[int], int | None],
    lunch_positions,
    evaluate_fn,
    n: int,
) -> tuple[int, dict[str, int], list[int], int | None]:
    """Iteratively apply 2-opt swaps to improve a feasible ordering.

    For each pair (i, j) with 0 ≤ i < j < n, try reversing
    `order[i:j+1]`.  If the new ordering is still feasible under some
    lunch position AND has lower total drive, accept it and restart
    the sweep.  Terminates when a full sweep finds no improvement or
    after MAX_TWO_OPT_ITERATIONS iterations.
    """
    best_drive, best_starts, best_order, best_lunch = current

    for _ in range(MAX_TWO_OPT_ITERATIONS):
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                # Reverse order[i..j] (inclusive).  i=0, j=n-1 is
                # equivalent to reversing the whole route — valid but
                # usually not improving unless the NN start was wrong.
                new_order = (
                    best_order[:i]
                    + list(reversed(best_order[i:j + 1]))
                    + best_order[j + 1:]
                )
                trial = _best_over_lunch_positions(
                    new_order, lunch_positions, evaluate_fn
                )
                if trial is None:
                    continue
                if trial[0] < best_drive:
                    best_drive, best_starts, best_order, best_lunch = trial
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return (best_drive, best_starts, best_order, best_lunch)


def _compute_marginals(
    order: list[int],
    stops: list[tuple[str, Slot]],
    ctx: SolverContext,
    home_key: str,
) -> dict[str, int]:
    """Per-instance detour cost on the chosen route.

    detour(i) = travel(prev, i) + travel(i, next) - travel(prev, next)

    where prev/next are the actual neighbors of instance i in the ordered
    route, with home_key standing in for the virtual depot at both ends.
    This is what the envelope should use as the cost of placing instance
    i on this (clinician, day) on the NEXT Benders round — it replaces
    the home-leg approximation with an actual routing-based marginal.
    """
    travel = ctx.travel
    n = len(order)
    if n == 0:
        return {}

    ordered_stops = [stops[i] for i in order]
    pids = [str(ctx.instances_by_id[iid].patient_id) for iid, _ in ordered_stops]

    marginals: dict[str, int] = {}
    for k in range(n):
        prev_key = home_key if k == 0 else pids[k - 1]
        next_key = home_key if k == n - 1 else pids[k + 1]
        with_i = travel(prev_key, pids[k]) + travel(pids[k], next_key)
        without_i = travel(prev_key, next_key)
        marginals[ordered_stops[k][0]] = max(0, with_i - without_i)
    return marginals


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
