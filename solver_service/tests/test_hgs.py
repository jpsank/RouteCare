"""Tests for the HGS solver backend."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import SolverInput, ClinicianData, PatientData, VisitInstanceData, Location


def make_test_input():
    """Small test case: 3 patients, 2 visits each, 5 working days."""
    patients = [
        PatientData(
            id=1, name="Alice",
            location=Location(lat=36.09, lng=-94.19),
            visit_duration_minutes=60, required_visits_per_week=2,
            min_days_between_visits=2, max_days_between_visits=5, priority=0,
        ),
        PatientData(
            id=2, name="Bob",
            location=Location(lat=36.32, lng=-94.22),
            visit_duration_minutes=45, required_visits_per_week=2,
            min_days_between_visits=1, max_days_between_visits=7, priority=5,
        ),
        PatientData(
            id=3, name="Carol",
            location=Location(lat=36.18, lng=-94.15),
            visit_duration_minutes=30, required_visits_per_week=1,
            min_days_between_visits=1, max_days_between_visits=7, priority=0,
        ),
    ]

    instances = [
        VisitInstanceData(id="patient_1_visit_0", patient_id=1, location=patients[0].location, duration=60, priority=0),
        VisitInstanceData(id="patient_1_visit_1", patient_id=1, location=patients[0].location, duration=60, priority=0),
        VisitInstanceData(id="patient_2_visit_0", patient_id=2, location=patients[1].location, duration=45, priority=5),
        VisitInstanceData(id="patient_2_visit_1", patient_id=2, location=patients[1].location, duration=45, priority=5),
        VisitInstanceData(id="patient_3_visit_0", patient_id=3, location=patients[2].location, duration=30, priority=0),
    ]

    travel_matrix = {
        "home": {"1": 18, "2": 41, "3": 24},
        "1": {"home": 18, "2": 30, "3": 12},
        "2": {"home": 41, "1": 30, "3": 22},
        "3": {"home": 24, "1": 12, "2": 22},
    }

    return SolverInput(
        patients=patients,
        clinician=ClinicianData(
            home_location=Location(lat=36.18, lng=-94.13),
        ),
        instances=instances,
        locked_visits=[],
        calendar_blocks=[],
        travel_matrix=travel_matrix,
        week_start_on="2026-04-06",
        working_days=["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"],
    )


def test_hgs_produces_valid_output():
    from solvers.hgs import solve

    input = make_test_input()
    output = solve(input, time_budget=5)

    assert output.metadata["optimizer_type"] == "python_hgs"
    assert len(output.planned_visits) == 5, f"Expected 5 visits, got {len(output.planned_visits)}"

    # All instance IDs should be from the input
    input_ids = {inst.id for inst in input.instances}
    output_ids = {v.instance_id for v in output.planned_visits}
    assert output_ids == input_ids, f"Mismatch: {output_ids} vs {input_ids}"

    # Lunch placements should cover all working days
    for day in input.working_days:
        assert day in output.lunch_placements, f"Missing lunch for {day}"

    # No patient should have 2 visits on the same day
    from collections import Counter
    for date in input.working_days:
        day_pids = [v.patient_id for v in output.planned_visits if v.date == date]
        counts = Counter(day_pids)
        for pid, count in counts.items():
            assert count <= 1, f"Patient {pid} has {count} visits on {date}"


def test_hgs_empty_input():
    from solvers.hgs import solve

    input = SolverInput(
        patients=[],
        clinician=ClinicianData(),
        instances=[],
        travel_matrix={},
        week_start_on="2026-04-06",
        working_days=["2026-04-06"],
    )
    output = solve(input, time_budget=1)
    assert len(output.planned_visits) == 0
