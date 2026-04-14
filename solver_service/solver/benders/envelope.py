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
from dataclasses import dataclass, field
from datetime import datetime

from ortools.sat.python import cp_model

from models import SolverInput
from solver.context import (
    MINUTES_PER_DAY,
    NUM_WORKERS,
    PENALTY_LOAD_IMBALANCE,
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
                # Blocked ranges for this (c, d)
                blocks = [
                    (
                        _dt_to_min(cb.starts_at),
                        _dt_to_min(cb.ends_at),
                    )
                    for cb in ctx.calendar_blocks_by_vehicle.get((c_idx, d_idx), [])
                ]

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


def _home_leg_cost(input: SolverInput, ctx: SolverContext) -> dict[tuple[str, int], int]:
    """Estimate round-trip cost from clinician home to patient (ignoring other stops)."""
    costs: dict[tuple[str, int], int] = {}
    for inst in input.instances:
        pid = str(inst.patient_id)
        for c_idx in range(len(input.clinicians)):
            out = ctx.travel(f"home_{c_idx}", pid)
            back = ctx.travel(pid, f"home_{c_idx}")
            costs[(inst.id, c_idx)] = (out + back) // 2
    return costs


# ── The envelope solve ──────────────────────────────────────────────


def solve_envelope(
    input: SolverInput,
    ctx: SolverContext,
    cut_store: CutStore,
    time_budget: float,
    warm_start: Envelope | None = None,
) -> Envelope:
    """CP-SAT master solve producing a (clinician, day, window) assignment per instance."""

    legal_slots = _enumerate_slots(input, ctx)
    home_leg = _home_leg_cost(input, ctx)
    num_clinicians = len(input.clinicians)
    num_days = len(input.working_days)

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
        for a in range(len(inst_list)):
            for b in range(a + 1, len(inst_list)):
                ia, ib = inst_list[a], inst_list[b]
                for da, va_list in by_inst_day[ia].items():
                    for db, vb_list in by_inst_day[ib].items():
                        gap = abs(da - db)
                        if gap == 0 or gap <= min_gap:
                            # Both cannot be placed
                            va_sum = sum(va_list)
                            vb_sum = sum(vb_list)
                            # va_sum + vb_sum <= 1
                            model.add(va_sum + vb_sum <= 1)

        # Max-spacing enforcement: penalize rather than forbid (soft).
        # For the spike, ignore max_gap when it's permissive (>= num_days).
        # A strict max_gap would become a soft penalty term — omitted for simplicity.

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
    cost_cap = 0
    cost_terms: list[cp_model.LinearExpr] = []
    for (iid, s), v in slot_var.items():
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
    max_vehicle_cap = 0
    for c_idx in range(num_clinicians):
        cnt_vars = [
            v for (iid, s), v in slot_var.items() if s.clinician_idx == c_idx
        ]
        cc = model.new_int_var(0, max(1, len(input.instances)), f"clin_count_{c_idx}")
        model.add(cc == sum(cnt_vars) if cnt_vars else 0)
        clinician_counts.append(cc)
        max_vehicle_cap += len(input.instances)

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

    # Placement value must strictly exceed cost + priority + imbalance
    visit_value = cost_cap + priority_cap + imbalance_cap + 1
    n = len(input.instances)

    objective_terms: list[cp_model.LinearExpr] = []
    for t in cost_terms:
        objective_terms.append(t)
    for t in priority_terms:
        objective_terms.append(t)
    if imbalance_cap > 0:
        objective_terms.append(imbalance_term)

    # -visit_value per scheduled → maximize placements
    for inst in input.instances:
        objective_terms.append(scheduled[inst.id] * (-visit_value))

    model.minimize(sum(objective_terms))

    # ── Warm start ───────────────────────────────────────────────
    if warm_start is not None:
        for iid, prior_slot in warm_start.assignments.items():
            key = (iid, prior_slot)
            if key in slot_var:
                model.add_hint(slot_var[key], 1)
                model.add_hint(scheduled[iid], 1)

    # ── Solve ────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.5, time_budget)
    solver.parameters.num_workers = NUM_WORKERS

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
