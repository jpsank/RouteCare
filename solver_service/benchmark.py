"""Benchmark suites for the Benders solver.

Usage:
  python3 benchmark.py                      # runs the quick suite
  python3 benchmark.py --suite scale        # run one suite
  python3 benchmark.py --suite all          # run every suite
  python3 benchmark.py --strict             # fail-fast on first failure
  python3 benchmark.py --samples 3          # samples per scenario

Suites:
  quick         — baseline: 6 small-to-medium scenarios (the original set)
  scale         — problem sizes from 10 to 200 instances, scaling curve
  infeasibility — known-impossible inputs, checks diagnosis quality
  warm_start    — re-solve stability under perturbation
  realism       — clustered geography + realistic AM/PM windows
  adversarial   — dense blocks, pathological eligibility, tight windows
  all           — every suite above
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (  # noqa: E402
    CalendarBlockData,
    ClinicianData,
    LockedVisitData,
    PatientData,
    SolverInput,
    SolverOutput,
    VisitInstanceData,
)
from solver.benders import solve as benders_solve  # noqa: E402
from solver.benders.validate import ValidationError, validate_plan  # noqa: E402


# ── Config ──────────────────────────────────────────────────────────

TIME_BUDGET = 30
DEFAULT_SAMPLES = int(os.environ.get("BENCHMARK_SAMPLES", "3"))

from datetime import date as _date, timedelta as _td


def _weekdays_from(start: str, n: int) -> list[str]:
    """Generate n working days (Mon-Fri) starting from the given ISO date."""
    d = _date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:  # 0=Mon..4=Fri
            out.append(d.isoformat())
        d += _td(days=1)
    return out


WEEK = _weekdays_from("2026-04-20", 5)         # 5 working days
TWO_WEEK = _weekdays_from("2026-04-20", 10)    # 10 working days
ONE_MONTH = _weekdays_from("2026-04-20", 20)   # 20 working days


# ── Result types ────────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    name: str
    suite: str
    passed: bool = True
    samples: int = 0
    instances: int = 0
    clinicians: int = 0
    days: int = 0
    drive_min: int = 0
    drive_med: int = 0
    drive_max: int = 0
    time_med: float = 0.0
    time_max: float = 0.0
    placed_min: int = 0
    placed_max: int = 0
    rounds_max: int = 0
    cuts_max: int = 0
    validated: bool = True
    reason: str = ""
    extra: dict = field(default_factory=dict)

    def summary_line(self) -> str:
        status = "✓" if self.passed else "✗"
        drive_str = f"{self.drive_min}"
        if self.drive_min != self.drive_max:
            drive_str = f"{self.drive_min}|{self.drive_med}|{self.drive_max}"
        placed_str = f"{self.placed_min}/{self.instances}"
        if self.placed_min != self.placed_max:
            placed_str = f"{self.placed_min}-{self.placed_max}/{self.instances}"
        dims = f"n={self.instances} c={self.clinicians} d={self.days}"
        line = (
            f"  {status} {self.name:<42s} {dims:<16s} "
            f"placed={placed_str:<10s} drive={drive_str:<18s} "
            f"t={self.time_med:6.3f}s r={self.rounds_max}"
        )
        if self.cuts_max:
            line += f" cuts={self.cuts_max}"
        if self.extra:
            line += "  (" + ", ".join(f"{k}={v}" for k, v in self.extra.items()) + ")"
        if not self.passed and self.reason:
            line += f"\n      └─ FAIL: {self.reason}"
        return line


@dataclass
class SuiteReport:
    name: str
    results: list[ScenarioResult] = field(default_factory=list)

    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)


# ── Helpers ─────────────────────────────────────────────────────────


def _compute_drive(out: SolverOutput, inp: SolverInput) -> int:
    """Round-trip drive reconstructed from the plan + travel matrix."""
    total = 0
    by_vehicle: dict[tuple[int, str], list] = {}
    for v in out.planned_visits:
        by_vehicle.setdefault((v.clinician_idx, v.date), []).append(v)

    matrix = inp.travel_matrix

    def travel(a: str, b: str) -> int:
        return matrix.get(a, {}).get(b, 0)

    for (c_idx, _date), visits in by_vehicle.items():
        visits.sort(key=lambda v: v.starts_at)
        home_key = f"home_{c_idx}" if f"home_{c_idx}" in matrix else "home"
        pids = [str(v.patient_id) for v in visits]
        if not pids:
            continue
        cost = travel(home_key, pids[0])
        for a, b in zip(pids, pids[1:]):
            cost += travel(a, b)
        cost += travel(pids[-1], home_key)
        total += cost
    return total


def _random_matrix(
    pids: list[str],
    n_clinicians: int,
    seed: int,
    inter_min: int = 10,
    inter_max: int = 40,
    home_min: int = 8,
    home_max: int = 25,
) -> dict:
    rng = random.Random(seed)
    home_keys = [f"home_{c}" for c in range(n_clinicians)]
    matrix: dict[str, dict[str, int]] = {}
    for hk in home_keys:
        matrix[hk] = {p: rng.randint(home_min, home_max) for p in pids}
    for p in pids:
        matrix[p] = {}
        for hk in home_keys:
            matrix[p][hk] = matrix[hk][p]
        for q in pids:
            matrix[p][q] = 0 if p == q else rng.randint(inter_min, inter_max)
    for p in pids:
        for q in pids:
            if p < q:
                matrix[q][p] = matrix[p][q]
    return matrix


def _cluster_matrix(
    clusters: list[list[str]],
    cluster_homes: list[int],
    n_clinicians: int,
    intra: int = 8,
    inter: int = 35,
    home_near: int = 12,
    home_far: int = 40,
) -> dict:
    """Patients within a cluster are close; across clusters are far.

    cluster_homes[i] = the cluster index nearest clinician i's home.
    """
    pids_flat: list[str] = [p for c in clusters for p in c]
    cluster_of = {p: i for i, c in enumerate(clusters) for p in c}
    matrix: dict[str, dict[str, int]] = {}

    for c_idx in range(n_clinicians):
        hk = f"home_{c_idx}"
        home_cluster = cluster_homes[c_idx] if c_idx < len(cluster_homes) else 0
        matrix[hk] = {}
        for p in pids_flat:
            matrix[hk][p] = home_near if cluster_of[p] == home_cluster else home_far

    for p in pids_flat:
        matrix[p] = {}
        for c_idx in range(n_clinicians):
            hk = f"home_{c_idx}"
            matrix[p][hk] = matrix[hk][p]
        for q in pids_flat:
            if p == q:
                matrix[p][q] = 0
            elif cluster_of[p] == cluster_of[q]:
                matrix[p][q] = intra
            else:
                matrix[p][q] = inter
    return matrix


# ── Scenario framework ──────────────────────────────────────────────


@dataclass
class Scenario:
    name: str
    suite: str
    build: Callable[[], SolverInput]
    expect_feasible: bool = True
    # For expect_feasible=True: fraction of instances that must be placed
    # (default 1.0 = all).  Some stress tests accept near-full placement.
    min_placement_fraction: float = 1.0
    expected_reasons: set[str] = field(default_factory=set)
    extra_check: Callable[[SolverOutput, SolverInput], tuple[bool, str]] | None = None


def run_scenario(
    scenario: Scenario, samples: int, strict_placement: bool = True
) -> ScenarioResult:
    """Run one scenario N times and summarize.

    Args:
      scenario: the Scenario to evaluate.
      samples: number of solve samples (multi-worker CP-SAT is
        non-deterministic so multiple samples surface variance).
      strict_placement: if True, the pass check uses the WORST placement
        across samples (all samples must place enough).  If False, uses
        the BEST placement (at least one sample must succeed).
        Tests use strict=True with samples=1 under single-worker for
        determinism.  Benchmark uses strict=False with samples>1 to
        report best-case production behavior.
    """
    inp = scenario.build()
    result = ScenarioResult(
        name=scenario.name, suite=scenario.suite, samples=samples,
        instances=len(inp.instances), clinicians=len(inp.clinicians),
        days=len(inp.working_days),
    )

    drives: list[int] = []
    times: list[float] = []
    placed_counts: list[int] = []
    rounds_counts: list[int] = []
    cuts_counts: list[int] = []
    validate_ok: list[bool] = []
    last_out: SolverOutput | None = None

    for _ in range(samples):
        t0 = time.monotonic()
        try:
            out = benders_solve(inp, time_budget=TIME_BUDGET)
        except Exception as e:
            result.passed = False
            result.reason = f"solve crashed: {type(e).__name__}: {e}"
            return result
        dt = time.monotonic() - t0
        drives.append(_compute_drive(out, inp))
        times.append(dt)
        placed_counts.append(out.metadata.get("placed", len(out.planned_visits)))
        rounds_counts.append(out.metadata.get("benders_rounds", 0))
        cuts_counts.append(out.metadata.get("cuts_generated", 0))
        try:
            validate_plan(out, inp)
            validate_ok.append(True)
        except ValidationError:
            validate_ok.append(False)
        last_out = out

    assert last_out is not None

    result.drive_min = min(drives)
    result.drive_med = int(statistics.median(drives))
    result.drive_max = max(drives)
    result.time_med = statistics.median(times)
    result.time_max = max(times)
    result.placed_min = min(placed_counts)
    result.placed_max = max(placed_counts)
    result.rounds_max = max(rounds_counts)
    result.cuts_max = max(cuts_counts)
    result.validated = all(validate_ok)

    # Pass/fail rules
    if not result.validated:
        result.passed = False
        result.reason = "validate_plan failed (hard constraint violated)"
    elif scenario.expect_feasible:
        n = result.instances
        threshold = int(scenario.min_placement_fraction * n)
        # Strict mode: worst-case must meet threshold (all samples).
        # Lenient mode: best-case must meet threshold (at least one sample).
        placed_for_check = result.placed_min if strict_placement else result.placed_max
        if placed_for_check < threshold:
            result.passed = False
            result.reason = (
                f"placed {placed_for_check}/{n} < required "
                f"{threshold}/{n} ({scenario.min_placement_fraction:.0%})"
            )
        elif result.placed_min < n:
            # Acceptable partial placement — annotate without failing
            result.extra["partial"] = f"{result.placed_min}/{n}"
    else:
        # Infeasibility case — expect the solver to report unschedulable
        unsched = last_out.metadata.get("unschedulable", [])
        if result.placed_min == result.instances:
            result.passed = False
            result.reason = "expected unschedulable but solver placed everything"
        elif scenario.expected_reasons:
            all_reasons = {r for u in unsched for r in u.get("reasons", [])}
            if not (scenario.expected_reasons & all_reasons):
                result.passed = False
                result.reason = (
                    f"expected reasons {sorted(scenario.expected_reasons)}, "
                    f"got {sorted(all_reasons)}"
                )
            else:
                result.extra["reasons"] = ",".join(sorted(all_reasons))

    if result.passed and scenario.extra_check is not None:
        ok, msg = scenario.extra_check(last_out, inp)
        if not ok:
            result.passed = False
            result.reason = f"extra check: {msg}"

    return result


def run_suite(name: str, scenarios: list[Scenario], samples: int, strict: bool) -> SuiteReport:
    report = SuiteReport(name=name)
    print(f"\n┌─ {name} " + "─" * max(0, 74 - len(name)))
    for scenario in scenarios:
        # The CLI benchmark runs multi-worker for realistic perf numbers;
        # accept the BEST sample as a pass (strict_placement=False).
        # The pytest harness forces single-worker + samples=1 and passes
        # strict_placement=True so every sample must succeed.
        result = run_scenario(scenario, samples, strict_placement=False)
        report.results.append(result)
        print(result.summary_line())
        if not result.passed and strict:
            print("└─ STRICT mode: aborting on first failure")
            return report
    status = f"{report.pass_count()}/{len(report.results)} passed"
    if report.fail_count():
        status += f"  {report.fail_count()} FAILED"
    print(f"└─ {name}: {status}")
    return report


# ────────────────────────────────────────────────────────────────────
# SUITE: quick — baseline scenarios (the original benchmark set)
# ────────────────────────────────────────────────────────────────────


def _build_quick_small() -> SolverInput:
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
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_quick_medium() -> SolverInput:
    random.seed(42)
    patients, instances = [], []
    for pid in range(1, 9):
        req = random.choice([1, 2, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=dur,
                        required_visits=req, min_days_between_visits=1, max_days_between_visits=7)
        )
        for v in range(req):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur))
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 1, seed=4242)
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_quick_multi_clinician() -> SolverInput:
    patients = [
        PatientData(id=1, name="OnlyClin0", visit_duration_minutes=60, required_visits=2, min_days_between_visits=1),
        PatientData(id=2, name="OnlyClin1", visit_duration_minutes=60, required_visits=2, min_days_between_visits=1),
        PatientData(id=3, name="EitherA", visit_duration_minutes=45, required_visits=2, min_days_between_visits=1),
        PatientData(id=4, name="EitherB", visit_duration_minutes=45, required_visits=2, min_days_between_visits=1),
        PatientData(id=5, name="EitherC", visit_duration_minutes=30, required_visits=1),
        PatientData(id=6, name="EitherD", visit_duration_minutes=30, required_visits=1),
    ]
    instances = [
        VisitInstanceData(id="p1_v0", patient_id=1, duration=60, eligible_clinician_indices=[0]),
        VisitInstanceData(id="p1_v1", patient_id=1, duration=60, eligible_clinician_indices=[0]),
        VisitInstanceData(id="p2_v0", patient_id=2, duration=60, eligible_clinician_indices=[1]),
        VisitInstanceData(id="p2_v1", patient_id=2, duration=60, eligible_clinician_indices=[1]),
    ]
    for pid in [3, 4]:
        for v in range(2):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=45))
    for pid in [5, 6]:
        instances.append(VisitInstanceData(id=f"p{pid}_v0", patient_id=pid, duration=30))

    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 2, seed=77)
    # Override homes so clinician 0 is near p1 and clinician 1 near p2
    matrix["home_0"]["1"] = 10
    matrix["1"]["home_0"] = 10
    matrix["home_0"]["2"] = 55
    matrix["2"]["home_0"] = 55
    matrix["home_1"]["1"] = 55
    matrix["1"]["home_1"] = 55
    matrix["home_1"]["2"] = 10
    matrix["2"]["home_1"] = 10
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def quick_scenarios() -> list[Scenario]:
    return [
        Scenario("small", "quick", _build_quick_small),
        Scenario("medium", "quick", _build_quick_medium),
        Scenario("multi_clinician", "quick", _build_quick_multi_clinician),
    ]


# ────────────────────────────────────────────────────────────────────
# SUITE: scale — scaling curve from 10 to 200 instances
# ────────────────────────────────────────────────────────────────────


def _build_scale(n_instances: int, n_clinicians: int, days: list[str], seed: int,
                 cap_per_day: int = 8) -> SolverInput:
    """Generate a solvable scenario with roughly `n_instances` visits."""
    rng = random.Random(seed)
    # Estimate patients needed — most have 2 visits
    n_patients = max(1, n_instances // 2 + 2)
    patients = []
    instances = []
    next_id = 0
    for pid in range(1, n_patients + 1):
        if next_id >= n_instances:
            break
        req = rng.choice([1, 2, 2, 2])
        req = min(req, n_instances - next_id)
        dur = rng.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=dur,
                        required_visits=req, min_days_between_visits=1,
                        max_days_between_visits=len(days))
        )
        for v in range(req):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur))
            next_id += 1

    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, n_clinicians, seed=seed * 11)
    clinicians = [ClinicianData(max_visits_per_day=cap_per_day) for _ in range(n_clinicians)]
    return SolverInput(
        patients=patients, instances=instances, clinicians=clinicians,
        travel_matrix=matrix, start_date=days[0], working_days=days,
    )


def scale_scenarios() -> list[Scenario]:
    return [
        Scenario("scale_10_c1",  "scale", lambda: _build_scale(10,  1, WEEK,     seed=1)),
        Scenario("scale_25_c1",  "scale", lambda: _build_scale(25,  1, WEEK,     seed=2, cap_per_day=6)),
        Scenario("scale_25_c3",  "scale", lambda: _build_scale(25,  3, WEEK,     seed=3)),
        Scenario("scale_50_c3",  "scale", lambda: _build_scale(50,  3, WEEK,     seed=4)),
        Scenario("scale_50_c5",  "scale", lambda: _build_scale(50,  5, WEEK,     seed=5)),
        Scenario("scale_100_c5", "scale", lambda: _build_scale(100, 5, TWO_WEEK, seed=6)),
        Scenario("scale_100_c10","scale", lambda: _build_scale(100, 10, WEEK,    seed=7)),
        Scenario("scale_200_c10","scale", lambda: _build_scale(200, 10, TWO_WEEK, seed=8)),
    ]


# ────────────────────────────────────────────────────────────────────
# SUITE: infeasibility — known-impossible inputs
# ────────────────────────────────────────────────────────────────────


def _build_infeas_spacing_overflow() -> SolverInput:
    """3 visits with min_gap=2 → need 7 days, horizon is 5. Infeasible."""
    patients = [PatientData(id=1, name="Spaced", visit_duration_minutes=30,
                             required_visits=3, min_days_between_visits=2)]
    instances = [VisitInstanceData(id=f"p1_v{i}", patient_id=1, duration=30) for i in range(3)]
    matrix = {"home_0": {"1": 10}, "1": {"home_0": 10}}
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_infeas_window_too_short() -> SolverInput:
    """Patient has 20-min windows, needs 60-min visits."""
    p = PatientData(id=1, name="Short", visit_duration_minutes=60, required_visits=1)
    wins = {str(wd): [{"start_minute": 540, "end_minute": 560}] for wd in range(7)}
    i = VisitInstanceData(id="p1_v0", patient_id=1, duration=60, availability_windows=wins)
    matrix = {"home_0": {"1": 10}, "1": {"home_0": 10}}
    return SolverInput(
        patients=[p], instances=[i], clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_infeas_no_eligible() -> SolverInput:
    """Patient only eligible for a clinician that doesn't exist in this horizon."""
    p = PatientData(id=1, name="Orphan", visit_duration_minutes=30, required_visits=1)
    # Eligibility refers to clinician index 5, but we only have 2 clinicians
    i = VisitInstanceData(id="p1_v0", patient_id=1, duration=30, eligible_clinician_indices=[5])
    matrix = {"home_0": {"1": 10}, "home_1": {"1": 10}, "1": {"home_0": 10, "home_1": 10}}
    return SolverInput(
        patients=[p], instances=[i], clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_infeas_window_on_nonworking_day() -> SolverInput:
    """Patient only available Saturday but working days are Mon-Fri."""
    p = PatientData(id=1, name="Saturday", visit_duration_minutes=30, required_visits=1)
    wins = {"6": [{"start_minute": 540, "end_minute": 1020}]}  # wday 6 = Sat in ctx convention
    i = VisitInstanceData(id="p1_v0", patient_id=1, duration=30, availability_windows=wins)
    matrix = {"home_0": {"1": 10}, "1": {"home_0": 10}}
    return SolverInput(
        patients=[p], instances=[i], clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_infeas_overcapacity() -> SolverInput:
    """More 120-min visits than fit in a week's day capacity."""
    # 1 clinician × 5 days × cap=4 per day = 20 visit slots; demand 25 of 120-min
    patients = [
        PatientData(id=i, name=f"P{i}", visit_duration_minutes=120, required_visits=1)
        for i in range(1, 26)
    ]
    instances = [VisitInstanceData(id=f"p{p.id}_v0", patient_id=p.id, duration=120)
                 for p in patients]
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 1, seed=99, home_min=10, home_max=15, inter_min=10, inter_max=20)
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(max_visits_per_day=4)],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def infeasibility_scenarios() -> list[Scenario]:
    return [
        Scenario(
            "infeas_spacing_overflow", "infeasibility",
            _build_infeas_spacing_overflow,
            expect_feasible=False,
            expected_reasons={"spacing_infeasible_for_horizon"},
        ),
        Scenario(
            "infeas_window_too_short", "infeasibility",
            _build_infeas_window_too_short,
            expect_feasible=False,
            expected_reasons={"window_too_short", "no_legal_slot"},
        ),
        Scenario(
            "infeas_no_eligible", "infeasibility",
            _build_infeas_no_eligible,
            expect_feasible=False,
            expected_reasons={"no_legal_slot", "no_eligible_clinician"},
        ),
        Scenario(
            "infeas_window_nonworking_day", "infeasibility",
            _build_infeas_window_on_nonworking_day,
            expect_feasible=False,
            expected_reasons={"no_window_any_working_day", "no_legal_slot"},
        ),
        Scenario(
            "infeas_overcapacity", "infeasibility",
            _build_infeas_overcapacity,
            expect_feasible=False,
        ),
    ]


# ────────────────────────────────────────────────────────────────────
# SUITE: warm_start — assignment stability under perturbation
# ────────────────────────────────────────────────────────────────────


def _build_warmstart_base() -> SolverInput:
    random.seed(333)
    patients, instances = [], []
    for pid in range(1, 11):
        req = random.choice([1, 2, 2])
        dur = random.choice([30, 45, 60])
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=dur,
                        required_visits=req, min_days_between_visits=1)
        )
        for v in range(req):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=dur))
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 2, seed=3333)
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _perturb_add_patient(inp: SolverInput) -> SolverInput:
    """Add a new patient to the existing scenario."""
    new_pid = max(p.id for p in inp.patients) + 1
    new_patient = PatientData(
        id=new_pid, name=f"NewP{new_pid}", visit_duration_minutes=45,
        required_visits=1, min_days_between_visits=1,
    )
    new_instance = VisitInstanceData(id=f"p{new_pid}_v0", patient_id=new_pid, duration=45)

    new_matrix = {k: dict(v) for k, v in inp.travel_matrix.items()}
    pid_str = str(new_pid)
    # Give the new patient reasonable travel values
    for c in range(len(inp.clinicians)):
        new_matrix[f"home_{c}"][pid_str] = 18
    new_matrix[pid_str] = {f"home_{c}": 18 for c in range(len(inp.clinicians))}
    for existing_pid in new_matrix:
        if existing_pid.startswith("home_") or existing_pid == pid_str:
            continue
        new_matrix[pid_str][existing_pid] = 25
        new_matrix[existing_pid][pid_str] = 25

    return SolverInput(
        patients=list(inp.patients) + [new_patient],
        instances=list(inp.instances) + [new_instance],
        clinicians=list(inp.clinicians),
        travel_matrix=new_matrix,
        start_date=inp.start_date,
        working_days=list(inp.working_days),
    )


def _perturb_cancel_patient(inp: SolverInput) -> SolverInput:
    """Remove one patient from the scenario."""
    victim_pid = inp.patients[-1].id
    new_patients = [p for p in inp.patients if p.id != victim_pid]
    new_instances = [i for i in inp.instances if i.patient_id != victim_pid]
    return SolverInput(
        patients=new_patients, instances=new_instances,
        clinicians=list(inp.clinicians),
        travel_matrix=inp.travel_matrix,
        start_date=inp.start_date,
        working_days=list(inp.working_days),
    )


def _perturb_shorter_clinician_day(inp: SolverInput) -> SolverInput:
    """Shrink clinician 0's workday by 2 hours."""
    new_clinicians = list(inp.clinicians)
    c0 = new_clinicians[0].model_copy()
    c0.workday_end_minute = c0.workday_end_minute - 120
    new_clinicians[0] = c0
    return SolverInput(
        patients=list(inp.patients), instances=list(inp.instances),
        clinicians=new_clinicians, travel_matrix=inp.travel_matrix,
        start_date=inp.start_date, working_days=list(inp.working_days),
    )


def _warmstart_check(perturb_name: str, perturb_fn: Callable[[SolverInput], SolverInput],
                     min_stability: float):
    """Return an extra_check closure that measures warm-start stability."""

    def check(out: SolverOutput, inp: SolverInput) -> tuple[bool, str]:
        perturbed = perturb_fn(inp)
        warm_out = benders_solve(perturbed, time_budget=TIME_BUDGET, upper_bound=out)

        base_assign = {v.instance_id: (v.clinician_idx, v.date) for v in out.planned_visits}
        warm_assign = {v.instance_id: (v.clinician_idx, v.date) for v in warm_out.planned_visits}

        shared_ids = set(base_assign.keys()) & set(warm_assign.keys())
        if not shared_ids:
            return False, "no shared instances between base and perturbed"

        stable = sum(1 for iid in shared_ids if base_assign[iid] == warm_assign[iid])
        stability_pct = stable / len(shared_ids) * 100

        if stability_pct < min_stability:
            return False, (
                f"{perturb_name}: stability {stability_pct:.0f}% "
                f"({stable}/{len(shared_ids)} unchanged) < {min_stability:.0f}%"
            )
        return True, ""

    return check


def warm_start_scenarios() -> list[Scenario]:
    # Different perturbations disrupt the plan by different amounts.
    # These thresholds are empirical baselines — current solver uses
    # lightweight hints-only warm-start, not a continuity-weighted
    # objective.  Raising these thresholds would require a real
    # continuity penalty term in the envelope.
    specs = [
        ("ws_add_patient",    _perturb_add_patient,            70.0),
        ("ws_cancel_patient", _perturb_cancel_patient,         40.0),
        ("ws_shrink_day",     _perturb_shorter_clinician_day,  70.0),
    ]
    return [
        Scenario(
            name, "warm_start", _build_warmstart_base,
            extra_check=_warmstart_check(name, perturb, stability),
        )
        for name, perturb, stability in specs
    ]


# ────────────────────────────────────────────────────────────────────
# SUITE: realism — clustered geography, realistic windows
# ────────────────────────────────────────────────────────────────────


def _build_realism_clusters() -> SolverInput:
    """20 patients in 3 geographic clusters, 2 clinicians with home near
    different clusters. The envelope should assign each cluster to its
    nearest clinician for clean load distribution."""
    clusters: list[list[str]] = [[], [], []]
    patients = []
    instances = []
    for pid in range(1, 21):
        cluster_idx = (pid - 1) // 7 if pid <= 21 else 2
        cluster_idx = min(cluster_idx, 2)
        clusters[cluster_idx].append(str(pid))
        req = 2 if pid % 3 != 0 else 1
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=45,
                        required_visits=req, min_days_between_visits=1)
        )
        for v in range(req):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=45))

    # Clinician 0's home is near cluster 0; clinician 1's home is near cluster 1.
    # Cluster 2 is between them.
    matrix = _cluster_matrix(clusters, cluster_homes=[0, 1], n_clinicians=2,
                             intra=6, inter=35, home_near=10, home_far=50)
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _build_realism_am_pm() -> SolverInput:
    """Mix of AM-only, PM-only, and flexible patients with realistic wide
    windows (8-12, 1-6).  The envelope should pick each window according
    to patient availability; the timing pass must honor it exactly.
    """
    patients = []
    instances = []
    am_windows = {str(wd): [{"start_minute": 480, "end_minute": 720}] for wd in range(1, 6)}
    pm_windows = {str(wd): [{"start_minute": 780, "end_minute": 1080}] for wd in range(1, 6)}

    # 3 AM-only, 3 PM-only, 4 flexible — total 13 visits across 10 vehicles
    for pid in range(1, 4):
        patients.append(PatientData(id=pid, name=f"AM{pid}",
                                     visit_duration_minutes=60, required_visits=2,
                                     min_days_between_visits=1))
        for v in range(2):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid,
                                               duration=60, availability_windows=am_windows))
    for pid in range(4, 7):
        patients.append(PatientData(id=pid, name=f"PM{pid}",
                                     visit_duration_minutes=45, required_visits=1,
                                     min_days_between_visits=1))
        instances.append(VisitInstanceData(id=f"p{pid}_v0", patient_id=pid,
                                            duration=45, availability_windows=pm_windows))
    for pid in range(7, 11):
        patients.append(PatientData(id=pid, name=f"Flex{pid}",
                                     visit_duration_minutes=30, required_visits=1,
                                     min_days_between_visits=1))
        instances.append(VisitInstanceData(id=f"p{pid}_v0", patient_id=pid, duration=30))

    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 2, seed=555)
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData(), ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def realism_scenarios() -> list[Scenario]:
    return [
        Scenario("clustered_geography", "realism", _build_realism_clusters),
        Scenario("am_pm_mixed", "realism", _build_realism_am_pm),
    ]


# ────────────────────────────────────────────────────────────────────
# SUITE: adversarial — dense constraints, pathological inputs
# ────────────────────────────────────────────────────────────────────


def _build_adv_tight_windows() -> SolverInput:
    """6 patients with only a 9-11 window on every day — force the envelope
    to spread them across days. Each visit is 60 min so max ~1 per day."""
    patients = [
        PatientData(id=i, name=f"P{i}", visit_duration_minutes=60, required_visits=1)
        for i in range(1, 7)
    ]
    wins = {str(wd): [{"start_minute": 540, "end_minute": 660}] for wd in range(7)}
    instances = [
        VisitInstanceData(id=f"p{i}_v0", patient_id=i, duration=60, availability_windows=wins)
        for i in range(1, 7)
    ]
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 1, seed=881)
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=TWO_WEEK[0], working_days=TWO_WEEK,
    )


def _build_adv_dense_blocks() -> SolverInput:
    """Each workday has a ~90 min mid-morning block — forces the solver
    to thread visits around them but without colliding with the lunch
    window (which the timing pass enforces strictly)."""
    random.seed(22)
    patients = []
    instances = []
    for pid in range(1, 7):
        req = random.choice([1, 2])
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=45,
                        required_visits=req, min_days_between_visits=1)
        )
        for v in range(req):
            instances.append(VisitInstanceData(id=f"p{pid}_v{v}", patient_id=pid, duration=45))

    # Block 9:00-10:30 every weekday — mid-morning, clear of lunch
    blocks = []
    for date in WEEK:
        blocks.append(CalendarBlockData(
            clinician_idx=0, date=date,
            starts_at=f"{date}T09:00:00", ends_at=f"{date}T10:30:00",
        ))
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 1, seed=2222)
    return SolverInput(
        patients=patients, instances=instances, clinicians=[ClinicianData()],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
        calendar_blocks=blocks,
    )


def _build_adv_skewed_eligibility() -> SolverInput:
    """5 clinicians, 50% of patients locked to clinician 0.  Flexible
    patients should be routed to other clinicians by the load-balance
    penalty rather than stacking on clinician 0."""
    random.seed(44)
    patients = []
    instances = []
    for pid in range(1, 17):
        eligible: list[int] = [0] if pid <= 8 else list(range(5))
        patients.append(
            PatientData(id=pid, name=f"P{pid}", visit_duration_minutes=30,
                        required_visits=1, min_days_between_visits=1)
        )
        instances.append(
            VisitInstanceData(id=f"p{pid}_v0", patient_id=pid, duration=30,
                              eligible_clinician_indices=eligible)
        )
    pids = [str(p.id) for p in patients]
    matrix = _random_matrix(pids, 5, seed=4444)
    return SolverInput(
        patients=patients, instances=instances,
        clinicians=[ClinicianData() for _ in range(5)],
        travel_matrix=matrix, start_date=WEEK[0], working_days=WEEK,
    )


def _check_adv_skewed_load(out: SolverOutput, inp: SolverInput) -> tuple[bool, str]:
    """Clinician 0 shouldn't absorb all flexible patients on top of its locked 8."""
    per_clin: dict[int, int] = {}
    for v in out.planned_visits:
        per_clin[v.clinician_idx] = per_clin.get(v.clinician_idx, 0) + 1
    c0 = per_clin.get(0, 0)
    if c0 > 12:
        return False, (f"clinician 0 took {c0} visits, expected ≤12 "
                       f"(distribution: {per_clin})")
    return True, ""


def adversarial_scenarios() -> list[Scenario]:
    return [
        Scenario("adv_tight_windows", "adversarial", _build_adv_tight_windows),
        Scenario("adv_dense_blocks",  "adversarial", _build_adv_dense_blocks),
        Scenario(
            "adv_skewed_eligibility", "adversarial",
            _build_adv_skewed_eligibility,
            extra_check=_check_adv_skewed_load,
        ),
    ]


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────

SUITES: dict[str, Callable[[], list[Scenario]]] = {
    "quick": quick_scenarios,
    "scale": scale_scenarios,
    "infeasibility": infeasibility_scenarios,
    "warm_start": warm_start_scenarios,
    "realism": realism_scenarios,
    "adversarial": adversarial_scenarios,
}


def main():
    parser = argparse.ArgumentParser(description="Benders solver benchmark suites")
    parser.add_argument(
        "--suite", default="quick",
        choices=list(SUITES.keys()) + ["all"],
        help="Which suite to run (default: quick)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Abort on first scenario failure",
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES,
        help=f"Samples per scenario (default: {DEFAULT_SAMPLES})",
    )
    args = parser.parse_args()

    suites_to_run: list[str]
    if args.suite == "all":
        suites_to_run = list(SUITES.keys())
    else:
        suites_to_run = [args.suite]

    all_reports: list[SuiteReport] = []
    total_failures = 0

    for suite_name in suites_to_run:
        scenarios = SUITES[suite_name]()
        report = run_suite(suite_name, scenarios, args.samples, args.strict)
        all_reports.append(report)
        total_failures += report.fail_count()
        if args.strict and report.fail_count() > 0:
            break

    # Overall summary
    print("\n" + "═" * 80)
    print(f"{'SUITE':<18s}{'PASSED':>10s}{'FAILED':>10s}{'TOTAL':>10s}")
    print("─" * 80)
    for report in all_reports:
        total = len(report.results)
        print(f"{report.name:<18s}{report.pass_count():>10d}{report.fail_count():>10d}{total:>10d}")
    print("═" * 80)

    sys.exit(1 if total_failures > 0 else 0)


if __name__ == "__main__":
    main()
