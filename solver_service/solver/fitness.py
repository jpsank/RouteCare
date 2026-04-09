"""3-tier fitness computation for the VRPTW solver.

Tier 1 (Hard): calendar block overlap, duplicate patient-day → INFINITY
Tier 2 (Adaptive λ): transit excess, overtime, break violations, lunch window → λ-weighted
Tier 3 (Fixed w): availability soft overrides, spacing, density, day-offsets, priority

Adaptive λ mechanism: after each LNS generation, if the fraction of feasible
solutions exceeds the target band, decrease λ (allow more exploration); if below,
increase λ (push toward feasibility).  This keeps the search near the feasibility
boundary where the best feasible solutions live.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from models import PlannedVisit, SolverInput
from solver.context import (
    DENSITY_BASE_WEIGHT,
    PENALTY_DAY_OFFSET,
    PENALTY_SOFT_OVERRIDE,
    PENALTY_SPACING_MAX,
    PENALTY_SPACING_MIN,
    SolverContext,
    target_day_offsets,
)
from solver.timing import DayViolations, TimedRoute


# ── Adaptive Lambda State ────────────────────────────────────────────

# Target: ~20-30% of evaluated solutions should be Tier-2-infeasible
LAMBDA_TARGET_LOW = 0.20
LAMBDA_TARGET_HIGH = 0.30
LAMBDA_ADJUST_DELTA = 0.10  # adjustment factor per generation

# Initial λ values — calibrated so 1 unit of violation ≈ 1 extra visit's travel cost
LAMBDA_INITIAL = {
    "transit": 5.0,  # per minute of transit excess
    "overtime": 5.0,  # per minute of overtime
    "break": 100.0,  # per missed break (categorical)
    "lunch_window": 3.0,  # per minute outside lunch window
}


@dataclass
class AdaptiveLambda:
    """Tracks and adjusts Tier 2 penalty weights."""

    transit: float = LAMBDA_INITIAL["transit"]
    overtime: float = LAMBDA_INITIAL["overtime"]
    break_: float = LAMBDA_INITIAL["break"]
    lunch_window: float = LAMBDA_INITIAL["lunch_window"]

    # Rolling window of recent feasibility observations
    _recent_feasible: list[bool] = field(default_factory=list)
    _window_size: int = 20

    def record(self, is_feasible: bool):
        """Record whether a solution was Tier-2-feasible."""
        self._recent_feasible.append(is_feasible)
        if len(self._recent_feasible) > self._window_size:
            self._recent_feasible.pop(0)

    def adapt(self):
        """Adjust λ values based on recent feasibility fraction."""
        if len(self._recent_feasible) < 5:
            return  # not enough data yet

        infeasible_frac = 1.0 - (
            sum(self._recent_feasible) / len(self._recent_feasible)
        )

        if infeasible_frac > LAMBDA_TARGET_HIGH:
            # Too many infeasible → increase λ
            factor = 1.0 + LAMBDA_ADJUST_DELTA
            self.transit *= factor
            self.overtime *= factor
            self.break_ *= factor
            self.lunch_window *= factor
        elif infeasible_frac < LAMBDA_TARGET_LOW:
            # Too few infeasible → decrease λ (allow more exploration)
            factor = 1.0 - LAMBDA_ADJUST_DELTA
            self.transit = max(0.1, self.transit * factor)
            self.overtime = max(0.1, self.overtime * factor)
            self.break_ = max(1.0, self.break_ * factor)
            self.lunch_window = max(0.1, self.lunch_window * factor)

    def to_dict(self) -> dict:
        return {
            "lambda_transit": round(self.transit, 2),
            "lambda_overtime": round(self.overtime, 2),
            "lambda_break": round(self.break_, 2),
            "lambda_lunch_window": round(self.lunch_window, 2),
            "infeasible_frac": round(
                1.0 - (sum(self._recent_feasible) / len(self._recent_feasible))
                if self._recent_feasible
                else 0.0,
                3,
            ),
        }


# ── Tier 1: Hard Constraints ────────────────────────────────────────


def has_tier1_violation(
    planned: list[PlannedVisit],
    ctx: SolverContext,
    input: SolverInput,
) -> bool:
    """Check for hard constraint violations that make solution invalid."""
    # One patient per day
    for date in input.working_days:
        pids = [v.patient_id for v in planned if v.date == date]
        if len(pids) != len(set(pids)):
            return True

    # Calendar block overlap
    for v in planned:
        charting = input.clinicians[v.clinician_idx].charting_buffer_minutes
        v_start = _minute_from_iso(v.starts_at)
        v_end = _minute_from_iso(v.ends_at) + charting
        for cb in ctx.calendar_blocks_by_date.get(v.date, []):
            b_start = _minute_from_iso(cb.starts_at)
            b_end = _minute_from_iso(cb.ends_at)
            if v_start < b_end and v_end > b_start:
                return True

    return False


# ── Tier 2: Adaptive-λ Violations ───────────────────────────────────


def aggregate_tier2_violations(
    timed_routes: dict[int, TimedRoute],
    ctx: SolverContext,
) -> DayViolations:
    """Sum all Tier 2 violations across all vehicles."""
    total = DayViolations()
    for v_idx in range(len(ctx.vehicles)):
        tr = timed_routes.get(v_idx)
        if tr and tr.violations:
            total.transit_excess_minutes += tr.violations.transit_excess_minutes
            total.overtime_minutes += tr.violations.overtime_minutes
            total.break_violations += tr.violations.break_violations
            total.lunch_window_violation += tr.violations.lunch_window_violation
    return total


def tier2_penalty(violations: DayViolations, lambdas: AdaptiveLambda) -> float:
    """Compute Tier 2 penalty using adaptive λ weights."""
    return (
        lambdas.transit * violations.transit_excess_minutes
        + lambdas.overtime * violations.overtime_minutes
        + lambdas.break_ * violations.break_violations
        + lambdas.lunch_window * violations.lunch_window_violation
    )


def is_tier2_feasible(violations: DayViolations) -> bool:
    """A solution is Tier-2-feasible when all violation metrics are zero."""
    return (
        violations.transit_excess_minutes == 0
        and violations.overtime_minutes == 0
        and violations.break_violations == 0
        and violations.lunch_window_violation == 0
    )


# ── Tier 3: Fixed-Weight Preferences ────────────────────────────────


def compute_spacing_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
) -> int:
    """Penalty for visits to the same patient that violate min/max spacing."""
    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in planned:
        patient_dates[v.patient_id].append(v.date)

    penalty = 0
    for pid, dates in patient_dates.items():
        patient = patients_by_id.get(pid)
        if not patient or len(dates) < 2:
            continue
        ordinals = sorted(datetime.fromisoformat(d).toordinal() for d in set(dates))
        for i in range(len(ordinals) - 1):
            gap = ordinals[i + 1] - ordinals[i]
            if gap <= patient.min_days_between_visits:
                penalty += PENALTY_SPACING_MIN
            if gap > patient.max_days_between_visits:
                penalty += PENALTY_SPACING_MAX * (gap - patient.max_days_between_visits)
    return penalty


def compute_density_fitness_penalty(
    assignments: dict[int, list[str]],
    ctx: SolverContext,
    input: SolverInput,
) -> int:
    """Penalty reflecting schedule density preference."""
    density = input.clinicians[0].schedule_density
    num_days = len(ctx.working_days)
    if num_days <= 1:
        return 0

    counts = [len(assignments.get(d, [])) for d in range(num_days)]

    if density < 0.5:
        spread = max(counts) - min(counts) if counts else 0
        w = int(DENSITY_BASE_WEIGHT * 2 * (0.5 - density))
        return spread * w
    elif density > 0.5:
        active = sum(1 for c in counts if c > 0)
        w = int(DENSITY_BASE_WEIGHT * 2 * (density - 0.5))
        return active * w
    return 0


def compute_day_offset_fitness_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
    ctx: SolverContext,
) -> int:
    """Penalty for visits not landing on their ideal target days."""
    num_days = len(ctx.working_days)
    date_to_idx = ctx.date_to_idx

    patient_visits: dict[int, list[int]] = defaultdict(list)
    for v in planned:
        d = date_to_idx.get(v.date)
        if d is not None:
            patient_visits[v.patient_id].append(d)

    penalty = 0
    for pid, day_indices in patient_visits.items():
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        insts = ctx.instances_by_patient.get(pid, [])
        targets = target_day_offsets(
            len(insts),
            num_days,
            patient.min_days_between_visits,
            input.clinicians[0].schedule_density,
        )
        sorted_days = sorted(day_indices)
        for k, actual_day in enumerate(sorted_days):
            target = targets[k] if k < len(targets) else targets[-1]
            penalty += abs(actual_day - target) * PENALTY_DAY_OFFSET
    return penalty


# ── Unified Fitness ──────────────────────────────────────────────────


def compute_total_fitness(
    planned: list[PlannedVisit],
    drive_cost: int,
    input: SolverInput,
    ctx: SolverContext,
    timed_routes: dict[int, TimedRoute] | None = None,
    lambdas: AdaptiveLambda | None = None,
) -> tuple[int, float, dict]:
    """Compute unified 3-tier fitness.

    Returns (placed_count, cost, penalty_breakdown).

    When timed_routes and lambdas are provided, includes Tier 2 adaptive
    penalties.  Otherwise falls back to Tier 3 only (backward compatible).
    """
    patients_by_id = ctx.patients_by_id
    placed_count = len(planned)

    # Tier 1: hard violations → INFINITY
    if has_tier1_violation(planned, ctx, input):
        return placed_count, float("inf"), {"tier1_violation": True}

    # Build day assignments for density calculation
    date_to_idx = ctx.date_to_idx
    assignments: dict[int, list[str]] = defaultdict(list)
    for v in planned:
        d = date_to_idx.get(v.date)
        if d is not None:
            assignments[d].append(v.instance_id)

    # Tier 2: adaptive-λ penalties
    t2_penalty = 0.0
    t2_violations = DayViolations()
    t2_feasible = True
    if timed_routes is not None and lambdas is not None:
        t2_violations = aggregate_tier2_violations(timed_routes, ctx)
        t2_penalty = tier2_penalty(t2_violations, lambdas)
        t2_feasible = is_tier2_feasible(t2_violations)

    # Tier 3: fixed-weight preferences
    soft_penalty = sum(
        PENALTY_SOFT_OVERRIDE for v in planned if v.soft_constraint_override
    )
    spacing_penalty = compute_spacing_penalty(planned, patients_by_id, input)
    density_penalty = compute_density_fitness_penalty(dict(assignments), ctx, input)
    offset_penalty = compute_day_offset_fitness_penalty(
        planned, patients_by_id, input, ctx
    )

    t3_penalty = soft_penalty + spacing_penalty + density_penalty + offset_penalty

    cost = float(drive_cost) + t2_penalty + t3_penalty

    breakdown = {
        "drive": drive_cost,
        "tier2_penalty": round(t2_penalty, 2),
        "tier2_feasible": t2_feasible,
        "transit_excess": t2_violations.transit_excess_minutes,
        "overtime": t2_violations.overtime_minutes,
        "break_violations": t2_violations.break_violations,
        "lunch_window_violation": t2_violations.lunch_window_violation,
        "soft_penalty": soft_penalty,
        "spacing_penalty": spacing_penalty,
        "density_penalty": density_penalty,
        "offset_penalty": offset_penalty,
        "total_cost": round(cost, 2),
    }

    return placed_count, cost, breakdown


# ── Helpers ──────────────────────────────────────────────────────────


def _minute_from_iso(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute
