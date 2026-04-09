"""Day routing: optimal sequencing + timing for a single day's visits.

Given fixed patients for a day, finds the optimal visit order using HGS (PyVRP)
and/or exhaustive permutations, then retimes with lunch, blocks, and locked visits.
"""

from __future__ import annotations

import itertools
import logging
import os
from datetime import datetime

from pyvrp import Model as VRPModel
from pyvrp.stop import MaxRuntime

from models import SolverInput, PlannedVisit, VisitInstanceData
from solvers.cpsat_context import (
    HGS_MAX_SECONDS,
    MAX_VISITS_PER_DAY,
    SLOT_STEP,
    SolverContext,
    TRANSIT_BUFFER,
    datetime_to_minute,
    day_bounds,
    minutes_to_datetime,
    round_up,
)

logger = logging.getLogger(__name__)



def _empty_result(need_lunch: bool, lunch_start: int, lunch_dur: int, drive_cost: int = 0) -> dict:
    """Construct a route result with no floating visits."""
    lunch = {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur} if need_lunch else None
    return {"visits": [], "lunch": lunch, "drive_cost": drive_cost, "winner": None, "dropped": []}


def trivial_empty_route_day(input: SolverInput, date: str) -> dict:
    """No floating or locked visits on this date: placeholder lunch only."""
    c = input.clinician
    lunch_dur = c.lunch_duration_minutes
    if lunch_dur <= 0:
        return _empty_result(False, 0, 0)
    day_start, day_end = day_bounds(c, date)
    half_w = c.lunch_window_minutes // 2
    lunch_earliest = max(c.lunch_start_minute - half_w, day_start)
    return _empty_result(True, round_up(lunch_earliest, SLOT_STEP), lunch_dur)


def route_day(
    date: str,
    instances: list[VisitInstanceData],
    input: SolverInput,
    ctx: SolverContext,
) -> dict:
    """Find optimal route order and concrete times for a single day's visits.

    Strategy:
      1. Collect locked visits for this day as fixed-position route stops
      2. HGS (required=False, prize=1M) finds the best feasible ordering;
         only drops visits when hard constraints make inclusion impossible
      3. For ≤5 floating visits, also try all permutations (exact safety net)
      4. Pick the result with the most visits, then lowest cost
      5. Dropped visits are fed back to CP-SAT via marginals for rescheduling
    """
    clinician = input.clinician
    travel = ctx.travel

    day_start_minute, day_end_minute = day_bounds(clinician, date)

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

    # Blocked time ranges
    day_blocks = []
    for cb in ctx.calendar_blocks_by_date.get(date, ()):
        day_blocks.append((datetime_to_minute(cb.starts_at), datetime_to_minute(cb.ends_at)))

    # Locked visits for this day — route stops only, NOT added to day_blocks.
    # They are interleaved by _evaluate_route's locked_cursor loop which handles
    # travel routing, charting buffer, and transit buffer correctly.
    locked_stops = []
    for lv in ctx.locked_visits_by_date.get(date, ()):
        s = datetime_to_minute(lv.starts_at)
        locked_stops.append({
            "patient_id": lv.patient_id,
            "start_min": s,
            "duration": lv.duration_minutes,
            "charting": clinician.charting_buffer_minutes,
        })
    locked_stops.sort(key=lambda s: s["start_min"])

    lunch_start = round_up(lunch_earliest, SLOT_STEP)

    if not instances and not locked_stops:
        return _empty_result(need_lunch, lunch_start, lunch_dur)

    if not instances:
        drive_cost = 0
        prev = "home"
        for ls in locked_stops:
            drive_cost += travel(prev, str(ls["patient_id"]))
            prev = str(ls["patient_id"])
        drive_cost += travel(prev, "home")
        return _empty_result(need_lunch, lunch_start, lunch_dur, drive_cost)

    max_drive = clinician.max_drive_minutes_per_day or float("inf")

    n = len(instances)
    skip_perm_n5 = os.environ.get("CPSAT_SKIP_PERM_ENUM_FOR_N5", "").lower() in ("1", "true", "yes")

    # HGS (required=False, prize=1M) finds the best feasible ordering.
    # With prize >> max routing cost, it only drops visits when hard
    # constraints make inclusion genuinely infeasible.  Dropped visits
    # are fed back to the assignment layer via marginals.
    hgs_instances = instances
    hgs_order = None
    if n >= 2:
        hgs_result = _hgs_route_optional(instances, travel, clinician, date)
        if hgs_result is not None:
            hgs_instances, hgs_order = hgs_result

    # Candidates: HGS ordering + exhaustive permutations for small n.
    # Perms operate on the full set, so they catch any HGS heuristic
    # misses for n ≤ 5.  Each candidate carries its instance set so we
    # can compare across different visit counts.
    candidates: list[tuple[str, tuple[int, ...], list]] = []

    if hgs_order is not None:
        candidates.append(("hgs", hgs_order, hgs_instances))

    if n <= 4 or (n == 5 and not skip_perm_n5):
        for perm in itertools.permutations(range(n)):
            candidates.append(("perm", perm, instances))
    elif not candidates:
        candidates.append(("nn", nearest_neighbor(instances, travel), instances))

    # Pick the best: more visits always wins, then lower cost.
    best_result = None
    best_visits = -1
    best_cost = float("inf")

    for source, order, inst_set in candidates:
        result = _evaluate_route(
            order, inst_set, date, clinician, travel, day_blocks,
            lunch_earliest, lunch_latest, lunch_dur, lunch_target,
            locked_stops=locked_stops, force_lunch=need_lunch,
            day_start_minute=day_start_minute, day_end_minute=day_end_minute,
        )
        if result["drive_cost"] > max_drive:
            # Tag all visits as dropped due to drive limit for marginal feedback.
            if not best_result and result["visits"]:
                drive_dropped = result.copy()
                drive_dropped["dropped"] = drive_dropped.get("dropped", []) + [
                    {"instance_id": v.instance_id, "reason": "drive_limit"}
                    for v in drive_dropped["visits"]
                ]
                drive_dropped["visits"] = []
                drive_dropped["winner"] = None
                best_result = drive_dropped
            continue
        nv = len(result["visits"])
        if nv > best_visits or (nv == best_visits and result["total_cost"] < best_cost):
            best_visits = nv
            best_cost = result["total_cost"]
            result["winner"] = source
            best_result = result

    if best_result is None:
        best_result = _empty_result(need_lunch, lunch_start, lunch_dur)
        best_result["dropped"] = []
    if "dropped" not in best_result:
        best_result["dropped"] = []
    return best_result


def _hgs_route_optional(
    instances: list[VisitInstanceData],
    travel,
    clinician,
    date: str,
) -> tuple[list[VisitInstanceData], tuple[int, ...]] | None:
    """HGS with optional clients: finds best feasible visit subset in one solve.

    Returns (subset_instances, ordering_into_subset) or None on failure.
    """
    n = len(instances)
    if n < 2:
        return None

    day_start, day_end = day_bounds(clinician, date)

    # Pre-compute per-instance time window from availability windows.
    # When a patient has availability windows for this weekday, use the
    # widest window as HGS tw bounds (HGS supports one window per client).
    # This makes HGS order-aware of timing constraints.
    dt_date = datetime.fromisoformat(date)
    wday_str = str((dt_date.weekday() + 1) % 7)

    inst_tw: list[tuple[int, int]] = []
    for inst in instances:
        if inst.availability_windows:
            windows = inst.availability_windows.get(wday_str, [])
            if windows:
                # Use the widest window to give HGS the most flexibility
                best_w = max(
                    windows,
                    key=lambda w: w.get("end_minute", 1440) - w.get("start_minute", 0),
                )
                tw_e = max(day_start, best_w.get("start_minute", day_start))
                tw_l = min(day_end, best_w.get("end_minute", day_end))
                if tw_l > tw_e:
                    inst_tw.append((tw_e, tw_l))
                else:
                    inst_tw.append((day_start, day_end))
            else:
                inst_tw.append((day_start, day_end))
        else:
            inst_tw.append((day_start, day_end))

    try:
        model = VRPModel()
        depot = model.add_depot(x=0, y=0)

        # Prize must exceed any realistic routing cost so HGS only drops
        # visits when hard constraints (max_distance, shift_duration) force it.
        prize = 1_000_000
        clients = []
        for idx, inst in enumerate(instances):
            tw_e, tw_l = inst_tw[idx]
            client = model.add_client(
                x=0, y=0,
                delivery=[1],
                service_duration=inst.duration,
                tw_early=tw_e,
                tw_late=tw_l,
                required=False,
                prize=prize,
            )
            clients.append(client)

        for i, inst in enumerate(instances):
            pid = str(inst.patient_id)
            t_out = travel("home", pid)
            t_back = travel(pid, "home")
            model.add_edge(depot, clients[i], distance=t_out, duration=t_out)
            model.add_edge(clients[i], depot, distance=t_back, duration=t_back)

            for j, inst_j in enumerate(instances):
                if i == j:
                    continue
                t = travel(pid, str(inst_j.patient_id))
                model.add_edge(clients[i], clients[j], distance=t, duration=t)

        workday_dur = day_end - day_start
        max_dist = clinician.max_drive_minutes_per_day or 999_999
        model.add_vehicle_type(
            num_available=1,
            capacity=[MAX_VISITS_PER_DAY],
            shift_duration=workday_dur,
            max_distance=max_dist,
            tw_early=day_start,
            tw_late=day_end,
        )

        hgs_seconds = max(0.1, min(HGS_MAX_SECONDS, n * 0.03))
        result = model.solve(stop=MaxRuntime(hgs_seconds), seed=42, display=False)

        if not result.is_feasible():
            return None

        routes = list(result.best.routes())
        if not routes:
            return None

        original_indices = []
        for visit_idx in routes[0].visits():
            node = visit_idx - 1
            if 0 <= node < n:
                original_indices.append(node)

        if not original_indices:
            return None

        subset = [instances[i] for i in original_indices]
        reindexed_order = tuple(range(len(subset)))
        return subset, reindexed_order

    except Exception as e:
        logger.warning("HGS optional routing failed for %d instances: %s", n, e)
        return None


def _evaluate_route(
    perm: tuple[int, ...],
    instances: list[VisitInstanceData],
    date: str,
    clinician,
    travel,
    blocks: list[tuple[int, int]],
    lunch_earliest: int,
    lunch_latest: int,
    lunch_dur: int,
    lunch_target: int,
    force_lunch: bool = True,
    locked_stops: list[dict] | None = None,
    day_start_minute: int | None = None,
    day_end_minute: int | None = None,
) -> dict:
    """Evaluate a specific visit ordering with locked visits interleaved.

    Returns a partial route when some visits don't fit — dropped visits are
    listed with reasons so the assignment layer can adjust marginals.
    """
    max_cont = clinician.max_continuous_work_minutes
    break_dur = clinician.required_break_minutes
    charting = clinician.charting_buffer_minutes
    workday_end = day_end_minute if day_end_minute is not None else clinician.workday_end_minute

    current_time = day_start_minute if day_start_minute is not None else clinician.workday_start_minute
    prev_key = "home"
    accumulated_work = 0
    lunch_taken = False
    lunch_placement = None
    visits = []
    drive_cost = 0
    dropped: list[dict] = []

    pending_locked = list(locked_stops or [])
    locked_cursor = 0

    sorted_blocks = sorted(blocks) if blocks else []

    dt_date = datetime.fromisoformat(date)
    wday_str = str((dt_date.weekday() + 1) % 7)

    # Pre-sort availability windows per instance (avoid re-sorting on every permutation eval)
    sorted_windows: dict[int, list[dict]] = {}
    for i, inst in enumerate(instances):
        if inst.availability_windows:
            ws = inst.availability_windows.get(wday_str, [])
            if ws:
                sorted_windows[i] = sorted(ws, key=lambda w: w.get("start_minute", 0))

    for idx in perm:
        inst = instances[idx]
        pid_key = str(inst.patient_id)
        visit_dur = max(0, inst.duration - charting)
        footprint = inst.duration

        # Snapshot state before attempting this visit — restore on skip.
        snap = (current_time, prev_key, accumulated_work, drive_cost,
                locked_cursor, lunch_taken, lunch_placement)

        transit = travel(prev_key, pid_key)
        transit_buf = TRANSIT_BUFFER if prev_key != "home" else 0
        raw_start = current_time + transit + transit_buf

        took_break = False
        if max_cont and max_cont > 0 and accumulated_work + transit + footprint > max_cont:
            raw_start += break_dur
            accumulated_work = 0
            took_break = True

        earliest_start = round_up(raw_start, SLOT_STEP)

        # Fixpoint: process locked stops and calendar blocks until stable.
        # A locked stop can push earliest_start past a block and vice versa.
        prev_earliest = -1
        while prev_earliest != earliest_start:
            prev_earliest = earliest_start

            # Process locked visits that conflict with our proposed time slot
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
                if max_cont and max_cont > 0 and accumulated_work + transit + footprint > max_cont:
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
                    earliest_start = round_up(b_end + TRANSIT_BUFFER, SLOT_STEP)

        drive_cost += transit

        # Availability window: pick the feasible window closest to
        # earliest_start (minimizes idle gap) instead of always taking
        # the first.  This leaves more slack for subsequent visits.
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

        # Lunch insertion — must fit in window AND not overlap prior visits/charting
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
                accumulated_work = 0

        if earliest_start + footprint > workday_end:
            # Visit doesn't fit — restore state and skip.
            (current_time, prev_key, accumulated_work, drive_cost,
             locked_cursor, lunch_taken, lunch_placement) = snap
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

        visits.append(PlannedVisit(
            instance_id=inst.id,
            patient_id=inst.patient_id,
            date=date,
            starts_at=starts_at,
            ends_at=ends_at,
            soft_constraint_override=soft_override,
        ))

        accumulated_work += footprint if took_break else transit + footprint
        current_time = earliest_start + footprint
        prev_key = pid_key

    # Handle remaining locked stops
    for ls in pending_locked[locked_cursor:]:
        ls_pid = str(ls["patient_id"])
        drive_cost += travel(prev_key, ls_pid)
        prev_key = ls_pid
        current_time = max(current_time, ls["start_min"] + ls["duration"] + ls["charting"])

    drive_cost += travel(prev_key, "home")

    # Lunch fallback — clamp to current_time so it doesn't overlap placed visits
    if not lunch_taken:
        if force_lunch and lunch_dur > 0:
            lunch_start = round_up(max(lunch_earliest, current_time), SLOT_STEP)
            lunch_placement = {"start_minute": lunch_start, "end_minute": lunch_start + lunch_dur}
        else:
            lunch_placement = None

    if lunch_placement:
        lunch_drift = abs(lunch_placement["start_minute"] - lunch_target)
    elif force_lunch:
        lunch_drift = 200
    else:
        lunch_drift = 0

    return {
        "visits": visits,
        "lunch": lunch_placement,
        "drive_cost": drive_cost,
        "total_cost": drive_cost + lunch_drift,
        "dropped": dropped,
    }


def nearest_neighbor(
    instances: list[VisitInstanceData],
    travel,
) -> tuple[int, ...]:
    """Nearest-neighbor ordering as fallback."""
    remaining = set(range(len(instances)))
    order = []
    current = "home"
    while remaining:
        best_idx = min(remaining, key=lambda i: travel(current, str(instances[i].patient_id)))
        order.append(best_idx)
        remaining.discard(best_idx)
        current = str(instances[best_idx].patient_id)
    return tuple(order)
