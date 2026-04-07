# RouteCare Scheduling Optimizer

A living design document for the route-optimized scheduling algorithm.

---

## Overview

The scheduler solves a variant of the **Multi-Period Vehicle Routing Problem with Time Windows (MP-VRPTW)** — assigning home visits to days and times such that total drive time is minimized and all constraints are satisfied.

The problem has two fundamental dimensions:
- **Space** — physical locations of patient homes and travel times between them
- **Time** — when visits can occur, constrained by calendar events, working hours, patient availability, and transit times

These dimensions are coupled: spatial decisions (which patients to group on a day) create temporal consequences (transit time consumes the time budget), and temporal constraints (existing calendar events) restrict which spatial arrangements are feasible.

---

## Architecture

### Solver Architecture

```
Primary: CP-SAT (default, on-demand)             ← Google OR-Tools
  Constraint programming model with all domain constraints.
  Enforces spacing, availability, breaks, blocks during search.
  Proves optimality or returns best-found within time budget.
  Replaces both Ruby greedy optimizer and VRP-only solvers.

Legacy: HGS (travel-only, near-optimal)          ← PyVRP
  Hybrid Genetic Search for pure VRP.
  Fast but does not enforce healthcare constraints.
  Selectable via ?backend=hgs.

Legacy: BCP (travel-only, exact)                  ← VRPSolverEasy / BaPCod
  Branch-Cut-and-Price exact VRP solver.
  Requires BaPCod binary (academic license).
  Selectable via ?backend=bcp.

Legacy: Greedy + ALNS (Ruby, in-app)              ← WeeklyOptimizer
  Regret-based insertion + adaptive large neighborhood search.
  Full constraint support but heuristic-only (no optimality proof).
  Still used by the Rails controller; being replaced by CP-SAT.

Future: NCO (real-time, <100ms)
  Neural model for mid-day tactical adjustments.
  Patient cancels, clinician running late, traffic changes.
```

### Solver Flow

```
User clicks Re-optimize:
  → Rails calls Python solver service (/solve?backend=cpsat)
  → CP-SAT builds model with all constraints
  → Returns optimal/near-optimal schedule in 5-30s
  → Rails persists result

Pipeline mode (?backend=pipeline):
  1. HGS (PyVRP) → near-optimal VRP solution
  2. Feed HGS upper bound to BCP (VRPSolverEasy)
  3. Return best result (travel-only, no domain constraints)
```

### Solver Abstraction Layer

The Python solver service receives `SolverInput` (JSON) via HTTP POST and returns `SolverOutput`. The Rails app builds the input from the database and persists the result.

```
Rails:  SolverInput.build(user:, week_start_on:) → JSON
Python: POST /solve?backend=cpsat               → SolverOutput (JSON)
Rails:  SchedulePersister.persist(output)        → WeeklySchedule
```

Backends: `cpsat` (default), `hgs`, `bcp`, `pipeline`

---

## Formal Problem Statement

### Given

```
P = {p_0, p_1, ..., p_n}
  p_0                       = home (start and end of every route)
  p_1..p_n                  = visit instances
                              patient a needing k visits → instances p_a1..p_ak
                              all at the same (lat, lon), independent time positions

T[i,j] = travel_time(p_i, p_j)    # from Mapbox Matrix API or haversine fallback

D = {1, ..., d}                    # working days in the week

F_k ⊂ P  ∀ k ∈ D                  # confirmed visits and calendar blocks on day k

duration(p_i)   = visit_duration_minutes + charting_buffer_minutes
window(p_i, k)  = [earliest_i_on_day_k, latest_i_on_day_k]
priority(p_i)   = int

min_gap(a)      = minimum days between any two instances of patient a
max_gap(a)      = maximum days between any two instances of patient a

[ws, we]        = workday window in minutes from midnight
max_continuous  = max continuous work minutes before mandatory break
break_duration  = length of mandatory break in minutes
lunch_target    = target lunch start in minutes from midnight
lunch_window    = how far lunch may shift from target
lunch_duration  = length of lunch break in minutes
max_drive_k     = max total drive minutes allowed on day k
density         = physician preference: 0 = spread evenly, 1 = load fewer days
```

### Find

```
R_k = ordered path through F_k ∪ S_k, ∀ k ∈ D

where:
  S_k ⊂ P_free   chosen by the optimizer
  P_free = P \ ∪_k F_k
  each R_k starts and ends at p_0
```

### Objective

```
minimize  F(R) =

  α * Σ_k  Σ_(i,j) ∈ R_k  T[i,j]                             # total drive time

  + β * Σ_a  Σ_(i,j) ∈ instances(a)  spacing_penalty(a, i, j) # visit regularity

  + γ * Σ_k  Σ_i ∈ R_k  window_penalty(p_i, k)               # availability windows

  + δ * density_penalty(R)                                     # schedule density preference

  + ε * Σ_k  |lunch_start_k - lunch_target|                   # lunch timing

where:
  spacing_penalty(a, i, j) = max(0, min_gap(a) - |day_i - day_j|)
                            + max(0, |day_i - day_j| - max_gap(a))

  window_penalty(p_i, k)   = max(0, window(p_i,k).earliest - arrival(p_i))
                            + max(0, arrival(p_i) - window(p_i,k).latest)

  density_penalty(R)        = density * Σ_k 1[|S_k| > 0]
                            + (1-density) * variance(|S_k| across k)
```

### Subject To (Hard Constraints)

```
# 1. Every free instance assigned exactly once
∪_k S_k = P_free

# 2. Fixed nodes are immovable
∀ n ∈ F_k: n.day = k, n.time_start = n.fixed_time

# 3. At most one instance of each patient per day
|R_k ∩ instances(a)| ≤ 1   ∀ a, ∀ k

# 4. Transit feasibility — gap between nodes covers travel time
∀ k, ∀ consecutive (i,j) ∈ R_k:
  arrival(j) ≥ departure(i) + T[i,j]
  where departure(i) = arrival(i) + duration(i)

# 5. No overlap with fixed nodes
∀ free p_i ∈ R_k, ∀ fixed f ∈ F_k:
  departure(p_i) ≤ arrival(f)  OR  departure(f) ≤ arrival(p_i)

# 6. Workday bounds
∀ k, ∀ p_i ∈ R_k:
  arrival(p_i) ≥ ws
  departure(p_i) ≤ we

# 7. Max drive time per day (soft during final enforcement, hard during day assignment)
∀ k:  Σ_(i,j) ∈ R_k  T[i,j]  ≤  max_drive_k

# 8. Mandatory break after max continuous work
∀ k: any contiguous block of scheduled time exceeding max_continuous minutes
     must be followed by a break of at least break_duration minutes

# 9. Lunch must occur
∀ k ∈ working_days: ∃ lunch interval in R_k of length ≥ lunch_duration
  within [lunch_target - lunch_window, lunch_target + lunch_window]
```

---

## Primary Implementation: CP-SAT (Python)

### OR-Tools CP-SAT — Constraint Programming Solver

[OR-Tools](https://github.com/google/or-tools) CP-SAT is a constraint programming solver with SAT-based search. Unlike VRP-specific solvers (PyVRP, BaPCod), it models arbitrary constraints natively — making it ideal for healthcare scheduling where domain constraints (spacing, availability, breaks) go far beyond standard vehicle routing.

**Key advantages over VRP solvers:**
- All constraints enforced *during* search, not post-hoc
- Can prove optimality (or return best-found within time budget)
- No binary licensing issues — pure Python, installs via `pip install ortools`
- Single solver replaces HGS + BCP + Ruby greedy

**Model structure:**

Decision variables:
- `day_var[i]` — which working day each visit instance is assigned to (or "unscheduled")
- `start_time[i]` — minute-of-day when visit starts (constrained to 15-min intervals)
- `position[i][d]` — sequencing order within a day
- `assign[i][d]` — binary: is instance i assigned to day d?
- `lunch_start_var[d]` — lunch start time on each day (within lunch window)

Hard constraints:
- One patient per day (including locked visits)
- Max 5 visits per day (including locked visits)
- Visit sequencing: `start[j] >= start[i] + duration[i] + travel(i→j) + transit_buffer`
- Calendar blocks: no temporal overlap with blocked ranges
- Locked visits: time ranges blocked, patient-day excluded
- Workday boundaries: all visits within [workday_start, workday_end]
- 15-minute slot granularity
- **Lunch break:** no visit may overlap `[lunch_start, lunch_start + lunch_duration]` on any day; lunch_start is a variable within the lunch window, optimized during search
- **Max continuous work:** total visit duration on each side of lunch ≤ `max_continuous_work_minutes`; lunch resets the work accumulator (matching Ruby Retimer behavior)

Soft constraints (penalty variables in objective):
- **Unscheduled:** 10,000 × (priority + 1) per unplaced visit
- **Min spacing:** 100 per day short of `min_days_between_visits`
- **Max spacing:** 50 per day over `max_days_between_visits`
- **Availability windows:** 150 per visit outside patient's preferred time-of-day
- **Max drive per day:** 50 per minute over `max_drive_minutes_per_day`
- **Lunch drift:** 1 per minute of lunch deviation from target
- **Target day offset:** 10 per day of deviation from ideal evenly-spaced day
- **Schedule density:** spread/cluster penalty based on clinician preference

Objective:
```
minimize:
  total_travel (home→visits→home, all days)
  + Σ soft_constraint_penalties
```

**Lunch placement:** Lunch is a first-class constraint during optimization. The solver finds the optimal lunch time within the configured window while ensuring no visit overlaps it. Lunch times are extracted directly from the model solution.

**Performance:** Solves 5-visit/5-day problems in <5s. Scales to ~50 visits across 5-7 days within 30s time budget. Proves optimality on small instances.

---

## Legacy Implementations

### Ruby Greedy Optimizer (WeeklyOptimizer)

The original on-demand optimizer, still used by the Rails controller.

**Construction (regret insertion):**
1. Build travel matrix (Mapbox Matrix API, haversine fallback) including home node
2. Build all visit instances with target day offsets (respects `schedule_density` and `min_gap`)
3. Iteratively place the visit with highest *regret* (gap between best and second-best day)
   - Priority and time-window tightness boost regret
   - Day scoring: target day proximity + spacing penalty − geographic cluster bonus
   - Strict pass first (spacing/drive as hard filters), relaxed pass if no candidates (as penalties)
4. Block placed visit's full footprint (duration + charting buffer) in time ranges

**Post-optimization (ALNS):**
- 30 iterations of destroy/repair
- 5 destroy strategies: worst-cost, geographic cluster, random, same-patient, full-day
- Adaptive weights: strategies that produce improvements get used more

**Route ordering:** Nearest-neighbor + true 2-opt per day.

**Retiming:** Assigns concrete times respecting transit gaps, charting buffer, lunch window, mandatory breaks, locked visit ranges. Rounds to 15-minute intervals.

### Python HGS (PyVRP) — Travel-Only VRP

[PyVRP](https://github.com/PyVRP/PyVRP) — Hybrid Genetic Search. Fast near-optimal VRP solver. **Does not enforce** healthcare constraints (spacing, availability, breaks, calendar blocks). Useful as a travel-time baseline.

### Python BCP (VRPSolverEasy / BaPCod) — Travel-Only Exact

[VRPSolverEasy](https://github.com/inria-UFF/VRPSolverEasy) — Branch-Cut-and-Price exact solver. Requires BaPCod binary (academic license, not bundled in Docker). Same limitation as HGS: travel-only, no domain constraints.

---

## Future: NCO — Neural Combinatorial Optimization

Attention-based model trained on historical schedule data for real-time mid-day adjustments (<100ms). Patient cancels, clinician running late, traffic changes.

---

## Parameters

| Parameter | Scope | Affects |
|---|---|---|
| `max_continuous_work_minutes` | Physician | Mandatory break trigger |
| `required_break_minutes` | Physician | Length of mandatory break |
| `lunch_start_minute` | Physician | Target lunch time |
| `lunch_duration_minutes` | Physician | Length of lunch |
| `lunch_window_minutes` | Physician | How far lunch can shift from target |
| `workday_start_minute` | Physician | Earliest visit start |
| `workday_end_minute` | Physician | Latest visit end |
| `working_days` | Physician | Which days of week are active |
| `max_drive_minutes_per_day` | Physician | Daily drive cap (round-trip including return home) |
| `schedule_density` | Physician | Cluster visits into fewer days vs. spread evenly |
| `charting_buffer_minutes` | Physician | Post-visit documentation gap (not added to visit block) |
| `required_visits_per_week` | Patient | Number of visit instances to schedule |
| `visit_duration_minutes` | Patient | Duration of each visit |
| `min_days_between_visits` | Patient | Minimum spacing between instances |
| `max_days_between_visits` | Patient | Maximum spacing between instances |
| `priority` | Patient | Scheduling urgency (higher = earlier slots, route penalty discounted) |
| `availability_windows` | Patient | Time-of-day soft preference by day of week |

---

## Constraints

### Hard Constraints
*Never violate — infeasible schedules are discarded*

- No visits during existing calendar events
- No more than one visit per patient per day
- Visits cannot exceed physician's configured working hours per day
- Mandatory break after `max_continuous_work_minutes` of accumulated work
- Lunch break must occur within the configured window
- Patient visit frequency met within the week
- Transit time between consecutive visits must fit within the time gap

### Soft Constraints
*Optimize toward — violations are reported as warnings but schedules remain valid*

- **Max drive time per day** — hard during day assignment, soft warning on final routes (never drops visits)
- **Patient spacing** — visits to the same patient separated by at least `min_days_between_visits`
- **Patient max spacing** — visits not further apart than `max_days_between_visits`
- **Patient time-of-day preference** — respect availability windows
- **Patient priority** — high-acuity patients scheduled earlier in day/week
- **Physician schedule density** — prefer configured density (sparse vs. loaded days)
- **Geographic day-clustering** — group nearby patients on the same day
- **Charting buffer** — gap after each visit for documentation
- **Lunch timing** — lunch falls near `lunch_start_minute`, within flexibility window

---

## File Layout

```
solver_service/                     # Python solver microservice
  main.py                           # FastAPI app, /solve endpoint
  models.py                         # Pydantic models (SolverInput, SolverOutput)
  Dockerfile                        # python:3.11-slim, uvicorn
  requirements.txt                  # ortools, pyvrp, fastapi, etc.
  solvers/
    __init__.py                      # Exports: cpsat_solve, hgs_solve, bcp_solve
    cpsat.py                         # CP-SAT solver (primary, all constraints)
    hgs.py                           # PyVRP HGS solver (legacy, travel-only)
    bcp.py                           # VRPSolverEasy BCP solver (legacy, travel-only)
    base.py                          # Shared utilities (lunch, return-home, spacing)
  tests/
    test_cpsat.py                    # 11 tests covering all constraint types
    test_hgs.py                      # HGS basic tests

app/services/scheduling/             # Ruby (legacy, being replaced)
  weekly_optimizer.rb                # Greedy + ALNS (still used by controller)
  fitness_function.rb                # Weighted multi-objective scoring
  retimer.rb                         # Assign concrete times within a day
  visit_instance_builder.rb          # Build instances from patients + locked visits
  schedule_persister.rb              # Persist solver output to DB
  travel_time_matrix_builder.rb      # Mapbox Matrix API + haversine fallback
  calendar_constraints.rb            # Blocked time ranges from calendar events
  conflict_detector.rb               # Post-optimization conflict audit
  rescheduler.rb                     # Manual single-visit reschedule
  time_window.rb                     # Simple (start_minute, end_minute) data class

app/services/integrations/
  routing_client.rb                  # Mapbox Matrix API + haversine fallback
```
