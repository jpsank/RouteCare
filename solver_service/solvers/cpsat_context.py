"""Shared context, constants, and helpers for the CP-SAT solver pipeline."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from models import SolverInput, SolverOutput

# ── Constants ────────────────────────────────────────────────────────

MAX_VISITS_PER_DAY = 5

# Assignment penalties
PENALTY_SPACING_MIN = 200
PENALTY_SPACING_MAX = 300
PENALTY_AVAILABILITY = 150
PENALTY_DAY_OFFSET = 10
PENALTY_SOFT_OVERRIDE = 80
DENSITY_BASE_WEIGHT = 60
# When the solver must drop visits (capacity/hard constraints), prefer keeping
# high-priority patients.  This weight acts as a tiebreaker *within* the same
# visit-count tier — the categorical visit_value gap (computed at model-build
# time) guarantees N visits always beats N-1.
PRIORITY_WEIGHT = 500

# Routing constants
SLOT_STEP = 15
TRANSIT_BUFFER = 5

# Pipeline (tests may monkeypatch MAX_ITERATIONS)
MAX_ITERATIONS = max(1, int(os.environ.get("CPSAT_MAX_ITERATIONS", "5")))
NUM_WORKERS = int(os.environ.get("CPSAT_NUM_WORKERS", min(os.cpu_count() or 4, 8)))
HGS_MAX_SECONDS = float(os.environ.get("CPSAT_HGS_MAX_SECONDS", "0.5"))


# ── Helpers ──────────────────────────────────────────────────────────

def minutes_to_datetime(date_str: str, minutes: int) -> str:
    """Convert a date string + minutes-since-midnight to ISO datetime."""
    dt = datetime.fromisoformat(date_str)
    dt = dt.replace(hour=minutes // 60, minute=minutes % 60, second=0)
    return dt.isoformat()


def datetime_to_minute(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str)
    return dt.hour * 60 + dt.minute


def round_up(minute: int, step: int) -> int:
    remainder = minute % step
    return minute if remainder == 0 else minute + (step - remainder)


def day_bounds(clinician, date: str) -> tuple[int, int]:
    """Return (start_minute, end_minute) for a date, respecting per_day_hours overrides."""
    dt = datetime.fromisoformat(date)
    wday = (dt.weekday() + 1) % 7
    pdh = clinician.per_day_hours.get(str(wday), {})
    return (
        pdh.get("start", clinician.workday_start_minute),
        pdh.get("end", clinician.workday_end_minute),
    )


def day_index(date_str: str, working_days: list[str]) -> int | None:
    try:
        return working_days.index(date_str)
    except ValueError:
        return None


def target_day_offsets(n_visits: int, num_days: int, min_gap: int, density: float) -> list[int]:
    """Compute ideal target day indices for evenly spreading n_visits across num_days."""
    if n_visits <= 1:
        return [0]
    max_step = (num_days - 1) / (n_visits - 1)
    min_step = max(float(min_gap), 1.0)
    step = max(max_step - density * (max_step - min_step), min_step)
    return [min(round(k * step), num_days - 1) for k in range(n_visits)]


def build_lunch_placements(input: SolverInput) -> dict[str, dict]:
    """Default lunch placement for all working days, respecting per_day_hours."""
    c = input.clinician
    if c.lunch_duration_minutes <= 0:
        return {date: None for date in input.working_days}
    half_window = c.lunch_window_minutes // 2
    placements = {}
    for date in input.working_days:
        dt = datetime.fromisoformat(date)
        wday = (dt.weekday() + 1) % 7
        pdh = c.per_day_hours.get(str(wday), {})
        day_start = pdh.get("start", c.workday_start_minute)
        earliest = max(c.lunch_start_minute - half_window, day_start)
        placements[date] = {
            "start_minute": earliest,
            "end_minute": earliest + c.lunch_duration_minutes,
        }
    return placements


def compute_return_home(routes_by_day: dict[str, list[int]], travel_matrix: dict) -> dict[str, int]:
    """Compute last patient → home travel time per day."""
    result = {}
    for date, patient_ids in routes_by_day.items():
        if not patient_ids:
            continue
        last_id = str(patient_ids[-1])
        result[date] = travel_matrix.get(last_id, {}).get("home", 0)
    return result


def empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "cpsat"},
    )


# ── Typed Context ───────────────────────────────────────────────────


@dataclass
class SolverContext:
    """Shared pre-computed data passed between assignment, routing, and fitness."""

    day_wdays: list[int]
    blocked_ranges_by_day: dict[int, list[tuple[int, int]]]
    locked_patient_days: dict[int, set[int]]
    locked_count_by_day: dict[int, int]
    instances_by_patient: dict[int, list]
    calendar_blocks_by_date: dict[str, list]
    locked_visits_by_date: dict[str, list]
    travel: Callable[[str, str], int]
    patients_by_id: dict[int, object]
    inst_idx_map: dict[str, int]
    date_to_idx: dict[str, int]


# ── Context Builder ──────────────────────────────────────────────────

def build_context(input: SolverInput) -> SolverContext:
    """Pre-compute shared data structures used by both assignment and routing."""
    clinician = input.clinician
    working_days = input.working_days
    matrix = input.travel_matrix

    day_wdays = []
    for d in working_days:
        dt = datetime.fromisoformat(d)
        day_wdays.append((dt.weekday() + 1) % 7)

    blocked_ranges_by_day: dict[int, list[tuple[int, int]]] = defaultdict(list)
    locked_patient_days: dict[int, set[int]] = defaultdict(set)
    locked_count_by_day: dict[int, int] = defaultdict(int)

    for lv in input.locked_visits:
        day_idx = day_index(lv.date, working_days)
        if day_idx is None:
            continue
        locked_patient_days[lv.patient_id].add(day_idx)
        locked_count_by_day[day_idx] += 1
        start_min = datetime_to_minute(lv.starts_at)
        end_min = start_min + lv.duration_minutes + clinician.charting_buffer_minutes
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    for cb in input.calendar_blocks:
        day_idx = day_index(cb.date, working_days)
        if day_idx is None:
            continue
        start_min = datetime_to_minute(cb.starts_at)
        end_min = datetime_to_minute(cb.ends_at)
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    instances_by_patient: dict[int, list] = defaultdict(list)
    for inst in input.instances:
        instances_by_patient[inst.patient_id].append(inst)

    def travel_fn(from_key: str, to_key: str) -> int:
        return matrix.get(from_key, {}).get(to_key, 0)

    patients_by_id = {p.id: p for p in input.patients}
    inst_idx_map = {inst.id: i for i, inst in enumerate(input.instances)}
    date_to_idx = {d: i for i, d in enumerate(input.working_days)}

    calendar_blocks_by_date: dict[str, list] = defaultdict(list)
    for cb in input.calendar_blocks:
        calendar_blocks_by_date[cb.date].append(cb)
    locked_visits_by_date: dict[str, list] = defaultdict(list)
    for lv in input.locked_visits:
        locked_visits_by_date[lv.date].append(lv)

    return SolverContext(
        day_wdays=day_wdays,
        blocked_ranges_by_day=dict(blocked_ranges_by_day),
        locked_patient_days=dict(locked_patient_days),
        locked_count_by_day=dict(locked_count_by_day),
        instances_by_patient=dict(instances_by_patient),
        calendar_blocks_by_date=dict(calendar_blocks_by_date),
        locked_visits_by_date=dict(locked_visits_by_date),
        travel=travel_fn,
        patients_by_id=patients_by_id,
        inst_idx_map=inst_idx_map,
        date_to_idx=date_to_idx,
    )
