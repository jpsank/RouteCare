"""Shared logic for mapping our problem to VRP solver models."""

from __future__ import annotations
from datetime import datetime, timedelta
from models import SolverInput, SolverOutput, PlannedVisit

MAX_VISITS_PER_DAY = 5


def minutes_to_datetime(date_str: str, minutes: int) -> str:
    """Convert a date string + minutes-since-midnight to ISO datetime."""
    dt = datetime.fromisoformat(date_str)
    dt = dt.replace(hour=minutes // 60, minute=minutes % 60, second=0)
    return dt.isoformat()


def build_lunch_placements(input: SolverInput) -> dict[str, dict]:
    """Default lunch placement for all working days."""
    c = input.clinician
    half_window = c.lunch_window_minutes // 2
    earliest = max(c.lunch_start_minute - half_window, c.workday_start_minute)
    placements = {}
    for date in input.working_days:
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


def validate_spacing(
    planned: list[PlannedVisit],
    patients_by_id: dict[int, dict],
) -> list[PlannedVisit]:
    """Flag visits that violate min/max spacing as soft_constraint_override."""
    from collections import defaultdict

    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in planned:
        patient_dates[v.patient_id].append(v.date)

    violations: set[str] = set()
    for pid, dates in patient_dates.items():
        if len(dates) < 2:
            continue
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        sorted_dates = sorted(dates)
        for i in range(len(sorted_dates) - 1):
            d1 = datetime.fromisoformat(sorted_dates[i])
            d2 = datetime.fromisoformat(sorted_dates[i + 1])
            gap = (d2 - d1).days
            if gap < patient.min_days_between_visits or gap > patient.max_days_between_visits:
                # Flag all visits for this patient as overridden
                for v in planned:
                    if v.patient_id == pid:
                        violations.add(v.instance_id)

    return [
        PlannedVisit(
            instance_id=v.instance_id,
            patient_id=v.patient_id,
            date=v.date,
            starts_at=v.starts_at,
            ends_at=v.ends_at,
            soft_constraint_override=v.soft_constraint_override or v.instance_id in violations,
        )
        for v in planned
    ]
