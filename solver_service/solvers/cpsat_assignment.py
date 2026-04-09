"""CP-SAT day-assignment model for healthcare scheduling.

Assigns visit instances to working days.  The objective is categorical:
scheduling N visits is strictly better than N-1, regardless of cost.
Within the same visit-count tier, the model minimizes travel + soft
penalties (spacing, availability, density, day-offsets, priority).
"""

from __future__ import annotations

import os
from collections import defaultdict

from ortools.sat.python import cp_model

from solvers.cpsat_context import (
    MAX_VISITS_PER_DAY,
    NUM_WORKERS,
    DENSITY_BASE_WEIGHT,
    PENALTY_AVAILABILITY,
    PENALTY_DAY_OFFSET,
    PENALTY_SPACING_MAX,
    PRIORITY_WEIGHT,
    target_day_offsets,
)
from models import SolverInput


def assign_days(
    input: SolverInput,
    ctx: dict,
    day_marginal_costs: dict[int, dict[int, int]],
    home_leg: dict[int, int],
    time_budget: int,
    iteration: int = 0,
    prev_assignments: dict[int, list[int]] | None = None,
) -> tuple[dict[int, list[int]] | None, str]:
    """CP-SAT model for day assignment only.

    Returns (assignments, status_string).
    """
    clinician = input.clinician
    patients_by_id = ctx["patients_by_id"]
    inst_idx_map = ctx["inst_idx_map"]
    working_days = input.working_days
    num_days = len(working_days)
    num_instances = len(input.instances)
    day_indices = list(range(num_days))

    instances_by_patient = ctx["instances_by_patient"]
    locked_patient_days = ctx["locked_patient_days"]
    locked_count_by_day = ctx["locked_count_by_day"]
    day_wdays = ctx["day_wdays"]

    # Pre-compute available work minutes per day (per_day_hours overrides + locked/blocked time deducted).
    # instance.duration already includes charting_buffer, so this gives a realistic capacity ceiling.
    blocked_ranges_by_day = ctx.get("blocked_ranges_by_day", {})
    day_available_minutes: dict[int, int] = {}
    for d in range(num_days):
        wday = str(day_wdays[d])
        pdh = clinician.per_day_hours.get(wday, {})
        day_start = pdh.get("start", clinician.workday_start_minute)
        day_end = pdh.get("end", clinician.workday_end_minute)
        avail = max(0, day_end - day_start)
        for bs, be in blocked_ranges_by_day.get(d, []):
            avail -= max(0, min(be, day_end) - max(bs, day_start))
        if clinician.lunch_duration_minutes > 0:
            avail -= clinician.lunch_duration_minutes
        day_available_minutes[d] = max(0, avail)

    model = cp_model.CpModel()

    # ── Variables ───────────────────────────────────────────────────────

    day_var = []
    scheduled = []
    for i in range(num_instances):
        dv = model.new_int_var(0, num_days, f"day_{i}")
        day_var.append(dv)
        sv = model.new_bool_var(f"sched_{i}")
        scheduled.append(sv)
        model.add(dv < num_days).only_enforce_if(sv)
        model.add(dv == num_days).only_enforce_if(sv.negated())

    assign = {}
    for i in range(num_instances):
        for d in day_indices:
            b = model.new_bool_var(f"a_{i}_{d}")
            assign[(i, d)] = b
            model.add(day_var[i] == d).only_enforce_if(b)
            model.add(day_var[i] != d).only_enforce_if(b.negated())

    # ── Hard Constraints ───────────────────────────────────────────────

    # 1. One patient per day
    for pid, insts in instances_by_patient.items():
        inst_indices = [inst_idx_map[inst.id] for inst in insts]
        for d in day_indices:
            if d in locked_patient_days.get(pid, set()):
                for i in inst_indices:
                    model.add(assign[(i, d)] == 0)
            else:
                model.add(sum(assign[(i, d)] for i in inst_indices) <= 1)

    # 2. Max visits per day (count cap)
    for d in day_indices:
        avail_mins = day_available_minutes[d]
        count_avail = MAX_VISITS_PER_DAY - locked_count_by_day.get(d, 0)
        if count_avail <= 0 or avail_mins <= 0:
            for i in range(num_instances):
                model.add(assign[(i, d)] == 0)
        else:
            model.add(sum(assign[(i, d)] for i in range(num_instances)) <= count_avail)

    # 3. Each instance on exactly one day or unscheduled
    for i in range(num_instances):
        model.add(sum(assign[(i, d)] for d in day_indices) + (1 - scheduled[i]) == 1)

    # ── Soft Constraints ───────────────────────────────────────────────

    penalties = []

    # 4. Priority tiebreaker — when the solver must drop visits, prefer
    #    keeping high-priority patients.  The categorical visit_value
    #    (computed below from proven cost bounds) ensures N scheduled visits
    #    always beats N-1; this penalty only discriminates *which* visit to
    #    drop within the same count tier.
    for i, inst in enumerate(input.instances):
        patient = patients_by_id.get(inst.patient_id)
        priority_boost = (patient.priority + 1) if patient else 1
        penalties.append((scheduled[i].negated(), PRIORITY_WEIGHT * priority_boost))

    # 5. Spacing (min/max gap between visits of same patient)
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) < 2:
            continue
        inst_indices = [inst_idx_map[inst.id] for inst in insts]
        min_gap = patient.min_days_between_visits
        max_gap = patient.max_days_between_visits
        locked_days = sorted(locked_patient_days.get(pid, set()))

        for a_idx in range(len(inst_indices)):
            i = inst_indices[a_idx]

            # vs locked visits
            for locked_d in locked_days:
                for d in day_indices:
                    if d == locked_d:
                        continue
                    gap = abs(d - locked_d)
                    if gap <= min_gap:
                        model.add(assign[(i, d)] == 0)
                    if gap > max_gap:
                        pen = model.new_bool_var(f"spx_{i}_{d}_{locked_d}")
                        model.add_min_equality(pen, [assign[(i, d)], scheduled[i]])
                        penalties.append((pen, PENALTY_SPACING_MAX * (gap - max_gap)))

            # vs other instances
            for b_idx in range(a_idx + 1, len(inst_indices)):
                j = inst_indices[b_idx]
                both = model.new_bool_var(f"bs_{i}_{j}")
                model.add_min_equality(both, [scheduled[i], scheduled[j]])

                gap = model.new_int_var(0, num_days, f"g_{i}_{j}")
                diff = model.new_int_var(-num_days, num_days, f"d_{i}_{j}")
                model.add(diff == day_var[i] - day_var[j])
                model.add_abs_equality(gap, diff)

                if min_gap >= 1:
                    model.add(gap > min_gap).only_enforce_if(both)

                if max_gap < num_days:
                    viol = model.new_int_var(0, num_days, f"xv_{i}_{j}")
                    model.add(viol >= gap - max_gap).only_enforce_if(both)
                    model.add(viol == 0).only_enforce_if(both.negated())
                    penalties.append((viol, PENALTY_SPACING_MAX))

    # 6. Availability windows
    for i, inst in enumerate(input.instances):
        if not inst.availability_windows:
            continue
        for d in day_indices:
            wday = str(day_wdays[d])
            windows = inst.availability_windows.get(wday, [])
            if not windows:
                penalties.append((assign[(i, d)], PENALTY_AVAILABILITY))

    # 7. Pre-compute per-(day, instance) marginal cost vars
    marginal_var: dict[tuple[int, int], tuple] = {}  # (d, i) → (cp_var, cost_value)
    for d in day_indices:
        for i in range(num_instances):
            cost = day_marginal_costs.get(d, {}).get(i, home_leg.get(i, 0))
            if cost > 0:
                c = model.new_int_var(0, cost, f"mc_{i}_{d}")
                model.add(c == cost).only_enforce_if(assign[(i, d)])
                model.add(c == 0).only_enforce_if(assign[(i, d)].negated())
                marginal_var[(d, i)] = (c, cost)

    # 7b. Hard day capacity: visit durations must fit in available time.
    # Only uses exact visit durations (not transit estimates) so marginal
    # overestimates can't block feasible placements.  Actual timing
    # feasibility (including transit) is enforced by the routing phase.
    for d in day_indices:
        avail_mins = day_available_minutes[d]
        if avail_mins <= 0:
            continue
        dur_terms = [assign[(i, d)] * inst.duration for i, inst in enumerate(input.instances)]
        if dur_terms:
            model.add(sum(dur_terms) <= avail_mins)

    # Drive limit is enforced as a hard constraint in the routing phase (on
    # actual drive costs).  The assignment model's travel_terms already steer
    # toward lower-drive solutions via the objective.

    # 8. Target day offsets (spread visits evenly)
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) <= 1:
            continue

        inst_indices = sorted(inst_idx_map[inst.id] for inst in insts)
        targets = target_day_offsets(
            len(insts), num_days, patient.min_days_between_visits, clinician.schedule_density,
        )

        for k, i in enumerate(inst_indices):
            target = targets[k] if k < len(targets) else targets[-1]
            dev = model.new_int_var(0, num_days, f"dd_{i}")
            raw = model.new_int_var(-num_days, num_days, f"dr_{i}")
            model.add(raw == day_var[i] - target)
            model.add_abs_equality(dev, raw)
            dev_s = model.new_int_var(0, num_days, f"ds_{i}")
            model.add(dev_s == dev).only_enforce_if(scheduled[i])
            model.add(dev_s == 0).only_enforce_if(scheduled[i].negated())
            penalties.append((dev_s, PENALTY_DAY_OFFSET))

    # 9. Schedule density
    day_counts = []
    for d in day_indices:
        cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, f"cnt_{d}")
        model.add(cnt == sum(assign[(i, d)] for i in range(num_instances)))
        day_counts.append(cnt)

    density = clinician.schedule_density
    if density < 0.5 and num_days > 1:
        max_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "mx")
        min_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "mn")
        model.add_max_equality(max_cnt, day_counts)
        model.add_min_equality(min_cnt, day_counts)
        spread = model.new_int_var(0, MAX_VISITS_PER_DAY, "sp")
        model.add(spread == max_cnt - min_cnt)
        w = int(DENSITY_BASE_WEIGHT * 2 * (0.5 - density))
        if w > 0:
            penalties.append((spread, w))
    elif density > 0.5:
        for d in day_indices:
            active = model.new_bool_var(f"act_{d}")
            model.add(day_counts[d] > 0).only_enforce_if(active)
            model.add(day_counts[d] == 0).only_enforce_if(active.negated())
            w = int(DENSITY_BASE_WEIGHT * 2 * (density - 0.5))
            if w > 0:
                penalties.append((active, w))

    # ── Objective: travel cost from distance matrix + penalties ────────

    travel_terms = []
    travel_fn = ctx["travel"]

    # Per-(day, instance) marginal routing cost — reuse shared vars from step 7
    for (d, i), (var, _cost) in marginal_var.items():
        travel_terms.append(var)

    # Pairwise inter-patient costs (iteration 0 only — teaches geographic clustering).
    travel_ub = sum(cost for (_, cost) in marginal_var.values())

    if iteration == 0:
        prune_mult = max(1, int(os.environ.get("CPSAT_PAIRWISE_PRUNE_MULT", "3")))
        home_vals = sorted(home_leg.values())
        median_home = home_vals[len(home_vals) // 2] if home_vals else 0
        pairwise_threshold = max(1, median_home) * prune_mult
        pids = [str(inst.patient_id) for inst in input.instances]
        for i in range(num_instances):
            for j in range(i + 1, num_instances):
                if pids[i] == pids[j]:
                    continue
                t = travel_fn(pids[i], pids[j])
                if t == 0 or t > pairwise_threshold:
                    continue
                travel_ub += t
                for d in day_indices:
                    both = model.new_bool_var(f"pw_{i}_{j}_{d}")
                    model.add_min_equality(both, [assign[(i, d)], assign[(j, d)]])
                    pw = model.new_int_var(0, t, f"pwc_{i}_{j}_{d}")
                    model.add(pw == t).only_enforce_if(both)
                    model.add(pw == 0).only_enforce_if(both.negated())
                    travel_terms.append(pw)

    travel_ub = max(travel_ub, 1)
    travel_cap = min(travel_ub, 50_000_000)
    total_travel = model.new_int_var(0, travel_cap, "tt")
    model.add(total_travel == sum(travel_terms)) if travel_terms else model.add(total_travel == 0)

    penalty_terms = []
    for var, weight in penalties:
        w = int(weight)
        if w > 0:
            penalty_terms.append(var * w)

    # Tight objective domain from penalty structure
    penalty_cap = 0
    for var, wt in penalties:
        w = int(wt)
        if w <= 0:
            continue
        label = str(var)
        if any(x in label for x in ("xv_", "dd_", "ds_")):
            penalty_cap += w * num_days
        elif any(x in label for x in ("(mx(", "(mn(", "(sp(", " mx(", " mn(", " sp(")):
            penalty_cap += w * MAX_VISITS_PER_DAY
        else:
            penalty_cap += w

    penalty_cap = max(penalty_cap, 1)
    penalty_cap = min(penalty_cap, 500_000_000)
    total_penalty = model.new_int_var(0, penalty_cap, "tp")
    model.add(total_penalty == sum(penalty_terms)) if penalty_terms else model.add(total_penalty == 0)

    # Categorical visit-count objective: each additional scheduled visit
    # improves the objective by visit_value, which provably exceeds the
    # maximum possible cost (travel + penalties).  This makes the integer
    # "number of scheduled visits" a strict tier — a solution with N visits
    # always beats one with N-1, regardless of routing costs.
    visit_value = travel_cap + penalty_cap + 1
    model.minimize(
        total_travel + total_penalty
        - visit_value * sum(scheduled[i] for i in range(num_instances))
    )

    # ── Warm-start from previous iteration's assignment (or prior upper_bound on iter 0) ──
    full_assign_hints = os.environ.get("CPSAT_FULL_ASSIGN_HINTS", "").lower() in ("1", "true", "yes")
    if prev_assignments is not None:
        prev_inst_day = {}
        for d, inst_list in prev_assignments.items():
            for i in inst_list:
                prev_inst_day[i] = d
        for i in range(num_instances):
            if i in prev_inst_day:
                model.add_hint(day_var[i], prev_inst_day[i])
                model.add_hint(scheduled[i], 1)
                if full_assign_hints:
                    for d in day_indices:
                        model.add_hint(assign[(i, d)], 1 if d == prev_inst_day[i] else 0)
            else:
                model.add_hint(scheduled[i], 0)
                model.add_hint(day_var[i], num_days)

    # ── Solve ──────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget
    solver.parameters.num_workers = NUM_WORKERS
    if os.environ.get("CPSAT_USE_FEASIBILITY_JUMP", "1").lower() not in ("0", "false", "no"):
        if hasattr(solver.parameters, "use_feasibility_jump"):
            solver.parameters.use_feasibility_jump = True

    status = solver.solve(model)
    status_name = solver.status_name(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status_name

    # Extract assignments (sorted instance indices per day for stable routing I/O)
    result: dict[int, list[int]] = defaultdict(list)
    for i in range(num_instances):
        if solver.value(scheduled[i]):
            d = solver.value(day_var[i])
            if d < num_days:
                result[d].append(i)

    for d_key in result:
        result[d_key].sort()

    return dict(result), status_name
