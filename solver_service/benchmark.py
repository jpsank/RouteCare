"""Benchmark: CP-SAT decomposed solver (optional comparison with ILS).

Runs on identical inputs at multiple scales and compares:
  - Solution quality (total drive time, constraint violations)
  - Runtime
  - Feasibility (all visits placed?)

Usage:
  cd solver_service
  python3 benchmark.py                 # CP-SAT only (default)
  python3 benchmark.py --with-hgs      # also run PyVRP ILS baseline
  python3 benchmark.py --re-solve    # CP-SAT cold + warm re-solve timing on each scenario
"""

import argparse
import sys
import os
import time
import random
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from models import (
    SolverInput, SolverOutput, ClinicianData, PatientData,
    VisitInstanceData, LockedVisitData, CalendarBlockData, Location,
)


# ── Test Scenarios ──────────────────────────────────────────────────────

def make_scenario_small():
    """3 patients, 5 visits, 5 days. Typical light caseload."""
    patients = [
        PatientData(id=1, name="Alice", location=Location(lat=36.09, lng=-94.19),
                    visit_duration_minutes=60, required_visits_per_week=2,
                    min_days_between_visits=2, max_days_between_visits=5, priority=0),
        PatientData(id=2, name="Bob", location=Location(lat=36.32, lng=-94.22),
                    visit_duration_minutes=45, required_visits_per_week=2,
                    min_days_between_visits=1, max_days_between_visits=7, priority=5),
        PatientData(id=3, name="Carol", location=Location(lat=36.18, lng=-94.15),
                    visit_duration_minutes=30, required_visits_per_week=1,
                    min_days_between_visits=1, max_days_between_visits=7, priority=0),
    ]
    instances = [
        VisitInstanceData(id="patient_1_visit_0", patient_id=1, location=patients[0].location, duration=60),
        VisitInstanceData(id="patient_1_visit_1", patient_id=1, location=patients[0].location, duration=60),
        VisitInstanceData(id="patient_2_visit_0", patient_id=2, location=patients[1].location, duration=45),
        VisitInstanceData(id="patient_2_visit_1", patient_id=2, location=patients[1].location, duration=45),
        VisitInstanceData(id="patient_3_visit_0", patient_id=3, location=patients[2].location, duration=30),
    ]
    matrix = {
        "home": {"1": 18, "2": 41, "3": 24},
        "1": {"home": 18, "2": 30, "3": 12},
        "2": {"home": 41, "1": 30, "3": 22},
        "3": {"home": 24, "1": 12, "2": 22},
    }
    return SolverInput(
        patients=patients, instances=instances,
        clinician=ClinicianData(home_location=Location(lat=36.18, lng=-94.13)),
        travel_matrix=matrix, week_start_on="2026-04-06",
        working_days=["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"],
    )


def make_scenario_medium():
    """8 patients, 15 visits, 5 days. Typical full caseload."""
    random.seed(42)
    base_lat, base_lng = 36.15, -94.15

    patients = []
    instances = []
    for pid in range(1, 9):
        lat = base_lat + random.uniform(-0.2, 0.2)
        lng = base_lng + random.uniform(-0.2, 0.2)
        visits_per_week = random.choice([1, 2, 2, 3])
        duration = random.choice([30, 45, 60])
        min_gap = 2 if visits_per_week >= 3 else 1
        priority = random.choice([0, 0, 0, 3, 5])

        patients.append(PatientData(
            id=pid, name=f"Patient_{pid}", location=Location(lat=lat, lng=lng),
            visit_duration_minutes=duration, required_visits_per_week=visits_per_week,
            min_days_between_visits=min_gap, max_days_between_visits=7,
            priority=priority,
        ))
        for v in range(visits_per_week):
            instances.append(VisitInstanceData(
                id=f"patient_{pid}_visit_{v}", patient_id=pid,
                location=Location(lat=lat, lng=lng), duration=duration,
                priority=priority,
            ))

    # Build travel matrix from coordinates (haversine-ish approximation)
    matrix = _build_matrix(patients, base_lat, base_lng)

    return SolverInput(
        patients=patients, instances=instances,
        clinician=ClinicianData(
            home_location=Location(lat=base_lat, lng=base_lng),
            max_drive_minutes_per_day=180,
            schedule_density=0.3,
            charting_buffer_minutes=10,
            lunch_start_minute=720,
            lunch_duration_minutes=30,
            lunch_window_minutes=90,
            max_continuous_work_minutes=240,
            required_break_minutes=15,
        ),
        travel_matrix=matrix, week_start_on="2026-04-06",
        working_days=["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"],
    )


def make_scenario_heavy():
    """12 patients, 25 visits, 5 days. Heavy caseload with constraints."""
    random.seed(99)
    base_lat, base_lng = 36.15, -94.15

    patients = []
    instances = []
    for pid in range(1, 13):
        lat = base_lat + random.uniform(-0.3, 0.3)
        lng = base_lng + random.uniform(-0.3, 0.3)
        visits_per_week = random.choice([1, 2, 2, 3, 3])
        duration = random.choice([30, 45, 60])
        min_gap = 2 if visits_per_week >= 3 else 1
        priority = random.choice([0, 0, 3, 5])

        # Some patients have availability windows
        avail = {}
        if pid % 3 == 0:  # every 3rd patient has afternoon-only windows
            for wday in range(7):
                avail[str(wday)] = [{"start_minute": 780, "end_minute": 1020}]  # 1pm-5pm

        patients.append(PatientData(
            id=pid, name=f"Patient_{pid}", location=Location(lat=lat, lng=lng),
            visit_duration_minutes=duration, required_visits_per_week=visits_per_week,
            min_days_between_visits=min_gap, max_days_between_visits=7,
            priority=priority, availability_windows=avail,
        ))
        for v in range(visits_per_week):
            instances.append(VisitInstanceData(
                id=f"patient_{pid}_visit_{v}", patient_id=pid,
                location=Location(lat=lat, lng=lng), duration=duration,
                priority=priority, availability_windows=avail,
            ))

    matrix = _build_matrix(patients, base_lat, base_lng)

    # Add locked visits and calendar blocks
    locked = [
        LockedVisitData(
            patient_id=1, date="2026-04-06",
            starts_at="2026-04-06T09:00:00", ends_at="2026-04-06T10:00:00",
            duration_minutes=60,
        ),
    ]
    blocks = [
        CalendarBlockData(
            date="2026-04-08",
            starts_at="2026-04-08T14:00:00", ends_at="2026-04-08T15:30:00",
        ),
    ]

    return SolverInput(
        patients=patients, instances=instances,
        clinician=ClinicianData(
            home_location=Location(lat=base_lat, lng=base_lng),
            max_drive_minutes_per_day=200,
            schedule_density=0.3,
            charting_buffer_minutes=10,
            lunch_start_minute=720,
            lunch_duration_minutes=30,
            lunch_window_minutes=90,
            max_continuous_work_minutes=240,
            required_break_minutes=15,
        ),
        locked_visits=locked,
        calendar_blocks=blocks,
        travel_matrix=matrix, week_start_on="2026-04-06",
        working_days=["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"],
    )


def _build_matrix(patients, home_lat, home_lng):
    """Build travel matrix from lat/lng using simple distance → minutes."""
    import math

    def travel_min(lat1, lng1, lat2, lng2):
        # ~1 degree lat ≈ 69 miles, drive at ~30mph → minutes
        dist = math.sqrt((lat1 - lat2) ** 2 + (lng1 - lng2) ** 2) * 69
        return max(1, int(dist * 2))  # 2 min per mile

    matrix = {"home": {}}
    for p in patients:
        pid = str(p.id)
        t = travel_min(home_lat, home_lng, p.location.lat, p.location.lng)
        matrix["home"][pid] = t
        matrix.setdefault(pid, {})["home"] = t

    for p1 in patients:
        for p2 in patients:
            if p1.id == p2.id:
                continue
            t = travel_min(p1.location.lat, p1.location.lng, p2.location.lat, p2.location.lng)
            matrix.setdefault(str(p1.id), {})[str(p2.id)] = t

    return matrix


# ── Benchmark Runner ────────────────────────────────────────────────────

def evaluate_output(output: SolverOutput, input: SolverInput) -> dict:
    """Compute quality metrics for a solver output."""
    patients_by_id = {p.id: p for p in input.patients}

    # Drive cost
    total_drive = 0
    routes_by_day: dict[str, list] = defaultdict(list)
    for v in output.planned_visits:
        routes_by_day[v.date].append(v)

    for date, visits in routes_by_day.items():
        sorted_visits = sorted(visits, key=lambda v: v.starts_at)
        prev = "home"
        for v in sorted_visits:
            total_drive += input.travel_matrix.get(prev, {}).get(str(v.patient_id), 0)
            prev = str(v.patient_id)
        total_drive += input.travel_matrix.get(prev, {}).get("home", 0)

    # Spacing violations
    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in output.planned_visits:
        patient_dates[v.patient_id].append(v.date)

    spacing_violations = 0
    for pid, dates in patient_dates.items():
        if len(dates) < 2:
            continue
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        sorted_dates = sorted(dates)
        for i in range(len(sorted_dates) - 1):
            gap = (datetime.fromisoformat(sorted_dates[i + 1]) -
                   datetime.fromisoformat(sorted_dates[i])).days
            if gap < patient.min_days_between_visits:
                spacing_violations += 1
            if gap > patient.max_days_between_visits:
                spacing_violations += 1

    # One-patient-per-day violations
    one_per_day_violations = 0
    for date, visits in routes_by_day.items():
        pids = [v.patient_id for v in visits]
        one_per_day_violations += len(pids) - len(set(pids))

    # Availability window violations
    avail_violations = 0
    for v in output.planned_visits:
        patient = patients_by_id.get(v.patient_id)
        if not patient or not patient.availability_windows:
            continue
        dt = datetime.fromisoformat(v.starts_at)
        wday = str((dt.weekday() + 1) % 7)
        windows = patient.availability_windows.get(wday, [])
        if not windows:
            continue
        start_min = dt.hour * 60 + dt.minute
        in_any = any(w["start_minute"] <= start_min < w["end_minute"] for w in windows)
        if not in_any:
            avail_violations += 1

    # Visits on days with calendar blocks that overlap
    block_violations = 0
    for cb in input.calendar_blocks:
        cb_start = _to_minute(cb.starts_at)
        cb_end = _to_minute(cb.ends_at)
        for v in output.planned_visits:
            if v.date != cb.date:
                continue
            v_start = _to_minute(v.starts_at)
            v_end = _to_minute(v.ends_at)
            if v_start < cb_end and v_end > cb_start:
                block_violations += 1

    unscheduled = len(input.instances) - len(output.planned_visits)

    return {
        "visits_placed": len(output.planned_visits),
        "unscheduled": unscheduled,
        "total_drive": total_drive,
        "spacing_violations": spacing_violations,
        "one_per_day_violations": one_per_day_violations,
        "avail_violations": avail_violations,
        "block_violations": block_violations,
        "soft_overrides": sum(1 for v in output.planned_visits if v.soft_constraint_override),
        "fitness": output.fitness,
    }


def _to_minute(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str)
    return dt.hour * 60 + dt.minute


def run_benchmark(re_solve: bool = False) -> None:
    scenarios = [
        ("Small (3 patients, 5 visits)", make_scenario_small()),
        ("Medium (8 patients, 15 visits)", make_scenario_medium()),
        ("Heavy (12 patients, 25 visits)", make_scenario_heavy()),
    ]

    backends = []

    # CP-SAT (decomposed)
    try:
        from solvers.cpsat import solve as cpsat_solve
        backends.append(("CP-SAT", cpsat_solve, 10))
    except ImportError as e:
        print(f"CP-SAT unavailable: {e}", file=sys.stderr)

    if not backends:
        print("No solvers available; fix imports and retry.", file=sys.stderr)
        sys.exit(1)

    print("=" * 90)
    print("BENCHMARK: CP-SAT Decomposed")
    print("=" * 90)

    for scenario_name, input_data in scenarios:
        print(f"\n{'─' * 90}")
        print(f"Scenario: {scenario_name}")
        print(f"  Instances: {len(input_data.instances)}, Days: {len(input_data.working_days)}, "
              f"Locked: {len(input_data.locked_visits)}, Blocks: {len(input_data.calendar_blocks)}")
        print(f"{'─' * 90}")

        print(f"\n  {'Backend':<12} {'Time':>8} {'Placed':>7} {'Unsched':>8} {'Drive':>7} "
              f"{'SpaceV':>7} {'1/Day':>6} {'AvailV':>7} {'BlockV':>7} {'Fitness':>10}")
        print(f"  {'─'*12} {'─'*8} {'─'*7} {'─'*8} {'─'*7} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*10}")

        for name, solver_fn, budget in backends:
            try:
                start = time.perf_counter()
                output = solver_fn(input_data, time_budget=budget)
                elapsed = time.perf_counter() - start

                metrics = evaluate_output(output, input_data)

                print(f"  {name:<12} {elapsed:>7.2f}s {metrics['visits_placed']:>7} "
                      f"{metrics['unscheduled']:>8} {metrics['total_drive']:>7} "
                      f"{metrics['spacing_violations']:>7} {metrics['one_per_day_violations']:>6} "
                      f"{metrics['avail_violations']:>7} {metrics['block_violations']:>7} "
                      f"{metrics['fitness']:>10.0f}")

                if re_solve and name == "CP-SAT":
                    t1 = time.perf_counter()
                    warm = solver_fn(input_data, time_budget=budget, upper_bound=output)
                    warm_elapsed = time.perf_counter() - t1
                    wm = evaluate_output(warm, input_data)
                    print(f"  {'CP-SAT warm':<12} {warm_elapsed:>7.2f}s {wm['visits_placed']:>7} "
                          f"{wm['unscheduled']:>8} {wm['total_drive']:>7} "
                          f"{wm['spacing_violations']:>7} {wm['one_per_day_violations']:>6} "
                          f"{wm['avail_violations']:>7} {wm['block_violations']:>7} "
                          f"{wm['fitness']:>10.0f}")
            except Exception as e:
                print(f"  {name:<12} {'ERROR':>8} — {e}")

    print(f"\n{'=' * 90}")
    print("Legend:")
    print("  Drive    = total travel minutes (home→visits→home, all days)")
    print("  SpaceV   = spacing violations (min/max gap between same-patient visits)")
    print("  1/Day    = one-patient-per-day violations")
    print("  AvailV   = availability window violations")
    print("  BlockV   = calendar block overlap violations")
    print("  Fitness  = solver's own objective value")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare scheduling solver backends.")
    parser.add_argument(
        "--re-solve",
        action="store_true",
        help="After each CP-SAT run, time a second solve with upper_bound set (warm start).",
    )
    args = parser.parse_args()
    run_benchmark(re_solve=args.re_solve)
