"""Benders solver acceptance tests.

Stresses the properties the architecture promises:
  - Feasibility: validate_plan passes on every output
  - Multi-window availability: patient with two disjoint windows on one
    day is scheduled within one of them, not between
  - Locked visit anchoring: locked visit stays at its exact time
  - Tight spacing: min_days_between_visits is strictly respected
  - Multi-clinician + eligibility: instances route to eligible clinician only
  - Convergence: benchmark scenarios terminate in ≤5 Benders rounds
"""

from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models import (  # noqa: E402
    CalendarBlockData,
    ClinicianData,
    LockedVisitData,
    PatientData,
    SolverInput,
    VisitInstanceData,
)
from solver.benders import solve, validate_plan, ValidationError  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────


WORKING_DAYS_WEEK = [
    "2026-04-20",  # Mon
    "2026-04-21",
    "2026-04-22",
    "2026-04-23",
    "2026-04-24",  # Fri
]


def _make_input(
    patients,
    instances,
    clinicians=None,
    matrix=None,
    working_days=WORKING_DAYS_WEEK,
    locked_visits=None,
    calendar_blocks=None,
) -> SolverInput:
    if clinicians is None:
        clinicians = [ClinicianData()]
    if matrix is None:
        # Build a symmetric matrix from patient/home stubs
        ids = [str(p.id) for p in patients]
        home_keys = [f"home_{i}" for i in range(len(clinicians))]
        matrix = {}
        for k in home_keys + ids:
            matrix[k] = {}
            for k2 in home_keys + ids:
                if k == k2:
                    matrix[k][k2] = 0
                else:
                    matrix[k][k2] = 20
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


def _mk_pat(pid, name="P", dur=60, req=1, min_gap=1, max_gap=7, priority=0):
    return PatientData(
        id=pid,
        name=name,
        visit_duration_minutes=dur,
        required_visits=req,
        min_days_between_visits=min_gap,
        max_days_between_visits=max_gap,
        priority=priority,
    )


def _mk_inst(iid, pid, dur=60, eligible=None, windows=None):
    return VisitInstanceData(
        id=iid,
        patient_id=pid,
        duration=dur,
        eligible_clinician_indices=eligible or [],
        availability_windows=windows or {},
    )


# ── Property: validate_plan passes on all test outputs ─────────────


def _assert_valid(out, inp):
    # Raises if invalid — fail the test with the rule name
    validate_plan(out, inp)
    assert out.metadata.get("validated") is True


# ── Small scenarios mirroring benchmark.py ─────────────────────────


def test_small_scenario_converges_and_validates():
    patients = [
        _mk_pat(1, "Alice", dur=60, req=2, min_gap=2, max_gap=5),
        _mk_pat(2, "Bob", dur=45, req=2, min_gap=1, max_gap=7, priority=5),
        _mk_pat(3, "Carol", dur=30, req=1),
    ]
    instances = [
        _mk_inst("p1_v0", 1, 60),
        _mk_inst("p1_v1", 1, 60),
        _mk_inst("p2_v0", 2, 45),
        _mk_inst("p2_v1", 2, 45),
        _mk_inst("p3_v0", 3, 30),
    ]
    matrix = {
        "home_0": {"1": 18, "2": 41, "3": 24},
        "1": {"home_0": 18, "2": 30, "3": 12},
        "2": {"home_0": 41, "1": 30, "3": 22},
        "3": {"home_0": 24, "1": 12, "2": 22},
    }
    inp = _make_input(patients, instances, matrix=matrix)
    out = solve(inp, time_budget=10)
    assert out.metadata["status"] == "FEASIBLE"
    assert out.metadata["placed"] == 5
    assert out.metadata["benders_rounds"] <= 5
    _assert_valid(out, inp)


def test_medium_scenario_converges_and_validates():
    random.seed(42)
    patients = []
    instances = []
    # Cap required visits at 2 per patient so min_gap=1 (→ ≥2 day gap) fits
    # in a 5-day horizon without over-constraining.
    for pid in range(1, 9):
        req = random.choice([1, 2, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            _mk_pat(pid, f"P{pid}", dur=dur, req=req, min_gap=1, max_gap=7)
        )
        for v in range(req):
            instances.append(_mk_inst(f"p{pid}_v{v}", pid, dur))
    # Simple matrix: all pairwise 25, homes 15
    ids = [str(p.id) for p in patients]
    matrix = {"home_0": {i: 15 for i in ids}}
    for i in ids:
        matrix[i] = {j: (0 if i == j else 25) for j in ids}
        matrix[i]["home_0"] = 15
    inp = _make_input(patients, instances, matrix=matrix)
    out = solve(inp, time_budget=20)
    assert out.metadata["placed"] == len(instances), out.metadata
    assert out.metadata["benders_rounds"] <= 5
    _assert_valid(out, inp)


# ── Adversarial: multi-window availability ─────────────────────────


def test_multi_window_availability_exact():
    """Patient available 9-11 AM OR 2-4 PM on Monday only.  Must schedule
    inside one of those windows, not between."""
    patients = [_mk_pat(1, "Carol", dur=45, req=1)]
    instances = [
        _mk_inst(
            "p1_v0", 1, 45,
            windows={
                "1": [
                    {"start_minute": 540, "end_minute": 660},
                    {"start_minute": 840, "end_minute": 960},
                ]
            },
        )
    ]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 1
    _assert_valid(out, inp)

    v = out.planned_visits[0]
    assert v.date == "2026-04-20"  # Monday
    start_m = int(v.starts_at[-8:-6]) * 60 + int(v.starts_at[-5:-3])
    end_m = int(v.ends_at[-8:-6]) * 60 + int(v.ends_at[-5:-3])
    in_window_1 = 540 <= start_m and end_m <= 660
    in_window_2 = 840 <= start_m and end_m <= 960
    assert in_window_1 or in_window_2, f"visit [{start_m},{end_m}) fell between windows"


def test_forbidden_weekday():
    """Patient available only Monday; other days must be refused."""
    patients = [_mk_pat(1, "MondayOnly", dur=60, req=1)]
    instances = [
        _mk_inst("p1_v0", 1, 60,
                 windows={"1": [{"start_minute": 540, "end_minute": 1020}]})
    ]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.planned_visits[0].date == "2026-04-20"
    _assert_valid(out, inp)


# ── Adversarial: locked-visit anchoring ────────────────────────────


def test_locked_visit_stays_anchored():
    patients = [
        _mk_pat(1, "Fixed", dur=60, req=1),
        _mk_pat(2, "Free", dur=60, req=1),
    ]
    instances = [_mk_inst("p2_v0", 2, 60)]
    locked = [
        LockedVisitData(
            patient_id=1,
            clinician_idx=0,
            date="2026-04-22",
            starts_at="2026-04-22T10:30:00",
            ends_at="2026-04-22T11:30:00",
            duration_minutes=60,
        )
    ]
    inp = _make_input(patients, instances, locked_visits=locked)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 1
    _assert_valid(out, inp)
    # Verify the locked visit is respected: no planned visit on Wed overlaps 10:30-11:30
    for v in out.planned_visits:
        if v.date != "2026-04-22":
            continue
        start_m = int(v.starts_at[-8:-6]) * 60 + int(v.starts_at[-5:-3])
        end_m = int(v.ends_at[-8:-6]) * 60 + int(v.ends_at[-5:-3])
        assert not (start_m < 690 and end_m > 630), "overlapped locked"


# ── Adversarial: tight spacing ─────────────────────────────────────


def test_min_spacing_strict():
    # req=2, min_gap=2 → need gap > 2 (≥3) between the two visits.
    # 5-day horizon: only (Mon, Thu), (Mon, Fri), (Tue, Fri) fit.
    patients = [_mk_pat(1, "Spaced", dur=60, req=2, min_gap=2, max_gap=7)]
    instances = [_mk_inst(f"p1_v{i}", 1, 60) for i in range(2)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=10)
    assert out.metadata["placed"] == 2
    _assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    for a, b in zip(ords, ords[1:]):
        assert (b - a) > 2, f"spacing {b-a} days violates min_gap=2"


# ── Multi-clinician with eligibility ───────────────────────────────


def test_multi_clinician_eligibility():
    patients = [
        _mk_pat(1, "ForClin0", dur=60, req=2, min_gap=2),
        _mk_pat(2, "ForClin1", dur=60, req=2, min_gap=2),
        _mk_pat(3, "Either", dur=30, req=1),
    ]
    instances = [
        _mk_inst("p1_v0", 1, 60, eligible=[0]),
        _mk_inst("p1_v1", 1, 60, eligible=[0]),
        _mk_inst("p2_v0", 2, 60, eligible=[1]),
        _mk_inst("p2_v1", 2, 60, eligible=[1]),
        _mk_inst("p3_v0", 3, 30, eligible=[0, 1]),
    ]
    clinicians = [ClinicianData(), ClinicianData()]
    matrix = {
        "home_0": {"1": 15, "2": 60, "3": 25},
        "home_1": {"1": 60, "2": 15, "3": 25},
        "1": {"home_0": 15, "home_1": 60, "2": 40, "3": 20},
        "2": {"home_0": 60, "home_1": 15, "1": 40, "3": 20},
        "3": {"home_0": 25, "home_1": 25, "1": 20, "2": 20},
    }
    inp = _make_input(patients, instances, clinicians=clinicians, matrix=matrix)
    out = solve(inp, time_budget=10)
    assert out.metadata["placed"] == 5
    _assert_valid(out, inp)

    for v in out.planned_visits:
        inst = next(i for i in instances if i.id == v.instance_id)
        if inst.eligible_clinician_indices:
            assert v.clinician_idx in inst.eligible_clinician_indices


def test_multi_clinician_load_balance_prefers_spread():
    """3 patients fully eligible for 2 clinicians — envelope should spread them."""
    patients = [_mk_pat(i, f"P{i}", dur=30, req=1) for i in range(1, 4)]
    instances = [_mk_inst(f"p{i}_v0", i, 30) for i in range(1, 4)]
    clinicians = [ClinicianData(), ClinicianData()]
    matrix = {
        "home_0": {"1": 10, "2": 10, "3": 10},
        "home_1": {"1": 10, "2": 10, "3": 10},
        "1": {"home_0": 10, "home_1": 10, "2": 15, "3": 15},
        "2": {"home_0": 10, "home_1": 10, "1": 15, "3": 15},
        "3": {"home_0": 10, "home_1": 10, "1": 15, "2": 15},
    }
    inp = _make_input(patients, instances, clinicians=clinicians, matrix=matrix)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 3
    _assert_valid(out, inp)
    per_clin = {0: 0, 1: 0}
    for v in out.planned_visits:
        per_clin[v.clinician_idx] += 1
    assert max(per_clin.values()) - min(per_clin.values()) <= 1, (
        f"load imbalance: {per_clin}"
    )


# ── Conflict → cut → converge ──────────────────────────────────────


def test_conflict_generates_cuts_and_converges():
    """Three 60-min patients all with only the 9-11 window on any weekday —
    at most 1 fits per day because 2×60min service + travel > 120min.
    Forces the envelope to spread them across different days via cuts.

    Weak cuts take several rounds to enumerate the infeasible pairs.
    The spike contract says we log but don't fail on slow convergence —
    this test asserts the loop terminates with a valid plan, not speed.
    """
    patients = [_mk_pat(i, f"P{i}", dur=60, req=1) for i in range(1, 4)]
    instances = [
        _mk_inst(
            f"p{i}_v0", i, 60,
            windows={str(wd): [{"start_minute": 540, "end_minute": 660}]
                     for wd in range(1, 6)},
        )
        for i in range(1, 4)
    ]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=15)
    assert out.metadata["placed"] == 3
    # Loop must terminate with cuts actually being generated
    assert out.metadata["cuts_generated"] >= 1
    _assert_valid(out, inp)


# ── Calendar block exclusion ───────────────────────────────────────


def test_calendar_block_respected():
    patients = [_mk_pat(1, "P", dur=60, req=1)]
    instances = [_mk_inst("p1_v0", 1, 60)]
    # Block all of Monday
    blocks = [
        CalendarBlockData(
            clinician_idx=0,
            date="2026-04-20",
            starts_at="2026-04-20T08:00:00",
            ends_at="2026-04-20T18:00:00",
        )
    ]
    inp = _make_input(patients, instances, calendar_blocks=blocks)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 1
    _assert_valid(out, inp)
    assert out.planned_visits[0].date != "2026-04-20"


# ── Convergence benchmark ──────────────────────────────────────────


def test_convergence_ceiling_on_heavy_scenario():
    """~12 patients, ~25 instances — mirrors benchmark.make_scenario_heavy."""
    random.seed(123)
    patients = []
    instances = []
    for pid in range(1, 13):
        req = random.choice([1, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            _mk_pat(pid, f"P{pid}", dur=dur, req=req, min_gap=1, max_gap=7)
        )
        for v in range(req):
            instances.append(_mk_inst(f"p{pid}_v{v}", pid, dur))

    ids = [str(p.id) for p in patients]
    matrix = {"home_0": {i: 18 for i in ids}}
    for i in ids:
        matrix[i] = {j: (0 if i == j else 22) for j in ids}
        matrix[i]["home_0"] = 18

    inp = _make_input(patients, instances, matrix=matrix)
    out = solve(inp, time_budget=30)
    assert out.metadata["placed"] >= int(0.9 * len(instances)), (
        f"placed {out.metadata['placed']}/{len(instances)}"
    )
    # Convergence target from spike contract
    assert out.metadata["benders_rounds"] <= 5, (
        f"took {out.metadata['benders_rounds']} rounds"
    )
    _assert_valid(out, inp)
