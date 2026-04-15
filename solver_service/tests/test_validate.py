"""Unit tests for validate_plan — the independent feasibility verifier.

One positive + one negative case per rule.  These tests construct
SolverOutput objects by hand rather than running the solver, so they
check the validator's logic in isolation.

Rule vocabulary (as of commit 06c841e):
  unknown_instance, bad_clinician_idx, date_out_of_horizon,
  duplicate_instance, eligibility, one_patient_per_day, day_capacity,
  min_spacing, day_bounds, availability_missing_weekday,
  availability_window, calendar_block_overlap, locked_overlap
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models import (  # noqa: E402
    CalendarBlockData,
    LockedVisitData,
    PlannedVisit,
    SolverInput,
    SolverOutput,
)
from solver.benders import ValidationError, validate_plan  # noqa: E402
from tests.helpers import (  # noqa: E402
    WORKING_DAYS_WEEK,
    make_input,
    mk_clinician,
    mk_instance,
    mk_patient,
)


# ── Helpers for building test outputs ───────────────────────────────


def _mk_visit(
    instance_id: str,
    patient_id: int,
    clinician_idx: int,
    date: str,
    start_min: int,
    duration: int,
) -> PlannedVisit:
    def _t(m: int) -> str:
        return f"{date}T{m // 60:02d}:{m % 60:02d}:00"
    return PlannedVisit(
        instance_id=instance_id,
        patient_id=patient_id,
        clinician_idx=clinician_idx,
        date=date,
        starts_at=_t(start_min),
        ends_at=_t(start_min + duration),
    )


def _mk_output(visits: list[PlannedVisit]) -> SolverOutput:
    return SolverOutput(
        planned_visits=visits,
        lunch_placements={d: {} for d in WORKING_DAYS_WEEK},
        fitness=0.0,
        metadata={},
    )


def _expect_rule(inp: SolverInput, out: SolverOutput, rule: str) -> None:
    """Assert validate_plan raises ValidationError with the given rule."""
    with pytest.raises(ValidationError) as exc_info:
        validate_plan(out, inp)
    assert exc_info.value.rule == rule, (
        f"expected rule {rule!r}, got {exc_info.value.rule!r}: {exc_info.value.detail}"
    )


# ── Minimal valid baseline ──────────────────────────────────────────


def _baseline() -> tuple[SolverInput, list[PlannedVisit]]:
    """A 2-patient input with a matching legal plan."""
    patients = [mk_patient(1, "A", dur=60), mk_patient(2, "B", dur=45)]
    instances = [mk_instance("p1_v0", 1, dur=60), mk_instance("p2_v0", 2, dur=45)]
    inp = make_input(patients, instances)
    visits = [
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 540, 60),
        _mk_visit("p2_v0", 2, 0, WORKING_DAYS_WEEK[2], 540, 45),
    ]
    return inp, visits


def test_validate_passes_on_legal_plan():
    """Baseline: a hand-crafted legal plan passes validate_plan cleanly."""
    inp, visits = _baseline()
    out = _mk_output(visits)
    validate_plan(out, inp)  # should not raise


# ── Rule: unknown_instance ──────────────────────────────────────────


def test_validate_rejects_unknown_instance():
    inp, _ = _baseline()
    bad = [_mk_visit("ghost_v0", 99, 0, WORKING_DAYS_WEEK[0], 540, 30)]
    _expect_rule(inp, _mk_output(bad), "unknown_instance")


# ── Rule: bad_clinician_idx ─────────────────────────────────────────


def test_validate_rejects_bad_clinician_idx():
    inp, _ = _baseline()
    bad = [_mk_visit("p1_v0", 1, 99, WORKING_DAYS_WEEK[0], 540, 60)]
    _expect_rule(inp, _mk_output(bad), "bad_clinician_idx")


# ── Rule: date_out_of_horizon ───────────────────────────────────────


def test_validate_rejects_date_outside_horizon():
    inp, _ = _baseline()
    bad = [_mk_visit("p1_v0", 1, 0, "2030-01-01", 540, 60)]
    _expect_rule(inp, _mk_output(bad), "date_out_of_horizon")


# ── Rule: duplicate_instance ────────────────────────────────────────


def test_validate_rejects_duplicate_instance():
    inp, visits = _baseline()
    # Schedule the same instance twice
    visits = [
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 540, 60),
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[2], 540, 60),
    ]
    _expect_rule(inp, _mk_output(visits), "duplicate_instance")


# ── Rule: eligibility ───────────────────────────────────────────────


def test_validate_rejects_visit_on_ineligible_clinician():
    patients = [mk_patient(1, "A", dur=30)]
    # Instance eligible only for clinician 0
    instances = [mk_instance("p1_v0", 1, dur=30, eligible=[0])]
    from models import ClinicianData
    inp = make_input(patients, instances, clinicians=[ClinicianData(), ClinicianData()])
    # But the plan assigns it to clinician 1
    bad = [_mk_visit("p1_v0", 1, 1, WORKING_DAYS_WEEK[0], 540, 30)]
    _expect_rule(inp, _mk_output(bad), "eligibility")


# ── Rule: one_patient_per_day ───────────────────────────────────────


def test_validate_rejects_same_patient_twice_on_same_day():
    # Patient 1 with two instances both scheduled Monday
    patients = [mk_patient(1, "A", dur=30, req=2)]
    instances = [
        mk_instance("p1_v0", 1, dur=30),
        mk_instance("p1_v1", 1, dur=30),
    ]
    inp = make_input(patients, instances)
    bad = [
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 540, 30),
        _mk_visit("p1_v1", 1, 0, WORKING_DAYS_WEEK[0], 600, 30),
    ]
    _expect_rule(inp, _mk_output(bad), "one_patient_per_day")


# ── Rule: day_capacity ──────────────────────────────────────────────


def test_validate_rejects_day_capacity_overflow():
    # Clinician with max_visits_per_day=2, but plan puts 3 on same day
    patients = [mk_patient(pid, f"P{pid}", dur=30) for pid in range(1, 4)]
    instances = [mk_instance(f"p{pid}_v0", pid, dur=30) for pid in range(1, 4)]
    inp = make_input(patients, instances, clinicians=[mk_clinician(max_visits_per_day=2)])
    bad = [
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 540, 30),
        _mk_visit("p2_v0", 2, 0, WORKING_DAYS_WEEK[0], 600, 30),
        _mk_visit("p3_v0", 3, 0, WORKING_DAYS_WEEK[0], 660, 30),
    ]
    _expect_rule(inp, _mk_output(bad), "day_capacity")


# ── Rule: min_spacing ───────────────────────────────────────────────


def test_validate_rejects_min_spacing_violation():
    # min_gap=1 → gap must be strictly > 1 → Mon+Tue forbidden
    patients = [mk_patient(1, "A", dur=30, req=2, min_gap=1)]
    instances = [mk_instance("p1_v0", 1, dur=30), mk_instance("p1_v1", 1, dur=30)]
    inp = make_input(patients, instances)
    bad = [
        _mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 540, 30),  # Mon
        _mk_visit("p1_v1", 1, 0, WORKING_DAYS_WEEK[1], 540, 30),  # Tue
    ]
    _expect_rule(inp, _mk_output(bad), "min_spacing")


# ── Rule: day_bounds ────────────────────────────────────────────────


def test_validate_rejects_visit_before_workday_start():
    patients = [mk_patient(1, "A", dur=30)]
    instances = [mk_instance("p1_v0", 1, dur=30)]
    # Clinician 8am-6pm
    inp = make_input(patients, instances, clinicians=[mk_clinician(
        workday_start_minute=480, workday_end_minute=1080,
    )])
    # Plan schedules 7:00am — before workday
    bad = [_mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 420, 30)]
    _expect_rule(inp, _mk_output(bad), "day_bounds")


def test_validate_rejects_visit_past_workday_end():
    patients = [mk_patient(1, "A", dur=30)]
    instances = [mk_instance("p1_v0", 1, dur=30)]
    inp = make_input(patients, instances, clinicians=[mk_clinician(
        workday_start_minute=480, workday_end_minute=1080,
    )])
    # Plan schedules 18:30 with 30min duration — extends past 6pm
    bad = [_mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 1110, 30)]
    _expect_rule(inp, _mk_output(bad), "day_bounds")


# ── Rule: availability_missing_weekday ─────────────────────────────


def test_validate_rejects_visit_on_weekday_without_availability():
    # Patient available only Monday ("1"); plan schedules Tuesday
    patients = [mk_patient(1, "MondayOnly", dur=30)]
    mon_only = {"1": [{"start_minute": 540, "end_minute": 1020}]}
    instances = [mk_instance("p1_v0", 1, dur=30, windows=mon_only)]
    inp = make_input(patients, instances)
    bad = [_mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[1], 540, 30)]  # Tuesday
    _expect_rule(inp, _mk_output(bad), "availability_missing_weekday")


# ── Rule: availability_window ──────────────────────────────────────


def test_validate_rejects_visit_outside_declared_window():
    # Patient has window 9-11am on every weekday, plan schedules at 14:00
    patients = [mk_patient(1, "MorningOnly", dur=60)]
    am_only = {
        str(wd): [{"start_minute": 540, "end_minute": 660}]  # 9-11am
        for wd in range(1, 6)
    }
    instances = [mk_instance("p1_v0", 1, dur=60, windows=am_only)]
    inp = make_input(patients, instances)
    bad = [_mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 840, 60)]  # 14:00
    _expect_rule(inp, _mk_output(bad), "availability_window")


# ── Rule: calendar_block_overlap ───────────────────────────────────


def test_validate_rejects_visit_overlapping_calendar_block():
    patients = [mk_patient(1, "A", dur=60)]
    instances = [mk_instance("p1_v0", 1, dur=60)]
    # Block 9-10am on Monday
    blocks = [CalendarBlockData(
        clinician_idx=0, date=WORKING_DAYS_WEEK[0],
        starts_at=f"{WORKING_DAYS_WEEK[0]}T09:00:00",
        ends_at=f"{WORKING_DAYS_WEEK[0]}T10:00:00",
    )]
    inp = make_input(patients, instances, calendar_blocks=blocks)
    # Visit at 9:30-10:30 overlaps the block
    bad = [_mk_visit("p1_v0", 1, 0, WORKING_DAYS_WEEK[0], 570, 60)]
    _expect_rule(inp, _mk_output(bad), "calendar_block_overlap")


# ── Rule: locked_overlap ────────────────────────────────────────────


def test_validate_rejects_visit_overlapping_locked_visit():
    patients = [
        mk_patient(1, "Fixed", dur=60),
        mk_patient(2, "Free", dur=30),
    ]
    instances = [mk_instance("p2_v0", 2, dur=30)]  # Only free patient is scheduled
    locked = [LockedVisitData(
        patient_id=1, clinician_idx=0, date=WORKING_DAYS_WEEK[0],
        starts_at=f"{WORKING_DAYS_WEEK[0]}T10:00:00",
        ends_at=f"{WORKING_DAYS_WEEK[0]}T11:00:00",
        duration_minutes=60,
    )]
    inp = make_input(patients, instances, locked_visits=locked)
    # Plan schedules free patient at 10:30-11:00 — overlaps the locked visit
    bad = [_mk_visit("p2_v0", 2, 0, WORKING_DAYS_WEEK[0], 630, 30)]
    _expect_rule(inp, _mk_output(bad), "locked_overlap")
