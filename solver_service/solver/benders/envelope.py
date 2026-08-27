"""CP-SAT envelope (Benders master problem).

Decides for each visit instance a (clinician, day, window) slot satisfying
all discrete/combinatorial constraints.  Does NOT model routing order —
that is the subproblem's job.

Hard constraints:
  - At most one slot per instance (scheduled or unscheduled)
  - Eligibility (variable elimination at build time)
  - One patient per day (across all clinicians)
  - Per-clinician day capacity (max_visits_per_day minus locked count)
  - Min/max spacing between same-patient instances
  - Locked visit days already consumed
  - Instance duration must fit within chosen window
  - No-good cuts from prior Benders rounds

Soft terms in the objective:
  - Categorical visit-value (placement strictly dominates cost)
  - Approximate routing cost (home leg proxy)
  - Priority preference for scheduling high-priority patients
  - Load balance across clinicians
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from ortools.sat.python import cp_model

from models import SolverInput
from solver.context import (
    MINUTES_PER_DAY,
    NUM_WORKERS,
    PENALTY_LOAD_IMBALANCE,
    PENALTY_SPACING_MAX,
    PRIORITY_WEIGHT,
    SolverContext,
    day_bounds,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slot:
    """A chosen (clinician, day, window) slot for a visit instance."""

    clinician_idx: int
    day_idx: int
    window_idx: int
    window_start: int  # minute-of-day
    window_end: int  # minute-of-day


@dataclass
class Envelope:
    """Master-problem output: which instance lands where."""

    assignments: dict[str, Slot] = field(default_factory=dict)
    unscheduled: list[str] = field(default_factory=list)
    approximate_cost: int = 0
    status: str = "UNKNOWN"

    def slots_for_vehicle(self, c_idx: int, d_idx: int) -> list[tuple[str, Slot]]:
        return [
            (iid, slot)
            for iid, slot in self.assignments.items()
            if slot.clinician_idx == c_idx and slot.day_idx == d_idx
        ]


@dataclass
class NoGoodCut:
    """Forbids a specific combination of (instance, clinician, day) triples.

    Constraint: sum_{(i,c,d) in forbidden} slot[i,c,d,*] <= len(forbidden) - 1
    """

    forbidden: list[tuple[str, int, int]]
    reason: str = ""


@dataclass
class CutStore:
    cuts: list[NoGoodCut] = field(default_factory=list)

    def add(self, cut: NoGoodCut) -> None:
        self.cuts.append(cut)

    def __len__(self) -> int:
        return len(self.cuts)


# ── Slot enumeration ─────────────────────────────────────────────────


def _enumerate_slots(
    input: SolverInput,
    ctx: SolverContext,
) -> dict[str, list[Slot]]:
    """For each instance, list all legal (c, d, w) slots.

    A slot is dropped at build time if any of the following is true:
      - Clinician not in instance.eligible_clinician_indices (when non-empty)
      - Day is already consumed by a locked visit for this patient
      - Availability window missing on this weekday
      - Instance duration doesn't fit in the window
      - Window is entirely covered by a calendar block for this (c, d)
    """
    legal: dict[str, list[Slot]] = {}

    for inst in input.instances:
        slots: list[Slot] = []
        eligible = (
            set(inst.eligible_clinician_indices)
            if inst.eligible_clinician_indices
            else set(range(len(input.clinicians)))
        )
        locked_vehicles = ctx.locked_patient_days.get(inst.patient_id, set())

        for c_idx in eligible:
            if c_idx >= len(input.clinicians):
                continue
            clinician = input.clinicians[c_idx]
            for d_idx, date in enumerate(input.working_days):
                if (c_idx, d_idx) in locked_vehicles:
                    continue
                wday = str(ctx.day_wdays[d_idx])
                ds, de = day_bounds(clinician, date)
                # Blocked ranges for this (c, d): calendar blocks plus the
                # patient's own unavailable (blackout) windows on this weekday —
                # both shrink the usable room inside an availability window the
                # same way, regardless of whether availability windows exist at all.
                blocks = [
                    (
                        _dt_to_min(cb.starts_at),
                        _dt_to_min(cb.ends_at),
                    )
                    for cb in ctx.calendar_blocks_by_vehicle.get((c_idx, d_idx), [])
                ]
                if inst.unavailability_windows:
                    blocks.extend(
                        (
                            int(w.get("start_minute", 0)),
                            int(w.get("end_minute", 1440)),
                        )
                        for w in inst.unavailability_windows.get(wday, [])
                    )

                # Build window list.
                # - availability_windows empty dict → patient is always available
                # - availability_windows has entries but not this wday → not available
                # - availability_windows has this wday → use listed windows
                windows: list[tuple[int, int, int]] = []  # (idx, start, end)
                has_avail = bool(inst.availability_windows)
                if has_avail:
                    raw_wins = inst.availability_windows.get(wday, [])
                    if not raw_wins:
                        continue  # hard: not available this weekday
                    for w_idx, w in enumerate(raw_wins):
                        ws = max(ds, int(w.get("start_minute", ds)))
                        we = min(de, int(w.get("end_minute", de)))
                        if we - ws >= inst.duration:
                            windows.append((w_idx, ws, we))
                else:
                    # No availability constraints — full day is one window
                    windows.append((0, ds, de))

                for w_idx, ws, we in windows:
                    # Reject if a calendar block leaves no room
                    if _block_kills_window(ws, we, blocks, inst.duration):
                        continue
                    slots.append(
                        Slot(
                            clinician_idx=c_idx,
                            day_idx=d_idx,
                            window_idx=w_idx,
                            window_start=ws,
                            window_end=we,
                        )
                    )

        legal[inst.id] = slots

    return legal


def _dt_to_min(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def _block_kills_window(ws: int, we: int, blocks: list[tuple[int, int]], duration: int) -> bool:
    """True if calendar blocks leave no contiguous slot of `duration` inside [ws, we)."""
    if not blocks:
        return False
    # Collect free sub-intervals
    free: list[tuple[int, int]] = []
    cur = ws
    for bs, be in sorted(blocks):
        bs_c = max(ws, bs)
        be_c = min(we, be)
        if bs_c >= we or be_c <= ws:
            continue
        if bs_c > cur:
            free.append((cur, bs_c))
        cur = max(cur, be_c)
    if cur < we:
        free.append((cur, we))
    return all((b - a) < duration for a, b in free) if free else True


# ── Approximate routing cost ─────────────────────────────────────────

# Fraction of the nearest-neighbor edge to add to each instance's base
# home-leg.  0.0 = pure home-leg, 1.0 = attribute the full NN edge to
# each endpoint (over-counts).  Biases day assignment toward plans
# where each instance can cluster with a neighbor.
#
# Empirical sweep (5-sample multi-worker runs on the benchmark scenarios):
# median drive is IDENTICAL between α=0.0 and α=0.1 across all six
# scenarios.  α=0.1 tightens multi-clinician variance but loosens HEAVY
# variance.  On synthetic inputs the two are a wash.  Default is 0.0 so
# production behavior is deterministic-equivalent; the env var is
# provided for tuning once real-data profiling arrives.
NN_MARGIN_FRAC = float(os.environ.get("SOLVER_ENVELOPE_NN_MARGIN", "0.0"))

# Per-instance penalty for deviating from a warm-start assignment.
# Makes continuity a cost term instead of just a hint — the solver will
# only move an instance off its prior slot when the travel saving
# exceeds this weight.  Roughly calibrated so a 50-min travel improvement
# is needed to justify moving an assignment.
CONTINUITY_WEIGHT = int(os.environ.get("SOLVER_ENVELOPE_CONTINUITY_WEIGHT", "50"))


def _compute_approx_costs(
    input: SolverInput,
    ctx: SolverContext,
) -> dict[tuple[str, int], int]:
    """Per-(instance, clinician) routing cost proxy.

    cost(i, c) = half_round_trip_from_home(c, patient(i))
               + NN_MARGIN_FRAC × nearest_edge_to_another_patient(i)

    The home-leg term pulls instances toward geographically-close
    clinicians.  The NN-margin term teaches the envelope that clustering
    with a nearby other-patient is cheap, which biases day assignment
    toward plans where each instance lands on a day with a neighbor —
    without the envelope having to model routing sequence explicitly.
    """
    costs: dict[tuple[str, int], int] = {}

    # Precompute NN travel per instance: min travel from this patient
    # to any other instance's patient (different patient id).
    nn_travel: dict[str, int] = {}
    instance_pids = {inst.id: str(inst.patient_id) for inst in input.instances}
    for inst in input.instances:
        pid_i = instance_pids[inst.id]
        best: int | None = None
        for other in input.instances:
            if other.id == inst.id:
                continue
            pid_j = instance_pids[other.id]
            if pid_i == pid_j:
                continue
            t = ctx.travel(pid_i, pid_j)
            if best is None or t < best:
                best = t
        nn_travel[inst.id] = int(best) if best is not None else 0

    # Compose base home-leg + NN margin
    for inst in input.instances:
        pid = str(inst.patient_id)
        margin = int(NN_MARGIN_FRAC * nn_travel[inst.id])
        for c_idx in range(len(input.clinicians)):
            out = ctx.travel(f"home_{c_idx}", pid)
            back = ctx.travel(pid, f"home_{c_idx}")
            costs[(inst.id, c_idx)] = (out + back) // 2 + margin
    return costs


# Backward-compat alias (used by tests or external callers)
_home_leg_cost = _compute_approx_costs


# ── The envelope solve ──────────────────────────────────────────────


def solve_envelope(
    input: SolverInput,
    ctx: SolverContext,
    cut_store: CutStore,
    time_budget: float,
    warm_start: Envelope | None = None,
    marginal_costs: dict[tuple[str, int, int], int] | None = None,
    precomputed_slots: dict[str, list[Slot]] | None = None,
    precomputed_approx_costs: dict[tuple[str, int], int] | None = None,
) -> Envelope:
    """CP-SAT master solve producing a (clinician, day, window) assignment per instance.

    When `marginal_costs` is provided, per-(instance, clinician, day)
    detour costs from the previous Benders round replace the home-leg
    approximation for matching slots.  This is how the envelope's cost
    estimate converges to reality across rounds: initial round uses
    approximation, subsequent rounds use actual routed detour.  Marginals
    missing from the dict fall back to the home-leg proxy.

    `precomputed_slots` and `precomputed_approx_costs` let the Benders
    outer loop compute slot enumeration and approximate costs once and
    pass them in across multiple rounds — these depend only on
    (input, ctx) and don't change between rounds.  Both are optional;
    if omitted they're computed per call (for single-shot use).
    """

    legal_slots = precomputed_slots if precomputed_slots is not None else _enumerate_slots(input, ctx)
    home_leg = precomputed_approx_costs if precomputed_approx_costs is not None else _compute_approx_costs(input, ctx)
    num_clinicians = len(input.clinicians)
    num_days = len(input.working_days)
    marginals = marginal_costs or {}

    model = cp_model.CpModel()

    # Variables: one bool per legal (instance, clinician, day, window) slot.
    # Keyed by (instance_id, slot_object).
    slot_var: dict[tuple[str, Slot], cp_model.IntVar] = {}
    scheduled: dict[str, cp_model.IntVar] = {}
    # Convenience lookup: all slot vars for a given (instance, clinician, day)
    vehicle_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = {}

    for inst in input.instances:
        slots = legal_slots[inst.id]
        if not slots:
            # Instance has no legal slot — it will be unscheduled
            sv = model.new_bool_var(f"sched_{inst.id}")
            scheduled[inst.id] = sv
            model.add(sv == 0)
            continue

        sv = model.new_bool_var(f"sched_{inst.id}")
        scheduled[inst.id] = sv

        slot_vars_for_inst: list[cp_model.IntVar] = []
        for s in slots:
            v = model.new_bool_var(
                f"slot_{inst.id}_{s.clinician_idx}_{s.day_idx}_{s.window_idx}"
            )
            slot_var[(inst.id, s)] = v
            slot_vars_for_inst.append(v)
            vehicle_slot_vars.setdefault(
                (inst.id, s.clinician_idx, s.day_idx), []
            ).append(v)

        # scheduled[i] == sum(slot vars)
        model.add(sum(slot_vars_for_inst) == sv)

    # ── One patient per day (across all clinicians) ───────────────
    for pid, insts in ctx.instances_by_patient.items():
        inst_ids = [inst.id for inst in insts]
        for d_idx in range(num_days):
            vars_on_day: list[cp_model.IntVar] = []
            for iid in inst_ids:
                for (iid2, s), v in slot_var.items():
                    if iid2 == iid and s.day_idx == d_idx:
                        vars_on_day.append(v)
            if vars_on_day:
                model.add(sum(vars_on_day) <= 1)

    # ── Day capacity per clinician ────────────────────────────────
    for c_idx in range(num_clinicians):
        for d_idx in range(num_days):
            vehicle = ctx.vehicle_by_key.get((c_idx, d_idx))
            if vehicle is None:
                continue
            vars_on_vehicle: list[cp_model.IntVar] = []
            for (iid, s), v in slot_var.items():
                if s.clinician_idx == c_idx and s.day_idx == d_idx:
                    vars_on_vehicle.append(v)
            if vars_on_vehicle and vehicle.capacity >= 0:
                model.add(sum(vars_on_vehicle) <= vehicle.capacity)

    # ── Min/max spacing between same-patient instances ────────────
    spacing_penalty_terms: list[cp_model.LinearExpr] = []
    spacing_penalty_cap = 0

    for pid, insts in ctx.instances_by_patient.items():
        patient = ctx.patients_by_id.get(pid)
        if not patient or len(insts) < 2:
            continue
        min_gap = patient.min_days_between_visits
        max_gap = patient.max_days_between_visits

        # Group vars by instance for this patient
        by_inst_day: dict[str, dict[int, list[cp_model.IntVar]]] = {}
        for inst in insts:
            by_inst_day[inst.id] = {}
            for (iid, s), v in slot_var.items():
                if iid == inst.id:
                    by_inst_day[inst.id].setdefault(s.day_idx, []).append(v)

        inst_list = sorted(by_inst_day.keys())

        # For every pair of instances, forbid pairs of days violating min spacing
        # and softly penalize pairs violating max spacing.
        for a in range(len(inst_list)):
            for b in range(a + 1, len(inst_list)):
                ia, ib = inst_list[a], inst_list[b]
                for da, va_list in by_inst_day[ia].items():
                    for db, vb_list in by_inst_day[ib].items():
                        gap = abs(da - db)
                        va_sum = sum(va_list)
                        vb_sum = sum(vb_list)
                        if gap == 0 or gap <= min_gap:
                            # Hard: both cannot be placed
                            model.add(va_sum + vb_sum <= 1)
                        elif max_gap < num_days and gap > max_gap:
                            # Soft: penalize proportional to gap excess.
                            # both == 1 iff both chosen on these specific days.
                            excess = gap - max_gap
                            both = model.new_bool_var(
                                f"maxgap_{ia}_{da}_{ib}_{db}"
                            )
                            model.add(both <= va_sum)
                            model.add(both <= vb_sum)
                            model.add(both >= va_sum + vb_sum - 1)
                            weight = PENALTY_SPACING_MAX * excess
                            spacing_penalty_terms.append(both * weight)
                            spacing_penalty_cap += weight

    # ── Per-patient visits cap by required_visits ─────────────────
    # An instance represents a *potential* visit slot; we never place more
    # than required_visits of them because there are exactly that many
    # instances pre-built.  No constraint needed beyond scheduled[i] <= 1.

    # ── Locked visits → patient-day exclusion was handled in _enumerate_slots ──

    # ── No-good cuts from prior Benders rounds ────────────────────
    for cut in cut_store.cuts:
        cut_vars: list[cp_model.IntVar] = []
        for iid, c_idx, d_idx in cut.forbidden:
            key = (iid, c_idx, d_idx)
            cut_vars.extend(vehicle_slot_vars.get(key, []))
        if cut_vars:
            model.add(sum(cut_vars) <= max(0, len(cut.forbidden) - 1))

    # ── Objective ────────────────────────────────────────────────
    # Three-tier lex via coefficient stacking:
    #   tier1: maximize placements  (visit_value per scheduled)
    #   tier2: minimize drops weighted by priority
    #   tier3: minimize approximate routing cost + load imbalance

    # Compute caps first so visit_value dominates.
    # Cost for each slot: use the per-(instance, clinician, day)
    # marginal from the previous Benders round if available; otherwise
    # fall back to the home-leg + NN-margin approximation.  This closes
    # the feedback loop — the envelope converges on real routed costs
    # across rounds instead of being stuck on the initial approximation.
    cost_cap = 0
    cost_terms: list[cp_model.LinearExpr] = []
    for (iid, s), v in slot_var.items():
        marginal = marginals.get((iid, s.clinician_idx, s.day_idx))
        if marginal is not None:
            c = marginal
        else:
            c = home_leg.get((iid, s.clinician_idx), 0)
        if c > 0:
            cost_terms.append(v * c)
            cost_cap += c

    # Priority drop penalty
    priority_terms: list[cp_model.LinearExpr] = []
    priority_cap = 0
    for inst in input.instances:
        patient = ctx.patients_by_id.get(inst.patient_id)
        pri = (patient.priority + 1) if patient else 1
        w = PRIORITY_WEIGHT * pri
        priority_terms.append(scheduled[inst.id].Not() * w)
        priority_cap += w

    # Load imbalance penalty: count per clinician, penalize max - min
    clinician_counts: list[cp_model.IntVar] = []
    for c_idx in range(num_clinicians):
        cnt_vars = [
            v for (iid, s), v in slot_var.items() if s.clinician_idx == c_idx
        ]
        cc = model.new_int_var(0, max(1, len(input.instances)), f"clin_count_{c_idx}")
        if cnt_vars:
            model.add(cc == sum(cnt_vars))
        else:
            model.add(cc == 0)
        clinician_counts.append(cc)

    imbalance_term: cp_model.LinearExpr | int = 0
    imbalance_cap = 0
    if num_clinicians > 1 and clinician_counts:
        n_max = model.new_int_var(0, len(input.instances), "n_max")
        n_min = model.new_int_var(0, len(input.instances), "n_min")
        model.add_max_equality(n_max, clinician_counts)
        model.add_min_equality(n_min, clinician_counts)
        spread = model.new_int_var(0, len(input.instances), "spread")
        model.add(spread == n_max - n_min)
        imbalance_term = spread * PENALTY_LOAD_IMBALANCE
        imbalance_cap = len(input.instances) * PENALTY_LOAD_IMBALANCE

    # Continuity penalty — when warm-starting, discourage moving instances
    # off their prior (clinician, day) unless there's a meaningful saving.
    continuity_penalty_terms: list[cp_model.LinearExpr] = []
    continuity_penalty_cap = 0
    if warm_start is not None and CONTINUITY_WEIGHT > 0:
        for iid, prior_slot in warm_start.assignments.items():
            prior_c, prior_d = prior_slot.clinician_idx, prior_slot.day_idx
            for (iid2, s), v in slot_var.items():
                if iid2 != iid:
                    continue
                if (s.clinician_idx, s.day_idx) != (prior_c, prior_d):
                    continuity_penalty_terms.append(v * CONTINUITY_WEIGHT)
                    continuity_penalty_cap += CONTINUITY_WEIGHT

    # Placement value must strictly exceed all additive cost terms
    visit_value = (
        cost_cap + priority_cap + imbalance_cap
        + spacing_penalty_cap + continuity_penalty_cap + 1
    )

    objective_terms: list[cp_model.LinearExpr] = []
    for t in cost_terms:
        objective_terms.append(t)
    for t in priority_terms:
        objective_terms.append(t)
    for t in spacing_penalty_terms:
        objective_terms.append(t)
    for t in continuity_penalty_terms:
        objective_terms.append(t)
    if imbalance_cap > 0:
        objective_terms.append(imbalance_term)

    # -visit_value per scheduled → maximize placements
    for inst in input.instances:
        objective_terms.append(scheduled[inst.id] * (-visit_value))

    model.minimize(sum(objective_terms))

    # ── Warm start ───────────────────────────────────────────────
    # warm_start.assignments gives a desired (clinician, day) per instance.
    # We hint any legal slot var matching that (iid, c, d) — the exact
    # window_idx doesn't have to match.
    if warm_start is not None:
        for iid, prior_slot in warm_start.assignments.items():
            key = (iid, prior_slot.clinician_idx, prior_slot.day_idx)
            candidates = vehicle_slot_vars.get(key, [])
            if not candidates:
                continue
            # Hint the first matching slot var; scheduled[iid] follows.
            model.add_hint(candidates[0], 1)
            if iid in scheduled:
                model.add_hint(scheduled[iid], 1)

    # ── Solve ────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.5, time_budget)
    solver.parameters.num_workers = NUM_WORKERS
    # Deterministic tie-breaking: without a fixed seed, CP-SAT picks
    # different optima on equivalent-cost solutions across runs, which
    # makes A/B testing and re-solve stability impossible.
    solver.parameters.random_seed = 42

    status = solver.solve(model)
    status_name = solver.status_name(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Envelope(status=status_name)

    # Extract assignment
    env = Envelope(status=status_name)
    for (iid, s), v in slot_var.items():
        if solver.value(v):
            env.assignments[iid] = s
    for inst in input.instances:
        if inst.id not in env.assignments:
            env.unscheduled.append(inst.id)

    # Approximate cost — placement-aware (excludes the visit_value subsidy)
    env.approximate_cost = 0
    for iid, s in env.assignments.items():
        env.approximate_cost += home_leg.get((iid, s.clinician_idx), 0)

    return env


# ────────────────────────────────────────────────────────────────────
# Partial repair: build CP-SAT vars only for destroyed instances,
# hard-fix the rest.  Used by LNS to avoid re-solving the whole
# envelope on every iteration — the repair model is ~k/n the size.
# ────────────────────────────────────────────────────────────────────


def solve_envelope_partial(
    destroyed_ids: set[str],
    locked_assignments: dict[str, Slot],
    input: SolverInput,
    ctx: SolverContext,
    cut_store: CutStore,
    precomputed_slots: dict[str, list[Slot]],
    precomputed_approx_costs: dict[tuple[str, int], int],
    marginal_costs: dict[tuple[str, int, int], int] | None = None,
    prior_assignments: dict[str, Slot] | None = None,
    time_budget: float = 1.0,
) -> Envelope | None:
    """Solve only for `destroyed_ids`, hard-fixing everyone else.

    The locked instances don't appear as CP-SAT variables at all.
    Their constraints are pre-computed and baked in:
      - Per-vehicle day capacity is reduced by the locked count
      - Patient-day exclusions include locked placements
      - Spacing constraints are enforced cross-partition (destroyed
        ⊕ locked)
      - No-good cuts referencing only locked triples are auto-
        satisfied (subset would already be forbidden by locks)
      - Continuity is enforced by construction (locks can't move)

    `prior_assignments`, when provided, supplies (clinician, day) hints
    for the destroyed instances — typically the current LNS incumbent's
    placements.  CP-SAT uses these as starting guesses via `add_hint`,
    which often lets the search skip cold-start work when the improved
    solution is structurally close to the current one.

    Returns a full Envelope (locked ⊕ newly-placed destroyed) or
    None if CP-SAT is infeasible.
    """
    if not destroyed_ids:
        # No-op: return the locked state as-is
        env = Envelope(status="OPTIMAL")
        env.assignments = dict(locked_assignments)
        return env

    num_clinicians = len(input.clinicians)
    num_days = len(input.working_days)

    model = cp_model.CpModel()

    # ── Variables: slot vars only for destroyed instances ────────
    slot_var: dict[tuple[str, Slot], cp_model.IntVar] = {}
    scheduled: dict[str, cp_model.IntVar] = {}
    vehicle_slot_vars: dict[tuple[str, int, int], list[cp_model.IntVar]] = {}

    for iid in destroyed_ids:
        legal = precomputed_slots.get(iid, [])
        if not legal:
            sv = model.new_bool_var(f"psched_{iid}")
            scheduled[iid] = sv
            model.add(sv == 0)
            continue
        sv = model.new_bool_var(f"psched_{iid}")
        scheduled[iid] = sv
        slot_vars_for_inst: list[cp_model.IntVar] = []
        for s in legal:
            v = model.new_bool_var(
                f"pslot_{iid}_{s.clinician_idx}_{s.day_idx}_{s.window_idx}"
            )
            slot_var[(iid, s)] = v
            slot_vars_for_inst.append(v)
            vehicle_slot_vars.setdefault(
                (iid, s.clinician_idx, s.day_idx), []
            ).append(v)
        model.add(sum(slot_vars_for_inst) == sv)

    # ── Precompute locked bookkeeping ────────────────────────────
    # Patient → set of day indices already locked
    locked_patient_days: dict[int, set[int]] = {}
    # (clinician_idx, day_idx) → count of locked instances
    locked_count_by_vehicle: dict[tuple[int, int], int] = {}
    for iid, slot in locked_assignments.items():
        inst = ctx.instances_by_id.get(iid)
        if inst is None:
            continue
        locked_patient_days.setdefault(inst.patient_id, set()).add(slot.day_idx)
        key = (slot.clinician_idx, slot.day_idx)
        locked_count_by_vehicle[key] = locked_count_by_vehicle.get(key, 0) + 1

    destroyed_instances_by_patient: dict[int, list[str]] = {}
    for iid in destroyed_ids:
        inst = ctx.instances_by_id.get(iid)
        if inst is None:
            continue
        destroyed_instances_by_patient.setdefault(inst.patient_id, []).append(iid)

    # ── Constraint 1: forbid destroyed instance on a day where the
    #    same patient is already locked ──────────────────────────
    for iid in destroyed_ids:
        inst = ctx.instances_by_id.get(iid)
        if inst is None:
            continue
        forbidden_days = locked_patient_days.get(inst.patient_id, set())
        if not forbidden_days:
            continue
        for (iid2, s), v in slot_var.items():
            if iid2 != iid:
                continue
            if s.day_idx in forbidden_days:
                model.add(v == 0)

    # ── Constraint 2: at most one destroyed instance of the same
    #    patient per day (same semantic as full envelope) ────────
    for pid, iids in destroyed_instances_by_patient.items():
        if len(iids) < 2:
            continue
        for d_idx in range(num_days):
            vars_on_day: list[cp_model.IntVar] = []
            for iid in iids:
                for (iid2, s), v in slot_var.items():
                    if iid2 == iid and s.day_idx == d_idx:
                        vars_on_day.append(v)
            if vars_on_day:
                model.add(sum(vars_on_day) <= 1)

    # ── Constraint 3: per-vehicle day capacity minus locked ──────
    for c_idx in range(num_clinicians):
        for d_idx in range(num_days):
            vehicle = ctx.vehicle_by_key.get((c_idx, d_idx))
            if vehicle is None:
                continue
            locked_here = locked_count_by_vehicle.get((c_idx, d_idx), 0)
            available = max(0, vehicle.capacity - locked_here)
            vars_here: list[cp_model.IntVar] = []
            for (iid, s), v in slot_var.items():
                if s.clinician_idx == c_idx and s.day_idx == d_idx:
                    vars_here.append(v)
            if not vars_here:
                continue
            if available <= 0:
                for v in vars_here:
                    model.add(v == 0)
            else:
                model.add(sum(vars_here) <= available)

    # ── Constraint 4: spacing between destroyed instances + locks ─
    # Two spacing rules: min_gap (hard), max_gap (hard for simplicity
    # in partial repair — we don't model soft spacing here because
    # the incumbent already satisfies it and k is small).
    for pid, iids in destroyed_instances_by_patient.items():
        patient = ctx.patients_by_id.get(pid)
        if not patient:
            continue
        min_gap = patient.min_days_between_visits

        # destroyed ↔ locked
        locked_days_for_pid = locked_patient_days.get(pid, set())
        if locked_days_for_pid and min_gap > 0:
            for iid in iids:
                for (iid2, s), v in slot_var.items():
                    if iid2 != iid:
                        continue
                    for locked_d in locked_days_for_pid:
                        if abs(s.day_idx - locked_d) <= min_gap:
                            model.add(v == 0)

        # destroyed ↔ destroyed
        if len(iids) >= 2 and min_gap > 0:
            for i in range(len(iids)):
                for j in range(i + 1, len(iids)):
                    ia, ib = iids[i], iids[j]
                    for (iid_a, sa), va in slot_var.items():
                        if iid_a != ia:
                            continue
                        for (iid_b, sb), vb in slot_var.items():
                            if iid_b != ib:
                                continue
                            gap = abs(sa.day_idx - sb.day_idx)
                            if gap == 0 or gap <= min_gap:
                                model.add(va + vb <= 1)

    # ── Constraint 5: no-good cuts referencing destroyed triples ──
    for cut in cut_store.cuts:
        cut_vars: list[cp_model.IntVar] = []
        locked_hits = 0
        for iid, c_idx, d_idx in cut.forbidden:
            if iid in destroyed_ids:
                cut_vars.extend(vehicle_slot_vars.get((iid, c_idx, d_idx), []))
            elif iid in locked_assignments:
                locked_slot = locked_assignments[iid]
                if (locked_slot.clinician_idx, locked_slot.day_idx) == (c_idx, d_idx):
                    locked_hits += 1
        # Cut: sum of referenced triples ≤ len - 1
        # Locked contributes `locked_hits` to the sum.
        remaining_allowance = max(0, len(cut.forbidden) - 1 - locked_hits)
        if cut_vars:
            model.add(sum(cut_vars) <= remaining_allowance)
        elif locked_hits >= len(cut.forbidden):
            # All forbidden triples are locked and active — cut is
            # already violated structurally.  Nothing LNS can do.
            # Return None to signal the repair can't proceed.
            return None

    # ── Objective: minimize placement cost on destroyed, subject
    #    to "place all" dominance (visit_value) ──────────────────
    marginals = marginal_costs or {}
    cost_terms: list[cp_model.LinearExpr] = []
    cost_cap = 0
    for (iid, s), v in slot_var.items():
        c = marginals.get((iid, s.clinician_idx, s.day_idx))
        if c is None:
            c = precomputed_approx_costs.get((iid, s.clinician_idx), 0)
        if c > 0:
            cost_terms.append(v * c)
            cost_cap += c

    visit_value = cost_cap + 1
    objective: list[cp_model.LinearExpr] = list(cost_terms)
    for iid in destroyed_ids:
        if iid in scheduled:
            objective.append(scheduled[iid] * (-visit_value))

    if objective:
        model.minimize(sum(objective))

    # ── Hints from prior_assignments ─────────────────────────────
    # Seed CP-SAT with the current incumbent's placements for the
    # destroyed instances.  Hint any slot var matching (iid, c, d)
    # from the prior assignment — window_idx doesn't have to match.
    # Hints are soft: CP-SAT can still find a strictly better
    # solution, it just starts closer to a known-good state.
    if prior_assignments:
        for iid in destroyed_ids:
            prior = prior_assignments.get(iid)
            if prior is None:
                continue
            candidates = vehicle_slot_vars.get(
                (iid, prior.clinician_idx, prior.day_idx), []
            )
            if not candidates:
                continue
            model.add_hint(candidates[0], 1)
            if iid in scheduled:
                model.add_hint(scheduled[iid], 1)

    # ── Solve ────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.2, time_budget)
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = 42

    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    # Build full envelope: locked ⊕ newly-placed destroyed
    full_env = Envelope(status=solver.status_name(status))
    for iid, slot in locked_assignments.items():
        full_env.assignments[iid] = slot
    for (iid, s), v in slot_var.items():
        if solver.value(v):
            full_env.assignments[iid] = s

    return full_env
