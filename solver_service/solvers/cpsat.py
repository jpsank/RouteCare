"""CP-SAT solver: constraint programming model for healthcare scheduling.

Replaces the Ruby WeeklyOptimizer with a single OR-Tools CP-SAT model that
handles all domain constraints natively:
  - One patient per day
  - Max visits per day
  - Min/max days between visits (spacing)
  - Patient availability windows (per day-of-week)
  - Calendar blocks and locked visits
  - Lunch break placement
  - Max continuous work / mandatory breaks
  - Max drive minutes per day
  - Schedule density (spread vs. cluster)
  - Geographic clustering (minimize travel)
  - Patient priority
  - Charting buffer

The model assigns visits to days, sequences them via ordering variables,
and computes concrete start times — all within CP-SAT so constraints are
enforced during search, not post-hoc.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model

from models import SolverInput, SolverOutput, PlannedVisit
from solvers.base import MAX_VISITS_PER_DAY, build_lunch_placements, compute_return_home, minutes_to_datetime

# Penalty weights matching Ruby FitnessFunction::DEFAULT_WEIGHTS
WEIGHT_DRIVE = 1.0
WEIGHT_SPACING = 0.5
WEIGHT_AVAILABILITY = 0.3
WEIGHT_DENSITY = 0.2
WEIGHT_LUNCH = 0.1

# Large constant for soft constraint penalties (keeps solver from ignoring them)
PENALTY_UNSCHEDULED = 10_000
PENALTY_SPACING_MIN = 100  # per day short of min_gap
PENALTY_SPACING_MAX = 50   # per day over max_gap
PENALTY_AVAILABILITY = 150  # visit outside preferred window
PENALTY_DRIVE_OVER = 50    # per minute over max drive

PENALTY_LUNCH_DRIFT = 1  # per minute of lunch drift from target
PENALTY_DAY_OFFSET = 10  # per day of deviation from target offset

SLOT_STEP = 15  # 15-minute granularity
TRANSIT_BUFFER = 5  # minutes buffer between visits


def solve(
    input: SolverInput,
    time_budget: int = 30,
    upper_bound: SolverOutput | None = None,
) -> SolverOutput:
    """Build and solve the CP-SAT model."""
    if not input.instances:
        return _empty_output(input)

    clinician = input.clinician
    patients_by_id = {p.id: p for p in input.patients}
    working_days = input.working_days
    num_days = len(working_days)
    day_indices = list(range(num_days))

    # Day-of-week for each working day (0=Mon, 6=Sun in Python; but our data
    # uses 0=Sun, 1=Mon matching Ruby's wday)
    day_wdays = []
    for d in working_days:
        dt = datetime.fromisoformat(d)
        # Convert Python weekday (0=Mon) to Ruby wday (0=Sun)
        ruby_wday = (dt.weekday() + 1) % 7
        day_wdays.append(ruby_wday)

    # Pre-compute blocked minute ranges per day from locked visits + calendar blocks
    blocked_ranges_by_day: dict[int, list[tuple[int, int]]] = defaultdict(list)
    locked_patient_days: dict[int, set[int]] = defaultdict(set)  # patient_id → set of day_indices

    for lv in input.locked_visits:
        day_idx = _day_index(lv.date, working_days)
        if day_idx is None:
            continue
        locked_patient_days[lv.patient_id].add(day_idx)
        start_min = _datetime_to_minute(lv.starts_at)
        end_min = start_min + lv.duration_minutes + clinician.charting_buffer_minutes
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    for cb in input.calendar_blocks:
        day_idx = _day_index(cb.date, working_days)
        if day_idx is None:
            continue
        start_min = _datetime_to_minute(cb.starts_at)
        end_min = _datetime_to_minute(cb.ends_at)
        blocked_ranges_by_day[day_idx].append((start_min, end_min))

    # Count locked visits per day for max-visits constraint
    locked_count_by_day = defaultdict(int)
    for lv in input.locked_visits:
        day_idx = _day_index(lv.date, working_days)
        if day_idx is not None:
            locked_count_by_day[day_idx] += 1

    # Group instances by patient
    instances_by_patient: dict[int, list] = defaultdict(list)
    for inst in input.instances:
        instances_by_patient[inst.patient_id].append(inst)

    # Travel matrix helper
    matrix = input.travel_matrix

    def travel(from_key: str, to_key: str) -> int:
        return matrix.get(from_key, {}).get(to_key, 0)

    # ── Build CP-SAT Model ──────────────────────────────────────────────

    model = cp_model.CpModel()
    num_instances = len(input.instances)

    # Decision variables
    # For each instance i: which day (0..num_days-1) or num_days = "unscheduled"
    day_var = []
    scheduled = []  # Boolean: is this instance scheduled?
    for i, inst in enumerate(input.instances):
        dv = model.new_int_var(0, num_days, f"day_{i}")
        day_var.append(dv)
        sv = model.new_bool_var(f"sched_{i}")
        scheduled.append(sv)
        # Link: scheduled iff day < num_days
        model.add(dv < num_days).only_enforce_if(sv)
        model.add(dv == num_days).only_enforce_if(sv.negated())

    # For each instance i on each day d: binary assignment variable
    assign = {}
    for i in range(num_instances):
        for d in day_indices:
            b = model.new_bool_var(f"assign_{i}_{d}")
            assign[(i, d)] = b
            # Link to day_var
            model.add(day_var[i] == d).only_enforce_if(b)
            model.add(day_var[i] != d).only_enforce_if(b.negated())

    # ── Hard Constraints ────────────────────────────────────────────────

    # 1. One patient per day: for each patient, at most one instance per day
    for pid, insts in instances_by_patient.items():
        inst_indices = [input.instances.index(inst) for inst in insts]
        for d in day_indices:
            # At most 1 instance of this patient on this day
            # (also considering locked visits)
            if d in locked_patient_days.get(pid, set()):
                # Patient already has a locked visit on this day — no new visits
                for i in inst_indices:
                    model.add(assign[(i, d)] == 0)
            else:
                model.add(sum(assign[(i, d)] for i in inst_indices) <= 1)

    # 2. Max visits per day (including locked)
    for d in day_indices:
        locked_on_day = locked_count_by_day[d]
        available_slots = MAX_VISITS_PER_DAY - locked_on_day
        if available_slots <= 0:
            # Day is full from locked visits
            for i in range(num_instances):
                model.add(assign[(i, d)] == 0)
        else:
            model.add(sum(assign[(i, d)] for i in range(num_instances)) <= available_slots)

    # 3. Each instance assigned to exactly one day (or unscheduled)
    for i in range(num_instances):
        model.add(sum(assign[(i, d)] for d in day_indices) + (1 - scheduled[i]) == 1)

    # ── Ordering and Timing Variables ───────────────────────────────────

    # For each day, we need to sequence the visits and compute start times.
    # We use position variables: position[i][d] = order on day d (0-based)
    # and start_time[i] = minute of day when visit starts.

    start_time = []
    for i, inst in enumerate(input.instances):
        # Constrain to 15-minute intervals directly in the model
        slot_var = model.new_int_var(
            clinician.workday_start_minute // SLOT_STEP,
            (clinician.workday_end_minute - 1) // SLOT_STEP,
            f"slot_{i}",
        )
        st = model.new_int_var(
            clinician.workday_start_minute,
            clinician.workday_end_minute - 1,
            f"start_{i}",
        )
        model.add(st == slot_var * SLOT_STEP)
        start_time.append(st)

    # Position variables for sequencing within a day
    position = {}
    for i in range(num_instances):
        for d in day_indices:
            pos = model.new_int_var(0, MAX_VISITS_PER_DAY - 1, f"pos_{i}_{d}")
            position[(i, d)] = pos

    # On each day, assigned visits must have distinct positions
    for d in day_indices:
        day_assigns = [assign[(i, d)] for i in range(num_instances)]
        day_positions = [position[(i, d)] for i in range(num_instances)]

        # AllDifferent among positions of assigned visits
        # Use pairwise constraints: if both i and j are on day d, they have different positions
        for i in range(num_instances):
            for j in range(i + 1, num_instances):
                both = model.new_bool_var(f"both_{i}_{j}_{d}")
                model.add_min_equality(both, [assign[(i, d)], assign[(j, d)]])
                model.add(position[(i, d)] != position[(j, d)]).only_enforce_if(both)

    # ── Temporal Constraints (start times respect sequencing + travel) ──

    # For each pair of visits on the same day: if i is before j, then
    # start[j] >= start[i] + duration[i] + charting_buffer + travel(i→j) + transit_buffer
    for d in day_indices:
        for i in range(num_instances):
            inst_i = input.instances[i]
            for j in range(num_instances):
                if i == j:
                    continue
                inst_j = input.instances[j]

                # Boolean: both on day d AND i is before j
                i_before_j = model.new_bool_var(f"before_{i}_{j}_{d}")
                both_on_d = model.new_bool_var(f"bothd_{i}_{j}_{d}")
                model.add_min_equality(both_on_d, [assign[(i, d)], assign[(j, d)]])
                model.add(position[(i, d)] < position[(j, d)]).only_enforce_if(i_before_j)
                model.add(position[(i, d)] >= position[(j, d)]).only_enforce_if(i_before_j.negated())

                # Combined: both on day d AND i before j
                combined = model.new_bool_var(f"comb_{i}_{j}_{d}")
                model.add_min_equality(combined, [both_on_d, i_before_j])

                # If i is immediately before j (we enforce this for all ordered pairs,
                # CP-SAT will find consistent timing)
                travel_time = travel(str(inst_i.patient_id), str(inst_j.patient_id))
                gap = inst_i.duration + travel_time + TRANSIT_BUFFER
                model.add(
                    start_time[j] >= start_time[i] + gap
                ).only_enforce_if(combined)

    # Visit must end before workday ends
    for i, inst in enumerate(input.instances):
        visit_duration = inst.duration - clinician.charting_buffer_minutes  # actual visit time
        model.add(start_time[i] + visit_duration <= clinician.workday_end_minute).only_enforce_if(scheduled[i])

    # ── Calendar Block Constraints ──────────────────────────────────────

    # If visit i is on day d, its time range must not overlap any blocked range
    for d in day_indices:
        for block_start, block_end in blocked_ranges_by_day[d]:
            for i, inst in enumerate(input.instances):
                # If assigned to day d, either visit ends before block or starts after block
                # visit range: [start_time[i], start_time[i] + duration)
                # block range: [block_start, block_end)
                # No overlap: start_time[i] + duration <= block_start OR start_time[i] >= block_end
                ends_before = model.new_bool_var(f"eb_{i}_{d}_{block_start}")
                starts_after = model.new_bool_var(f"sa_{i}_{d}_{block_start}")
                model.add(start_time[i] + inst.duration <= block_start).only_enforce_if(ends_before)
                model.add(start_time[i] >= block_end).only_enforce_if(starts_after)

                # If assigned to this day, at least one must be true
                model.add(ends_before + starts_after >= 1).only_enforce_if(assign[(i, d)])

    # ── Lunch Break Constraint ──────────────────────────────────────────
    #
    # Lunch is modeled as a flexible blocked interval on each day.
    # lunch_start[d] is a variable within the lunch window.
    # No visit may overlap [lunch_start[d], lunch_start[d] + lunch_duration].

    half_window = clinician.lunch_window_minutes // 2
    lunch_earliest = max(clinician.lunch_start_minute - half_window, clinician.workday_start_minute)
    lunch_latest = min(
        clinician.lunch_start_minute + half_window,
        clinician.workday_end_minute - clinician.lunch_duration_minutes,
    )
    lunch_dur = clinician.lunch_duration_minutes

    lunch_start_var = {}
    for d in day_indices:
        # Lunch start constrained to 15-min intervals within the window
        ls_slot = model.new_int_var(
            lunch_earliest // SLOT_STEP,
            lunch_latest // SLOT_STEP,
            f"lunch_slot_{d}",
        )
        ls = model.new_int_var(lunch_earliest, lunch_latest, f"lunch_{d}")
        model.add(ls == ls_slot * SLOT_STEP)
        lunch_start_var[d] = ls

        # No visit may overlap [lunch_start, lunch_start + lunch_duration]
        for i, inst in enumerate(input.instances):
            # If assigned to day d: visit ends before lunch OR visit starts after lunch
            # visit range: [start_time[i], start_time[i] + duration)
            # lunch range: [ls, ls + lunch_dur)
            v_before_lunch = model.new_bool_var(f"vbl_{i}_{d}")
            v_after_lunch = model.new_bool_var(f"val_{i}_{d}")
            model.add(start_time[i] + inst.duration <= ls).only_enforce_if(v_before_lunch)
            model.add(start_time[i] >= ls + lunch_dur).only_enforce_if(v_after_lunch)
            model.add(v_before_lunch + v_after_lunch >= 1).only_enforce_if(assign[(i, d)])

    # ── Mandatory Break Constraint ────────────────────────────────────
    #
    # After max_continuous_work_minutes of accumulated work, a break of
    # required_break_minutes must occur. Lunch counts as a break.
    #
    # Model: the total duration of visits on each side of lunch must not
    # exceed max_continuous_work_minutes. This matches the Ruby Retimer's
    # approach where lunch resets the accumulated work counter.

    max_cont = clinician.max_continuous_work_minutes
    break_dur = clinician.required_break_minutes

    if max_cont and max_cont > 0:
        for d in day_indices:
            ls = lunch_start_var[d]
            lunch_end_var = model.new_int_var(
                lunch_earliest + lunch_dur,
                lunch_latest + lunch_dur,
                f"lunch_end_{d}",
            )
            model.add(lunch_end_var == ls + lunch_dur)

            # For each visit: is it pre-lunch or post-lunch?
            pre_lunch = {}
            post_lunch = {}
            for i, inst in enumerate(input.instances):
                pre = model.new_bool_var(f"pre_{i}_{d}")
                post = model.new_bool_var(f"post_{i}_{d}")
                # pre ↔ (start_time[i] + duration <= ls), i.e. visit ends before lunch
                model.add(start_time[i] + inst.duration <= ls).only_enforce_if(pre)
                model.add(start_time[i] + inst.duration > ls).only_enforce_if(pre.negated())
                # post ↔ (start_time[i] >= lunch_end), i.e. visit starts after lunch
                model.add(start_time[i] >= lunch_end_var).only_enforce_if(post)
                model.add(start_time[i] < lunch_end_var).only_enforce_if(post.negated())
                pre_lunch[(i, d)] = pre
                post_lunch[(i, d)] = post

            # Sum of durations for pre-lunch visits on this day
            pre_work_terms = []
            post_work_terms = []
            for i, inst in enumerate(input.instances):
                # pre-lunch work: duration if (assigned to d AND pre-lunch)
                pre_and_assigned = model.new_bool_var(f"preass_{i}_{d}")
                model.add_min_equality(pre_and_assigned, [assign[(i, d)], pre_lunch[(i, d)]])
                pw = model.new_int_var(0, inst.duration, f"prework_{i}_{d}")
                model.add(pw == inst.duration).only_enforce_if(pre_and_assigned)
                model.add(pw == 0).only_enforce_if(pre_and_assigned.negated())
                pre_work_terms.append(pw)

                # post-lunch work
                post_and_assigned = model.new_bool_var(f"postass_{i}_{d}")
                model.add_min_equality(post_and_assigned, [assign[(i, d)], post_lunch[(i, d)]])
                qw = model.new_int_var(0, inst.duration, f"postwork_{i}_{d}")
                model.add(qw == inst.duration).only_enforce_if(post_and_assigned)
                model.add(qw == 0).only_enforce_if(post_and_assigned.negated())
                post_work_terms.append(qw)

            # Total pre-lunch work <= max_continuous
            if pre_work_terms:
                total_pre = model.new_int_var(0, max_cont + MAX_VISITS_PER_DAY * 300, f"totpre_{d}")
                model.add(total_pre == sum(pre_work_terms))
                model.add(total_pre <= max_cont)

            # Total post-lunch work <= max_continuous
            if post_work_terms:
                total_post = model.new_int_var(0, max_cont + MAX_VISITS_PER_DAY * 300, f"totpost_{d}")
                model.add(total_post == sum(post_work_terms))
                model.add(total_post <= max_cont)

    # ── Soft Constraints as Penalty Variables ───────────────────────────

    penalties = []

    # 4. Unscheduled penalty (weighted by priority)
    for i, inst in enumerate(input.instances):
        patient = patients_by_id.get(inst.patient_id)
        priority_boost = (patient.priority + 1) if patient else 1
        cost = PENALTY_UNSCHEDULED * priority_boost
        # penalty when NOT scheduled
        not_sched = scheduled[i].negated()
        penalties.append((not_sched, cost))

    # 5. Spacing constraints (min/max days between visits for same patient)
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient or len(insts) < 2:
            continue
        inst_indices = [input.instances.index(inst) for inst in insts]
        min_gap = patient.min_days_between_visits
        max_gap = patient.max_days_between_visits

        # Also account for locked visit days
        locked_days_for_patient = sorted(locked_patient_days.get(pid, set()))

        for a_idx in range(len(inst_indices)):
            i = inst_indices[a_idx]

            # Spacing between this instance and locked visits
            for locked_d in locked_days_for_patient:
                both_sched = scheduled[i]
                for d in day_indices:
                    if d == locked_d:
                        continue
                    gap = abs(d - locked_d)
                    if gap < min_gap:
                        # Penalize if assigned to day d (too close to locked visit)
                        pen_var = model.new_bool_var(f"sp_lock_{i}_{d}_{locked_d}")
                        model.add_min_equality(pen_var, [assign[(i, d)], both_sched])
                        penalties.append((pen_var, PENALTY_SPACING_MIN * (min_gap - gap)))

            # Spacing between pairs of new instances
            for b_idx in range(a_idx + 1, len(inst_indices)):
                j = inst_indices[b_idx]
                # Both scheduled
                both = model.new_bool_var(f"both_sched_{i}_{j}")
                model.add_min_equality(both, [scheduled[i], scheduled[j]])

                # |day_var[i] - day_var[j]| — model as gap variable
                gap = model.new_int_var(0, num_days, f"gap_{i}_{j}")
                diff = model.new_int_var(-num_days, num_days, f"diff_{i}_{j}")
                model.add(diff == day_var[i] - day_var[j])
                model.add_abs_equality(gap, diff)

                # Min gap violation
                if min_gap > 1:
                    min_viol = model.new_int_var(0, min_gap, f"min_viol_{i}_{j}")
                    model.add(min_viol >= min_gap - gap).only_enforce_if(both)
                    model.add(min_viol == 0).only_enforce_if(both.negated())
                    penalties.append((min_viol, PENALTY_SPACING_MIN))

                # Max gap violation
                if max_gap < num_days:
                    max_viol = model.new_int_var(0, num_days, f"max_viol_{i}_{j}")
                    model.add(max_viol >= gap - max_gap).only_enforce_if(both)
                    model.add(max_viol == 0).only_enforce_if(both.negated())
                    penalties.append((max_viol, PENALTY_SPACING_MAX))

    # 6. Patient availability windows
    for i, inst in enumerate(input.instances):
        if not inst.availability_windows:
            continue
        for d in day_indices:
            wday = str(day_wdays[d])
            windows = inst.availability_windows.get(wday, [])
            if not windows:
                continue

            # If assigned to day d, start_time should be within at least one window.
            # For each window, create a fully-reified bool: in_w ↔ (start >= w_start AND start <= w_end - 1)
            in_window_bools = []
            for w_idx, w in enumerate(windows):
                w_start = w.get("start_minute", clinician.workday_start_minute)
                w_end = w.get("end_minute", clinician.workday_end_minute)
                # Use two helper bools for each bound, fully reified
                ge_start = model.new_bool_var(f"ge_{i}_{d}_{w_idx}")
                lt_end = model.new_bool_var(f"lt_{i}_{d}_{w_idx}")
                # ge_start ↔ (start_time >= w_start)
                model.add(start_time[i] >= w_start).only_enforce_if(ge_start)
                model.add(start_time[i] < w_start).only_enforce_if(ge_start.negated())
                # lt_end ↔ (start_time <= w_end - 1), i.e. start_time < w_end
                model.add(start_time[i] <= w_end - 1).only_enforce_if(lt_end)
                model.add(start_time[i] > w_end - 1).only_enforce_if(lt_end.negated())
                # in_w ↔ (ge_start AND lt_end)
                in_w = model.new_bool_var(f"inw_{i}_{d}_{w_idx}")
                model.add_min_equality(in_w, [ge_start, lt_end])
                in_window_bools.append(in_w)

            if in_window_bools:
                # in_any ↔ OR(in_window_bools)
                in_any = model.new_bool_var(f"inany_{i}_{d}")
                model.add_max_equality(in_any, in_window_bools)
                # Penalize: assigned to day d AND outside all windows
                outside = in_any.negated()
                oaa = model.new_bool_var(f"oaa_{i}_{d}")
                model.add_min_equality(oaa, [assign[(i, d)], outside])
                penalties.append((oaa, PENALTY_AVAILABILITY))

    # 7. Max drive per day (soft)
    if clinician.max_drive_minutes_per_day:
        max_drive = clinician.max_drive_minutes_per_day
        for d in day_indices:
            # Approximate: sum of travel from home to each patient and back
            # (exact sequencing-dependent drive is hard in CP; we use a proxy)
            # We sum travel(home, patient) for each assigned patient as a lower bound
            drive_terms = []
            for i, inst in enumerate(input.instances):
                # travel from home to this patient (as proxy for its contribution)
                t_home = travel("home", str(inst.patient_id))
                t_back = travel(str(inst.patient_id), "home")
                avg_leg = (t_home + t_back) // 2  # average of to/from home
                contrib = model.new_int_var(0, avg_leg, f"drv_{i}_{d}")
                model.add(contrib == avg_leg).only_enforce_if(assign[(i, d)])
                model.add(contrib == 0).only_enforce_if(assign[(i, d)].negated())
                drive_terms.append(contrib)

            if drive_terms:
                total_drive = model.new_int_var(0, 10000, f"totdrv_{d}")
                model.add(total_drive == sum(drive_terms))
                over = model.new_int_var(0, 10000, f"drvover_{d}")
                model.add(over >= total_drive - max_drive)
                model.add(over >= 0)
                penalties.append((over, PENALTY_DRIVE_OVER))

    # 8. Lunch drift from target (soft)
    lunch_target = clinician.lunch_start_minute
    for d in day_indices:
        drift = model.new_int_var(0, clinician.workday_end_minute, f"lunch_drift_{d}")
        diff = model.new_int_var(
            -(clinician.workday_end_minute), clinician.workday_end_minute, f"lunch_diff_{d}"
        )
        model.add(diff == lunch_start_var[d] - lunch_target)
        model.add_abs_equality(drift, diff)
        penalties.append((drift, PENALTY_LUNCH_DRIFT))

    # 9. Target day offset — spread visits evenly across the week
    #    For each patient with N visits, compute ideal day offsets and penalize
    #    deviation. This matches Ruby's evenly_spaced_day_offsets().
    for pid, insts in instances_by_patient.items():
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        n_visits = len(insts)
        if n_visits <= 1:
            continue

        inst_indices = [input.instances.index(inst) for inst in insts]
        min_gap_p = patient.min_days_between_visits

        # Compute target offsets: evenly spaced across working days
        max_step = (num_days - 1) / max(n_visits - 1, 1)
        min_step = max(float(min_gap_p), 1.0)
        step = max_step - (clinician.schedule_density * (max_step - min_step))
        step = max(step, min_step)

        targets = [round(idx * step) for idx in range(n_visits)]
        targets = [min(t, num_days - 1) for t in targets]

        # Sort instances by index to give stable target assignment
        for k, i in enumerate(sorted(inst_indices)):
            target_day = targets[k] if k < len(targets) else targets[-1]
            # |day_var[i] - target_day| penalty
            dev = model.new_int_var(0, num_days, f"daydev_{i}")
            raw_diff = model.new_int_var(-num_days, num_days, f"daydiff_{i}")
            model.add(raw_diff == day_var[i] - target_day)
            model.add_abs_equality(dev, raw_diff)
            # Only penalize if scheduled
            dev_if_sched = model.new_int_var(0, num_days, f"daydevs_{i}")
            model.add(dev_if_sched == dev).only_enforce_if(scheduled[i])
            model.add(dev_if_sched == 0).only_enforce_if(scheduled[i].negated())
            penalties.append((dev_if_sched, PENALTY_DAY_OFFSET))

    # ── Objective: Minimize Travel + Penalties ──────────────────────────

    # Travel cost: for each pair on the same day where i is before j,
    # add the travel time. Also add home→first and last→home.
    travel_cost_terms = []

    # Home → first visit on each day (position 0)
    for d in day_indices:
        for i, inst in enumerate(input.instances):
            is_first = model.new_bool_var(f"first_{i}_{d}")
            model.add(position[(i, d)] == 0).only_enforce_if(is_first)
            model.add(position[(i, d)] != 0).only_enforce_if(is_first.negated())
            first_and_assigned = model.new_bool_var(f"fa_{i}_{d}")
            model.add_min_equality(first_and_assigned, [is_first, assign[(i, d)]])

            t = travel("home", str(inst.patient_id))
            if t > 0:
                cost_var = model.new_int_var(0, t, f"hf_{i}_{d}")
                model.add(cost_var == t).only_enforce_if(first_and_assigned)
                model.add(cost_var == 0).only_enforce_if(first_and_assigned.negated())
                travel_cost_terms.append(cost_var)

    # Last visit → home on each day
    for d in day_indices:
        for i, inst in enumerate(input.instances):
            # "last" = has the highest position among assigned visits
            # Approximate: position == (count_on_day - 1)
            # We'll use: no other assigned visit has a higher position
            is_last_candidates = []
            for j in range(num_instances):
                if j == i:
                    continue
                j_higher = model.new_bool_var(f"jh_{i}_{j}_{d}")
                j_on_d = assign[(j, d)]
                j_pos_higher = model.new_bool_var(f"jph_{i}_{j}_{d}")
                model.add(position[(j, d)] > position[(i, d)]).only_enforce_if(j_pos_higher)
                model.add(position[(j, d)] <= position[(i, d)]).only_enforce_if(j_pos_higher.negated())
                model.add_min_equality(j_higher, [j_on_d, j_pos_higher])
                is_last_candidates.append(j_higher)

            no_one_after = model.new_bool_var(f"noa_{i}_{d}")
            if is_last_candidates:
                any_after = model.new_bool_var(f"aa_{i}_{d}")
                model.add_max_equality(any_after, is_last_candidates)
                model.add(no_one_after == 1 - any_after)
            else:
                model.add(no_one_after == 1)

            last_and_assigned = model.new_bool_var(f"la_{i}_{d}")
            model.add_min_equality(last_and_assigned, [no_one_after, assign[(i, d)]])

            t = travel(str(inst.patient_id), "home")
            if t > 0:
                cost_var = model.new_int_var(0, t, f"lh_{i}_{d}")
                model.add(cost_var == t).only_enforce_if(last_and_assigned)
                model.add(cost_var == 0).only_enforce_if(last_and_assigned.negated())
                travel_cost_terms.append(cost_var)

    # Inter-visit travel (consecutive pairs)
    for d in day_indices:
        for i in range(num_instances):
            inst_i = input.instances[i]
            for j in range(num_instances):
                if i == j:
                    continue
                inst_j = input.instances[j]
                t = travel(str(inst_i.patient_id), str(inst_j.patient_id))
                if t == 0:
                    continue

                # i immediately before j: both on d, pos[i] + 1 == pos[j],
                # and no other visit has position between them
                # Simplified: use the "i_before_j" already implied by positions
                # We already have temporal constraints; here we just want cost.
                # Use: both on d AND pos[j] == pos[i] + 1
                consec = model.new_bool_var(f"consec_{i}_{j}_{d}")
                adj = model.new_bool_var(f"adj_{i}_{j}_{d}")
                model.add(position[(j, d)] == position[(i, d)] + 1).only_enforce_if(adj)
                model.add(position[(j, d)] != position[(i, d)] + 1).only_enforce_if(adj.negated())

                both_on = model.new_bool_var(f"bon_{i}_{j}_{d}")
                model.add_min_equality(both_on, [assign[(i, d)], assign[(j, d)]])
                model.add_min_equality(consec, [both_on, adj])

                cost_var = model.new_int_var(0, t, f"tc_{i}_{j}_{d}")
                model.add(cost_var == t).only_enforce_if(consec)
                model.add(cost_var == 0).only_enforce_if(consec.negated())
                travel_cost_terms.append(cost_var)

    # Density penalty: penalize variance in visits per day
    # Count visits per day
    day_counts = []
    for d in day_indices:
        cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, f"cnt_{d}")
        model.add(cnt == sum(assign[(i, d)] for i in range(num_instances)))
        day_counts.append(cnt)

    # Schedule density: balance between spreading (low density) and clustering (high density)
    density = clinician.schedule_density
    # For density=0 (spread), penalize variance. For density=1 (cluster), penalize active days.
    if density < 0.5 and num_days > 1:
        # Spread: penalize difference between max and min day counts
        max_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "max_cnt")
        min_cnt = model.new_int_var(0, MAX_VISITS_PER_DAY, "min_cnt")
        model.add_max_equality(max_cnt, day_counts)
        model.add_min_equality(min_cnt, day_counts)
        spread_diff = model.new_int_var(0, MAX_VISITS_PER_DAY, "spread_diff")
        model.add(spread_diff == max_cnt - min_cnt)
        density_weight = int(WEIGHT_DENSITY * 10 * (1.0 - density))
        travel_cost_terms.append(spread_diff * density_weight if density_weight > 0 else 0)
    elif density > 0.5:
        # Cluster: penalize number of active days
        for d in day_indices:
            has_visits = model.new_bool_var(f"active_{d}")
            model.add(day_counts[d] > 0).only_enforce_if(has_visits)
            model.add(day_counts[d] == 0).only_enforce_if(has_visits.negated())
            density_weight = int(WEIGHT_DENSITY * 10 * density)
            if density_weight > 0:
                travel_cost_terms.append(has_visits * density_weight)

    # Build total objective
    # Scale travel cost by WEIGHT_DRIVE
    total_travel = model.new_int_var(0, 1_000_000, "total_travel")
    if travel_cost_terms:
        model.add(total_travel == sum(travel_cost_terms))
    else:
        model.add(total_travel == 0)

    # Penalty terms
    penalty_terms = []
    for var_or_bool, weight in penalties:
        w = int(weight)
        if w > 0:
            if isinstance(var_or_bool, cp_model.IntVar):
                penalty_terms.append(var_or_bool * w)
            else:
                # BoolVar — convert to int contribution
                pv = model.new_int_var(0, w, f"pen_{id(var_or_bool)}")
                model.add(pv == w).only_enforce_if(var_or_bool)
                model.add(pv == 0).only_enforce_if(var_or_bool.negated())
                penalty_terms.append(pv)

    total_penalty = model.new_int_var(0, 10_000_000, "total_penalty")
    if penalty_terms:
        model.add(total_penalty == sum(penalty_terms))
    else:
        model.add(total_penalty == 0)

    model.minimize(total_travel + total_penalty)

    # ── Solve ───────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_budget
    solver.parameters.num_workers = 8  # parallel search
    solver.parameters.log_search_progress = False

    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _empty_output(input, metadata={
            "optimizer_type": "cpsat",
            "status": solver.status_name(status),
            "proven_optimal": False,
        })

    # ── Extract Solution ────────────────────────────────────────────────

    planned_visits = []
    routes_by_day: dict[str, list[int]] = defaultdict(list)

    for i, inst in enumerate(input.instances):
        if not solver.value(scheduled[i]):
            continue

        d = solver.value(day_var[i])
        if d >= num_days:
            continue

        date = working_days[d]
        s = solver.value(start_time[i])
        visit_duration = inst.duration - clinician.charting_buffer_minutes

        # Check if this was a soft constraint override
        soft_override = False
        # Check availability window compliance
        wday = str(day_wdays[d])
        windows = inst.availability_windows.get(wday, [])
        if windows:
            in_any = any(
                w.get("start_minute", 0) <= s < w.get("end_minute", 1440)
                for w in windows
            )
            if not in_any:
                soft_override = True

        planned_visits.append(PlannedVisit(
            instance_id=inst.id,
            patient_id=inst.patient_id,
            date=date,
            starts_at=minutes_to_datetime(date, s),
            ends_at=minutes_to_datetime(date, s + visit_duration),
            soft_constraint_override=soft_override,
        ))
        routes_by_day[date].append(inst.patient_id)

    # Sort planned visits by date and start time
    planned_visits.sort(key=lambda v: (v.date, v.starts_at))

    # Lunch placements — extract from model variables (lunch was a constraint during solve)
    lunch = {}
    for d in day_indices:
        ls = solver.value(lunch_start_var[d])
        lunch[working_days[d]] = {
            "start_minute": ls,
            "end_minute": ls + clinician.lunch_duration_minutes,
        }

    # Return home
    return_home = compute_return_home(
        {d: pids for d, pids in routes_by_day.items()},
        matrix,
    )

    # Unschedulable
    placed_ids = {v.instance_id for v in planned_visits}
    unschedulable = []
    for inst in input.instances:
        if inst.id not in placed_ids:
            patient = patients_by_id.get(inst.patient_id)
            unschedulable.append({
                "patient_name": patient.name if patient else "Unknown",
                "patient_id": inst.patient_id,
            })

    # Drive violations
    drive_violations = []
    if clinician.max_drive_minutes_per_day:
        for date, pids in routes_by_day.items():
            total_drive = 0
            prev = "home"
            for pid in pids:
                total_drive += travel(prev, str(pid))
                prev = str(pid)
            total_drive += travel(prev, "home")
            if total_drive > clinician.max_drive_minutes_per_day:
                drive_violations.append({
                    "date": date,
                    "drive_minutes": total_drive,
                    "max_drive": clinician.max_drive_minutes_per_day,
                })

    proven_optimal = status == cp_model.OPTIMAL
    fitness = solver.objective_value

    return SolverOutput(
        planned_visits=planned_visits,
        lunch_placements=lunch,
        fitness=fitness,
        metadata={
            "optimizer_type": "cpsat",
            "proven_optimal": proven_optimal,
            "status": solver.status_name(status),
            "unschedulable": unschedulable,
            "drive_violations": drive_violations,
            "soft_constraint_overrides": sum(1 for v in planned_visits if v.soft_constraint_override),
            "return_home_by_day": return_home,
            "runtime_seconds": solver.wall_time,
            "branches": solver.num_branches,
        },
    )


# ── Helpers ─────────────────────────────────────────────────────────────

def _day_index(date_str: str, working_days: list[str]) -> int | None:
    try:
        return working_days.index(date_str)
    except ValueError:
        return None


def _datetime_to_minute(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str)
    return dt.hour * 60 + dt.minute


def _round_up(minute: int, step: int) -> int:
    remainder = minute % step
    return minute if remainder == 0 else minute + (step - remainder)


def _empty_output(input: SolverInput, metadata: dict | None = None) -> SolverOutput:
    return SolverOutput(
        planned_visits=[],
        lunch_placements=build_lunch_placements(input),
        fitness=0.0,
        metadata=metadata or {"optimizer_type": "cpsat"},
    )
