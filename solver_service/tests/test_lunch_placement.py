"""Regression tests: the concrete timing pass must never place lunch on
top of a locked visit or calendar block. Bug: the "no free instances,
only locked stops" shortcut in cpsat_time_vehicle_route computed lunch
purely from the clinician's preferred lunch window, with zero awareness
of where locked visits actually sat — so a locked visit spanning that
window got a lunch block stamped on top of it."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models import CalendarBlockData, LockedVisitData  # noqa: E402
from solver.context import build_context  # noqa: E402
from solver.cpsat_timing import cpsat_time_vehicle_route  # noqa: E402
from tests.helpers import WORKING_DAYS_WEEK, make_input, mk_clinician, mk_patient  # noqa: E402


def _vehicle_for(ctx, date):
    return next(v for v in ctx.vehicles if v.date == date and v.clinician_idx == 0)


def test_lunch_avoids_a_locked_visit_spanning_the_preferred_window():
    date = WORKING_DAYS_WEEK[0]
    p = [mk_patient(1)]
    # Locked visit 11:00-12:30 (660-750) — fully covers the default lunch
    # window (lunch_start_minute=720, window=90 → [675, 765)).
    locked = [LockedVisitData(
        patient_id=1, clinician_idx=0, date=date,
        starts_at=f"{date}T11:00:00", ends_at=f"{date}T12:30:00",
        duration_minutes=90,
    )]
    inp = make_input(p, [], clinicians=[mk_clinician()], locked_visits=locked)
    ctx = build_context(inp)
    vehicle = _vehicle_for(ctx, date)

    route = cpsat_time_vehicle_route(vehicle, [], inp, ctx)

    assert route.lunch is not None
    lunch_start, lunch_end = route.lunch["start_minute"], route.lunch["end_minute"]
    locked_start, locked_end = 660, 750
    assert lunch_end <= locked_start or lunch_start >= locked_end, (
        f"lunch [{lunch_start},{lunch_end}) overlaps locked visit [{locked_start},{locked_end})"
    )


def test_lunch_avoids_a_calendar_block_spanning_the_preferred_window():
    date = WORKING_DAYS_WEEK[0]
    p = [mk_patient(1)]
    blocks = [CalendarBlockData(
        clinician_idx=0, date=date,
        starts_at=f"{date}T11:00:00", ends_at=f"{date}T12:30:00",
    )]
    inp = make_input(p, [], clinicians=[mk_clinician()], calendar_blocks=blocks)
    ctx = build_context(inp)
    vehicle = _vehicle_for(ctx, date)

    route = cpsat_time_vehicle_route(vehicle, [], inp, ctx)

    assert route.lunch is not None
    lunch_start, lunch_end = route.lunch["start_minute"], route.lunch["end_minute"]
    block_start, block_end = 660, 750
    assert lunch_end <= block_start or lunch_start >= block_end, (
        f"lunch [{lunch_start},{lunch_end}) overlaps calendar block [{block_start},{block_end})"
    )


def test_lunch_stays_at_preferred_time_when_nothing_blocks_it():
    date = WORKING_DAYS_WEEK[0]
    p = [mk_patient(1)]
    locked = [LockedVisitData(
        patient_id=1, clinician_idx=0, date=date,
        starts_at=f"{date}T09:00:00", ends_at=f"{date}T09:30:00",
        duration_minutes=30,
    )]
    inp = make_input(p, [], clinicians=[mk_clinician()], locked_visits=locked)
    ctx = build_context(inp)
    vehicle = _vehicle_for(ctx, date)

    route = cpsat_time_vehicle_route(vehicle, [], inp, ctx)

    # Default lunch_start_minute=720 with nothing in the way — should land there.
    assert route.lunch == {"start_minute": 720, "end_minute": 750}
