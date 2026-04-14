"""Head-to-head benchmark: old CP-SAT solver vs new Benders solver.

Runs identical inputs through both solvers, measuring:
  - placed count
  - total drive minutes (from validated plan)
  - solve wall time
  - validation status (does the plan satisfy hard constraints?)

Scenarios mirror tests/test_benders.py for reproducibility.
"""

from __future__ import annotations

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    CalendarBlockData,
    ClinicianData,
    LockedVisitData,
    PatientData,
    SolverInput,
    SolverOutput,
    VisitInstanceData,
)
from solver.benders import solve as benders_solve
from solver.benders.validate import ValidationError, validate_plan
from solvers import cpsat_solve


WORKING_DAYS = [
    "2026-04-20", "2026-04-21", "2026-04-22",
    "2026-04-23", "2026-04-24",
]

TIME_BUDGET = 30


# ── Scenarios ────────────────────────────────────────────────────────


def scenario_small():
    """3 patients, 5 instances, 1 clinician."""
    patients = [
        PatientData(id=1, name="Alice", visit_duration_minutes=60, required_visits=2,
                    min_days_between_visits=2, max_days_between_visits=5),
        PatientData(id=2, name="Bob", visit_duration_minutes=45, required_visits=2,
                    min_days_between_visits=1, max_days_between_visits=7, priority=5),
        PatientData(id=3, name="Carol", visit_duration_minutes=30, required_visits=1),
    ]
    instances = [
        VisitInstanceData(id="p1_v0", patient_id=1, duration=60),
        VisitInstanceData(id="p1_v1", patient_id=1, duration=60),
        VisitInstanceData(id="p2_v0", patient_id=2, duration=45),
        VisitInstanceData(id="p2_v1", patient_id=2, duration=45),
        VisitInstanceData(id="p3_v0", patient_id=3, duration=30),
    ]
    matrix = {
        "home_0": {"1": 18, "2": 41, "3": 24},
        "1": {"home_0": 18, "2": 30, "3": 12},
        "2": {"home_0": 41, "1": 30, "3": 22},
        "3": {"home_0": 24, "1": 12, "2": 22},
    }
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WORKING_DAYS[0], working_days=WORKING_DAYS,
    )


def scenario_medium():
    """8 patients, ~13 instances, 1 clinician, random geometry."""
    random.seed(42)
    patients = []
    instances = []
    for pid in range(1, 9):
        req = random.choice([1, 2, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}",
                        visit_duration_minutes=dur, required_visits=req,
                        min_days_between_visits=1, max_days_between_visits=7)
        )
        for v in range(req):
            instances.append(
                VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur)
            )
    ids = [str(p.id) for p in patients]
    matrix = {"home_0": {i: 15 for i in ids}}
    for i in ids:
        matrix[i] = {j: (0 if i == j else 25) for j in ids}
        matrix[i]["home_0"] = 15
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WORKING_DAYS[0], working_days=WORKING_DAYS,
    )


def scenario_heavy():
    """12 patients, ~24 instances, 1 clinician, varied geography."""
    random.seed(123)
    patients = []
    instances = []
    for pid in range(1, 13):
        req = random.choice([1, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}",
                        visit_duration_minutes=dur, required_visits=req,
                        min_days_between_visits=1, max_days_between_visits=7)
        )
        for v in range(req):
            instances.append(
                VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur)
            )
    # Random-ish symmetric matrix with home=18, patient-pair=22±
    ids = [str(p.id) for p in patients]
    matrix = {"home_0": {i: 18 for i in ids}}
    rng = random.Random(999)
    for i in ids:
        matrix[i] = {}
        matrix[i]["home_0"] = 18
        for j in ids:
            if i == j:
                matrix[i][j] = 0
            else:
                matrix[i][j] = rng.randint(8, 35)
    # Symmetrize
    for i in ids:
        for j in ids:
            if i < j:
                matrix[j][i] = matrix[i][j]
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WORKING_DAYS[0], working_days=WORKING_DAYS,
    )


def scenario_extra_heavy():
    """20 patients, ~38 instances, 1 clinician, 10-day horizon."""
    random.seed(777)
    patients = []
    instances = []
    for pid in range(1, 21):
        req = random.choice([1, 2, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}",
                        visit_duration_minutes=dur, required_visits=req,
                        min_days_between_visits=1, max_days_between_visits=9)
        )
        for v in range(req):
            instances.append(
                VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur)
            )
    ids = [str(p.id) for p in patients]
    matrix = {"home_0": {i: 18 for i in ids}}
    rng = random.Random(7777)
    for i in ids:
        matrix[i] = {}
        matrix[i]["home_0"] = 18
        for j in ids:
            matrix[i][j] = 0 if i == j else rng.randint(8, 40)
    for i in ids:
        for j in ids:
            if i < j:
                matrix[j][i] = matrix[i][j]
    # 10-day horizon
    days = [f"2026-04-{20+d:02d}" for d in range(10)]
    # Skip the weekend (Apr 25/26)
    days = [d for d in days if d not in ("2026-04-25", "2026-04-26")]
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=days[0], working_days=days,
    )


def scenario_multi_window():
    """Patients with disjoint availability windows — the new solver's strict
    feasibility contract matters here.  The old solver treats availability
    windows as soft penalties, so it may schedule *between* windows with a
    soft_constraint_override flag.  The new solver refuses."""
    patients = [
        PatientData(id=1, name="MorningOrAfternoon",
                    visit_duration_minutes=45, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=2, name="MorningOnly",
                    visit_duration_minutes=60, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=3, name="LunchWindow",
                    visit_duration_minutes=30, required_visits=1),
    ]
    # Patient 1: only 9-10:30 or 14-15:30 on weekdays (disjoint windows)
    p1_windows = {
        str(wd): [
            {"start_minute": 540, "end_minute": 630},
            {"start_minute": 840, "end_minute": 930},
        ]
        for wd in range(1, 6)
    }
    p2_windows = {
        str(wd): [{"start_minute": 540, "end_minute": 720}]
        for wd in range(1, 6)
    }
    p3_windows = {
        str(wd): [{"start_minute": 720, "end_minute": 780}]
        for wd in range(1, 6)
    }
    instances = [
        VisitInstanceData(id="p1_v0", patient_id=1, duration=45,
                          availability_windows=p1_windows),
        VisitInstanceData(id="p1_v1", patient_id=1, duration=45,
                          availability_windows=p1_windows),
        VisitInstanceData(id="p2_v0", patient_id=2, duration=60,
                          availability_windows=p2_windows),
        VisitInstanceData(id="p2_v1", patient_id=2, duration=60,
                          availability_windows=p2_windows),
        VisitInstanceData(id="p3_v0", patient_id=3, duration=30,
                          availability_windows=p3_windows),
    ]
    matrix = {
        "home_0": {"1": 15, "2": 20, "3": 10},
        "1": {"home_0": 15, "2": 25, "3": 12},
        "2": {"home_0": 20, "1": 25, "3": 18},
        "3": {"home_0": 10, "1": 12, "2": 18},
    }
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WORKING_DAYS[0], working_days=WORKING_DAYS,
    )


def scenario_multi_clinician():
    """6 patients, 2 clinicians, eligibility constraints (new solver only)."""
    patients = [
        PatientData(id=1, name="OnlyClin0", visit_duration_minutes=60, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=2, name="OnlyClin1", visit_duration_minutes=60, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=3, name="EitherA", visit_duration_minutes=45, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=4, name="EitherB", visit_duration_minutes=45, required_visits=2,
                    min_days_between_visits=1),
        PatientData(id=5, name="EitherC", visit_duration_minutes=30, required_visits=1),
        PatientData(id=6, name="EitherD", visit_duration_minutes=30, required_visits=1),
    ]
    instances = []
    # Clinician 0 only
    instances.append(VisitInstanceData(id="p1_v0", patient_id=1, duration=60, eligible_clinician_indices=[0]))
    instances.append(VisitInstanceData(id="p1_v1", patient_id=1, duration=60, eligible_clinician_indices=[0]))
    # Clinician 1 only
    instances.append(VisitInstanceData(id="p2_v0", patient_id=2, duration=60, eligible_clinician_indices=[1]))
    instances.append(VisitInstanceData(id="p2_v1", patient_id=2, duration=60, eligible_clinician_indices=[1]))
    # Either — solver assigns
    for pid in [3, 4]:
        instances.append(VisitInstanceData(id=f"p{pid}_v0", patient_id=pid, duration=45))
        instances.append(VisitInstanceData(id=f"p{pid}_v1", patient_id=pid, duration=45))
    for pid in [5, 6]:
        instances.append(VisitInstanceData(id=f"p{pid}_v0", patient_id=pid, duration=30))

    matrix = {
        "home_0": {"1": 15, "2": 60, "3": 20, "4": 40, "5": 25, "6": 35},
        "home_1": {"1": 60, "2": 15, "3": 40, "4": 20, "5": 35, "6": 25},
    }
    import itertools
    pts = ["1", "2", "3", "4", "5", "6"]
    rng = random.Random(7)
    for a in pts:
        if a not in matrix:
            matrix[a] = {}
        matrix[a]["home_0"] = matrix["home_0"][a]
        matrix[a]["home_1"] = matrix["home_1"][a]
        for b in pts:
            if a == b:
                matrix[a][b] = 0
            elif b in matrix[a]:
                continue
            else:
                matrix[a][b] = rng.randint(15, 45)
    # symmetrize
    for a in pts:
        for b in pts:
            if a < b and matrix[a].get(b) is not None:
                matrix[b][a] = matrix[a][b]

    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WORKING_DAYS[0], working_days=WORKING_DAYS,
    )


# ── Result extraction ───────────────────────────────────────────────


def _compute_drive(out: SolverOutput, inp: SolverInput) -> int:
    """Compute round-trip drive for each (clinician, day) from planned visits.

    Works for both old and new solver output shapes.
    """
    total = 0
    by_vehicle: dict[tuple[int, str], list] = {}
    for v in out.planned_visits:
        c_idx = getattr(v, "clinician_idx", 0) or 0
        by_vehicle.setdefault((c_idx, v.date), []).append(v)

    matrix = inp.travel_matrix

    def travel(a: str, b: str) -> int:
        return matrix.get(a, {}).get(b, 0) or matrix.get(a, {}).get("home", 0) or 0

    for (c_idx, date), visits in by_vehicle.items():
        visits.sort(key=lambda v: v.starts_at)
        home_keys = [f"home_{c_idx}", "home"]
        home = home_keys[0] if home_keys[0] in matrix else home_keys[1]
        pids = [str(v.patient_id) for v in visits]
        if not pids:
            continue
        cost = travel(home, pids[0])
        for a, b in zip(pids, pids[1:]):
            cost += travel(a, b)
        cost += travel(pids[-1], home)
        total += cost
    return total


def _count_soft_overrides(out: SolverOutput) -> int:
    return sum(1 for v in out.planned_visits if v.soft_constraint_override)


def run_one(name: str, inp: SolverInput, run_old: bool = True) -> None:
    print(f"\n── {name} ──")
    print(f"  patients={len(inp.patients)}  instances={len(inp.instances)}"
          f"  clinicians={len(inp.clinicians)}  days={len(inp.working_days)}")

    old_placed = None
    old_drive = None
    # Old solver
    if run_old and len(inp.clinicians) == 1:
        t0 = time.monotonic()
        old_out = cpsat_solve(inp, time_budget=TIME_BUDGET)
        old_dt = time.monotonic() - t0
        old_drive = _compute_drive(old_out, inp)
        old_overrides = _count_soft_overrides(old_out)
        try:
            validate_plan(old_out, inp)
            old_valid = "✓"
        except ValidationError as e:
            old_valid = f"✗ {e.rule}"
        old_placed = len(old_out.planned_visits)
        tag = f" overrides={old_overrides}" if old_overrides else ""
        print(f"  OLD    placed={old_placed:2d}/{len(inp.instances)}  drive={old_drive:4d}  "
              f"time={old_dt:5.2f}s  validate={old_valid}{tag}")
    else:
        print(f"  OLD    N/A (multi-clinician not supported)")

    # New solver
    t0 = time.monotonic()
    new_out = benders_solve(inp, time_budget=TIME_BUDGET)
    new_dt = time.monotonic() - t0
    new_drive = _compute_drive(new_out, inp)
    new_overrides = _count_soft_overrides(new_out)
    try:
        validate_plan(new_out, inp)
        new_valid = "✓"
    except ValidationError as e:
        new_valid = f"✗ {e.rule}"
    new_placed = new_out.metadata.get("placed", len(new_out.planned_visits))
    rounds = new_out.metadata.get("benders_rounds", "?")
    cuts = new_out.metadata.get("cuts_generated", "?")
    tag = f" overrides={new_overrides}" if new_overrides else ""
    print(f"  NEW    placed={new_placed:2d}/{len(inp.instances)}  drive={new_drive:4d}  "
          f"time={new_dt:5.2f}s  validate={new_valid}  rounds={rounds}  cuts={cuts}{tag}")

    if old_placed is not None and old_placed == new_placed and old_placed > 0:
        delta = new_drive - old_drive
        pct = (delta / old_drive * 100) if old_drive else 0
        print(f"  Δ drive  new - old = {delta:+d} min ({pct:+.1f}%)")


# ── Main ────────────────────────────────────────────────────────────


def main():
    run_one("SMALL", scenario_small())
    run_one("MEDIUM", scenario_medium())
    run_one("HEAVY", scenario_heavy())
    run_one("EXTRA-HEAVY (20 patients, 10 days)", scenario_extra_heavy())
    run_one("MULTI-WINDOW (adversarial)", scenario_multi_window())
    run_one("MULTI-CLINICIAN", scenario_multi_clinician(), run_old=True)


if __name__ == "__main__":
    main()
