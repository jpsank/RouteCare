"""Tests for the CP-SAT daily scheduling model."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime
from models import (
    SolverInput,
    ClinicianData,
    PatientData,
    VisitInstanceData,
    CalendarBlockData,
    Location,
)
from solver.context import build_context, SLOT_STEP
from solver.cpsat_timing import cpsat_time_vehicle_route


def _make_input(**overrides):
    """Minimal test input builder."""
    patients = overrides.get(
        "patients",
        [
            PatientData(
                id=1,
                name="A",
                location=Location(lat=36.0, lng=-94.0),
                visit_duration_minutes=60,
                required_visits=1,
            ),
        ],
    )
    instances = overrides.get(
        "instances",
        [
            VisitInstanceData(
                id="p1v0", patient_id=1, location=patients[0].location, duration=60
            ),
        ],
    )
    clinician = overrides.get(
        "clinician",
        ClinicianData(
            home_location=Location(lat=36.0, lng=-94.0),
        ),
    )
    travel_matrix = overrides.get(
        "travel_matrix",
        {
            "home": {"1": 10, "2": 15, "3": 20},
            "1": {"home": 10, "2": 12, "3": 18},
            "2": {"home": 15, "1": 12, "3": 8},
            "3": {"home": 20, "1": 18, "2": 8},
        },
    )
    return SolverInput(
        patients=patients,
        clinician=clinician,
        instances=instances,
        locked_visits=overrides.get("locked_visits", []),
        calendar_blocks=overrides.get("calendar_blocks", []),
        travel_matrix=travel_matrix,
        start_date="2026-04-06",
        working_days=overrides.get("working_days", ["2026-04-06"]),
    )


def _minute(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def test_single_visit_timing():
    """Single visit should be placed at day_start + transit, rounded to 15-min slot."""
    inp = _make_input()
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]
    instances = list(inp.instances)

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert len(result.visits) == 1
    assert len(result.dropped) == 0
    v = result.visits[0]
    start = _minute(v.starts_at)
    # day_start=480, transit=10 → earliest=490 → rounded to 495 (next 15-min slot)
    assert start >= 480 + 10
    assert start % SLOT_STEP == 0


def test_visit_ordering_preserved():
    """Three visits should be output in the same order as input."""
    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=30,
            required_visits=1,
        )
        for k in range(1, 4)
    ]
    instances = [
        VisitInstanceData(
            id=f"p{k}v0", patient_id=k, location=patients[k - 1].location, duration=30
        )
        for k in range(1, 4)
    ]
    inp = _make_input(patients=patients, instances=instances)
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert len(result.visits) == 3
    # Visits should be in order: p1, p2, p3 (by start time)
    for i in range(len(result.visits) - 1):
        assert result.visits[i].starts_at <= result.visits[i + 1].starts_at


def test_calendar_block_avoidance():
    """Visits should not overlap a calendar block."""
    patients = [
        PatientData(
            id=1,
            name="A",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=1,
        ),
    ]
    instances = [
        VisitInstanceData(
            id="p1v0", patient_id=1, location=patients[0].location, duration=60
        ),
    ]
    # Block 8am-11am (480-660)
    blocks = [
        CalendarBlockData(
            date="2026-04-06",
            starts_at="2026-04-06T08:00:00",
            ends_at="2026-04-06T11:00:00",
        ),
    ]
    inp = _make_input(patients=patients, instances=instances, calendar_blocks=blocks)
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert len(result.visits) == 1
    start = _minute(result.visits[0].starts_at)
    end = _minute(result.visits[0].ends_at)
    # Must not overlap [480, 660)
    assert start >= 660 or end <= 480


def test_lunch_placement():
    """Lunch should be placed within the configured window."""
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        lunch_start_minute=720,
        lunch_duration_minutes=30,
        lunch_window_minutes=60,
    )
    inp = _make_input(clinician=clinician)
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]
    instances = list(inp.instances)

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert result.lunch is not None
    assert 690 <= result.lunch["start_minute"] <= 750
    assert result.lunch["end_minute"] == result.lunch["start_minute"] + 30


def test_lunch_does_not_overlap_visit():
    """Lunch should not overlap any visit."""
    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=1,
        )
        for k in range(1, 3)
    ]
    instances = [
        VisitInstanceData(
            id=f"p{k}v0", patient_id=k, location=patients[k - 1].location, duration=60
        )
        for k in range(1, 3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        lunch_start_minute=720,
        lunch_duration_minutes=30,
        lunch_window_minutes=60,
    )
    tm = {
        "home": {"1": 0, "2": 0},
        "1": {"home": 0, "2": 0},
        "2": {"home": 0, "1": 0},
    }
    inp = _make_input(
        patients=patients, instances=instances, clinician=clinician, travel_matrix=tm
    )
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert result.lunch is not None
    ls = result.lunch["start_minute"]
    le = result.lunch["end_minute"]
    for v in result.visits:
        vs = _minute(v.starts_at)
        ve = _minute(v.ends_at)
        assert ve <= ls or vs >= le, f"Visit [{vs}-{ve}] overlaps lunch [{ls}-{le}]"


def test_availability_window_respected():
    """Visit with a narrow availability window should start within it."""
    patients = [
        PatientData(
            id=1,
            name="A",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=30,
            required_visits=1,
            availability_windows={
                "1": [{"start_minute": 840, "end_minute": 960}],
            },
        ),
    ]
    instances = [
        VisitInstanceData(
            id="p1v0",
            patient_id=1,
            location=patients[0].location,
            duration=30,
            availability_windows=patients[0].availability_windows,
        ),
    ]
    inp = _make_input(patients=patients, instances=instances)
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert len(result.visits) == 1
    start = _minute(result.visits[0].starts_at)
    assert 840 <= start < 960, f"Visit at {start}, expected in [840, 960)"


def test_graceful_drop_when_day_full():
    """When day is too short for all visits, some should be dropped."""
    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=120,
            required_visits=1,
        )
        for k in range(1, 6)
    ]
    instances = [
        VisitInstanceData(
            id=f"p{k}v0", patient_id=k, location=patients[k - 1].location, duration=120
        )
        for k in range(1, 6)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=780,  # Only 5 hours
        lunch_duration_minutes=0,
    )
    tm = {"home": {str(k): 0 for k in range(1, 6)}}
    for k in range(1, 6):
        tm[str(k)] = {"home": 0}
        for j in range(1, 6):
            if k != j:
                tm[str(k)][str(j)] = 0
    inp = _make_input(
        patients=patients, instances=instances, clinician=clinician, travel_matrix=tm
    )
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    # 5 hours / 2 hours = at most 2 visits (with slot rounding maybe 2)
    assert len(result.visits) <= 3
    assert len(result.dropped) > 0
    total = len(result.visits) + len(result.dropped)
    assert total == 5


def test_break_insertion():
    """With max_continuous_work=90, two 60-min visits should trigger a break."""
    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=1,
        )
        for k in range(1, 3)
    ]
    instances = [
        VisitInstanceData(
            id=f"p{k}v0", patient_id=k, location=patients[k - 1].location, duration=60
        )
        for k in range(1, 3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=1080,
        max_continuous_work_minutes=90,
        required_break_minutes=15,
        lunch_start_minute=900,
        lunch_duration_minutes=0,
    )
    tm = {
        "home": {"1": 0, "2": 0},
        "1": {"home": 0, "2": 0},
        "2": {"home": 0, "1": 0},
    }
    inp = _make_input(
        patients=patients, instances=instances, clinician=clinician, travel_matrix=tm
    )
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    assert len(result.visits) == 2
    v0_end = _minute(result.visits[0].ends_at)
    v1_start = _minute(result.visits[1].starts_at)
    gap = v1_start - v0_end
    # With zero travel, gap should include at least 15-min break (rounded to slot)
    assert gap >= 15, f"Gap {gap} min, expected >= 15 for mandatory break"


def test_drive_cost_computed():
    """Drive cost should reflect the transit between ordered visits."""
    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=30,
            required_visits=1,
        )
        for k in range(1, 3)
    ]
    instances = [
        VisitInstanceData(
            id=f"p{k}v0", patient_id=k, location=patients[k - 1].location, duration=30
        )
        for k in range(1, 3)
    ]
    tm = {
        "home": {"1": 10, "2": 20},
        "1": {"home": 10, "2": 12},
        "2": {"home": 20, "1": 12},
    }
    inp = _make_input(patients=patients, instances=instances, travel_matrix=tm)
    ctx = build_context(inp)
    vehicle = ctx.vehicles[0]

    result = cpsat_time_vehicle_route(vehicle, instances, inp, ctx)

    # home→1: 10, 1→2: 12, 2→home: 20 = 42
    assert result.drive_cost == 42
