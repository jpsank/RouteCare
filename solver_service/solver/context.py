"""Shared context, constants, and helpers for the VRPTW solver.

All times are expressed in *absolute minutes* — minutes elapsed since the start of
the scheduling horizon at 00:00.  Day 0 at 8 AM = day_offset + 480.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from models import ClinicianData, SolverInput, SolverOutput, VisitInstanceData

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_MAX_VISITS_PER_DAY = 5
MINUTES_PER_DAY = 1440

# Routing constants
SLOT_STEP = 15
TRANSIT_BUFFER = 5

# Penalty weights (envelope objective)
PENALTY_SPACING_MIN = 200
PENALTY_SPACING_MAX = 300
PENALTY_AVAILABILITY = 150
PENALTY_DAY_OFFSET = 10
PENALTY_SOFT_OVERRIDE = 80
PENALTY_LOAD_IMBALANCE = 40
DENSITY_BASE_WEIGHT = 60
PRIORITY_WEIGHT = 500

# Pipeline tunables
NUM_WORKERS = int(os.environ.get("SOLVER_NUM_WORKERS", min(os.cpu_count() or 4, 8)))
MAX_BENDERS_ROUNDS = int(os.environ.get("SOLVER_BENDERS_MAX_ROUNDS", "10"))
ENVELOPE_BUDGET_FRAC = float(os.environ.get("SOLVER_ENVELOPE_FRAC", "0.35"))
SUBPROBLEM_BUDGET_FRAC = float(os.environ.get("SOLVER_SUBPROBLEM_FRAC", "0.55"))


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


def target_day_offsets(
    n_visits: int, num_days: int, min_gap: int, density: float
) -> list[int]:
    """Compute ideal target day indices for evenly spreading n_visits across num_days."""
    if n_visits <= 1:
        return [0]
    max_step = (num_days - 1) / (n_visits - 1)
    min_step = max(float(min_gap), 1.0)
    step = max(max_step - density * (max_step - min_step), min_step)
    return [min(round(k * step), num_days - 1) for k in range(n_visits)]


# ── Vehicle / Node Definitions ───────────────────────────────────────


@dataclass
class VehicleDef:
    """One vehicle = one working day for one clinician."""

    vehicle_idx: int
    clinician_idx: int  # index into SolverContext.clinicians
    clinician: ClinicianData = field(repr=False)  # denormalized for direct access
    date: str  # ISO date
    day_index: int  # index into working_days
    day_start: int  # day-local start minute
    day_end: int  # day-local end minute
    tw_early: int  # absolute-minutes shift start
    tw_late: int  # absolute-minutes shift end
    shift_duration: int  # day_end - day_start
    max_drive: int  # max drive minutes (or 999_999)
    capacity: int  # MAX_VISITS_PER_DAY - locked count
    depot_idx: int  # index into depots (one per clinician home)


# ── Solver Context ───────────────────────────────────────────────────


@dataclass
class SolverContext:
    """Pre-computed shared data for the VRPTW solver."""

    # Clinicians
    clinicians: list[ClinicianData]

    # Calendar
    working_days: list[str]
    start_ordinal: int  # ordinal of start_date

    # Vehicles (one per clinician per working day)
    vehicles: list[VehicleDef]
    vehicle_by_date: dict[str, VehicleDef]  # first vehicle per date (backward compat)
    vehicle_by_key: dict[tuple[int, int], VehicleDef] = field(default_factory=dict)

    # Node mapping (filled by model after client creation)
    node_to_instance_id: dict[int, str] = field(default_factory=dict)
    instance_id_to_node: dict[str, int] = field(default_factory=dict)

    # Pre-computed from input
    patients_by_id: dict[int, object] = field(default_factory=dict)
    instances_by_patient: dict[int, list] = field(default_factory=dict)
    instances_by_id: dict[str, VisitInstanceData] = field(default_factory=dict)
    travel: Callable[[str, str], int] = field(default=lambda a, b: 0)
    calendar_blocks_by_date: dict[str, list] = field(default_factory=dict)
    calendar_blocks_by_vehicle: dict[tuple[int, int], list] = field(default_factory=dict)
    locked_visits_by_date: dict[str, list] = field(default_factory=dict)
    locked_visits_by_vehicle: dict[tuple[int, int], list] = field(default_factory=dict)
    # locked_patient_days: patient_id → set of (clinician_idx, day_idx)
    locked_patient_days: dict[int, set[tuple[int, int]]] = field(default_factory=dict)
    locked_count_by_vehicle: dict[tuple[int, int], int] = field(default_factory=dict)
    day_wdays: list[int] = field(default_factory=list)
    date_to_idx: dict[str, int] = field(default_factory=dict)

    # ── Absolute-minute helpers ───────────────────────────────────

    def to_abs_minutes(self, date: str, minute_of_day: int) -> int:
        """Convert (date, minute-of-day) → absolute minutes from horizon start."""
        day_idx = self.date_to_idx[date]
        return day_idx * MINUTES_PER_DAY + minute_of_day

    def from_abs_minutes(self, am: int) -> tuple[str, int]:
        """Convert absolute minutes → (date, minute-of-day)."""
        day_idx = am // MINUTES_PER_DAY
        minute_of_day = am % MINUTES_PER_DAY
        return self.working_days[day_idx], minute_of_day

    def vehicle_for_date(self, date: str) -> VehicleDef | None:
        return self.vehicle_by_date.get(date)


# ── Context Builder ──────────────────────────────────────────────────


def build_context(input: SolverInput) -> SolverContext:
    """Build the shared context from solver input."""
    clinicians = input.clinicians
    working_days = input.working_days
    matrix = input.travel_matrix

    start_ordinal = datetime.fromisoformat(input.start_date).toordinal()

    # Day weekdays (0=Sun style matching per_day_hours keys)
    day_wdays: list[int] = []
    for d in working_days:
        dt = datetime.fromisoformat(d)
        day_wdays.append((dt.weekday() + 1) % 7)

    # Locked visits / calendar blocks pre-computation
    # locked_patient_days: patient_id → set of (clinician_idx, day_idx) tuples
    locked_patient_days: dict[int, set[tuple[int, int]]] = defaultdict(set)
    # locked_count_by_vehicle: (clinician_idx, day_idx) → count
    locked_count_by_vehicle: dict[tuple[int, int], int] = defaultdict(int)
    calendar_blocks_by_date: dict[str, list] = defaultdict(list)
    calendar_blocks_by_vehicle: dict[tuple[int, int], list] = defaultdict(list)
    locked_visits_by_date: dict[str, list] = defaultdict(list)
    locked_visits_by_vehicle: dict[tuple[int, int], list] = defaultdict(list)

    date_to_idx = {d: i for i, d in enumerate(working_days)}

    for lv in input.locked_visits:
        d_idx = date_to_idx.get(lv.date)
        if d_idx is None:
            continue
        c_idx = lv.clinician_idx
        locked_patient_days[lv.patient_id].add((c_idx, d_idx))
        locked_count_by_vehicle[(c_idx, d_idx)] += 1
        locked_visits_by_date[lv.date].append(lv)
        locked_visits_by_vehicle[(c_idx, d_idx)].append(lv)

    for cb in input.calendar_blocks:
        calendar_blocks_by_date[cb.date].append(cb)
        d_idx = date_to_idx.get(cb.date)
        if d_idx is not None:
            calendar_blocks_by_vehicle[(cb.clinician_idx, d_idx)].append(cb)

    # Instance grouping
    instances_by_patient: dict[int, list] = defaultdict(list)
    instances_by_id: dict[str, VisitInstanceData] = {}
    for inst in input.instances:
        instances_by_patient[inst.patient_id].append(inst)
        instances_by_id[inst.id] = inst

    # Travel function — supports "home_0", "home_1", etc. with backward
    # compat: if matrix has "home" but not "home_0", map "home_0" → "home".
    has_legacy_home = "home" in matrix and "home_0" not in matrix

    def travel_fn(from_key: str, to_key: str) -> int:
        fk = from_key
        tk = to_key
        if has_legacy_home:
            if fk.startswith("home_"):
                fk = "home"
            if tk.startswith("home_"):
                tk = "home"
        return matrix.get(fk, {}).get(tk, 0)

    patients_by_id = {p.id: p for p in input.patients}

    # Build vehicles — one per clinician per working day
    vehicles: list[VehicleDef] = []
    vehicle_by_date: dict[str, VehicleDef] = {}
    vehicle_by_key: dict[tuple[int, int], VehicleDef] = {}
    v_idx = 0
    for c_idx, clinician in enumerate(clinicians):
        cap = getattr(clinician, "max_visits_per_day", DEFAULT_MAX_VISITS_PER_DAY)
        for d_idx, date in enumerate(working_days):
            ds, de = day_bounds(clinician, date)
            locked_count = locked_count_by_vehicle.get((c_idx, d_idx), 0)
            v = VehicleDef(
                vehicle_idx=v_idx,
                clinician_idx=c_idx,
                clinician=clinician,
                date=date,
                day_index=d_idx,
                day_start=ds,
                day_end=de,
                tw_early=d_idx * MINUTES_PER_DAY + ds,
                tw_late=d_idx * MINUTES_PER_DAY + de,
                shift_duration=de - ds,
                max_drive=clinician.max_drive_minutes_per_day or 999_999,
                capacity=max(0, cap - locked_count),
                depot_idx=c_idx,
            )
            vehicles.append(v)
            vehicle_by_key[(c_idx, d_idx)] = v
            # Backward compat: vehicle_by_date stores first clinician's vehicle
            if date not in vehicle_by_date:
                vehicle_by_date[date] = v
            v_idx += 1

    return SolverContext(
        clinicians=clinicians,
        working_days=working_days,
        start_ordinal=start_ordinal,
        vehicles=vehicles,
        vehicle_by_date=vehicle_by_date,
        vehicle_by_key=vehicle_by_key,
        patients_by_id=patients_by_id,
        instances_by_patient=dict(instances_by_patient),
        instances_by_id=instances_by_id,
        travel=travel_fn,
        calendar_blocks_by_date=dict(calendar_blocks_by_date),
        calendar_blocks_by_vehicle=dict(calendar_blocks_by_vehicle),
        locked_visits_by_date=dict(locked_visits_by_date),
        locked_visits_by_vehicle=dict(locked_visits_by_vehicle),
        locked_patient_days=dict(locked_patient_days),
        locked_count_by_vehicle=dict(locked_count_by_vehicle),
        day_wdays=day_wdays,
        date_to_idx=date_to_idx,
    )


# ── Output helpers ───────────────────────────────────────────────────


def build_default_lunch_placements(input: SolverInput) -> dict[str, dict]:
    """Default lunch placement for all working days (uses first clinician)."""
    c = input.clinicians[0]
    if c.lunch_duration_minutes <= 0:
        return {date: None for date in input.working_days}
    half_window = c.lunch_window_minutes // 2
    placements = {}
    for date in input.working_days:
        ds, _ = day_bounds(c, date)
        earliest = max(c.lunch_start_minute - half_window, ds)
        placements[date] = {
            "start_minute": earliest,
            "end_minute": earliest + c.lunch_duration_minutes,
        }
    return placements


def empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_default_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "vrptw"},
    )
