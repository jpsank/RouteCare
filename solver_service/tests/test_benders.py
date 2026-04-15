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
from tests.helpers import (  # noqa: E402
    WORKING_DAYS_WEEK,
    assert_valid as _assert_valid,
    make_input as _make_input,
    mk_instance as _mk_inst,
    mk_patient as _mk_pat,
)


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


def test_max_spacing_soft_penalty_prefers_tighter_gap():
    """Patient with max_gap=1 should be scheduled on consecutive-allowable days.

    min_gap=0 means gap >=1 required; max_gap=1 means gap >1 is penalized.
    With req=2 in a 5-day horizon, the envelope should prefer day pairs
    with gap==2 (Mon/Wed style — the minimum legal, which is also at-the-max)
    over wider gaps.
    """
    patients = [_mk_pat(1, "TightGap", dur=30, req=2, min_gap=1, max_gap=1)]
    instances = [_mk_inst(f"p1_v{i}", 1, 30) for i in range(2)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=10)
    assert out.metadata["placed"] == 2
    _assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    gap = ords[1] - ords[0]
    # min_gap=1 forbids gap<=1, so smallest legal gap is 2. max_gap=1 penalizes
    # gap>1, so the envelope will still pick the smallest legal (gap=2) because
    # penalty is smallest there.
    assert gap == 2, f"expected tight gap=2, got gap={gap}"


def test_min_gap_semantic_zero_allows_consecutive_forbids_same_day():
    """min_days_between_visits=0 → 0 clear days required between visits.
    Same day forbidden, consecutive days allowed."""
    patients = [_mk_pat(1, "P", dur=30, req=2, min_gap=0, max_gap=7)]
    instances = [_mk_inst(f"p1_v{i}", 1, 30) for i in range(2)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 2
    _assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    gap = ords[1] - ords[0]
    assert gap >= 1, f"same-day forbidden, got gap={gap}"
    # Consecutive days (gap=1) are legal under min_gap=0


def test_min_gap_semantic_one_requires_one_clear_day():
    """min_days_between_visits=1 → 1 clear day required between visits.
    Smallest legal placement: Mon+Wed (Tuesday between = 1 clear day)."""
    patients = [_mk_pat(1, "P", dur=30, req=2, min_gap=1, max_gap=7)]
    instances = [_mk_inst(f"p1_v{i}", 1, 30) for i in range(2)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 2
    _assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    gap = ords[1] - ords[0]
    assert gap >= 2, (
        f"min_gap=1 requires 1 clear day → gap ≥ 2, got gap={gap} "
        f"(consecutive days should be forbidden)"
    )


def test_min_gap_semantic_two_requires_two_clear_days():
    """min_days_between_visits=2 → 2 clear days required between visits.
    Smallest legal placement: Mon+Thu (Tue, Wed between = 2 clear days)."""
    patients = [_mk_pat(1, "P", dur=30, req=2, min_gap=2, max_gap=7)]
    instances = [_mk_inst(f"p1_v{i}", 1, 30) for i in range(2)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 2
    _assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    gap = ords[1] - ords[0]
    assert gap >= 3, (
        f"min_gap=2 requires 2 clear days → gap ≥ 3, got gap={gap}"
    )


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

    With pairwise cross-vehicle cuts, the first conflict (a pair found
    infeasible on one day) should propagate to all days simultaneously,
    giving 2-3 round convergence instead of the 8+ we saw with weak-only.
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
    assert out.metadata["cuts_generated"] >= 1
    # Strong cuts should converge this adversarial case in few rounds
    assert out.metadata["benders_rounds"] <= 5, (
        f"expected ≤5 rounds with strong cuts, got {out.metadata['benders_rounds']}"
    )
    _assert_valid(out, inp)


# ── Calendar block exclusion ───────────────────────────────────────


def test_warm_start_preserves_assignments_when_unchanged():
    """Re-solving the same input with the prior output as upper_bound should
    preserve the same (clinician, day) per instance — this is the continuity-
    of-care mechanism."""
    patients = [
        _mk_pat(1, "A", dur=60, req=2, min_gap=1),
        _mk_pat(2, "B", dur=45, req=2, min_gap=1),
        _mk_pat(3, "C", dur=30, req=1),
    ]
    instances = [
        _mk_inst("p1_v0", 1, 60), _mk_inst("p1_v1", 1, 60),
        _mk_inst("p2_v0", 2, 45), _mk_inst("p2_v1", 2, 45),
        _mk_inst("p3_v0", 3, 30),
    ]
    matrix = {
        "home_0": {"1": 18, "2": 41, "3": 24},
        "1": {"home_0": 18, "2": 30, "3": 12},
        "2": {"home_0": 41, "1": 30, "3": 22},
        "3": {"home_0": 24, "1": 12, "2": 22},
    }
    inp = _make_input(patients, instances, matrix=matrix)

    first = solve(inp, time_budget=5)
    assert first.metadata["placed"] == 5
    first_days = {v.instance_id: v.date for v in first.planned_visits}

    second = solve(inp, time_budget=5, upper_bound=first)
    assert second.metadata["placed"] == 5
    assert second.metadata["warm_start_used"] is True
    second_days = {v.instance_id: v.date for v in second.planned_visits}
    # Every instance should land on the same date
    assert first_days == second_days, (
        f"warm start didn't preserve days: {first_days} vs {second_days}"
    )


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


def test_diagnosis_window_too_short():
    """Patient has 30-min windows but needs 60-min duration → window_too_short."""
    patients = [_mk_pat(1, "P", dur=60, req=1)]
    instances = [
        _mk_inst(
            "p1_v0", 1, 60,
            windows={str(wd): [{"start_minute": 540, "end_minute": 570}]
                     for wd in range(7)},
        )
    ]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=5)
    assert out.metadata["placed"] == 0
    assert len(out.metadata["unschedulable"]) == 1
    reasons = set(out.metadata["unschedulable"][0]["reasons"])
    assert "window_too_short" in reasons or "no_legal_slot" in reasons


def test_diagnosis_spacing_infeasible_for_horizon():
    """Patient needs 3 visits with min_gap=2 in a 5-day horizon.
    Gap must be >2, so min horizon = (3-1)*3+1 = 7 days. Infeasible.
    """
    patients = [_mk_pat(1, "Spaced", dur=30, req=3, min_gap=2, max_gap=7)]
    instances = [_mk_inst(f"p1_v{i}", 1, 30) for i in range(3)]
    inp = _make_input(patients, instances)
    out = solve(inp, time_budget=10)
    # At least one visit can't be placed
    assert out.metadata["placed"] < 3
    unsched = out.metadata["unschedulable"]
    assert len(unsched) >= 1
    all_reasons = {r for u in unsched for r in u["reasons"]}
    assert "spacing_infeasible_for_horizon" in all_reasons


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
