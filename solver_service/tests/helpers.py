"""Shared test helpers for the Benders solver test suite.

Factory functions for building small test scenarios without boilerplate.
Keep this module thin — just input construction plus a couple of
assertion helpers.  Anything bigger belongs in a dedicated test module.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta as _td

from models import (
    CalendarBlockData,
    ClinicianData,
    LockedVisitData,
    PatientData,
    SolverInput,
    SolverOutput,
    VisitInstanceData,
)
from solver.benders import validate_plan


# ── Fixed calendar references ───────────────────────────────────────

# Five Mon-Fri weekdays starting 2026-04-20
WORKING_DAYS_WEEK: list[str] = [
    "2026-04-20",  # Mon
    "2026-04-21",  # Tue
    "2026-04-22",  # Wed
    "2026-04-23",  # Thu
    "2026-04-24",  # Fri
]


def weekdays_from(start: str, n: int) -> list[str]:
    """Generate n working days (Mon-Fri) starting from the given ISO date."""
    d = _date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += _td(days=1)
    return out


# ── Scenario factories ─────────────────────────────────────────────


def mk_patient(
    pid: int,
    name: str = "P",
    dur: int = 60,
    req: int = 1,
    min_gap: int = 1,
    max_gap: int = 7,
    priority: int = 0,
) -> PatientData:
    return PatientData(
        id=pid,
        name=name,
        visit_duration_minutes=dur,
        required_visits=req,
        min_days_between_visits=min_gap,
        max_days_between_visits=max_gap,
        priority=priority,
    )


def mk_instance(
    iid: str,
    pid: int,
    dur: int = 60,
    eligible: list[int] | None = None,
    windows: dict | None = None,
    unavailable: dict | None = None,
) -> VisitInstanceData:
    return VisitInstanceData(
        id=iid,
        patient_id=pid,
        duration=dur,
        eligible_clinician_indices=eligible or [],
        availability_windows=windows or {},
        unavailability_windows=unavailable or {},
    )


def mk_clinician(
    max_visits_per_day: int = 5,
    lunch_duration_minutes: int = 30,
    workday_start_minute: int = 480,
    workday_end_minute: int = 1080,
) -> ClinicianData:
    return ClinicianData(
        max_visits_per_day=max_visits_per_day,
        lunch_duration_minutes=lunch_duration_minutes,
        workday_start_minute=workday_start_minute,
        workday_end_minute=workday_end_minute,
    )


def uniform_matrix(
    patients: list[PatientData],
    n_clinicians: int = 1,
    inter_travel: int = 20,
    home_travel: int = 20,
) -> dict:
    """Symmetric matrix with uniform inter-patient and home-leg travel."""
    pids = [str(p.id) for p in patients]
    home_keys = [f"home_{i}" for i in range(n_clinicians)]
    matrix: dict[str, dict[str, int]] = {}
    for hk in home_keys:
        matrix[hk] = {p: home_travel for p in pids}
        matrix[hk][hk] = 0
    for p in pids:
        matrix[p] = {}
        for hk in home_keys:
            matrix[p][hk] = home_travel
        for q in pids:
            matrix[p][q] = 0 if p == q else inter_travel
    return matrix


def make_input(
    patients: list[PatientData],
    instances: list[VisitInstanceData],
    clinicians: list[ClinicianData] | None = None,
    matrix: dict | None = None,
    working_days: list[str] | None = None,
    locked_visits: list[LockedVisitData] | None = None,
    calendar_blocks: list[CalendarBlockData] | None = None,
) -> SolverInput:
    """Build a SolverInput with sensible defaults for quick test setup.

    Defaults:
      - 1 clinician with stock ClinicianData
      - working_days = WORKING_DAYS_WEEK (Mon-Fri 2026-04-20 to 04-24)
      - matrix = uniform 20min inter-patient, 20min home-leg
    """
    if clinicians is None:
        clinicians = [ClinicianData()]
    if working_days is None:
        working_days = WORKING_DAYS_WEEK
    if matrix is None:
        matrix = uniform_matrix(patients, n_clinicians=len(clinicians))
    return SolverInput(
        patients=patients,
        instances=instances,
        clinicians=clinicians,
        travel_matrix=matrix,
        start_date=working_days[0],
        working_days=working_days,
        locked_visits=locked_visits or [],
        calendar_blocks=calendar_blocks or [],
    )


# ── Assertion helpers ──────────────────────────────────────────────


def assert_valid(out: SolverOutput, inp: SolverInput) -> None:
    """Raise on any hard-constraint violation in the output plan."""
    validate_plan(out, inp)
    assert out.metadata.get("validated") is True, (
        f"output.metadata.validated=False — expected True"
    )


def minute_range_from_visit(visit) -> tuple[int, int]:
    """Parse ISO datetime start/end into (start_min, end_min) of day."""
    s = visit.starts_at
    e = visit.ends_at
    start_m = int(s[-8:-6]) * 60 + int(s[-5:-3])
    end_m = int(e[-8:-6]) * 60 + int(e[-5:-3])
    return start_m, end_m
