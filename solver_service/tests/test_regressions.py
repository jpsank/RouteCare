"""Regression tests — one named test per bug fixed in session history.

Each test is the minimal reproducer of a specific historical bug.  If a
future refactor reintroduces the bug, the test named after it will fail,
and the name tells you exactly what's broken.

Add a new test here whenever a bug is found AND fixed.  Name it
`test_regression_<short-bug-description>`.  Include a docstring citing
the commit or PR where the original fix landed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models import (  # noqa: E402
    CalendarBlockData,
    LockedVisitData,
)
from solver.benders import solve, validate_plan  # noqa: E402
from tests.helpers import (  # noqa: E402
    WORKING_DAYS_WEEK,
    assert_valid,
    make_input,
    minute_range_from_visit,
    mk_clinician,
    mk_instance,
    mk_patient,
)


# ── Bug: cpsat_timing soft-override escape hatch ───────────────────
# Commit ad65ea7 — the concrete timing pass had a soft-override mechanism
# that allowed visits to be placed outside their declared availability
# windows with a small penalty when no window fit.  This violated the
# Benders architecture's strict feasibility contract.  The realism/
# am_pm_mixed benchmark scenario caught a PM visit placed at 16:30-17:15
# outside the declared 13:00-17:00 window.


def test_regression_no_soft_override_on_availability_window():
    """A visit whose declared window cannot accommodate it must be dropped,
    not placed with a soft override.  The timing pass has no escape hatch."""
    # Patient has a PM-only window 13:00-17:00, visit is 60 min
    patients = [mk_patient(1, "PM", dur=60, req=1)]
    pm_windows = {
        str(wd): [{"start_minute": 780, "end_minute": 1020}]
        for wd in range(1, 6)
    }
    instances = [mk_instance("p1_v0", 1, dur=60, windows=pm_windows)]
    inp = make_input(patients, instances)

    out = solve(inp, time_budget=5)
    assert_valid(out, inp)

    # If the visit is placed, it must be strictly inside the PM window.
    for v in out.planned_visits:
        start_m, end_m = minute_range_from_visit(v)
        assert 780 <= start_m, f"visit starts at {start_m} before PM window"
        assert end_m <= 1020, f"visit ends at {end_m} after PM window"


# ── Bug: _envelope_from_upper_bound bails on missing instance ──────
# Commit ad65ea7 — the warm-start conversion returned None whenever the
# prior plan referenced an instance_id not present in the new input.
# That meant every cancellation re-solve silently became a cold-start.
# The benchmark's ws_cancel_patient scenario caught this at 18% assignment
# stability before the fix.


def test_regression_warm_start_survives_cancellation():
    """Solving with a prior plan where one patient has been cancelled
    should still warm-start the remaining instances."""
    patients = [mk_patient(pid, f"P{pid}", dur=45, req=1) for pid in range(1, 6)]
    instances = [mk_instance(f"p{pid}_v0", pid, dur=45) for pid in range(1, 6)]
    base_inp = make_input(patients, instances)

    base_out = solve(base_inp, time_budget=5)
    assert_valid(base_out, base_inp)
    assert base_out.metadata["placed"] == 5

    # Cancel the last patient from the input but keep the prior plan
    cancelled_patients = patients[:-1]
    cancelled_instances = instances[:-1]
    cancelled_inp = make_input(cancelled_patients, cancelled_instances)

    warm_out = solve(cancelled_inp, time_budget=5, upper_bound=base_out)
    assert_valid(warm_out, cancelled_inp)
    assert warm_out.metadata["warm_start_used"] is True, (
        "warm_start_used should be True after cancellation"
    )

    # Most non-cancelled instances should keep the same (clinician, date)
    base_map = {v.instance_id: (v.clinician_idx, v.date) for v in base_out.planned_visits}
    warm_map = {v.instance_id: (v.clinician_idx, v.date) for v in warm_out.planned_visits}
    shared = set(base_map.keys()) & set(warm_map.keys())
    stable = sum(1 for iid in shared if base_map[iid] == warm_map[iid])
    assert stable >= len(shared) * 0.7, (
        f"only {stable}/{len(shared)} instances kept stable after cancellation"
    )


# ── Bug: subproblem ignored lunch reservation ─────────────────────
# Commit ad65ea7 — the subproblem's forward pass evaluated route
# feasibility against full day bounds, ignoring the lunch break the
# concrete timing pass would later enforce.  This allowed the envelope
# to commit to days that the timing pass couldn't honor.  Fix: subtract
# lunch_duration_minutes from effective_day_end in the forward pass.


def test_regression_subproblem_reserves_lunch_capacity():
    """A vehicle-day packed with visits totaling exactly (day_length -
    lunch_duration) should succeed; packing with zero lunch slack should
    not produce a plan that the timing pass later rejects."""
    # Clinician has 8h day and 30min lunch.  Effective capacity is 7.5h.
    # Pack 5 × 90min visits on one day = 7.5h, no slack for lunch.
    clinician = mk_clinician(
        max_visits_per_day=5,
        lunch_duration_minutes=30,
        workday_start_minute=480,   # 8am
        workday_end_minute=960,     # 4pm = 8h
    )
    patients = [mk_patient(pid, f"P{pid}", dur=90, req=1) for pid in range(1, 6)]
    instances = [mk_instance(f"p{pid}_v0", pid, dur=90) for pid in range(1, 6)]
    inp = make_input(patients, instances, clinicians=[clinician])

    out = solve(inp, time_budget=10)
    assert_valid(out, inp)

    # The plan must not place all 5 visits on the same day — lunch
    # reservation means there's no way to fit 5×90min + 30min lunch
    # in an 8-hour window.
    dates_seen: dict[str, int] = {}
    for v in out.planned_visits:
        dates_seen[v.date] = dates_seen.get(v.date, 0) + 1
    for date, count in dates_seen.items():
        assert count < 5, (
            f"{count} visits packed on {date} — lunch reservation wasn't enforced"
        )


# ── Bug: envelope relaxed missing-weekday availability ─────────────
# The envelope's _enumerate_slots used to treat "patient has no window
# for this weekday" as "patient is always available that day" when the
# availability_windows dict was non-empty but missing that weekday.  Fix:
# when availability_windows is set but missing a weekday, skip the day
# entirely — the patient is not available on that weekday.


def test_regression_strict_missing_weekday_availability():
    """Patient available only on Monday must never be scheduled on
    Tue-Fri, even though their availability_windows dict is non-empty."""
    patients = [mk_patient(1, "MondayOnly", dur=60, req=1)]
    # "1" = Monday in the ctx convention (weekday 0 mapped via (wd+1)%7)
    mon_only = {"1": [{"start_minute": 540, "end_minute": 1020}]}
    instances = [mk_instance("p1_v0", 1, dur=60, windows=mon_only)]
    inp = make_input(patients, instances)

    out = solve(inp, time_budget=5)
    assert_valid(out, inp)
    assert out.metadata["placed"] == 1

    # The visit must land on Monday (2026-04-20)
    assert out.planned_visits[0].date == "2026-04-20"


# ── Bug: multi-window availability silently collapsed to widest ────
# An earlier iteration of the envelope used the widest window per day
# when building slot vars, which meant patients with [(9-11),(14-16)]
# could be placed at 12:30.  Fix: enumerate one slot per (instance, day,
# window), forcing the envelope to commit to one window at solve time.


def test_regression_multi_window_availability_enforced_exact():
    """Patient with two disjoint windows on one day must be scheduled
    strictly inside one of them, not the interval between."""
    patients = [mk_patient(1, "Carol", dur=45, req=1)]
    multi = {
        "1": [  # Monday
            {"start_minute": 540, "end_minute": 660},    # 9-11am
            {"start_minute": 840, "end_minute": 960},    # 2-4pm
        ]
    }
    instances = [mk_instance("p1_v0", 1, dur=45, windows=multi)]
    inp = make_input(patients, instances)

    out = solve(inp, time_budget=5)
    assert_valid(out, inp)
    assert out.metadata["placed"] == 1

    v = out.planned_visits[0]
    start_m, end_m = minute_range_from_visit(v)
    in_window_1 = 540 <= start_m and end_m <= 660
    in_window_2 = 840 <= start_m and end_m <= 960
    assert in_window_1 or in_window_2, (
        f"visit at [{start_m},{end_m}) fell between declared windows"
    )


# ── Bug: warm-start was hints-only with no cost term ───────────────
# Commit ad65ea7 — the envelope passed warm-start assignments through
# CP-SAT's `add_hint()`, which is a suggestion the solver is free to
# ignore.  Without a cost penalty, the solver freely re-shuffled the
# entire plan on every re-solve.  Fix: add CONTINUITY_WEIGHT applied as
# a per-slot penalty for deviating from the warm-start.


def test_regression_warm_start_has_continuity_cost_term():
    """Re-solving with an identical input + prior plan as warm-start
    must produce a byte-identical plan.  A pure hint-only warm-start
    wouldn't guarantee this."""
    patients = [
        mk_patient(1, "A", dur=60, req=2, min_gap=1),
        mk_patient(2, "B", dur=45, req=2, min_gap=1),
        mk_patient(3, "C", dur=30, req=1),
    ]
    instances = [
        mk_instance("p1_v0", 1, dur=60),
        mk_instance("p1_v1", 1, dur=60),
        mk_instance("p2_v0", 2, dur=45),
        mk_instance("p2_v1", 2, dur=45),
        mk_instance("p3_v0", 3, dur=30),
    ]
    inp = make_input(patients, instances)

    first = solve(inp, time_budget=5)
    second = solve(inp, time_budget=5, upper_bound=first)
    assert second.metadata["warm_start_used"] is True

    first_map = {v.instance_id: (v.clinician_idx, v.date) for v in first.planned_visits}
    second_map = {v.instance_id: (v.clinician_idx, v.date) for v in second.planned_visits}
    assert first_map == second_map, (
        f"warm-start re-solve drifted from original:\n"
        f"  first={first_map}\n  second={second_map}"
    )


# ── Bug: min_days_between_visits semantic ambiguity in validate.py ─
# A comment in validate.py contradicted the actual behavior: it said
# "min_gap=1 → consecutive days OK" when the code forbid consecutive
# days.  Actual semantic: min_gap=N means "at least N *clear* days
# between two visits."  Pin the semantic with explicit tests below
# (already present in test_benders.py::test_min_gap_semantic_*).


def test_regression_min_gap_semantic_one_clear_day():
    """min_days_between_visits=1 must forbid Mon+Tue but allow Mon+Wed."""
    patients = [mk_patient(1, "Spaced", dur=30, req=2, min_gap=1)]
    instances = [mk_instance(f"p1_v{i}", 1, dur=30) for i in range(2)]
    inp = make_input(patients, instances)

    out = solve(inp, time_budget=5)
    assert_valid(out, inp)
    dates = sorted(v.date for v in out.planned_visits)
    from datetime import datetime as dt
    ords = [dt.fromisoformat(d).toordinal() for d in dates]
    gap = ords[1] - ords[0]
    assert gap >= 2, f"min_gap=1 should require ≥1 clear day (gap ≥ 2); got gap={gap}"
