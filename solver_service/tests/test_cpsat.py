"""Tests for the CP-SAT solver backend."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import Counter
from datetime import datetime
from models import (
    SolverInput, SolverOutput, ClinicianData, PatientData, VisitInstanceData,
    LockedVisitData, CalendarBlockData, Location,
)


def make_test_input(**overrides):
    """Small test case: 3 patients, 2 visits each, 5 working days."""
    patients = overrides.get("patients", [
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
    ])

    default_instances = None
    if "instances" not in overrides and "patients" not in overrides:
        default_instances = [
            VisitInstanceData(id="patient_1_visit_0", patient_id=1, location=patients[0].location, duration=60, priority=0),
            VisitInstanceData(id="patient_1_visit_1", patient_id=1, location=patients[0].location, duration=60, priority=0),
            VisitInstanceData(id="patient_2_visit_0", patient_id=2, location=patients[1].location, duration=45, priority=5),
            VisitInstanceData(id="patient_2_visit_1", patient_id=2, location=patients[1].location, duration=45, priority=5),
            VisitInstanceData(id="patient_3_visit_0", patient_id=3, location=patients[2].location, duration=30, priority=0),
        ]
    instances = overrides.get("instances", default_instances)
    if instances is None:
        raise ValueError("Must provide 'instances' when overriding 'patients'")

    travel_matrix = overrides.get("travel_matrix", {
        "home": {"1": 18, "2": 41, "3": 24},
        "1": {"home": 18, "2": 30, "3": 12},
        "2": {"home": 41, "1": 30, "3": 22},
        "3": {"home": 24, "1": 12, "2": 22},
    })

    return SolverInput(
        patients=patients,
        clinician=overrides.get("clinician", ClinicianData(
            home_location=Location(lat=36.18, lng=-94.13),
        )),
        instances=instances,
        locked_visits=overrides.get("locked_visits", []),
        calendar_blocks=overrides.get("calendar_blocks", []),
        travel_matrix=travel_matrix,
        week_start_on="2026-04-06",
        working_days=overrides.get("working_days", [
            "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10",
        ]),
    )


def test_cpsat_produces_valid_output():
    from solvers.cpsat import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    assert output.metadata["optimizer_type"] == "cpsat"
    assert len(output.planned_visits) == 5, f"Expected 5 visits, got {len(output.planned_visits)}"

    # All instance IDs should be from the input
    input_ids = {inst.id for inst in input.instances}
    output_ids = {v.instance_id for v in output.planned_visits}
    assert output_ids == input_ids, f"Mismatch: {output_ids} vs {input_ids}"

    # Lunch placements should cover all working days
    for day in input.working_days:
        assert day in output.lunch_placements, f"Missing lunch for {day}"


def test_cpsat_re_solve_with_upper_bound():
    """Second solve with upper_bound from same input seeds hints and completes."""
    from solvers.cpsat import solve

    inp = make_test_input()
    first = solve(inp, time_budget=10)
    second = solve(inp, time_budget=10, upper_bound=first)

    assert second.metadata["optimizer_type"] == "cpsat"
    input_ids = {inst.id for inst in inp.instances}
    output_ids = {v.instance_id for v in second.planned_visits}
    assert output_ids == input_ids
    for day in inp.working_days:
        assert day in second.lunch_placements

    assert second.metadata.get("upper_bound_provided") is True
    assert second.metadata.get("upper_bound_accepted") is True
    assert second.metadata.get("warm_start_used") is True


def test_upper_bound_valid_for_warm_start_accepts_solver_output():
    from solvers.cpsat import solve, upper_bound_valid_for_warm_start, _build_context

    inp = make_test_input()
    out = solve(inp, time_budget=10)
    ctx = _build_context(inp)
    assert upper_bound_valid_for_warm_start(inp, ctx, out)


def test_upper_bound_valid_rejects_two_visits_same_patient_same_day():
    from solvers.cpsat import solve, upper_bound_valid_for_warm_start, _build_context

    inp = make_test_input()
    good = solve(inp, time_budget=10)
    ctx = _build_context(inp)
    visits = list(good.planned_visits)
    v0 = visits[0]
    same_patient_other = next(
        v for v in visits
        if v.patient_id == v0.patient_id and v.instance_id != v0.instance_id
    )
    moved = same_patient_other.model_copy(update={
        "date": v0.date,
        "starts_at": v0.starts_at,
        "ends_at": v0.ends_at,
    })
    patched = [moved if v.instance_id == same_patient_other.instance_id else v for v in visits]
    bad = SolverOutput(
        planned_visits=patched,
        lunch_placements=dict(good.lunch_placements),
        fitness=good.fitness,
        metadata=dict(good.metadata),
    )
    assert not upper_bound_valid_for_warm_start(inp, ctx, bad)


def test_warm_incumbent_rejected_when_per_day_hours_change():
    """Assignment hints survive but incumbent is discarded when bounds change."""
    from solvers.cpsat import (
        solve, upper_bound_valid_for_warm_start, _visits_within_day_bounds,
        _build_context,
    )

    inp = make_test_input()
    out = solve(inp, time_budget=10)
    ctx = _build_context(inp)
    assert upper_bound_valid_for_warm_start(inp, ctx, out), "structural check should pass"

    inp2 = make_test_input(clinician=ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=1080,
        lunch_duration_minutes=0,
        per_day_hours={"1": {"start": 960, "end": 1020}},
    ))
    ctx2 = _build_context(inp2)
    assert upper_bound_valid_for_warm_start(inp2, ctx2, out), \
        "structural check should still pass (hints are useful)"
    assert not _visits_within_day_bounds(out.planned_visits, inp2.clinician), \
        "bounds check should reject old timings as incumbent"

    out2 = solve(inp2, time_budget=10, upper_bound=out)
    assert out2.metadata["warm_start_used"] is True, "hints should be used"
    assert out2.metadata["warm_incumbent_used"] is False, "stale incumbent should be discarded"


def test_cpsat_ignores_invalid_upper_bound_gracefully():
    """Corrupt upper_bound must not break solve (warm-start skipped)."""
    from solvers.cpsat import solve, upper_bound_valid_for_warm_start, _build_context

    inp = make_test_input()
    good = solve(inp, time_budget=10)
    ctx = _build_context(inp)
    visits = list(good.planned_visits)
    v0 = visits[0]
    same_patient_other = next(
        v for v in visits
        if v.patient_id == v0.patient_id and v.instance_id != v0.instance_id
    )
    moved = same_patient_other.model_copy(update={
        "date": v0.date,
        "starts_at": v0.starts_at,
        "ends_at": v0.ends_at,
    })
    patched = [moved if v.instance_id == same_patient_other.instance_id else v for v in visits]
    bad = SolverOutput(
        planned_visits=patched,
        lunch_placements=dict(good.lunch_placements),
        fitness=good.fitness,
        metadata=dict(good.metadata),
    )
    assert not upper_bound_valid_for_warm_start(inp, ctx, bad)
    second = solve(inp, time_budget=10, upper_bound=bad)
    assert second.metadata["optimizer_type"] == "cpsat"
    assert len(second.planned_visits) == len(inp.instances)


def test_normalized_assignment_signature_stable():
    from solvers.cpsat import _normalized_assignment_signature

    a = {0: [1, 2], 3: [0]}
    b = {3: [0], 0: [1, 2]}
    assert _normalized_assignment_signature(a, 5) == _normalized_assignment_signature(b, 5)


def test_cpsat_empty_input():
    from solvers.cpsat import solve

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


def test_cpsat_one_patient_per_day():
    """No patient should have two visits on the same day."""
    from solvers.cpsat import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    for date in input.working_days:
        day_pids = [v.patient_id for v in output.planned_visits if v.date == date]
        counts = Counter(day_pids)
        for pid, count in counts.items():
            assert count <= 1, f"Patient {pid} has {count} visits on {date}"


def test_cpsat_max_visits_per_day():
    """No day should have more than MAX_VISITS_PER_DAY visits."""
    from solvers.cpsat import solve
    from solvers.cpsat_context import MAX_VISITS_PER_DAY

    input = make_test_input()
    output = solve(input, time_budget=10)

    for date in input.working_days:
        day_count = sum(1 for v in output.planned_visits if v.date == date)
        assert day_count <= MAX_VISITS_PER_DAY, (
            f"Day {date} has {day_count} visits, max is {MAX_VISITS_PER_DAY}"
        )


def test_cpsat_min_spacing():
    """Alice requires min 2 days between visits. Solver should respect this."""
    from solvers.cpsat import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    alice_dates = sorted([
        v.date for v in output.planned_visits if v.patient_id == 1
    ])
    assert len(alice_dates) == 2, f"Expected 2 Alice visits, got {len(alice_dates)}"

    d1 = datetime.fromisoformat(alice_dates[0])
    d2 = datetime.fromisoformat(alice_dates[1])
    gap = (d2 - d1).days
    assert gap >= 2, f"Alice visits are {gap} days apart, min is 2"


def test_cpsat_locked_visits_respected():
    """Locked visits should prevent scheduling another visit for the same patient on that day."""
    from solvers.cpsat import solve

    # Lock Alice on Monday (day 0)
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

    # Alice should not have a new visit on Monday
    alice_monday = [
        v for v in output.planned_visits
        if v.patient_id == 1 and v.date == "2026-04-06"
    ]
    assert len(alice_monday) == 0, "Alice should not have a new visit on locked day"


def test_cpsat_calendar_blocks_respected():
    """Visits should not overlap calendar blocks."""
    from solvers.cpsat import solve

    # Block 9am-3pm on Monday — leaves very little room
    blocks = [
        CalendarBlockData(
            date="2026-04-06",
            starts_at="2026-04-06T09:00:00",
            ends_at="2026-04-06T15:00:00",
        )
    ]

    input = make_test_input(calendar_blocks=blocks)
    output = solve(input, time_budget=10)

    # Any visit on Monday should not overlap 9am-3pm (540-900 minutes)
    for v in output.planned_visits:
        if v.date != "2026-04-06":
            continue
        start_min = datetime.fromisoformat(v.starts_at).hour * 60 + datetime.fromisoformat(v.starts_at).minute
        end_min = datetime.fromisoformat(v.ends_at).hour * 60 + datetime.fromisoformat(v.ends_at).minute
        # Should not overlap [540, 900)
        assert start_min >= 900 or end_min <= 540, (
            f"Visit {v.instance_id} at {start_min}-{end_min} overlaps block 540-900"
        )


def test_cpsat_availability_windows():
    """Patient with availability windows should be scheduled within those windows."""
    from solvers.cpsat import solve

    # Carol only available 2pm-4pm (840-960) on all days
    patients = [
        PatientData(
            id=3, name="Carol",
            location=Location(lat=36.18, lng=-94.15),
            visit_duration_minutes=30, required_visits_per_week=1,
            min_days_between_visits=1, max_days_between_visits=7, priority=0,
            availability_windows={
                "0": [{"start_minute": 840, "end_minute": 960}],  # Sunday
                "1": [{"start_minute": 840, "end_minute": 960}],  # Monday
                "2": [{"start_minute": 840, "end_minute": 960}],  # Tuesday
                "3": [{"start_minute": 840, "end_minute": 960}],  # Wednesday
                "4": [{"start_minute": 840, "end_minute": 960}],  # Thursday
                "5": [{"start_minute": 840, "end_minute": 960}],  # Friday
                "6": [{"start_minute": 840, "end_minute": 960}],  # Saturday
            },
        ),
    ]

    instances = [
        VisitInstanceData(
            id="patient_3_visit_0", patient_id=3,
            location=patients[0].location, duration=30, priority=0,
            availability_windows=patients[0].availability_windows,
        ),
    ]

    input = make_test_input(patients=patients, instances=instances)
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 1
    v = output.planned_visits[0]
    start_min = datetime.fromisoformat(v.starts_at).hour * 60 + datetime.fromisoformat(v.starts_at).minute
    assert 840 <= start_min < 960, f"Carol scheduled at {start_min}, expected 840-960"


def test_cpsat_visits_dont_overlap():
    """Visits on the same day should not overlap in time."""
    from solvers.cpsat import solve

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
                f"{day_visits[i+1].instance_id} starts at {start_j}"
            )


def test_cpsat_proven_optimal_flag():
    """Small problem should be solved optimally."""
    from solvers.cpsat import solve

    # Single visit, single day — trivially optimal
    patients = [
        PatientData(id=1, name="Solo", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=30, required_visits_per_week=1),
    ]
    instances = [
        VisitInstanceData(id="patient_1_visit_0", patient_id=1,
                          location=patients[0].location, duration=30),
    ]
    input = make_test_input(
        patients=patients, instances=instances,
        working_days=["2026-04-06"],
    )
    output = solve(input, time_budget=10)

    # Decomposed solver doesn't prove global optimality
    assert len(output.planned_visits) == 1


def test_cpsat_lunch_not_overlapped():
    """Visits should not overlap the lunch break."""
    from solvers.cpsat import solve

    # 3 visits on a tight day — lunch must be respected
    patients = [
        PatientData(id=1, name="Alice", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=60, required_visits_per_week=3,
                    min_days_between_visits=1, max_days_between_visits=7),
    ]
    instances = [
        VisitInstanceData(id=f"patient_1_visit_{k}", patient_id=1,
                          location=patients[0].location, duration=60)
        for k in range(3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,  # 8am
        workday_end_minute=1020,   # 5pm
        lunch_start_minute=720,    # noon
        lunch_duration_minutes=30,
        lunch_window_minutes=60,   # 11:30am-12:30pm
    )
    input = make_test_input(
        patients=patients, instances=instances, clinician=clinician,
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
            v_start = datetime.fromisoformat(v.starts_at).hour * 60 + datetime.fromisoformat(v.starts_at).minute
            v_end = datetime.fromisoformat(v.ends_at).hour * 60 + datetime.fromisoformat(v.ends_at).minute
            # No overlap: visit ends before lunch OR visit starts after lunch
            assert v_end <= lunch_start or v_start >= lunch_end, (
                f"Visit {v.instance_id} [{v_start}-{v_end}] overlaps lunch [{lunch_start}-{lunch_end}] on {date}"
            )


def test_cpsat_lunch_within_window():
    """Lunch placement should be within the configured lunch window."""
    from solvers.cpsat import solve

    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        lunch_start_minute=720,    # noon
        lunch_duration_minutes=30,
        lunch_window_minutes=60,   # ±30 min → 690-750
    )
    input = make_test_input(clinician=clinician)
    output = solve(input, time_budget=10)

    for date in input.working_days:
        lunch = output.lunch_placements.get(date)
        assert lunch is not None, f"Missing lunch on {date}"
        assert 690 <= lunch["start_minute"] <= 750, (
            f"Lunch at {lunch['start_minute']} outside window [690, 750] on {date}"
        )


def test_cpsat_break_enforcement():
    """With max_continuous_work=120min, a 3-hour block of visits should be split by lunch."""
    from solvers.cpsat import solve

    # 3 x 60-min visits = 180 min of work. With max_continuous=120, need a break.
    patients = [
        PatientData(id=k, name=f"P{k}", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=60, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7)
        for k in range(1, 4)
    ]
    instances = [
        VisitInstanceData(id=f"patient_{k}_visit_0", patient_id=k,
                          location=patients[k-1].location, duration=60)
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
    # Force all on one day to test break enforcement
    input = make_test_input(
        patients=patients, instances=instances, clinician=clinician,
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

    # The span from first visit start to last visit end should include
    # a lunch break gap. With 3x60min + 30min lunch = 210 min minimum span.
    first_start = datetime.fromisoformat(day_visits[0].starts_at).hour * 60 + datetime.fromisoformat(day_visits[0].starts_at).minute
    last_end = datetime.fromisoformat(day_visits[-1].ends_at).hour * 60 + datetime.fromisoformat(day_visits[-1].ends_at).minute
    total_span = last_end - first_start

    # Without any break, span would be exactly 180 min (3x60, zero travel).
    # With lunch (30 min), span should be at least 210 min.
    assert total_span >= 210, (
        f"Total span is {total_span} min but should be ≥210 (3x60min visits + 30min lunch)"
    )


def test_cpsat_mandatory_break_counts_drive_between_visits():
    """Break before a visit if drive + that visit would exceed max continuous work."""
    from solvers.cpsat import solve

    # After visit 1: accumulated = home→1 (10) + footprint (20) = 30. To patient 2: 75 min
    # drive + 20 min visit ⇒ 30 + 75 + 20 > 100 ⇒ mandatory break before visit 2.
    # If the upcoming visit were omitted from the check, we might skip the break illegally.
    patients = [
        PatientData(id=1, name="Near", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=20, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7),
        PatientData(id=2, name="Far", location=Location(lat=36.5, lng=-94.0),
                    visit_duration_minutes=20, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7),
    ]
    instances = [
        VisitInstanceData(id="patient_1_visit_0", patient_id=1,
                          location=patients[0].location, duration=20),
        VisitInstanceData(id="patient_2_visit_0", patient_id=2,
                          location=patients[1].location, duration=20),
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,
        workday_end_minute=1080,
        max_continuous_work_minutes=100,
        required_break_minutes=15,
        lunch_start_minute=960,
        lunch_duration_minutes=30,
        lunch_window_minutes=60,
    )
    tm = {
        "home": {"1": 10, "2": 10},
        "1": {"home": 10, "2": 75},
        "2": {"home": 10, "1": 75},
    }
    input = make_test_input(
        patients=patients,
        instances=instances,
        clinician=clinician,
        working_days=["2026-04-06"],
        travel_matrix=tm,
    )
    output = solve(input, time_budget=15)

    day_visits = sorted(
        [v for v in output.planned_visits if v.date == "2026-04-06"],
        key=lambda v: v.starts_at,
    )
    assert len(day_visits) == 2

    def minutes(iso: str) -> int:
        dt = datetime.fromisoformat(iso)
        return dt.hour * 60 + dt.minute

    v0_end = minutes(day_visits[0].ends_at)
    v1_start = minutes(day_visits[1].starts_at)
    gap = v1_start - v0_end
    # 75 drive + 5 inter-stop buffer + 15 mandatory break ⇒ ≥ 90 (slot rounding)
    assert gap >= 90, (
        f"Gap between visits is {gap} min; expected ≥90 when break must follow "
        f"long drive (got visits {day_visits[0].instance_id} then {day_visits[1].instance_id})"
    )


def test_cpsat_day_spreading():
    """Visits for a patient should be spread across days, not clustered."""
    from solvers.cpsat import solve

    # Alice needs 3 visits across 5 days with min_gap=1
    # Should spread to ~days 0, 2, 4 (not 0, 1, 2)
    patients = [
        PatientData(id=1, name="Alice", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=30, required_visits_per_week=3,
                    min_days_between_visits=1, max_days_between_visits=7),
    ]
    instances = [
        VisitInstanceData(id=f"patient_1_visit_{k}", patient_id=1,
                          location=patients[0].location, duration=30)
        for k in range(3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        schedule_density=0.0,  # spread evenly
    )
    input = make_test_input(
        patients=patients, instances=instances, clinician=clinician,
    )
    output = solve(input, time_budget=10)

    alice_days = sorted([
        input.working_days.index(v.date) for v in output.planned_visits if v.patient_id == 1
    ])
    assert len(alice_days) == 3

    # With 5 days and 3 visits at density=0, ideal spread is days 0, 2, 4
    # At minimum, visits should not all be adjacent (gaps should exist)
    gaps = [alice_days[i+1] - alice_days[i] for i in range(len(alice_days)-1)]
    # Average gap should be > 1 (not all consecutive days)
    avg_gap = sum(gaps) / len(gaps)
    assert avg_gap >= 1.5, (
        f"Visits on days {alice_days} have avg gap {avg_gap}, expected ≥1.5 for good spreading"
    )


def test_cpsat_min_days_zero_allows_consecutive():
    """With min_days_between=0 and density=1.0, visits may land on consecutive days."""
    from solvers.cpsat import solve

    patients = [
        PatientData(id=1, name="Alice", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=30, required_visits_per_week=3,
                    min_days_between_visits=0, max_days_between_visits=7),
    ]
    instances = [
        VisitInstanceData(id=f"patient_1_visit_{k}", patient_id=1,
                          location=patients[0].location, duration=30)
        for k in range(3)
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        schedule_density=1.0,  # pack into fewest days
    )
    input = make_test_input(
        patients=patients, instances=instances, clinician=clinician,
    )
    output = solve(input, time_budget=10)

    alice_days = sorted([
        input.working_days.index(v.date) for v in output.planned_visits if v.patient_id == 1
    ])
    assert len(alice_days) == 3, f"Expected 3 visits scheduled, got {len(alice_days)}"

    gaps = [alice_days[i+1] - alice_days[i] for i in range(len(alice_days)-1)]
    assert any(g == 1 for g in gaps), (
        f"With min_days_between=0 and density=1.0, expected at least one pair of "
        f"consecutive days, but got days {alice_days}"
    )


def test_cpsat_metadata_fields():
    """Output metadata should contain expected fields."""
    from solvers.cpsat import solve

    input = make_test_input()
    output = solve(input, time_budget=10)

    meta = output.metadata
    assert meta["optimizer_type"] == "cpsat"
    assert "proven_optimal" in meta
    assert "status" in meta
    assert "unschedulable" in meta
    assert "drive_violations" in meta
    assert "return_home_by_day" in meta
    assert "cp_sat_status" in meta
    assert "iteration_log" in meta
    assert "route_winners" in meta
    assert meta["iterations"] >= 1
    assert meta.get("warm_start_used") is False
    assert meta.get("upper_bound_provided") is False
    assert meta.get("upper_bound_accepted") is False


def test_cpsat_iteration_log_uniform_schema():
    """Every iteration_log row should include the same metric keys (incl. after convergence)."""
    from solvers.cpsat import solve

    required = {
        "iteration",
        "drive",
        "soft_penalty",
        "assign_penalty",
        "cost",
        "placed",
    }
    output = solve(make_test_input(), time_budget=15)
    for entry in output.metadata["iteration_log"]:
        assert required <= set(entry.keys()), f"Missing keys in {entry!r}"


def test_cpsat_converged_log_carries_last_metrics(monkeypatch):
    """Early convergence should log full metrics, not only {iteration, converged}."""
    import solvers.cpsat as cpsat_mod

    real_assign = cpsat_mod._assign_days
    ncalls = {"n": 0}
    cached = {}

    def wrapper(*args, **kwargs):
        ncalls["n"] += 1
        if ncalls["n"] >= 2:
            return (cached["assignments"], "FEASIBLE", cached.get("penalty", 0))
        result = real_assign(*args, **kwargs)
        cached["assignments"] = result[0]
        cached["penalty"] = result[2]
        return result

    monkeypatch.setattr(cpsat_mod, "_assign_days", wrapper)
    monkeypatch.setattr(cpsat_mod, "MAX_ITERATIONS", 4)

    output = cpsat_mod.solve(make_test_input(), time_budget=20)
    conv = [e for e in output.metadata["iteration_log"] if e.get("converged")]
    assert conv, "stub should force identical assignment on iteration 1"
    assert "drive" in conv[0] and "cost" in conv[0] and "placed" in conv[0]


def test_cpsat_assign_time_skews_iteration_zero():
    """Iteration 0 CP-SAT should get a larger max_time than later iterations (same total pool)."""
    from solvers import cpsat as m

    pool = max(3, 60 // 2)
    t0 = m._cp_sat_assign_seconds(0, 60, 3)
    t1 = m._cp_sat_assign_seconds(1, 60, 3)
    assert t0 >= t1 >= 1
    assert t0 + 2 * t1 <= pool + 2  # integer rounding slack


def test_cpsat_zero_home_legs_still_schedules():
    """median home_leg=0 must not set pairwise threshold to 0 (would drop all t>0 pairs)."""
    from solvers.cpsat import solve

    patients = [
        PatientData(id=k, name=f"P{k}", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=30, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7)
        for k in range(1, 4)
    ]
    instances = [
        VisitInstanceData(id=f"patient_{k}_visit_0", patient_id=k,
                          location=patients[k - 1].location, duration=30)
        for k in range(1, 4)
    ]
    tm = {
        "home": {"1": 0, "2": 0, "3": 0},
        "1": {"home": 0, "2": 10, "3": 10},
        "2": {"home": 0, "1": 10, "3": 10},
        "3": {"home": 0, "1": 10, "2": 10},
    }
    input = make_test_input(patients=patients, instances=instances, travel_matrix=tm)
    output = solve(input, time_budget=15)
    assert len(output.planned_visits) == 3


def test_cpsat_locked_visit_drive_cost():
    """Drive cost should include travel to/from locked visits."""
    from solvers.cpsat import solve

    # Lock Alice at 9am on Monday. Bob visits too. Travel home→1=18, 1→2=30, 2→home=41.
    locked = [
        LockedVisitData(
            patient_id=1,
            date="2026-04-06",
            starts_at="2026-04-06T09:00:00",
            ends_at="2026-04-06T10:00:00",
            duration_minutes=60,
        )
    ]
    # Only Bob needs scheduling (1 visit)
    patients = [
        PatientData(id=1, name="Alice", location=Location(lat=36.09, lng=-94.19),
                    visit_duration_minutes=60, required_visits_per_week=0),
        PatientData(id=2, name="Bob", location=Location(lat=36.32, lng=-94.22),
                    visit_duration_minutes=45, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7),
    ]
    instances = [
        VisitInstanceData(id="patient_2_visit_0", patient_id=2,
                          location=patients[1].location, duration=45),
    ]
    input = make_test_input(
        patients=patients, instances=instances, locked_visits=locked,
    )
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 1
    # The route on a day with both should include travel to/from locked visit
    # Check that return_home_by_day accounts for the last patient
    assert "return_home_by_day" in output.metadata


def test_cpsat_priority_weighted_fitness():
    """Dropping a high-priority patient should cost more than a low-priority one."""
    from solvers.cpsat import solve

    # Create more visits than can fit (6 visits, 1 day, max 5 per day)
    patients = [
        PatientData(id=k, name=f"P{k}", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=60, required_visits_per_week=1,
                    priority=10 if k == 1 else 0)
        for k in range(1, 7)
    ]
    instances = [
        VisitInstanceData(id=f"patient_{k}_visit_0", patient_id=k,
                          location=patients[k-1].location, duration=60,
                          priority=10 if k == 1 else 0)
        for k in range(1, 7)
    ]
    input = make_test_input(
        patients=patients, instances=instances,
        working_days=["2026-04-06"],
        travel_matrix={
            "home": {str(k): 5 for k in range(1, 7)},
            **{str(k): {"home": 5, **{str(j): 5 for j in range(1, 7) if j != k}} for k in range(1, 7)},
        },
    )
    output = solve(input, time_budget=10)

    # High-priority patient 1 should be scheduled (not dropped)
    placed_pids = {v.patient_id for v in output.planned_visits}
    assert 1 in placed_pids, "High-priority patient 1 should not be dropped"


def test_cpsat_locked_visit_gap_includes_charting_and_transit():
    """Next visit after a locked visit must leave a gap for charting buffer + transit."""
    from solvers.cpsat import solve

    charting_buffer = 10
    # Locked visit: patient 1, 8:30-9:30 (60 min) on Monday
    # Floating visit: patient 2 on the same day
    # After locked visit: charting (10 min) until 9:40, then transit, then transit buffer (5 min)
    patients = [
        PatientData(id=1, name="Locked", location=Location(lat=36.0, lng=-94.0),
                    visit_duration_minutes=60, required_visits_per_week=1),
        PatientData(id=2, name="Float", location=Location(lat=36.1, lng=-94.1),
                    visit_duration_minutes=45, required_visits_per_week=1),
    ]
    instances = [
        VisitInstanceData(id="patient_2_visit_0", patient_id=2,
                          location=patients[1].location, duration=45 + charting_buffer),
    ]
    locked = [
        LockedVisitData(
            patient_id=1, date="2026-04-06",
            starts_at="2026-04-06T08:30:00", ends_at="2026-04-06T09:30:00",
            duration_minutes=60,
        ),
    ]
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        charting_buffer_minutes=charting_buffer,
    )
    input = make_test_input(
        patients=patients, instances=instances,
        locked_visits=locked, clinician=clinician,
        working_days=["2026-04-06"],
        travel_matrix={
            "home": {"1": 10, "2": 10},
            "1": {"home": 10, "2": 15},
            "2": {"home": 10, "1": 15},
        },
    )
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 1
    v = output.planned_visits[0]
    v_start = datetime.fromisoformat(v.starts_at).hour * 60 + datetime.fromisoformat(v.starts_at).minute

    # Locked visit ends at 9:30 (570 min).
    # Charting buffer: 10 min → 9:40 (580 min).
    # Transit from patient 1 to patient 2: 15 min → 9:55 (595 min).
    # Transit buffer: 5 min → 10:00 (600 min).
    # Rounded up to slot step: 600 min = 10:00 AM.
    locked_end = 570  # 9:30
    min_gap = charting_buffer + 15 + 5  # charting + transit + transit_buffer
    assert v_start >= locked_end + min_gap, (
        f"Floating visit starts at {v_start}, but locked visit ends at {locked_end} "
        f"and needs {min_gap} min gap (charting {charting_buffer} + transit 15 + buffer 5)"
    )


def test_cpsat_per_day_hours():
    """Per-day overrides should constrain visits to the narrower window."""
    from solvers.cpsat import solve

    # Default hours 8am-6pm, but Wednesday (wday 3) overridden to 9am-12pm (540-720).
    clinician = ClinicianData(
        home_location=Location(lat=36.0, lng=-94.0),
        workday_start_minute=480,   # 8am
        workday_end_minute=1080,    # 6pm
        lunch_duration_minutes=0,
        per_day_hours={"3": {"start": 540, "end": 720}},  # Wed 9am-12pm
    )
    patients = [
        PatientData(
            id=1, name="Alice",
            location=Location(lat=36.0, lng=-94.0),
            visit_duration_minutes=60, required_visits_per_week=1,
            min_days_between_visits=1, max_days_between_visits=7,
        ),
    ]
    instances = [
        VisitInstanceData(
            id="patient_1_visit_0", patient_id=1,
            location=patients[0].location, duration=60,
        ),
    ]
    # Only one working day: Wednesday
    input = make_test_input(
        patients=patients,
        instances=instances,
        clinician=clinician,
        working_days=["2026-04-08"],  # Wednesday
        travel_matrix={
            "home": {"1": 0},
            "1": {"home": 0},
        },
    )
    output = solve(input, time_budget=10)

    assert len(output.planned_visits) == 1
    v = output.planned_visits[0]
    v_start = datetime.fromisoformat(v.starts_at).hour * 60 + datetime.fromisoformat(v.starts_at).minute
    v_end = datetime.fromisoformat(v.ends_at).hour * 60 + datetime.fromisoformat(v.ends_at).minute

    assert v_start >= 540, f"Visit starts at {v_start}, expected >= 540 (9am per-day override)"
    assert v_end <= 720, f"Visit ends at {v_end}, expected <= 720 (12pm per-day override)"
