"""Concrete timing post-processor for the VRPTW solver.

Given an ordered list of visit instances assigned to a vehicle (= clinician-day),
produces concrete start/end times with 15-min granularity, lunch insertion,
mandatory breaks, calendar block avoidance, and locked visit interleaving.

Adapted from cpsat_routing._evaluate_route — same timing logic, parameterized
per vehicle rather than using global clinician state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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


@dataclass
class DayViolations:
    """Tier 2 violation metrics from daily scheduling (continuous measures)."""

    transit_excess_minutes: int = 0  # total minutes of transit that didn't fit
    overtime_minutes: int = 0  # minutes past workday end
    break_violations: int = 0  # count of missed mandatory breaks
    lunch_window_violation: int = 0  # minutes outside lunch window (0 = within)


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


def time_vehicle_route(
    vehicle: VehicleDef,
    instances: list[VisitInstanceData],
    input: SolverInput,
    ctx: SolverContext,
) -> TimedRoute:
    """Produce concrete times for a vehicle's ordered visit sequence.

    This is the post-hoc timing step that handles constraints too granular
    for PyVRP: 15-min slot rounding, lunch insertion, mandatory breaks,
    calendar blocks, locked visit interleaving, and charting buffer.
    """
    clinician = vehicle.clinician
    travel = ctx.travel
    date = vehicle.date
    home_key = f"home_{vehicle.clinician_idx}"

    day_start_minute = vehicle.day_start
    day_end_minute = vehicle.day_end

    # Lunch config
    half_w = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_w, day_start_minute)
    lunch_latest = min(
        clinician.lunch_start_minute + half_w,
        day_end_minute - clinician.lunch_duration_minutes,
    )
    if lunch_latest < lunch_earliest:
        lunch_latest = lunch_earliest
    lunch_dur = clinician.lunch_duration_minutes
    lunch_target = clinician.lunch_start_minute
    need_lunch = lunch_dur > 0

    # Calendar blocks for this vehicle (clinician, day)
    vkey = (vehicle.clinician_idx, vehicle.day_index)
    day_blocks: list[tuple[int, int]] = []
    for cb in ctx.calendar_blocks_by_vehicle.get(vkey, ()):
        day_blocks.append(
            (datetime_to_minute(cb.starts_at), datetime_to_minute(cb.ends_at))
        )
    sorted_blocks = sorted(day_blocks) if day_blocks else []

    # Locked visits for this vehicle
    locked_stops: list[dict] = []
    for lv in ctx.locked_visits_by_vehicle.get(vkey, ()):
        s = datetime_to_minute(lv.starts_at)
        locked_stops.append(
            {
                "patient_id": lv.patient_id,
                "start_min": s,
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
        # Only locked visits — compute drive cost through them
        drive_cost = 0
        prev = home_key
        for ls in locked_stops:
            drive_cost += travel(prev, str(ls["patient_id"]))
            prev = str(ls["patient_id"])
        drive_cost += travel(prev, home_key)
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

    max_cont = clinician.max_continuous_work_minutes
    break_dur = clinician.required_break_minutes
    charting = clinician.charting_buffer_minutes
    max_drive = clinician.max_drive_minutes_per_day or float("inf")

    # Pre-compute availability windows per instance
    dt_date = datetime.fromisoformat(date)
    wday_str = str((dt_date.weekday() + 1) % 7)

    sorted_windows: dict[int, list[dict]] = {}
    for i, inst in enumerate(instances):
        if inst.availability_windows:
            ws = inst.availability_windows.get(wday_str, [])
            if ws:
                sorted_windows[i] = sorted(ws, key=lambda w: w.get("start_minute", 0))

    # ── Evaluate the route ordering ──────────────────────────────────

    current_time = day_start_minute
    prev_key = home_key
    accumulated_work = 0
    lunch_taken = False
    lunch_placement = None
    visits: list[PlannedVisit] = []
    drive_cost = 0
    dropped: list[dict] = []

    pending_locked = list(locked_stops)
    locked_cursor = 0

    for idx, inst in enumerate(instances):
        pid_key = str(inst.patient_id)
        visit_dur = max(0, inst.duration - charting)
        footprint = inst.duration

        # Snapshot state before attempting this visit
        snap = (
            current_time,
            prev_key,
            accumulated_work,
            drive_cost,
            locked_cursor,
            lunch_taken,
            lunch_placement,
        )

        transit = travel(prev_key, pid_key)
        transit_buf = TRANSIT_BUFFER if prev_key != home_key else 0
        raw_start = current_time + transit + transit_buf

        took_break = False
        if (
            max_cont
            and max_cont > 0
            and accumulated_work + transit + footprint > max_cont
        ):
            raw_start += break_dur
            accumulated_work = 0
            took_break = True

        earliest_start = round_up(raw_start, SLOT_STEP)

        # Fixpoint: process locked stops and calendar blocks until stable
        prev_earliest = -1
        while prev_earliest != earliest_start:
            prev_earliest = earliest_start

            while locked_cursor < len(pending_locked):
                ls = pending_locked[locked_cursor]
                if ls["start_min"] >= earliest_start + footprint:
                    break

                locked_cursor += 1
                ls_pid = str(ls["patient_id"])
                ls_end = ls["start_min"] + ls["duration"] + ls["charting"]

                t_to_locked = travel(prev_key, ls_pid)
                drive_cost += t_to_locked
                prev_key = ls_pid

                current_time = max(current_time + t_to_locked, ls_end)
                accumulated_work += t_to_locked + ls["duration"] + ls["charting"]

                transit = travel(prev_key, pid_key)
                transit_buf = TRANSIT_BUFFER
                raw_start = current_time + transit + transit_buf
                if (
                    max_cont
                    and max_cont > 0
                    and accumulated_work + transit + footprint > max_cont
                ):
                    raw_start += break_dur
                    accumulated_work = 0
                    took_break = True
                else:
                    took_break = False
                earliest_start = round_up(raw_start, SLOT_STEP)

            for b_start, b_end in sorted_blocks:
                if b_start >= earliest_start + footprint:
                    break
                if earliest_start < b_end:
                    # Calendar block forces idle time — count the work done
                    # waiting through the block as continuous work (transit +
                    # idle doesn't reset the break clock).
                    idle_through_block = b_end - earliest_start
                    accumulated_work += idle_through_block
                    earliest_start = round_up(b_end + TRANSIT_BUFFER, SLOT_STEP)

        drive_cost += transit

        # Availability window: pick closest feasible window
        windows = sorted_windows.get(idx)
        if windows:
            best_candidate = None
            best_gap = float("inf")
            for w in windows:
                w_start = w.get("start_minute", 0)
                w_end = w.get("end_minute", 1440)
                candidate = round_up(max(earliest_start, w_start), SLOT_STEP)
                if candidate + visit_dur <= w_end:
                    gap = candidate - earliest_start
                    if gap < best_gap:
                        best_gap = gap
                        best_candidate = candidate
            if best_candidate is not None:
                earliest_start = best_candidate

        # Lunch insertion
        lunch_inserted_now = False
        if lunch_dur > 0 and not lunch_taken and earliest_start >= lunch_earliest:
            lunch_start = max(lunch_earliest, current_time)
            lunch_start = round_up(lunch_start, SLOT_STEP)
            for b_start, b_end in sorted_blocks:
                if b_start >= lunch_start + lunch_dur:
                    break
                if lunch_start < b_end:
                    lunch_start = round_up(b_end, SLOT_STEP)
            lunch_end = lunch_start + lunch_dur
            if lunch_start <= lunch_latest:
                lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_end}
                if earliest_start < lunch_end:
                    earliest_start = round_up(lunch_end, SLOT_STEP)
                lunch_taken = True
                lunch_inserted_now = True
                accumulated_work = 0

        if earliest_start + footprint > day_end_minute:
            # Visit doesn't fit — if lunch was just inserted, retry without it
            # (defer lunch to after this visit instead of dropping the visit).
            if lunch_inserted_now:
                (
                    current_time,
                    prev_key,
                    accumulated_work,
                    drive_cost,
                    locked_cursor,
                    lunch_taken,
                    lunch_placement,
                ) = snap
                # Re-derive earliest_start without lunch
                transit = travel(prev_key, pid_key)
                transit_buf = TRANSIT_BUFFER if prev_key != home_key else 0
                raw_start = current_time + transit + transit_buf
                took_break = False
                if (
                    max_cont
                    and max_cont > 0
                    and accumulated_work + transit + footprint > max_cont
                ):
                    raw_start += break_dur
                    accumulated_work = 0
                    took_break = True
                earliest_start = round_up(raw_start, SLOT_STEP)
                # Re-check calendar blocks
                for b_start, b_end in sorted_blocks:
                    if b_start >= earliest_start + footprint:
                        break
                    if earliest_start < b_end:
                        earliest_start = round_up(b_end + TRANSIT_BUFFER, SLOT_STEP)
                drive_cost += transit
                # Re-check availability windows
                windows = sorted_windows.get(idx)
                if windows:
                    best_candidate = None
                    best_gap = float("inf")
                    for w in windows:
                        w_start = w.get("start_minute", 0)
                        w_end = w.get("end_minute", 1440)
                        candidate = round_up(max(earliest_start, w_start), SLOT_STEP)
                        if candidate + visit_dur <= w_end:
                            gap = candidate - earliest_start
                            if gap < best_gap:
                                best_gap = gap
                                best_candidate = candidate
                    if best_candidate is not None:
                        earliest_start = best_candidate

            if earliest_start + footprint > day_end_minute:
                # Still doesn't fit — restore state and drop
                (
                    current_time,
                    prev_key,
                    accumulated_work,
                    drive_cost,
                    locked_cursor,
                    lunch_taken,
                    lunch_placement,
                ) = snap
                dropped.append({"instance_id": inst.id, "reason": "workday_overflow"})
                continue

        starts_at = minutes_to_datetime(date, earliest_start)
        ends_at = minutes_to_datetime(date, earliest_start + visit_dur)

        soft_override = False
        if windows:
            soft_override = not any(
                w.get("start_minute", 0) <= earliest_start < w.get("end_minute", 1440)
                for w in windows
            )

        visits.append(
            PlannedVisit(
                instance_id=inst.id,
                patient_id=inst.patient_id,
                clinician_idx=vehicle.clinician_idx,
                date=date,
                starts_at=starts_at,
                ends_at=ends_at,
                soft_constraint_override=soft_override,
            )
        )

        accumulated_work += footprint if took_break else transit + footprint
        current_time = earliest_start + footprint
        prev_key = pid_key

    # Handle remaining locked stops
    for ls in pending_locked[locked_cursor:]:
        ls_pid = str(ls["patient_id"])
        drive_cost += travel(prev_key, ls_pid)
        prev_key = ls_pid
        current_time = max(
            current_time, ls["start_min"] + ls["duration"] + ls["charting"]
        )

    drive_cost += travel(prev_key, home_key)

    # Drive limit check — drop all visits if exceeded
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

    # Lunch fallback
    if not lunch_taken:
        if need_lunch:
            lunch_start = round_up(max(lunch_earliest, current_time), SLOT_STEP)
            lunch_placement = {
                "start_minute": lunch_start,
                "end_minute": lunch_start + lunch_dur,
            }
        else:
            lunch_placement = None

    if lunch_placement:
        lunch_drift = abs(lunch_placement["start_minute"] - lunch_target)
    elif need_lunch:
        lunch_drift = 200
    else:
        lunch_drift = 0

    # ── Compute violation metrics (same logic as cpsat_timing) ──────

    # Lunch window violation
    lunch_window_viol = 0
    if lunch_placement:
        if lunch_placement["start_minute"] < lunch_earliest:
            lunch_window_viol = lunch_earliest - lunch_placement["start_minute"]
        elif lunch_placement["start_minute"] > lunch_latest:
            lunch_window_viol = lunch_placement["start_minute"] - lunch_latest
    elif need_lunch:
        lunch_window_viol = 200

    # Overtime: last visit end + charting + return home > day_end
    overtime = 0
    if visits:
        last_end_min = _minute_from_iso(visits[-1].ends_at) + charting
        return_home_time = travel(str(visits[-1].patient_id), home_key)
        effective_end = last_end_min + return_home_time
        if effective_end > day_end_minute:
            overtime = effective_end - day_end_minute

    # Transit excess: insufficient gap between consecutive visits
    transit_excess = 0
    for vi in range(len(visits) - 1):
        v_end = _minute_from_iso(visits[vi].ends_at) + charting
        v_next_start = _minute_from_iso(visits[vi + 1].starts_at)
        needed = (
            travel(str(visits[vi].patient_id), str(visits[vi + 1].patient_id))
            + TRANSIT_BUFFER
        )
        gap = v_next_start - v_end
        if gap < needed:
            transit_excess += needed - gap

    # Break violations: consecutive-work spans exceeding max_continuous
    break_viols = 0
    if max_cont and max_cont > 0 and len(visits) > 1:
        acc = 0
        for vi, v in enumerate(visits):
            v_dur = (
                _minute_from_iso(v.ends_at) - _minute_from_iso(v.starts_at) + charting
            )
            if vi > 0:
                prev_end = _minute_from_iso(visits[vi - 1].ends_at) + charting
                cur_start = _minute_from_iso(v.starts_at)
                gap = cur_start - prev_end
                transit_t = travel(str(visits[vi - 1].patient_id), str(v.patient_id))
                if gap >= break_dur + transit_t:
                    acc = 0
                else:
                    acc += transit_t
            acc += v_dur
            if acc > max_cont:
                break_viols += 1
                acc = 0

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
        total_cost=drive_cost + lunch_drift,
        violations=violations,
    )


def _minute_from_iso(iso: str) -> int:
    """Extract minute-of-day from ISO datetime string."""
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def nearest_neighbor(
    instances: list[VisitInstanceData],
    travel: callable,
    home_key: str = "home_0",
) -> list[VisitInstanceData]:
    """Nearest-neighbor ordering as fallback. Returns reordered instance list."""
    remaining = set(range(len(instances)))
    order: list[int] = []
    current = home_key
    while remaining:
        best_idx = min(
            remaining, key=lambda i: travel(current, str(instances[i].patient_id))
        )
        order.append(best_idx)
        remaining.discard(best_idx)
        current = str(instances[best_idx].patient_id)
    return [instances[i] for i in order]
