"""Benchmark harness for the Benders solver.

Runs a fixed set of scenarios through the solver, measuring:
  - placed count
  - total drive minutes (reconstructed from the plan + travel matrix)
  - solve wall time
  - validation status (does the plan satisfy every hard constraint?)
  - Benders round count and cut count

Scenarios mirror tests/test_benders.py for reproducibility.  Add new
scenarios via scenario_*() factories and wire them into main().
"""

from __future__ import annotations

import os
import random
import statistics
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


WORKING_DAYS = [
    "2026-04-20", "2026-04-21", "2026-04-22",
    "2026-04-23", "2026-04-24",
]

TIME_BUDGET = 30
SAMPLES = int(os.environ.get("BENCHMARK_SAMPLES", "5"))


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


def run_one(name: str, inp: SolverInput) -> None:
    print(f"\n── {name} ──")
    print(f"  patients={len(inp.patients)}  instances={len(inp.instances)}"
          f"  clinicians={len(inp.clinicians)}  days={len(inp.working_days)}"
          f"  samples={SAMPLES}")

    drives: list[int] = []
    times: list[float] = []
    placed_counts: list[int] = []
    validate_results: list[str] = []
    rounds_counts: list[int] = []

    for _ in range(SAMPLES):
        t0 = time.monotonic()
        out = benders_solve(inp, time_budget=TIME_BUDGET)
        dt = time.monotonic() - t0

        drives.append(_compute_drive(out, inp))
        times.append(dt)
        placed_counts.append(out.metadata.get("placed", len(out.planned_visits)))
        rounds_counts.append(out.metadata.get("benders_rounds", 0))
        try:
            validate_plan(out, inp)
            validate_results.append("✓")
        except ValidationError as e:
            validate_results.append(f"✗{e.rule}")

    valid = "✓" if all(r == "✓" for r in validate_results) else "✗"
    placed_min = min(placed_counts)
    placed_max = max(placed_counts)
    placed_str = f"{placed_min}" if placed_min == placed_max else f"{placed_min}-{placed_max}"

    drive_min = min(drives)
    drive_med = int(statistics.median(drives))
    drive_max = max(drives)
    drive_str = f"{drive_min}" if drive_min == drive_max else f"{drive_min}|{drive_med}|{drive_max}"

    time_med = statistics.median(times)
    rounds_max = max(rounds_counts)

    print(f"  placed={placed_str}/{len(inp.instances)}  "
          f"drive(min|med|max)={drive_str}  "
          f"time_med={time_med:5.3f}s  validate={valid}  rounds_max={rounds_max}")


# ── Main ────────────────────────────────────────────────────────────


def main():
    run_one("SMALL", scenario_small())
    run_one("MEDIUM", scenario_medium())
    run_one("HEAVY", scenario_heavy())
    run_one("EXTRA-HEAVY (20 patients, 8 working days)", scenario_extra_heavy())
    run_one("MULTI-WINDOW (adversarial)", scenario_multi_window())
    run_one("MULTI-CLINICIAN", scenario_multi_clinician())


if __name__ == "__main__":
    main()
