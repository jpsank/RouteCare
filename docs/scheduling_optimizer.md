# RouteCare Scheduling Optimizer

A living design document for the route-optimized scheduling algorithm.

---

## Overview

The scheduler solves a variant of the **Vehicle Routing Problem with Time Windows (VRPTW)** — assigning home visits to days and times such that total drive time is minimized and all constraints are satisfied.

The problem has two fundamental dimensions:
- **Space** — physical locations of patient homes and travel times between them
- **Time** — when visits can occur, constrained by calendar events, working hours, patient availability, and transit times

These dimensions are coupled: spatial decisions (which patients to group on a day) create temporal consequences (transit time consumes the time budget), and temporal constraints (existing calendar events) restrict which spatial arrangements are feasible.

---

## Two-Optimizer Architecture

Two complementary optimizers handle different use cases:

### GA Optimizer (Global)
- Runs nightly at midnight via Solid Queue scheduled job
- Full global optimization over the entire week
- Can rearrange any visit that is not locked (i.e. pending visits only)
- Slow is acceptable — has hours to run
- Uses a genetic algorithm with NEAT-inspired crossover

### Greedy Optimizer (Local)
- Runs on-demand when a patient is added or a visit is manually changed
- Takes the existing schedule as context
- Re-solves a constrained subproblem: confirmed visits fixed, all pending + new visits free
- Must be fast — user is waiting
- Uses an insertion heuristic: finds the best slot for new/displaced visits one at a time

Both optimizers share the same graph representation, feasibility check, and fitness function. They differ only in search strategy.

---

## Formal Problem Statement

### Given

```
# Places
P = {p_0, p_1, ..., p_n}
  p_0                       = home (start and end of every route)
  p_1..p_n                  = visit instances
                              patient a needing k visits → instances p_a1..p_ak
                              all at the same (lat, lon), independent time positions

# Distance matrix
T[i,j] = travel_time(p_i, p_j)    # symmetric, in minutes
                                    # T[ai, aj] = 0 for same-patient instances

# Days
D = {1, ..., d}                    # working days in the week

# Fixed skeleton (pre-assigned before optimization)
F_k ⊂ P  ∀ k ∈ D                  # confirmed visits and calendar blocks on day k
                                    # each node in F_k has a locked (time, duration)

# Per-node parameters
duration(p_i)   = visit_duration_minutes + charting_buffer_minutes
window(p_i, k)  = [earliest_i_on_day_k, latest_i_on_day_k]   # availability window
priority(p_i)   = int              # lower = schedule earlier

# Per-patient spacing parameters
min_gap(a)      = minimum days between any two instances of patient a
max_gap(a)      = maximum days between any two instances of patient a

# Physician parameters
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
  spacing_penalty(a, i, j) = max(0, min_gap(a) - |day_i - day_j|)   # too close
                            + max(0, |day_i - day_j| - max_gap(a))   # too far

  window_penalty(p_i, k)   = max(0, window(p_i,k).earliest - arrival(p_i))
                            + max(0, arrival(p_i) - window(p_i,k).latest)

  density_penalty(R)        = density * Σ_k 1[|S_k| > 0]            # penalize spreading
                            + (1-density) * variance(|S_k| across k) # penalize unevenness
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

# 7. Max drive time per day
∀ k:  Σ_(i,j) ∈ R_k  T[i,j]  ≤  max_drive_k

# 8. Mandatory break after max continuous work
∀ k: any contiguous block of scheduled time exceeding max_continuous minutes
     must be followed by a break of at least break_duration minutes

# 9. Lunch must occur
∀ k ∈ working_days: ∃ lunch interval in R_k of length ≥ lunch_duration
  within [lunch_target - lunch_window, lunch_target + lunch_window]
```

---

## Graph Representation

A schedule is a **path through spacetime** — each visit is a point with both a physical location and a time position. The graph unifies spatial and temporal information into a single structure.

### Node

```
Node = {
  id:          string
  type:        visit | block | lunch | break | home | transit

  # Spatial
  location:    (lat, lon) | null     # null for non-spatial nodes

  # Temporal
  time_start:  fixed(int) | free(domain: [min, max])
  time_end:    fixed(int) | derived(time_start + duration)
  duration:    int                   # minutes

  # Constraint metadata
  hard:        bool                  # if true, optimizer cannot move this node
  soft_cost:   (actual_time) → float # penalty function for soft constraints
}
```

**Node types and freedom:**

| Type            | Space    | Time                          |
|-----------------|----------|-------------------------------|
| Home            | fixed    | fixed (workday start/end)     |
| Calendar block  | none     | fixed (hard)                  |
| Lunch           | none     | semi-fixed (soft window)      |
| Mandatory break | none     | derived (triggered by work accumulation) |
| Visit           | fixed    | free (optimizer controls)     |
| Transit         | derived  | derived (from adjacent nodes) |

Transit nodes have no independent existence — they are derived from the spatial edge between two adjacent visit/home nodes and computed on the fly during edge evaluation.

### Edge

An edge between two nodes means "this node is followed directly by that node."

```
Edge = {
  from:         Node
  to:           Node
  spatial_cost: travel_time(from.location, to.location)
  temporal_gap: to.time_start - from.time_end
  feasible:     temporal_gap >= spatial_cost
}
```

The feasibility condition `temporal_gap >= spatial_cost` is where the two dimensions are unified — you cannot arrive somewhere before you have had time to travel there.

### Multi-Instance Patient Encoding

If a patient requires N visits per week, they are represented as N independent nodes in the graph, all sharing the same physical location but with independent time positions:

```
patient_A_visit_1, patient_A_visit_2, patient_A_visit_3
  — all at location (lat, lon) of patient A
  — independent time_start domains
  — spacing constraint edges between instances
```

The one-visit-per-patient-per-day hard constraint prevents instances from being placed on the same day. Spacing penalty edges between instances discourage clustering.

### The Three Underlying Graphs

The unified graph is composed of three conceptual layers:

**G_s — Spatial graph (static)**
- Nodes: all physical locations (home + all patients)
- Edges: every pair, undirected, weight = travel time
- Built once per optimization run, never changes
- Used as a lookup table by G_t edge weights

**G_t — Temporal graph (dynamic)**
- Nodes: all events (visits, calendar blocks, lunch, breaks, transits)
- Edges: ordering and constraint relationships
- Fixed nodes (calendar blocks) carve out forbidden intervals
- Free nodes (visits) are what the optimizer manipulates

**G_w — Weekly spacing graph**
- Nodes: patients
- Edges: temporal relationships between visit instances of the same patient across days
- Weight: spacing penalty based on days between visits
- Encodes min/max interval constraints

**Total fitness of a schedule:**

```
F(S) = α * Σ spatial_cost(u,v) for all edges in S       # minimize drive time
     + β * Σ spacing_penalty for all visit pairs in S   # enforce regular intervals
     + γ * Σ soft_constraint_violations in S            # availability, lunch timing, etc.
     + ∞ * Σ hard_constraint_violations in S            # infeasible schedules discarded
```

---

## Visit Status and Locking

Visits have two relevant statuses for the optimizer:

| Status      | Behavior                                      |
|-------------|-----------------------------------------------|
| `confirmed` | Fixed node — neither optimizer will move it   |
| `pending`   | Free node — both optimizers can reschedule it |

Confirmed visits act as fixed skeleton nodes, equivalent to calendar blocks. The optimizer arranges pending visits around them.

---

## Feasibility Check

Applied after every crossover, mutation, or insertion:

```
is_feasible(path) =

  # Layer 1: fixed nodes are not displaced
  ∀ hard nodes n: n.time_start == n.fixed_time

  # Layer 2: spatial-temporal coupling — transit fits in the gap
  ∀ edges (u, v): v.time_start >= u.time_end + travel_time(u, v)

  # Layer 3: no overlap between non-transit nodes
  ∀ pairs (u, v): u.time_end <= v.time_start OR v.time_end <= u.time_start

  # Layer 4: free nodes within their allowed domain (soft — contributes to fitness)
  ∀ free nodes n: n.time_start ∈ n.domain
```

Layers 1–3 are hard — any violation makes the schedule infeasible and the candidate is discarded. Layer 4 violations are soft — they contribute to fitness penalty but do not discard the schedule.

---

## GA Optimizer — Algorithm

### Encoding

A chromosome encodes a full week's schedule as a list of (visit, day) assignments. Each visit node has a stable ID (patient_id + instance_index). Time positions within a day are not encoded in the chromosome — they are derived by a retiming pass after crossover/mutation.

### Population Initialization

1. Generate one individual using the greedy optimizer (good starting point)
2. Generate remaining individuals by randomly perturbing the greedy solution (swap days, shuffle visit order)

### Crossover — NEAT-Inspired Alignment

Visits are aligned by identity (not position) before crossover, solving the competing conventions problem:

```
Parent A:  [X:Mon, Y:Tue, Z:Mon, W:Thu]
Parent B:  [X:Wed, Y:Mon, Z:Fri, W:Tue]

Child:     [X:Mon, Y:Mon, Z:Fri, W:Thu]
            ^^^A        ^^^B       ^^^A
```

Each visit's day assignment is inherited independently from one parent. The child then undergoes a retiming pass to assign exact times.

### Mutation

Random mutations applied with decreasing probability as generations progress:

- Move a visit to a different day
- Swap two visits between days
- Shift a visit's time slot earlier or later within a day
- Reorder two adjacent visits within a day

### Speciation

Maintain a diverse population to avoid premature convergence:

- Group individuals by structural similarity (same day assignments = same species)
- Apply fitness sharing within species — penalize individuals too similar to others
- Protect novel structures for a minimum number of generations before they compete globally

### Cooling

Mutation rate starts high and decreases each generation, analogous to simulated annealing temperature. Early generations explore broadly; later generations refine.

### Termination

Run until a time budget is exhausted (e.g. 30 seconds for on-demand, longer for nightly run) or fitness improvement stalls for N generations.

---

## Greedy Optimizer — Algorithm

Used for on-demand insertion when a patient is added or a visit changes.

1. Lock all confirmed visits as fixed nodes
2. Treat all pending visits + new visits as free
3. For each unplaced visit (ordered by patient priority):
   - Generate candidate (day, time) slots across the week
   - Score each candidate: `spatial_insertion_cost + soft_constraint_penalties`
   - Spatial insertion cost = `d(prev, new) + d(new, next) - d(prev, next)`
   - Select the lowest-score feasible slot
   - Lock it in and proceed to the next visit
4. Run nearest-neighbor reordering on each day's visits
5. Run retiming pass to assign exact start times

The greedy optimizer is not globally optimal but is fast and produces a good solution as a starting point for the GA or for immediate display to the user.

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
| `max_drive_minutes_per_day` | Physician | Daily drive cap |
| `schedule_density` | Physician | Cluster visits into fewer days vs. spread evenly |
| `charting_buffer_minutes` | Physician | Post-visit documentation time |
| `required_visits_per_week` | Patient | Number of visit instances to schedule |
| `visit_duration_minutes` | Patient | Duration of each visit |
| `min_days_between_visits` | Patient | Minimum spacing between instances |
| `max_days_between_visits` | Patient | Maximum spacing between instances |
| `priority` | Patient | Scheduling order preference (high-acuity first) |
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
- Max drive time per day (if configured)
- Patient visit frequency met within the week
- Transit time between consecutive visits must fit within the time gap

### Soft Constraints
*Optimize toward — violations increase fitness penalty but schedule remains valid*

- **Patient spacing** — visits to the same patient separated by at least `min_days_between_visits`
- **Patient max spacing** — visits not further apart than `max_days_between_visits`
- **Patient time-of-day preference** — respect availability windows
- **Patient priority** — high-acuity patients scheduled earlier in day/week
- **Physician schedule density** — prefer configured density (sparse vs. loaded days)
- **Geographic day-clustering** — group nearby patients on the same day
- **Charting buffer** — N minutes after each visit for documentation
- **Lunch timing** — lunch falls near `lunch_start_minute`, within flexibility window
- **Visit start time regularity** — match prior week's visit times where possible
