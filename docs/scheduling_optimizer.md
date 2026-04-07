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

### Three-Tier Solver Architecture

```
Tier 1: BCP (nightly, exact)                    ← VRPSolverEasy
  Finds the provably optimal schedule for the week.
  Uses HGS solution as upper bound for faster pruning.
  Falls back to HGS result if BCP times out.

Tier 2: HGS (on-demand, near-optimal)           ← PyVRP
  Re-optimizes when structure changes (new patient, calendar update).
  5-30 seconds. Uses last BCP/HGS solution as warm start.

Tier 3: Greedy + ALNS (instant, on-demand)       ← Ruby (current)
  Regret-based insertion + adaptive large neighborhood search.
  Runs when user clicks Re-optimize. <1 second.

Future: NCO (real-time, <100ms)
  Neural model for mid-day tactical adjustments.
  Patient cancels, clinician running late, traffic changes.
```

### Nightly Pipeline

```
1. HGS (PyVRP) → near-optimal solution in 30-60s
2. Feed HGS solution as upper bound to BCP (VRPSolverEasy)
3. BCP either proves optimality or improves the solution
4. If BCP times out → HGS solution is used (still excellent)
5. Store as "master plan" for the week
```

### Real-Time Flow

```
Structural change (new patient, settings change):
  → Greedy (instant) for immediate display
  → HGS (background, 5-30s) for improved solution
  → Suggest improvement if found

Mid-day disruption (cancellation, delay):
  → NCO (<100ms) for instant route adjustment (future)
  → HGS (background) for better weekly re-optimization
```

### Solver Abstraction Layer

All solvers receive `SolverInputData` (plain data, no ActiveRecord) and return `SolverOutputData`. The `SolverRunner` builds input from the database, dispatches to the selected backend, and persists the result.

```
SolverInput.build(user:, week_start_on:)   → SolverInputData
Solver.solve(input, backend: :greedy)      → SolverOutputData
SolverRunner.run(user:, ..., backend:)     → WeeklySchedule (persisted)
```

Backends: `:greedy`, `:ga`, `:hgs` (future), `:bcp` (future), `:nco` (future)

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

## Current Implementation (Ruby)

### Greedy Optimizer — Regret Insertion + ALNS

The on-demand optimizer triggered by the Re-optimize button.

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
- Repair: regret-based re-insertion with rebuilt blocked ranges
- Only accepts improvements, rejects plans that violate hard constraints or drop visits

**Route ordering (per day):**
- Nearest-neighbor using travel matrix
- True 2-opt: reverse subsequences to reduce round-trip cost (including return home)

**Retiming:**
- Assigns concrete start/end times respecting transit gaps, charting buffer, lunch window, mandatory breaks, locked visit ranges
- Rounds to 15-minute intervals

**Max drive enforcement:**
- Hard constraint during day assignment (projected drive including new visit)
- Soft warning on final routes (report violations, never drop visits)

### GA Optimizer — HGS-Inspired Genetic Algorithm

Nightly optimizer via Solid Queue. ~11-16% improvement over greedy.

**Population:** 30-50 individuals, seeded with 1 greedy solution + random perturbations

**Crossover:** Route-segment crossover — randomly assign each day to a parent, inherit all visits for that day as a unit. Preserves geographic clusters.

**Mutation:** Move visit to different day, or swap two visits between days. Cooling rate 0.97x per generation.

**Education (HGS-inspired):** After crossover+mutation, each child undergoes local search (relocate + swap moves on day assignments) before entering the population. Every individual is a local optimum.

**Diversity management:**
- Speciation by structural distance (fraction of differing day assignments)
- Diversity-based survivor selection: keep most structurally different individuals alongside elites
- Diversity injection every 15 stagnant generations: inject 30% random individuals + boost mutation

**Feasibility:** Decode via nearest-neighbor + retimer. Check one-patient-per-day, min spacing, max drive. Infeasible chromosomes rejected.

**Termination:** Time budget (default 45s nightly, 10s for testing) or 50 generations of stagnation.

---

## Future: Python Solver Microservice

A sidecar Python service that the Rails app calls via HTTP through the solver abstraction.

### PyVRP — HGS (Tier 2)

[PyVRP](https://github.com/PyVRP/PyVRP) — Hybrid Genetic Search for VRP. State-of-the-art metaheuristic. Consistently wins academic VRP competitions. Handles CVRP, VRPTW, heterogeneous fleet, multi-depot.

```python
from pyvrp import Model, solve

model = Model()
model.add_depot(x=home_lng, y=home_lat)
for patient in patients:
    model.add_client(x=patient.lng, y=patient.lat,
                     tw_early=window_start, tw_late=window_end,
                     service_duration=visit_duration)
for day in working_days:
    model.add_vehicle_type(capacity=MAX_VISITS, max_duration=max_drive)

result = solve(model, stop=MaxRuntime(30))
```

### VRPSolverEasy — BCP (Tier 1)

[VRPSolverEasy](https://github.com/inria-UFF/VRPSolverEasy) — Branch-Cut-and-Price exact solver from INRIA. Finds provably optimal solutions. Based on BaPCod solver + COIN-OR CLP.

```python
import VRPSolverEasy as vrpse

model = vrpse.Model()
model.add_depot(id=0, x=home_lng, y=home_lat)
for patient in patients:
    model.add_customer(id=patient.id, x=patient.lng, y=patient.lat,
                       demand=1, tw_begin=start, tw_end=end)
model.add_vehicle_type(id=1, capacity=MAX_VISITS,
                       max_number=len(working_days))
model.set_parameters(time_limit=3600)

# Warm start with HGS solution as upper bound
model.set_initial_solution(hgs_routes, hgs_cost)

model.solve()
```

**Performance:** Provably optimal for ~100 customers. Some 200-customer instances solvable. Performance improves significantly with HGS upper bound.

### NCO — Neural Combinatorial Optimization (Tier 3, future)

Attention-based model trained on historical schedule data for real-time adjustments.

```
Training data:
  Input:  (patient locations, time windows, current partial route, disruption type)
  Output: (next action: insert, swap, skip, defer)

Architecture: Transformer encoder for problem state,
              autoregressive decoder for route sequence

Training: REINFORCE with baseline, reward = -route_cost
```

Produces route adjustments in <100ms. Trained on data specific to each clinician's patient geography and scheduling patterns.

### Multi-Objective Support

**BCP (nightly):** Run with different weight vectors, each run is exact for its combination:
```
Run 1: minimize 0.8*drive_time + 0.2*workload_balance
Run 2: minimize 0.5*drive_time + 0.5*workload_balance
Run 3: minimize 0.2*drive_time + 0.8*workload_balance
→ Present Pareto front of trade-off schedules
```

**HGS:** Multi-objective via Pareto dominance in population. Non-dominated individuals form the front. Single run produces multiple trade-off solutions.

**NCO:** Conditional generation — objective weights as model input. Clinician adjusts a preference slider, model generates matching solution in milliseconds.

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
app/services/scheduling/
  solver_input.rb              # SolverInputData — AR-free data structs
  solver.rb                    # Backend dispatch (greedy, ga, hgs, bcp)
  solver_runner.rb             # Build input → solve → persist
  solvers/
    greedy.rb                  # Regret insertion + ALNS (AR-free)
  weekly_optimizer.rb          # Legacy greedy (AR-coupled, still used by controller)
  ga_optimizer.rb              # GA with education + diversity (AR-coupled)
  chromosome.rb                # GA encoding: route-segment crossover, mutation
  fitness_function.rb          # Weighted multi-objective scoring
  feasibility_checker.rb       # Hard constraint validation
  retimer.rb                   # Assign concrete times within a day
  visit_instance.rb            # VisitInstance data struct
  visit_instance_builder.rb    # Build instances from patients + locked visits
  schedule_persister.rb        # Persist solver output to DB
  travel_time_matrix_builder.rb # Mapbox Matrix API + haversine fallback
  calendar_constraints.rb      # Blocked time ranges from calendar events
  conflict_detector.rb         # Post-optimization conflict audit
  rescheduler.rb               # Manual single-visit reschedule
  time_window.rb               # Simple (start_minute, end_minute) data class

app/services/integrations/
  routing_client.rb            # Mapbox Matrix API + haversine fallback

config/recurring.yml           # Nightly GA job at midnight
```
