"""Fitness penalty calculations for the CP-SAT solver pipeline."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from models import SolverInput, PlannedVisit

from solvers.cpsat_context import (
    DENSITY_BASE_WEIGHT,
    PENALTY_DAY_OFFSET,
    PENALTY_SPACING_MAX,
    PENALTY_SPACING_MIN,
    target_day_offsets,
)


def compute_spacing_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
) -> int:
    """Compute spacing violation penalty from routed visits."""
    # Pre-compute date ordinals to avoid repeated ISO parsing
    date_ordinals: dict[str, int] = {}
    for v in planned:
        if v.date not in date_ordinals:
            date_ordinals[v.date] = datetime.fromisoformat(v.date).toordinal()

    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in planned:
        patient_dates[v.patient_id].append(v.date)

    penalty = 0
    for pid, dates in patient_dates.items():
        unique_days = sorted(set(dates))
        if len(unique_days) < 2:
            continue
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        for i in range(len(unique_days) - 1):
            gap = date_ordinals[unique_days[i + 1]] - date_ordinals[unique_days[i]]
            if gap <= patient.min_days_between_visits:
                penalty += PENALTY_SPACING_MIN * (patient.min_days_between_visits - gap + 1)
            if gap > patient.max_days_between_visits:
                penalty += PENALTY_SPACING_MAX * (gap - patient.max_days_between_visits)
    return penalty


def compute_density_fitness_penalty(
    assignments: dict[int, list[int]],
    ctx: dict,
    input: SolverInput,
) -> int:
    """Mirror CP-SAT schedule_density penalties using realized assignment counts."""
    num_days = len(input.working_days)
    if num_days <= 1:
        return 0
    density = input.clinician.schedule_density
    locked_by_day = ctx["locked_count_by_day"]
    counts = [
        len(assignments.get(d, [])) + locked_by_day.get(d, 0) for d in range(num_days)
    ]
    if density < 0.5:
        w = int(DENSITY_BASE_WEIGHT * 2 * (0.5 - density))
        return w * (max(counts) - min(counts)) if w > 0 else 0
    w = int(DENSITY_BASE_WEIGHT * 2 * (density - 0.5))
    if w <= 0:
        return 0
    active = sum(1 for c in counts if c > 0)
    return w * active


def compute_day_offset_fitness_penalty(
    planned: list[PlannedVisit],
    patients_by_id: dict,
    input: SolverInput,
    ctx: dict,
) -> int:
    """Approximate CP-SAT target day-offset penalty from placed visits."""
    if not planned:
        return 0
    date_to_idx = ctx["date_to_idx"]
    inst_day: dict[str, int] = {}
    for v in planned:
        di = date_to_idx.get(v.date)
        if di is not None:
            inst_day[v.instance_id] = di

    instances_by_patient = ctx["instances_by_patient"]
    inst_idx_map = ctx["inst_idx_map"]
    num_days = len(input.working_days)
    clinician = input.clinician
    penalty = 0

    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) <= 1:
            continue
        targets = target_day_offsets(
            len(insts), num_days, patient.min_days_between_visits, clinician.schedule_density,
        )

        inst_indices = sorted(inst_idx_map[inst.id] for inst in insts)
        for k, gi in enumerate(inst_indices):
            inst = input.instances[gi]
            if inst.id not in inst_day:
                continue
            actual_d = inst_day[inst.id]
            target = targets[k] if k < len(targets) else targets[-1]
            penalty += PENALTY_DAY_OFFSET * abs(actual_d - target)

    return penalty
