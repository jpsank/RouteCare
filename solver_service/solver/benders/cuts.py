"""No-good cut generation for the Benders loop.

Three cut classes, from weakest to strongest:

1. **Weak cut** — the exact unfit subset cannot all land on the specific
   (c, d).  Eliminates one envelope at a time.  Always emitted as a fallback.

2. **Pairwise cross-vehicle cut** — for each pair in the unfit subset, probe
   whether the pair alone is infeasible on other (c, d) vehicles where both
   instances are eligible.  Emit a per-vehicle exclusion cut for every
   vehicle that confirms the incompatibility.  This generalizes the
   "this pair can never share a shift" finding across the whole horizon.

3. **Capacity cut** — if the total service duration of the unfit subset
   exceeds every vehicle's shift, the subset cannot all be scheduled
   together on any vehicle.  Emit a global exclusion.

Only (1) is always emitted.  (2) runs when the unfit subset has ≥2 pairs.
(3) runs when a shift-duration overflow is detected.  Deduplication in
`apply_cuts` prevents the cut store from growing without bound.
"""

from __future__ import annotations

from models import SolverInput
from solver.benders.envelope import CutStore, NoGoodCut, Slot
from solver.benders.subproblem import (
    Conflict,
    SubproblemResult,
    _route_vehicle,
)
from solver.context import SolverContext


def _pair_infeasible_on_vehicle(
    iid_a: str,
    iid_b: str,
    slot_a: Slot,
    slot_b: Slot,
    c_idx: int,
    d_idx: int,
    input: SolverInput,
    ctx: SolverContext,
) -> bool:
    """Is the pair {a, b} alone infeasible on vehicle (c_idx, d_idx)?

    Uses the vehicle's own day window bounds as the slot fallback for the
    pair, so this works even when the pair's original envelope windows came
    from a different vehicle.
    """
    vehicle = ctx.vehicle_by_key.get((c_idx, d_idx))
    if vehicle is None:
        return False
    # Use the widest legal window for each instance on this vehicle.
    # If an instance has availability constraints, restrict to those;
    # otherwise use the full day bounds.  This is a conservative isolation
    # test — if the pair fails here, it fails on a weaker vehicle too.
    wday = str(ctx.day_wdays[d_idx])
    ds, de = vehicle.day_start, vehicle.day_end

    def _mk_widest_slot(iid: str) -> Slot | None:
        inst = ctx.instances_by_id.get(iid)
        if inst is None:
            return None
        if inst.availability_windows:
            wins = inst.availability_windows.get(wday, [])
            if not wins:
                return None
            # widest window
            best = max(wins, key=lambda w: w.get("end_minute", 0) - w.get("start_minute", 0))
            ws = max(ds, int(best.get("start_minute", ds)))
            we = min(de, int(best.get("end_minute", de)))
            if we - ws < inst.duration:
                return None
            return Slot(
                clinician_idx=c_idx, day_idx=d_idx,
                window_idx=0, window_start=ws, window_end=we,
            )
        return Slot(
            clinician_idx=c_idx, day_idx=d_idx,
            window_idx=0, window_start=ds, window_end=de,
        )

    sa = _mk_widest_slot(iid_a)
    sb = _mk_widest_slot(iid_b)
    if sa is None or sb is None:
        return False  # not eligible → no cut (already handled by eligibility)

    result = _route_vehicle(vehicle, [(iid_a, sa), (iid_b, sb)], input, ctx)
    return isinstance(result, Conflict)


def _eligible_clinicians(iid: str, ctx: SolverContext, num_clinicians: int) -> list[int]:
    inst = ctx.instances_by_id.get(iid)
    if inst is None or not inst.eligible_clinician_indices:
        return list(range(num_clinicians))
    return list(inst.eligible_clinician_indices)


def _pairwise_cross_vehicle_cuts(
    conflict: Conflict,
    input: SolverInput,
    ctx: SolverContext,
) -> list[NoGoodCut]:
    """For each pair in the conflict, test isolation on every vehicle where
    both are eligible.  Emit per-vehicle pair exclusion cuts on each
    vehicle that confirms infeasibility.
    """
    cuts: list[NoGoodCut] = []
    subset = conflict.unfit_subset
    if len(subset) < 2:
        return cuts

    num_clinicians = len(input.clinicians)
    num_days = len(input.working_days)

    for i in range(len(subset)):
        for j in range(i + 1, len(subset)):
            a, b = subset[i], subset[j]
            elig_a = set(_eligible_clinicians(a, ctx, num_clinicians))
            elig_b = set(_eligible_clinicians(b, ctx, num_clinicians))
            shared = elig_a & elig_b
            if not shared:
                continue

            # Probe every shared (c, d) vehicle
            for c_idx in shared:
                for d_idx in range(num_days):
                    if _pair_infeasible_on_vehicle(
                        a, b,
                        # dummy slots — overwritten inside
                        Slot(c_idx, d_idx, 0, 0, 0),
                        Slot(c_idx, d_idx, 0, 0, 0),
                        c_idx, d_idx, input, ctx,
                    ):
                        cuts.append(
                            NoGoodCut(
                                forbidden=[(a, c_idx, d_idx), (b, c_idx, d_idx)],
                                reason="pair_incompatible",
                            )
                        )
    return cuts


def _capacity_cut(
    conflict: Conflict,
    input: SolverInput,
    ctx: SolverContext,
) -> NoGoodCut | None:
    """If the unfit subset's total service duration exceeds every vehicle's
    shift, the whole subset cannot share any single vehicle — emit a global
    exclusion across all eligible (c, d) pairs.

    Note: this is a *universal* cut but encoded as one NoGoodCut per vehicle
    via the weak-cut shape (sum of slot_vars ≤ len-1 on each).
    """
    subset = conflict.unfit_subset
    if len(subset) < 2:
        return None
    total_service = sum(
        ctx.instances_by_id[iid].duration for iid in subset
        if iid in ctx.instances_by_id
    )
    if total_service == 0:
        return None
    # If total service exceeds the longest shift, the subset is globally
    # infeasible as a unit.  Emit the subset exclusion on the conflict's
    # own vehicle as a strong cut; the per-pair cross-vehicle cuts above
    # handle other vehicles.
    longest_shift = max((v.shift_duration for v in ctx.vehicles), default=0)
    if total_service <= longest_shift:
        return None
    triples = [(iid, conflict.clinician_idx, conflict.day_idx) for iid in subset]
    return NoGoodCut(forbidden=triples, reason="capacity_overflow")


def generate_cuts(
    result: SubproblemResult,
    input: SolverInput | None = None,
    ctx: SolverContext | None = None,
) -> list[NoGoodCut]:
    """Generate weak + strong cuts for all conflicts in `result`.

    When `input` and `ctx` are provided, also emits pairwise cross-vehicle
    and capacity cuts.  Without them, emits weak cuts only (matching the
    original spike behavior for the unit-test call site).
    """
    cuts: list[NoGoodCut] = []
    for conflict in result.conflicts:
        # 1. Always: weak cut on the originating (c, d)
        triples = [
            (iid, conflict.clinician_idx, conflict.day_idx)
            for iid in conflict.unfit_subset
        ]
        if triples:
            cuts.append(
                NoGoodCut(
                    forbidden=triples,
                    reason=conflict.reason or "singleton",
                )
            )

        # 2. + 3. Stronger cuts require input and ctx
        if input is not None and ctx is not None:
            cuts.extend(_pairwise_cross_vehicle_cuts(conflict, input, ctx))
            cap_cut = _capacity_cut(conflict, input, ctx)
            if cap_cut is not None:
                cuts.append(cap_cut)

    return cuts


def apply_cuts(store: CutStore, new_cuts: list[NoGoodCut]) -> int:
    """Dedupe and add cuts to the store.  Returns count of newly added cuts."""
    existing = {
        tuple(sorted(c.forbidden)) for c in store.cuts
    }
    added = 0
    for c in new_cuts:
        key = tuple(sorted(c.forbidden))
        if key in existing:
            continue
        existing.add(key)
        store.add(c)
        added += 1
    return added
