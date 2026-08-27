"""CP-SAT daily scheduling model for the VRPTW solver.

Given a fixed ordered list of visits for one clinician-day, finds optimal
start times using OR-Tools CP-SAT with interval variables and no-overlap
constraints.  Handles lunch, mandatory breaks, calendar blocks, and
locked visits in a single declarative model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ortools.sat.python import cp_model

from models import SolverInput, PlannedVisit, VisitInstanceData
from solver.context import (
    SLOT_STEP,
    TRANSIT_BUFFER,
    VehicleDef,
    SolverContext,
    datetime_to_minute,
    minutes_to_datetime,
    round_up,
)

logger = logging.getLogger(__name__)


@dataclass
class DayViolations:
    """Tier 2 violation metrics from daily scheduling (continuous measures)."""

    transit_excess_minutes: int = 0
    overtime_minutes: int = 0
    break_violations: int = 0
    lunch_window_violation: int = 0


@dataclass
class TimedRoute:
    """Result of concrete timing for one vehicle (clinician-day)."""

    vehicle_idx: int
    visits: list[PlannedVisit] = field(default_factory=list)
    lunch: dict | None = None
    drive_cost: int = 0
    dropped: list[dict] = field(default_factory=list)
    total_cost: int = 0
    violations: DayViolations = field(default_factory=DayViolations)

# Max solve time per vehicle (seconds).  With ~5 visits the model is tiny.
CPSAT_TIMING_BUDGET = 0.5


def _minute_from_iso(iso: str) -> int:
    """Extract minute-of-day from ISO datetime string."""
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def cpsat_time_vehicle_route(
    vehicle: VehicleDef,
    instances: list[VisitInstanceData],
    input: SolverInput,
    ctx: SolverContext,
) -> TimedRoute:
    """Produce concrete times for a vehicle's ordered visit sequence using CP-SAT.

    Uses interval variables + add_no_overlap for a declarative constraint model.
    All visits are optional — CP-SAT maximizes placed count, then minimizes cost.
    """
    clinician = vehicle.clinician
    travel = ctx.travel
    date = vehicle.date
    day_start = vehicle.day_start
    day_end = vehicle.day_end
    home_key = f"home_{vehicle.clinician_idx}"

    # ── Trivial cases ────────────────────────────────────────────────

    lunch_dur = clinician.lunch_duration_minutes
    half_w = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_w, day_start)
    lunch_latest = min(clinician.lunch_start_minute + half_w, day_end - lunch_dur)
    if lunch_latest < lunch_earliest:
        lunch_latest = lunch_earliest
    lunch_target = clinician.lunch_start_minute
    need_lunch = lunch_dur > 0

    vkey = (vehicle.clinician_idx, vehicle.day_index)
    locked_stops = []
    for lv in ctx.locked_visits_by_vehicle.get(vkey, ()):
        locked_stops.append(
            {
                "patient_id": lv.patient_id,
                "start_min": datetime_to_minute(lv.starts_at),
                "duration": lv.duration_minutes,
                "charting": clinician.charting_buffer_minutes,
            }
        )
    locked_stops.sort(key=lambda s: s["start_min"])

    if not instances and not locked_stops:
        lunch_start = round_up(lunch_earliest, SLOT_STEP)
        lunch = (
            {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
            if need_lunch
            else None
        )
        return TimedRoute(vehicle_idx=vehicle.vehicle_idx, lunch=lunch)

    if not instances:
        drive_cost = 0
        prev = home_key
        prev_minute: int | None = None
        for ls in locked_stops:
            drive_cost += travel(prev, str(ls["patient_id"]), prev_minute)
            prev = str(ls["patient_id"])
            prev_minute = ls["start_min"] + ls["duration"] + ls["charting"]
        drive_cost += travel(prev, home_key, prev_minute)
        lunch_start = round_up(lunch_earliest, SLOT_STEP)
        lunch = (
            {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
            if need_lunch
            else None
        )
        return TimedRoute(
            vehicle_idx=vehicle.vehicle_idx,
            lunch=lunch,
            drive_cost=drive_cost,
            total_cost=drive_cost,
        )

    n = len(instances)
    charting = clinician.charting_buffer_minutes
    max_drive = clinician.max_drive_minutes_per_day or float("inf")

    # Compute drive cost: build the full stop sequence (visits + locked)
    # in time order and sum legs.  Before CP-SAT runs we don't know exact
    # interleaving, so we merge by earliest feasible position: locked stops
    # are fixed in time, visits are in the given order starting after day_start.
    pids = [str(inst.patient_id) for inst in instances]

    # Rough per-instance time-of-day estimate for traffic-bucket selection —
    # the exact time isn't known until after CP-SAT solves, so this uses the
    # instance's own availability window on this weekday (if any) as a
    # stand-in, same approach as the envelope/subproblem stages upstream.
    dt_date = datetime.fromisoformat(date)
    wday_str = str((dt_date.weekday() + 1) % 7)

    def _repr_minute(inst) -> int | None:
        if inst.availability_windows:
            wins = inst.availability_windows.get(wday_str, [])
            if wins:
                return int(wins[0].get("start_minute", day_start))
        return None

    inst_minutes = [_repr_minute(inst) for inst in instances]
    minute_by_pid: dict[str, int | None] = {str(ls["patient_id"]): ls["start_min"] for ls in locked_stops}
    for pid, minute in zip(pids, inst_minutes):
        minute_by_pid.setdefault(pid, minute)

    all_stops: list[str] = []  # patient-id keys in route order
    li = 0  # cursor into locked_stops
    vi = 0  # cursor into pids
    # Rough merge: locked stops are sorted by start_min; visits are in given
    # order and will be placed after the last locked stop that precedes them.
    # Conservative estimate: interleave locked stops at their fixed time and
    # visits in order between them.
    while li < len(locked_stops) and vi < len(pids):
        # Compare locked stop time vs approximate visit time.
        # Visit approximate time: day_start + sum of prior durations (rough).
        ls = locked_stops[li]
        approx_visit_time = day_start + sum(inst.duration for inst in instances[:vi])
        if ls["start_min"] <= approx_visit_time:
            all_stops.append(str(ls["patient_id"]))
            li += 1
        else:
            all_stops.append(pids[vi])
            vi += 1
    while li < len(locked_stops):
        all_stops.append(str(locked_stops[li]["patient_id"]))
        li += 1
    while vi < len(pids):
        all_stops.append(pids[vi])
        vi += 1

    drive_cost = 0
    prev = home_key
    prev_minute: int | None = None
    for stop_pid in all_stops:
        drive_cost += travel(prev, stop_pid, minute_by_pid.get(stop_pid))
        prev = stop_pid
        prev_minute = minute_by_pid.get(stop_pid)
    drive_cost += travel(prev, home_key, prev_minute)

    # Drive limit hard check
    if drive_cost > max_drive:
        dropped = [
            {"instance_id": inst.id, "reason": "drive_limit"} for inst in instances
        ]
        lunch_start = round_up(lunch_earliest, SLOT_STEP)
        lunch = (
            {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
            if need_lunch
            else None
        )
        return TimedRoute(vehicle_idx=vehicle.vehicle_idx, lunch=lunch, dropped=dropped)

    # ── Availability windows ─────────────────────────────────────────
    # (dt_date / wday_str computed earlier, above, for the drive-cost estimate)

    inst_windows: list[list[dict]] = []
    inst_unavail_windows: list[list[dict]] = []
    for inst in instances:
        if inst.availability_windows:
            ws = inst.availability_windows.get(wday_str, [])
            inst_windows.append(
                sorted(ws, key=lambda w: w.get("start_minute", 0)) if ws else []
            )
        else:
            inst_windows.append([])

        inst_unavail_windows.append(inst.unavailability_windows.get(wday_str, []))

    # ── Build CP-SAT model ───────────────────────────────────────────

    model = cp_model.CpModel()
    all_intervals = []

    # Visit intervals (optional)
    visit_start_vars = []
    visit_end_vars = []
    visit_intervals = []
    visit_present = []

    for i, inst in enumerate(instances):
        footprint = inst.duration  # includes charting
        visit_dur = max(0, inst.duration - charting)  # display duration

        present = model.new_bool_var(f"vp_{i}")
        visit_present.append(present)

        start = model.new_int_var(day_start, day_end - footprint, f"vs_{i}")
        end = model.new_int_var(day_start + footprint, day_end, f"ve_{i}")
        model.add(end == start + footprint)

        interval = model.new_optional_interval_var(
            start, footprint, end, present, f"vi_{i}"
        )
        visit_start_vars.append(start)
        visit_end_vars.append(end)
        visit_intervals.append(interval)
        all_intervals.append(interval)

    # ── Sequencing constraints (fixed order) ─────────────────────────

    # First visit: must start after home transit
    if n > 0:
        home_transit = travel(home_key, pids[0], inst_minutes[0])
        model.add(visit_start_vars[0] >= day_start + home_transit).only_enforce_if(
            visit_present[0]
        )

    # Consecutive visits: transit + buffer between them
    for i in range(n - 1):
        transit_time = travel(pids[i], pids[i + 1], inst_minutes[i + 1]) + TRANSIT_BUFFER
        # If both present, enforce ordering with transit
        both = model.new_bool_var(f"both_{i}_{i + 1}")
        model.add_min_equality(both, [visit_present[i], visit_present[i + 1]])
        model.add(
            visit_start_vars[i + 1] >= visit_end_vars[i] + transit_time
        ).only_enforce_if(both)

    # ── 15-minute slot rounding ──────────────────────────────────────

    for i in range(n):
        # visit_start[i] must be a multiple of SLOT_STEP
        # Express: start = SLOT_STEP * slot_var
        slot_var = model.new_int_var(
            day_start // SLOT_STEP,
            (day_end) // SLOT_STEP,
            f"slot_{i}",
        )
        model.add(visit_start_vars[i] == slot_var * SLOT_STEP)

    # ── Calendar blocks (fixed intervals) ────────────────────────────

    for b_idx, cb in enumerate(ctx.calendar_blocks_by_vehicle.get(vkey, [])):
        b_start = datetime_to_minute(cb.starts_at)
        b_end = datetime_to_minute(cb.ends_at)
        b_size = b_end - b_start
        if b_size > 0:
            block_interval = model.new_fixed_size_interval_var(
                b_start, b_size, f"block_{b_idx}"
            )
            all_intervals.append(block_interval)

    # ── Locked visits (fixed intervals) ──────────────────────────────

    locked_intervals = []
    for l_idx, ls in enumerate(locked_stops):
        ls_size = ls["duration"] + ls["charting"]
        locked_iv = model.new_fixed_size_interval_var(
            ls["start_min"], ls_size, f"locked_{l_idx}"
        )
        all_intervals.append(locked_iv)
        locked_intervals.append((l_idx, ls))

    # Add transit constraints from/to locked visits for adjacent ordered visits
    for l_idx, ls in enumerate(locked_stops):
        ls_pid = str(ls["patient_id"])
        ls_end = ls["start_min"] + ls["duration"] + ls["charting"]
        # For each visit: if visit follows this locked visit in time, enforce transit
        for i in range(n):
            # If visit i starts after locked visit ends, enforce transit gap
            follows = model.new_bool_var(f"follows_locked_{l_idx}_{i}")
            model.add(visit_start_vars[i] >= ls_end).only_enforce_if(follows)
            model.add(visit_start_vars[i] < ls_end).only_enforce_if(follows.negated())
            # When follows and present, enforce transit from locked patient
            both_follows = model.new_bool_var(f"bf_locked_{l_idx}_{i}")
            model.add_min_equality(both_follows, [follows, visit_present[i]])
            transit_from_locked = travel(ls_pid, pids[i], inst_minutes[i]) + TRANSIT_BUFFER
            model.add(
                visit_start_vars[i] >= ls_end + transit_from_locked
            ).only_enforce_if(both_follows)

    # ── Lunch interval ───────────────────────────────────────────────

    if need_lunch:
        lunch_start_var = model.new_int_var(lunch_earliest, lunch_latest, "ls")
        lunch_end_var = model.new_int_var(
            lunch_earliest + lunch_dur, lunch_latest + lunch_dur, "le"
        )
        model.add(lunch_end_var == lunch_start_var + lunch_dur)

        # Slot rounding for lunch
        lunch_slot = model.new_int_var(
            lunch_earliest // SLOT_STEP,
            lunch_latest // SLOT_STEP + 1,
            "lunch_slot",
        )
        model.add(lunch_start_var == lunch_slot * SLOT_STEP)

        lunch_interval = model.new_interval_var(
            lunch_start_var, lunch_dur, lunch_end_var, "li"
        )
        all_intervals.append(lunch_interval)

        # Lunch drift for objective
        lunch_drift = model.new_int_var(0, day_end - day_start, "ld")
        diff = model.new_int_var(-(day_end - day_start), day_end - day_start, "ldiff")
        model.add(diff == lunch_start_var - lunch_target)
        model.add_abs_equality(lunch_drift, diff)

    # ── Break intervals ──────────────────────────────────────────────

    max_cont = clinician.max_continuous_work_minutes
    break_dur = clinician.required_break_minutes
    break_intervals = []

    if max_cont and max_cont > 0 and break_dur > 0 and n > 1:
        # Create optional break between each consecutive visit pair
        for i in range(n - 1):
            bp = model.new_bool_var(f"brk_p_{i}")
            b_start = model.new_int_var(day_start, day_end, f"brk_s_{i}")
            b_end = model.new_int_var(day_start, day_end, f"brk_e_{i}")
            model.add(b_end == b_start + break_dur)
            brk_iv = model.new_optional_interval_var(
                b_start, break_dur, b_end, bp, f"brk_{i}"
            )
            all_intervals.append(brk_iv)
            break_intervals.append((i, bp, b_start))

            # Break must be between visit i end and visit i+1 start
            both_present = model.new_bool_var(f"brk_both_{i}")
            model.add_min_equality(
                both_present, [visit_present[i], visit_present[i + 1]]
            )
            brk_active = model.new_bool_var(f"brk_act_{i}")
            model.add_min_equality(brk_active, [bp, both_present])
            model.add(b_start >= visit_end_vars[i]).only_enforce_if(brk_active)
            model.add(b_end <= visit_start_vars[i + 1]).only_enforce_if(brk_active)

        # Enforce breaks using actual CP-SAT time variables.
        # For every pair (i, j) where i < j: if all visits i..j are present
        # AND no break or lunch falls between them, then the time span from
        # visit_start[i] to visit_end[j] must not exceed max_cont.
        # Equivalently: if the span exceeds max_cont, at least one break
        # (or lunch) must be active between i and j.
        for i in range(n):
            for j in range(i + 1, n):
                # Quick static check: minimum possible span (back-to-back).
                min_span = sum(instances[k].duration for k in range(i, j + 1))
                for k in range(i, j):
                    min_span += travel(pids[k], pids[k + 1], inst_minutes[k + 1]) + TRANSIT_BUFFER
                if min_span <= max_cont:
                    continue  # can't violate even back-to-back — skip

                # All visits in [i, j] must be present for this to matter
                all_present = model.new_bool_var(f"ap_{i}_{j}")
                model.add_min_equality(
                    all_present, [visit_present[k] for k in range(i, j + 1)]
                )

                # Actual span exceeds limit
                span_exceeds = model.new_bool_var(f"se_{i}_{j}")
                span = model.new_int_var(0, day_end - day_start, f"span_{i}_{j}")
                model.add(span == visit_end_vars[j] - visit_start_vars[i])
                model.add(span > max_cont).only_enforce_if(span_exceeds)
                model.add(span <= max_cont).only_enforce_if(span_exceeds.negated())

                # When all present AND span exceeds: need a break or lunch between i and j
                need_break = model.new_bool_var(f"nb_{i}_{j}")
                model.add_min_equality(need_break, [all_present, span_exceeds])

                break_vars_in_range = [
                    break_intervals[k][1]
                    for k in range(i, min(j, len(break_intervals)))
                ]
                if break_vars_in_range:
                    model.add(sum(break_vars_in_range) >= 1).only_enforce_if(need_break)

    # ── Availability window constraints (HARD) ───────────────────────
    # Under Benders, availability windows are hard: the timing pass either
    # honors them or drops the visit (which feeds back as a conflict to
    # the outer loop).  No soft override — that was the legacy escape
    # hatch the Benders architecture was built to eliminate.

    for i in range(n):
        windows = inst_windows[i]
        if not windows:
            continue

        # Commit to at least one window when the visit is present; the
        # chosen window's bounds hard-constrain the visit start.
        window_bools = []
        for w_idx, w in enumerate(windows):
            w_start = w.get("start_minute", 0)
            w_end = w.get("end_minute", 1440)
            visit_dur = max(0, instances[i].duration - charting)
            wb = model.new_bool_var(f"win_{i}_{w_idx}")
            model.add(visit_start_vars[i] >= w_start).only_enforce_if(wb)
            model.add(visit_start_vars[i] + visit_dur <= w_end).only_enforce_if(wb)
            window_bools.append(wb)

        # Require some window to be satisfied when the visit is present.
        # If infeasible, CP-SAT will set visit_present[i] = 0 (drop the
        # visit) rather than violate the window.
        model.add(sum(window_bools) >= 1).only_enforce_if(visit_present[i])

    # ── Unavailability window constraints (HARD, no override) ────────
    # Unlike availability windows, these are blackout ranges: a visit must
    # never overlap one, regardless of whether availability windows are
    # defined at all. Modeled as "entirely before" OR "entirely after".

    for i in range(n):
        unavail = inst_unavail_windows[i]
        if not unavail:
            continue

        for w_idx, w in enumerate(unavail):
            w_start = w.get("start_minute", 0)
            w_end = w.get("end_minute", 1440)
            before = model.new_bool_var(f"unavail_before_{i}_{w_idx}")
            after = model.new_bool_var(f"unavail_after_{i}_{w_idx}")
            model.add(visit_end_vars[i] <= w_start).only_enforce_if(before)
            model.add(visit_start_vars[i] >= w_end).only_enforce_if(after)
            model.add(before + after >= 1).only_enforce_if(visit_present[i])

    # ── No-overlap ───────────────────────────────────────────────────

    model.add_no_overlap(all_intervals)

    # ── Objective ────────────────────────────────────────────────────

    # Maximize visits placed (large weight), then minimize penalties
    visit_value = 1_000_000
    penalty_terms = []

    # Lunch drift penalty
    if need_lunch:
        penalty_terms.append(lunch_drift)

    # (No soft override penalties — windows are hard in Benders mode.)

    total_penalty = model.new_int_var(0, 10_000_000, "tp")
    if penalty_terms:
        model.add(total_penalty == sum(penalty_terms))
    else:
        model.add(total_penalty == 0)

    model.minimize(total_penalty - visit_value * sum(visit_present))

    # ── Solve ────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = CPSAT_TIMING_BUDGET
    solver.parameters.num_workers = 1  # small model, single thread is fastest

    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Complete failure — return empty with all dropped
        logger.warning("CP-SAT timing infeasible for %d visits on %s", n, date)
        dropped = [
            {"instance_id": inst.id, "reason": "cpsat_infeasible"} for inst in instances
        ]
        lunch_start = round_up(lunch_earliest, SLOT_STEP)
        lunch_pl = (
            {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
            if need_lunch
            else None
        )
        return TimedRoute(
            vehicle_idx=vehicle.vehicle_idx, lunch=lunch_pl, dropped=dropped
        )

    # ── Extract solution ─────────────────────────────────────────────

    visits: list[PlannedVisit] = []
    dropped: list[dict] = []

    for i, inst in enumerate(instances):
        if solver.value(visit_present[i]):
            start_min = solver.value(visit_start_vars[i])
            visit_dur = max(0, inst.duration - charting)
            starts_at = minutes_to_datetime(date, start_min)
            ends_at = minutes_to_datetime(date, start_min + visit_dur)
            visits.append(
                PlannedVisit(
                    instance_id=inst.id,
                    patient_id=inst.patient_id,
                    clinician_idx=vehicle.clinician_idx,
                    date=date,
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
            )
        else:
            dropped.append({"instance_id": inst.id, "reason": "cpsat_dropped"})

    # Lunch placement
    lunch_placement = None
    if need_lunch:
        ls = solver.value(lunch_start_var)
        lunch_placement = {"start_minute": ls, "end_minute": ls + lunch_dur}

    # ── Recompute exact drive cost from placed visits + locked stops ──
    # The pre-solve estimate used approximate interleaving; now we know
    # exactly which visits were placed and their times.
    if visits:
        # (start_minute, departure_minute, patient_id_str) — sort/sequence by
        # arrival, but use each stop's departure (end of visit, not arrival)
        # as the minute passed to the NEXT leg's travel() call.
        placed_stops: list[tuple[int, int, str]] = []
        for v in visits:
            placed_stops.append((
                _minute_from_iso(v.starts_at), _minute_from_iso(v.ends_at), str(v.patient_id)
            ))
        for ls_stop in locked_stops:
            departure = ls_stop["start_min"] + ls_stop["duration"] + ls_stop["charting"]
            placed_stops.append((ls_stop["start_min"], departure, str(ls_stop["patient_id"])))
        placed_stops.sort(key=lambda stop: stop[0])
        drive_cost = 0
        prev = home_key
        prev_minute: int | None = None
        for _start_minute, departure_minute, pid_str in placed_stops:
            drive_cost += travel(prev, pid_str, prev_minute)
            prev = pid_str
            prev_minute = departure_minute
        drive_cost += travel(prev, home_key, prev_minute)

    # ── Compute violation metrics ────────────────────────────────────

    # Lunch window violation: minutes outside the lunch window
    lunch_window_viol = 0
    if lunch_placement:
        actual_drift = abs(lunch_placement["start_minute"] - lunch_target)
        if lunch_placement["start_minute"] < lunch_earliest:
            lunch_window_viol = lunch_earliest - lunch_placement["start_minute"]
        elif lunch_placement["start_minute"] > lunch_latest:
            lunch_window_viol = lunch_placement["start_minute"] - lunch_latest
    elif need_lunch:
        actual_drift = 200
        lunch_window_viol = 200
    else:
        actual_drift = 0

    # Overtime: check if last visit end + return home > day_end
    overtime = 0
    if visits:
        last_end = _minute_from_iso(visits[-1].ends_at) + charting
        return_home_time = travel(str(visits[-1].patient_id), home_key, _minute_from_iso(visits[-1].ends_at))
        effective_end = last_end + return_home_time
        if effective_end > day_end:
            overtime = effective_end - day_end

    # Transit excess: check if any consecutive visit pair has insufficient gap
    transit_excess = 0
    for i in range(len(visits) - 1):
        v_end = _minute_from_iso(visits[i].ends_at) + charting
        v_next_start = _minute_from_iso(visits[i + 1].starts_at)
        needed = (
            travel(str(visits[i].patient_id), str(visits[i + 1].patient_id), v_end)
            + TRANSIT_BUFFER
        )
        gap = v_next_start - v_end
        if gap < needed:
            transit_excess += needed - gap

    # Break violations: count consecutive-work spans that exceed max_continuous
    break_viols = 0
    if max_cont and max_cont > 0 and len(visits) > 1:
        acc_work = 0
        for i, v in enumerate(visits):
            v_dur = (
                _minute_from_iso(v.ends_at) - _minute_from_iso(v.starts_at) + charting
            )
            if i > 0:
                prev_end = _minute_from_iso(visits[i - 1].ends_at) + charting
                cur_start = _minute_from_iso(v.starts_at)
                gap = cur_start - prev_end
                transit_t = travel(str(visits[i - 1].patient_id), str(v.patient_id), prev_end)
                # If gap is large enough to be a break, reset accumulator
                if gap >= break_dur + transit_t:
                    acc_work = 0
                else:
                    acc_work += transit_t
            acc_work += v_dur
            if acc_work > max_cont:
                break_viols += 1
                acc_work = 0  # assume break taken after violation

    violations = DayViolations(
        transit_excess_minutes=transit_excess,
        overtime_minutes=overtime,
        break_violations=break_viols,
        lunch_window_violation=lunch_window_viol,
    )

    return TimedRoute(
        vehicle_idx=vehicle.vehicle_idx,
        visits=visits,
        lunch=lunch_placement,
        drive_cost=drive_cost,
        dropped=dropped,
        total_cost=drive_cost + actual_drift,
        violations=violations,
    )
