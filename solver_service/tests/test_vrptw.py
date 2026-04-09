"""Tests for the VRPTW solver backend."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import Counter
from datetime import datetime
from models import (
    SolverInput,
    ClinicianData,
    PatientData,
    VisitInstanceData,
    LockedVisitData,
    CalendarBlockData,
    Location,
)


def make_test_input(**overrides):
    """Small test case: 3 patients, 2 visits each, 5 working days."""
    patients = overrides.get(
        "patients",
        [
            PatientData(
                id=1,
                name="Alice",
                location=Location(lat=36.09, lng=-94.19),
                visit_duration_minutes=60,
                required_visits=2,
                min_days_between_visits=2,
                max_days_between_visits=5,
                priority=0,
            ),
            PatientData(
                id=2,
                name="Bob",
                location=Location(lat=36.32, lng=-94.22),
                visit_duration_minutes=45,
                required_visits=2,
                min_days_between_visits=1,
                max_days_between_visits=7,
                priority=5,
            ),
            PatientData(
                id=3,
                name="Carol",
                location=Location(lat=36.18, lng=-94.15),
                visit_duration_minutes=30,
                required_visits=1,
                min_days_between_visits=1,
                max_days_between_visits=7,
                priority=0,
            ),
        ],
    )

    default_instances = None
    if "instances" not in overrides and "patients" not in overrides:
        default_instances = [
            VisitInstanceData(
                id="patient_1_visit_0",
                patient_id=1,
                location=patients[0].location,
                duration=60,
                priority=0,
            ),
            VisitInstanceData(
                id="patient_1_visit_1",
                patient_id=1,
                location=patients[0].location,
                duration=60,
                priority=0,
            ),
            VisitInstanceData(
                id="patient_2_visit_0",
                patient_id=2,
                location=patients[1].location,
                duration=45,
                priority=5,
            ),
            VisitInstanceData(
                id="patient_2_visit_1",
                patient_id=2,
                location=patients[1].location,
                duration=45,
                priority=5,
            ),
            VisitInstanceData(
                id="patient_3_visit_0",
                patient_id=3,
                location=patients[2].location,
                duration=30,
                priority=0,
            ),
        ]
    instances = overrides.get("instances", default_instances)
    if instances is None:
        raise ValueError("Must provide 'instances' when overriding 'patients'")

    travel_matrix = overrides.get(
        "travel_matrix",
        {
            "home": {"1": 18, "2": 41, "3": 24},
            "1": {"home": 18, "2": 30, "3": 12},
            "2": {"home": 41, "1": 30, "3": 22},
            "3": {"home": 24, "1": 12, "2": 22},
        },
    )

    return SolverInput(
        patients=patients,
        clinician=overrides.get(
            "clinician",
            ClinicianData(
                home_location=Location(lat=36.18, lng=-94.13),
            ),
        ),
        instances=instances,
        locked_visits=overrides.get("locked_visits", []),
        calendar_blocks=overrides.get("calendar_blocks", []),
        travel_matrix=travel_matrix,
        start_date="2026-04-06",
        working_days=overrides.get(
            "working_days",
            [
                "2026-04-06",
                "2026-04-07",
                "2026-04-08",
                "2026-04-09",
                "2026-04-10",
            ],
        ),
    )


def test_produces_valid_output():
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    assert output.metadata["optimizer_type"] == "vrptw"
    assert len(output.planned_visits) == 5, (
        f"Expected 5 visits, got {len(output.planned_visits)}"
    )

    input_ids = {inst.id for inst in input.instances}
    output_ids = {v.instance_id for v in output.planned_visits}
    assert output_ids == input_ids, f"Mismatch: {output_ids} vs {input_ids}"

    for day in input.working_days:
        assert day in output.lunch_placements, f"Missing lunch for {day}"


def test_re_solve_with_upper_bound():
    """Second solve with upper_bound from same input seeds warm-start."""
    from solver.vrptw import solve

    inp = make_test_input()
    first = solve(inp, time_budget=10)
    second = solve(inp, time_budget=10, upper_bound=first)

    assert second.metadata["optimizer_type"] == "vrptw"
    input_ids = {inst.id for inst in inp.instances}
    output_ids = {v.instance_id for v in second.planned_visits}
    assert output_ids == input_ids
    for day in inp.working_days:
        assert day in second.lunch_placements

    assert second.metadata.get("upper_bound_provided") is True
    assert second.metadata.get("warm_start_used") is True


def test_empty_input():
    from solver.vrptw import solve

    input = SolverInput(
        patients=[],
        clinician=ClinicianData(),
        instances=[],
        travel_matrix={},
        start_date="2026-04-06",
        working_days=["2026-04-06"],
    )
    output = solve(input, time_budget=1)
    assert len(output.planned_visits) == 0


def test_one_patient_per_day():
    """No patient should have two visits on the same day."""
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    for date in input.working_days:
        day_pids = [v.patient_id for v in output.planned_visits if v.date == date]
        counts = Counter(day_pids)
        for pid, count in counts.items():
            assert count <= 1, f"Patient {pid} has {count} visits on {date}"


def test_max_visits_per_day():
    """No day should have more than MAX_VISITS_PER_DAY visits."""
    from solver.vrptw import solve
    from solver.context import MAX_VISITS_PER_DAY

    input = make_test_input()
    output = solve(input, time_budget=10)

    for date in input.working_days:
        day_count = sum(1 for v in output.planned_visits if v.date == date)
        assert day_count <= MAX_VISITS_PER_DAY, (
            f"Day {date} has {day_count} visits, max is {MAX_VISITS_PER_DAY}"
        )


def test_min_spacing():
    """Alice requires min 2 days between visits. Solver should respect this."""
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    alice_dates = sorted([v.date for v in output.planned_visits if v.patient_id == 1])
    assert len(alice_dates) == 2, f"Expected 2 Alice visits, got {len(alice_dates)}"

    d1 = datetime.fromisoformat(alice_dates[0])
    d2 = datetime.fromisoformat(alice_dates[1])
    gap = (d2 - d1).days
    assert gap >= 2, f"Alice visits are {gap} days apart, min is 2"


def test_locked_visits_respected():
    """Locked visits should prevent scheduling another visit for the same patient on that day."""
    from solver.vrptw import solve

    locked = [
        LockedVisitData(
            patient_id=1,
            date="2026-04-06",
            starts_at="2026-04-06T09:00:00",
            ends_at="2026-04-06T10:00:00",
            duration_minutes=60,
        )
    ]

    input = make_test_input(locked_visits=locked)
    output = solve(input, time_budget=10)

    alice_monday = [
        v for v in output.planned_visits if v.patient_id == 1 and v.date == "2026-04-06"
    ]
    assert len(alice_monday) == 0, "Alice should not have a new visit on locked day"


def test_calendar_blocks_respected():
    """Visits should not overlap calendar blocks."""
    from solver.vrptw import solve

    blocks = [
        CalendarBlockData(
            date="2026-04-06",
            starts_at="2026-04-06T09:00:00",
            ends_at="2026-04-06T15:00:00",
        )
    ]

    input = make_test_input(calendar_blocks=blocks)
    output = solve(input, time_budget=10)

    for v in output.planned_visits:
        if v.date != "2026-04-06":
            continue
        start_min = (
            datetime.fromisoformat(v.starts_at).hour * 60
            + datetime.fromisoformat(v.starts_at).minute
        )
        end_min = (
            datetime.fromisoformat(v.ends_at).hour * 60
            + datetime.fromisoformat(v.ends_at).minute
        )
        assert start_min >= 900 or end_min <= 540, (
            f"Visit {v.instance_id} at {start_min}-{end_min} overlaps block 540-900"
        )


def test_availability_windows():
    """Patient with availability windows should be scheduled within those windows."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=3,
            name="Carol",
            location=Location(lat=36.18, lng=-94.15),
            visit_duration_minutes=30,
            required_visits=1,
            min_days_between_visits=1,
            max_days_between_visits=7,
            priority=0,
            availability_windows={
                "0": [{"start_minute": 840, "end_minute": 960}],
                "1": [{"start_minute": 840, "end_minute": 960}],
                "2": [{"start_minute": 840, "end_minute": 960}],
                "3": [{"start_minute": 840, "end_minute": 960}],
                "4": [{"start_minute": 840, "end_minute": 960}],
                "5": [{"start_minute": 840, "end_minute": 960}],
                "6": [{"start_minute": 840, "end_minute": 960}],
            },
        ),
    ]

    instances = [
        VisitInstanceData(
            id="patient_3_visit_0",
            patient_id=3,
            location=patients[0].location,
            duration=30,
            priority=0,
            availability_windows=patients[0].availability_windows,
        ),
    ]

    input = make_test_input(patients=patients, instances=instances)
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 1
    v = output.planned_visits[0]
    start_min = (
        datetime.fromisoformat(v.starts_at).hour * 60
        + datetime.fromisoformat(v.starts_at).minute
    )
    assert 840 <= start_min < 960, f"Carol scheduled at {start_min}, expected 840-960"


def test_visits_dont_overlap():
    """Visits on the same day should not overlap in time."""
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    for date in input.working_days:
        day_visits = sorted(
            [v for v in output.planned_visits if v.date == date],
            key=lambda v: v.starts_at,
        )
        for i in range(len(day_visits) - 1):
            end_i = day_visits[i].ends_at
            start_j = day_visits[i + 1].starts_at
            assert end_i <= start_j, (
                f"Visits overlap on {date}: {day_visits[i].instance_id} ends at {end_i}, "
                f"{day_visits[i + 1].instance_id} starts at {start_j}"
            )


def test_single_visit():
    """Single visit, single day — trivially optimal."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=1,
            name="Solo",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=30,
            required_visits=1,
        ),
    ]
    instances = [
        VisitInstanceData(
            id="patient_1_visit_0",
            patient_id=1,
            location=patients[0].location,
            duration=30,
        ),
    ]
    input = make_test_input(
        patients=patients,
        instances=instances,
        working_days=["2026-04-06"],
    )
    output = solve(input, time_budget=10)
    assert len(output.planned_visits) == 1


def test_lunch_not_overlapped():
    """Visits should not overlap the lunch break."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=1,
            name="Alice",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=3,
            min_days_between_visits=1,
            max_days_between_visits=7,
        ),
    ]
    instances = [
        VisitInstanceData(
            id=f"patient_1_visit_{k}",
            patient_id=1,
            location=patients[0].location,
            duration=60,
        )
        for k in range(3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=1020,
        lunch_start_minute=720,
        lunch_duration_minutes=30,
        lunch_window_minutes=60,
    )
    input = make_test_input(
        patients=patients,
        instances=instances,
        clinician=clinician,
        working_days=["2026-04-06", "2026-04-07", "2026-04-08"],
    )
    output = solve(input, time_budget=10)

    for date in input.working_days:
        lunch = output.lunch_placements.get(date)
        if not lunch:
            continue
        lunch_start = lunch["start_minute"]
        lunch_end = lunch["end_minute"]

        for v in output.planned_visits:
            if v.date != date:
                continue
            v_start = (
                datetime.fromisoformat(v.starts_at).hour * 60
                + datetime.fromisoformat(v.starts_at).minute
            )
            v_end = (
                datetime.fromisoformat(v.ends_at).hour * 60
                + datetime.fromisoformat(v.ends_at).minute
            )
            assert v_end <= lunch_start or v_start >= lunch_end, (
                f"Visit {v.instance_id} [{v_start}-{v_end}] overlaps lunch [{lunch_start}-{lunch_end}] on {date}"
            )


def test_lunch_within_window():
    """Lunch placement should be within the configured lunch window."""
    from solver.vrptw import solve

    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        lunch_start_minute=720,
        lunch_duration_minutes=30,
        lunch_window_minutes=60,
    )
    input = make_test_input(clinician=clinician)
    output = solve(input, time_budget=10)

    for date in input.working_days:
        lunch = output.lunch_placements.get(date)
        assert lunch is not None, f"Missing lunch on {date}"
        assert 690 <= lunch["start_minute"] <= 750, (
            f"Lunch at {lunch['start_minute']} outside window [690, 750] on {date}"
        )


def test_break_enforcement():
    """With max_continuous_work=120min, a 3-hour block of visits should include lunch."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=k,
            name=f"P{k}",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=1,
            min_days_between_visits=1,
            max_days_between_visits=7,
        )
        for k in range(1, 4)
    ]
    instances = [
        VisitInstanceData(
            id=f"patient_{k}_visit_0",
            patient_id=k,
            location=patients[k - 1].location,
            duration=60,
        )
        for k in range(1, 4)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=1080,
        max_continuous_work_minutes=120,
        required_break_minutes=15,
        lunch_start_minute=720,
        lunch_duration_minutes=30,
        lunch_window_minutes=120,
    )
    input = make_test_input(
        patients=patients,
        instances=instances,
        clinician=clinician,
        working_days=["2026-04-06"],
        travel_matrix={
            "home": {"1": 0, "2": 0, "3": 0},
            "1": {"home": 0, "2": 0, "3": 0},
            "2": {"home": 0, "1": 0, "3": 0},
            "3": {"home": 0, "1": 0, "2": 0},
        },
    )
    output = solve(input, time_budget=10)

    day_visits = sorted(
        [v for v in output.planned_visits if v.date == "2026-04-06"],
        key=lambda v: v.starts_at,
    )
    assert len(day_visits) == 3, f"Expected 3 visits, got {len(day_visits)}"

    first_start = (
        datetime.fromisoformat(day_visits[0].starts_at).hour * 60
        + datetime.fromisoformat(day_visits[0].starts_at).minute
    )
    last_end = (
        datetime.fromisoformat(day_visits[-1].ends_at).hour * 60
        + datetime.fromisoformat(day_visits[-1].ends_at).minute
    )
    total_span = last_end - first_start

    assert total_span >= 210, (
        f"Total span is {total_span} min but should be >=210 (3x60min visits + 30min lunch)"
    )


def test_day_spreading():
    """Visits for a patient should be spread across days, not clustered."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=1,
            name="Alice",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=30,
            required_visits=3,
            min_days_between_visits=1,
            max_days_between_visits=7,
        ),
    ]
    instances = [
        VisitInstanceData(
            id=f"patient_1_visit_{k}",
            patient_id=1,
            location=patients[0].location,
            duration=30,
        )
        for k in range(3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        schedule_density=0.0,
    )
    input = make_test_input(
        patients=patients,
        instances=instances,
        clinician=clinician,
    )
    output = solve(input, time_budget=10)

    alice_days = sorted(
        [
            input.working_days.index(v.date)
            for v in output.planned_visits
            if v.patient_id == 1
        ]
    )
    assert len(alice_days) == 3

    gaps = [alice_days[i + 1] - alice_days[i] for i in range(len(alice_days) - 1)]
    avg_gap = sum(gaps) / len(gaps)
    assert avg_gap >= 1.5, (
        f"Visits on days {alice_days} have avg gap {avg_gap}, expected >=1.5 for good spreading"
    )


def test_metadata_fields():
    """Output metadata should contain expected fields."""
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    meta = output.metadata
    assert meta["optimizer_type"] == "vrptw"
    assert "status" in meta
    assert "unschedulable" in meta
    assert "drive_violations" in meta
    assert "return_home_by_day" in meta


# ── Multi-Clinician Tests ───────────────────────────────────────────────────


def test_multi_clinician_basic():
    """Two clinicians should both receive visits."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=1,
            name="Alice",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=2,
            min_days_between_visits=1,
            max_days_between_visits=7,
        ),
        PatientData(
            id=2,
            name="Bob",
            location=Location(lat=36.3, lng=-94.3),
            visit_duration_minutes=45,
            required_visits=2,
            min_days_between_visits=1,
            max_days_between_visits=7,
        ),
    ]
    instances = [
        VisitInstanceData(
            id="patient_1_visit_0",
            patient_id=1,
            location=patients[0].location,
            duration=60,
        ),
        VisitInstanceData(
            id="patient_1_visit_1",
            patient_id=1,
            location=patients[0].location,
            duration=60,
        ),
        VisitInstanceData(
            id="patient_2_visit_0",
            patient_id=2,
            location=patients[1].location,
            duration=45,
        ),
        VisitInstanceData(
            id="patient_2_visit_1",
            patient_id=2,
            location=patients[1].location,
            duration=45,
        ),
    ]
    clinicians = [
        ClinicianData(home_location=Location(lat=36.0, lng=-94.0)),
        ClinicianData(home_location=Location(lat=36.3, lng=-94.3)),
    ]
    travel_matrix = {
        "home_0": {"1": 10, "2": 40},
        "home_1": {"1": 40, "2": 10},
        "1": {"home_0": 10, "home_1": 40, "2": 30},
        "2": {"home_0": 40, "home_1": 10, "1": 30},
    }
    input = SolverInput(
        patients=patients,
        clinicians=clinicians,
        instances=instances,
        travel_matrix=travel_matrix,
        start_date="2026-04-06",
        working_days=["2026-04-06", "2026-04-07", "2026-04-08"],
    )
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 4, (
        f"Expected 4 visits placed, got {len(output.planned_visits)}"
    )
    clinician_indices = {v.clinician_idx for v in output.planned_visits}
    assert len(clinician_indices) == 2, (
        f"Expected visits on both clinicians, got indices {clinician_indices}"
    )


def test_multi_clinician_eligibility():
    """Patients restricted to specific clinicians should be respected."""
    from solver.vrptw import solve

    patients = [
        PatientData(
            id=1,
            name="Alice",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60,
            required_visits=1,
        ),
        PatientData(
            id=2,
            name="Bob",
            location=Location(lat=36.3, lng=-94.3),
            visit_duration_minutes=45,
            required_visits=1,
        ),
    ]
    instances = [
        VisitInstanceData(
            id="patient_1_visit_0",
            patient_id=1,
            location=patients[0].location,
            duration=60,
            eligible_clinician_indices=[0],
        ),
        VisitInstanceData(
            id="patient_2_visit_0",
            patient_id=2,
            location=patients[1].location,
            duration=45,
            eligible_clinician_indices=[1],
        ),
    ]
    clinicians = [
        ClinicianData(home_location=Location(lat=36.0, lng=-94.0)),
        ClinicianData(home_location=Location(lat=36.3, lng=-94.3)),
    ]
    travel_matrix = {
        "home_0": {"1": 10, "2": 40},
        "home_1": {"1": 40, "2": 10},
        "1": {"home_0": 10, "home_1": 40, "2": 30},
        "2": {"home_0": 40, "home_1": 10, "1": 30},
    }
    input = SolverInput(
        patients=patients,
        clinicians=clinicians,
        instances=instances,
        travel_matrix=travel_matrix,
        start_date="2026-04-06",
        working_days=["2026-04-06", "2026-04-07"],
    )
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 2
    for v in output.planned_visits:
        if v.patient_id == 1:
            assert v.clinician_idx == 0, "Alice should be on clinician 0"
        elif v.patient_id == 2:
            assert v.clinician_idx == 1, "Bob should be on clinician 1"


def test_single_clinician_backward_compat():
    """Single clinician input (old format) should still work identically."""
    from solver.vrptw import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 5
    for v in output.planned_visits:
        assert v.clinician_idx == 0
