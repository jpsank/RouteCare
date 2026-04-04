# RouteCare -> Walmart Transportation Platform: Full Architecture Roadmap

> **Created:** April 4, 2026
> **Last updated:** April 4, 2026 (v2 -- integrated deep research report insights)
> **Purpose:** Comprehensive phased plan for adapting RouteCare (physician visit routing) to support Walmart's transportation network, including OTR trucking, last-mile delivery, and InHome delivery scheduling.

---

## Table of Contents

1. [Current State Summary](#current-state-summary)
2. [Walmart Network Overview](#walmart-network-overview)
3. [Constraint Comparison: Physician vs. Walmart](#constraint-comparison)
4. [Phase 0: Foundation Hardening](#phase-0-foundation-hardening)
5. [Phase 1: Domain Model Generalization](#phase-1-domain-model-generalization)
6. [Phase 2: Modular VRP Solver & Feasibility Services](#phase-2-modular-vrp-solver--feasibility-services)
7. [Phase 3: Hours of Service & Regulatory Constraints](#phase-3-hours-of-service--regulatory-constraints)
8. [Phase 3B: Integrated Load Planning](#phase-3b-integrated-load-planning)
9. [Phase 4: Walmart-Specific Adaptations](#phase-4-walmart-specific-adaptations)
10. [Phase 5: Dynamic Dispatch & Real-Time Re-Optimization](#phase-5-dynamic-dispatch--real-time-re-optimization)
11. [Phase 6: Multi-Tenant, Scale & Infrastructure](#phase-6-multi-tenant-scale--infrastructure)
12. [Phase 7: Simulation & Resilience](#phase-7-simulation--resilience)
13. [Evaluation Metrics](#evaluation-metrics)
14. [Algorithmic Strategy](#algorithmic-strategy)
15. [Timeline Summary](#timeline-summary)
16. [Technical References](#technical-references)

---

## Current State Summary

RouteCare is a route-optimized field scheduling platform for home-visit clinicians. It automates weekly visit scheduling with geographic/travel awareness, patient communication lifecycle, and external calendar sync.

**Tech stack:** Rails 8.1.3, React 19 + TypeScript, PostgreSQL 16, Mapbox GL JS, Solid Queue/Cache/Cable, Devise auth.

**Core optimization** (`app/services/scheduling/weekly_optimizer.rb`, 490 lines):
- Single-clinician, single-vehicle model
- Nearest-neighbor TSP heuristic for daily route ordering
- Cheapest-insertion penalty for slot scoring
- 15-minute slot granularity
- Time window constraints (patient availability, clinician workday, calendar blocks)
- Flexible lunch break placement
- Haversine distance estimate (no real routing API)
- Pseudo-random geocoding (no real geocoding API)

**Key limitations:**
- No real routing or geocoding APIs (haversine + CRC32 stubs)
- No return-to-home cost in drive metrics
- Synthetic baseline (`total * 1.35`) for savings reporting
- No multi-vehicle/multi-driver support
- No capacity constraints (weight, volume, temperature)
- No vehicle, facility, or load unit models
- No HOS/regulatory compliance
- No loading/packing constraints
- No facility calendar / dock scheduling
- No event logging or audit trail for routing decisions
- No test coverage for the optimizer

---

## Walmart Network Overview

### Network Scale & Topology

Walmart's network is a **multi-echelon, hybrid fulfillment system** where the same physical nodes play multiple roles. Per SEC 10-K (FY ended Jan 31, 2024): **4,615 retail units** and **162 distribution facilities**. The INFORMS Edelman-winning work (2024) describes **117 DCs, 26 FCs, 3 sortation centers, 96 transportation offices, ~600 Sam's Clubs, and 4,700+ stores** covering **90% of the U.S. population within a 10-mile radius**.

**Critically, stores are "dual-use" nodes:** they are replenishment sinks for traditional retail AND fulfillment origins for e-commerce and delivery. Walmart explicitly states it "leans on its 4,700 stores as fulfillment centers."

**Private Fleet:** ~12,000 drivers, ~10,000 tractors, ~80,000 trailers, 1.1B miles/year. Equipment includes OTR trucks, refrigerated trailers (reefers with up to 3 temperature compartments), and yard trucks.

### Multi-Echelon Flows

For scheduling, the relevant flows are:

```
Supplier -> DC/FC (inbound)     Appointment scheduling, dock calendars, live vs drop loads
DC -> Store (middle-mile)       Multi-stop milk runs within store receiving windows
Store -> Customer (last-mile)   Multiple modalities: InHome, Spark, associates, GoLocal
Returns / Reverse flows         Return facilities, customer returns from home
Backhauls                       Inventory pickup on return trips (trailers never empty)
```

### Walmart's Published System Architecture

Walmart's Load Planner (described in INFORMS Edelman issue) decomposes execution into **8 modules**: routing, HOS checks, feasible loading, stacking, dynamic routing, optimal loading, dynamic loading, fluid loading. Key design principles:

- **Human-in-loop execution** (transportation command center and DC associates review/edit)
- **Fast modular checks** (HOS and loading feasibility in **milliseconds** so they can be embedded in iterative search)
- **Simulation capability** to evaluate scenarios and respond to disruptions quickly
- **Data-driven pivots** using weather and traffic patterns
- **Framework uses mixed-integer programming, metaheuristics, and simulation** (Edelman summary)

### DC Types

| Type | Purpose |
|------|---------|
| Grocery DC | Temperature-controlled, perishable supply chain |
| General Merchandise DC | Non-perishable goods |
| Fashion DC | Apparel handling, sorting, cross-docking |
| Regional DC | Hub-and-spoke, ~150-mile radius to stores |
| Fulfillment Center (FC) | eCommerce fulfillment (ship-to-home, ship-to-store) |
| Consolidation Center | Aggregate supplier freight before distribution |
| Market Fulfillment Center (MFC) | Micro-fulfillment inside/adjacent to Supercenters (Alphabot/Symbotic) |
| Import DC | Overseas port goods handling |
| Sortation Center | Package sorting for last-mile |

### Transportation Channels

| Channel | Model | Scheduling Type | Closest RouteCare Analog |
|---------|-------|----------------|--------------------------|
| Private Fleet (OTR) | W-2 drivers, Class 8 trucks | Daily planning with HOS | WeeklySchedule + clinician workday |
| Spark | Gig contractors, personal vehicles | Real-time dynamic dispatch | None (online assignment problem) |
| GoLocal | White-label DaaS via Spark network | On-demand via API | None (API integration point) |
| InHome | W-2 associates, delivery vans | Recurring scheduled routes from stores | **Closest match** to RouteCare model |
| Drone (DroneUp) | Autonomous drones from stores | On-demand, <30 min radius | None |

### TMS & Technology
- Walmart's TMS is **primarily proprietary**, built by Walmart Global Tech
- No confirmed use of Blue Yonder, Manhattan Associates, or Oracle as primary TMS
- Walmart commercialized a "Route Optimization" product targeting middle-mile routing and trailer packing
- Public APIs available: GoLocal API, Inbound Load Board API (at `developer.walmart.com`)
- Fleet uses onboard telematics, ELD compliance, real-time GPS tracking
- Autonomous trucking partnerships: Gatik (middle-mile box trucks)

---

## Constraint Comparison

| Dimension | RouteCare (Current) | Walmart OTR Trucking | Walmart InHome | Walmart Spark |
|-----------|--------------------|--------------------|----------------|---------------|
| Origin points | 1 clinician home | Hundreds of DCs | Thousands of stores | Thousands of stores |
| Destinations | ~5-30 patients | Thousands of stores | Customer homes (recurring) | Customer homes (dynamic) |
| Vehicle types | 1 personal car | Class 8 trucks + multi-temp trailers | Delivery vans / EVs | Personal vehicles (gig) |
| Scheduling horizon | 1 week, batch | Daily planning, rolling | Recurring weekly | Real-time, per-order |
| Capacity constraints | Time only | Weight, cube, pallets, temp zones, compartments | Weight, volume, temp | Weight (light) |
| Loading constraints | None | 3D packing, FILO unload sequencing, reload minimization | Tote/bag level | Bag level |
| Regulatory | None | DOT HOS (11h/14h/30min/10h/60-70h) | Standard employment law | Gig worker regs |
| Return trips | Not modeled | Backhaul optimization critical | Return to store | Return to store |
| Time windows | Patient availability (flat) | **Hierarchical**: PO/MABD + dock appointment + store receiving | Customer delivery slots (2 daily windows) | 1-hour / 3-hour SLAs |
| Facility capacity | Not modeled | Dock doors as scarce resource (appointment calendars) | Store pick-wave capacity | Store staging capacity |
| Store readiness | N/A | DC load readiness | Order pick/stage dependency | Order pick/stage dependency |
| Communication | SMS/email confirmation | EDI, ELD telemetry | App notifications, smart-lock | App notifications |
| Replanning | Manual reschedule | Dynamic rerouting module, disruption simulation | ETA updates | Continuous re-dispatch |

### What Translates Directly

| RouteCare Concept | Walmart Equivalent |
|---|---|
| `PatientAvailabilityWindow` | Store receiving windows / Customer delivery time preferences |
| `CalendarBlock` (blocked times) | Driver HOS mandatory rest / Store dock closures / Facility maintenance windows |
| `WeeklySchedule` lifecycle (draft -> optimized -> approved) | Load plan lifecycle (planned -> tendered -> dispatched -> in-transit -> delivered) |
| `TravelTimeMatrixBuilder` | Distance/time matrices (needs real API at scale) |
| Insertion route penalty scoring | Same concept used in ALNS insertion operators |
| `Rescheduler` (single-stop adjustment) | Dynamic rerouting for cancelled/delayed deliveries |
| Patient messaging/confirmation pipeline | Customer delivery notification pipeline (ETA, smart-lock, proof of delivery) |
| Alerts system (conflicts, unconfirmed) | Exception management (late shipments, missed windows, HOS violations, reload alerts) |

### Major Gaps to Fill

Based on Walmart's published optimization system and deep research analysis:

1. **Integrated load planning** -- route feasibility conditional on feasible truck-load design
2. **Hierarchical time windows** -- nested PO/dock/receiving/customer windows
3. **Facility capacity as scarce resource** -- dock slots consumed by deliveries
4. **Modular feasibility services** -- millisecond HOS and loading checks embedded in search
5. **Multi-echelon assignment** -- which facility serves which demand (2E-VRP)
6. **Dynamic dispatch loop** -- commit/replan windows for last-mile
7. **Store readiness dependency** -- routes can't depart until orders are picked/staged
8. **Event logging** -- immutable event store for routing decisions
9. **Simulation capability** -- what-if scenarios for disruption response
10. **Evaluation metrics** -- three-tier metrics framework

---

## Phase 0: Foundation Hardening

**Goal:** Stabilize the physician visit product and prepare the codebase for generalization. This phase improves the existing product immediately.

**Estimated effort:** 1-2 weeks

### 0.5 -- Test Coverage for the Optimizer (DO FIRST)

**Problem:** Zero test coverage for the core optimization engine.

**Plan:** Create integration tests covering:

1. Basic optimization -- N patients, 1 week, verify visits created with valid start/end times
2. Time window respect -- patients with `PatientAvailabilityWindow` get visits within those windows
3. Calendar block avoidance -- visits don't overlap `CalendarBlock` records
4. Even spacing -- patient needing 3 visits/week gets Mon/Wed/Fri distribution
5. MAX_VISITS_PER_DAY -- verify cap of 5
6. Nearest-neighbor ordering -- geographically ordered within a day
7. Lunch break insertion -- placed within configured window
8. Soft constraint tracking -- `soft_constraint_override` set when no window matches
9. Edge cases -- no patients, single patient, all slots blocked, 5+ visits/week

**Files to create:**
- `test/services/scheduling/weekly_optimizer_test.rb`
- `test/services/scheduling/travel_time_matrix_builder_test.rb`
- `test/services/integrations/routing_client_test.rb`

### 0.1 -- Real Routing API (Replace Haversine Estimate)

**Problem:** `RoutingClient` uses `haversine_km / 38.0 * 60.0 + 4`. Ignores roads, traffic, one-way streets.

**Plan:** Upgrade to Mapbox Matrix API.

- Call Mapbox when `MAPBOX_ACCESS_TOKEN` configured; fall back to haversine for dev
- Batch support for >25 locations (Mapbox limit per request)
- Cache via Solid Cache with 24-hour TTL
- Use `httparty` (already in Gemfile)

**Files to modify:**
- `app/services/integrations/routing_client.rb`
- `app/services/integrations/base_client.rb`
- `app/services/scheduling/travel_time_matrix_builder.rb`

### 0.2 -- Real Geocoding (Replace Pseudo-Coordinates)

**Problem:** `GeocodingClient` returns CRC32-seeded fake coordinates.

**Plan:** Mapbox Geocoding API. Geocode-on-save for Patient and ClinicianProfile.

**Files to modify:**
- `app/services/integrations/geocoding_client.rb`
- `app/models/patient.rb`
- `app/models/clinician_profile.rb`

### 0.3 -- Add Return-to-Home Drive Cost

**Problem:** `calculate_drive_metrics!` ignores drive from last stop back home.

**Plan:** Calculate return drive, store in `optimization_summary` JSONB, include in totals, display on frontend.

**Files to modify:**
- `app/services/scheduling/weekly_optimizer.rb`
- `app/frontend/components/calendar/RoutePanel.tsx`

### 0.4 -- Replace Synthetic Baseline

**Problem:** `baseline_drive_minutes = total * 1.35` is meaningless.

**Plan:** Compute actual unoptimized baseline using pre-optimization visit order (sorted by patient name or time window start).

**Files to modify:**
- `app/services/scheduling/weekly_optimizer.rb`

### Phase 0 Execution Order

| Step | Depends On | Risk |
|------|-----------|------|
| **0.5** Tests | Nothing | Low |
| **0.1** Routing API | 0.5 | Medium |
| **0.2** Geocoding | 0.1 | Low |
| **0.3** Return-to-home | 0.1 | Low |
| **0.4** Baseline | 0.1 + 0.3 | Low |

---

## Phase 1: Domain Model Generalization

**Goal:** Abstract from physician-patient to a generic routing domain. Introduce hierarchical time windows, facility calendars, load units, and event logging as first-class concepts.

**Estimated effort:** 3-4 weeks

### Core Entity Models

```
Organization
  has_many :facilities
  has_many :fleets
  has_many :service_territories

Facility                          # Generalization of "clinician home"
  - type: depot | store | dc | fulfillment_center | sortation_center | home_base
  - latitude, longitude, address fields
  - dock_doors: integer           # Max concurrent load/unload operations
  has_many :dock_slots            # Scarce resource calendar
  has_many :operating_windows     # When facility can receive/dispatch

DockSlot                          # NEW: facility capacity as scarce resource
  - facility_id: references
  - starts_at: datetime
  - ends_at: datetime
  - capacity: integer             # Max concurrent vehicles in this slot
  - booked_count: integer         # Currently booked (< capacity = available)
  - slot_type: inbound | outbound | both

Fleet
  has_many :vehicles
  has_many :drivers

Vehicle
  - type: class_8_truck | sprinter_van | sedan | personal_car | ev_van
  - home_depot: references Facility
  belongs_to :fleet
  has_one :trailer                # For tractors; nullable for vans/cars

Trailer                           # NEW: separate from vehicle (tractor-trailer decoupling)
  - type: dry_van | reefer | flatbed
  - weight_capacity_lbs: decimal
  - volume_capacity_cuft: decimal
  - pallet_capacity: integer
  - compartment_count: integer    # 1-3 for reefers
  has_many :compartments

Compartment                       # NEW: multi-temperature compartments
  - trailer_id: references
  - position: integer             # 1=front, 2=middle, 3=rear
  - temperature_zone: frozen | refrigerated | ambient
  - volume_cuft: decimal          # Variable based on bulkhead placement
  - pallet_positions: integer

Driver                            # Generalization of "ClinicianProfile"
  - hos_status: off_duty | sleeper | driving | on_duty_not_driving
  - duty_cycle_minutes_remaining: integer
  - domicile_facility: references Facility
  - certifications: [] (hazmat, tanker, doubles, cdl_class_a, cdl_class_b, inhome_trained)
  - workday_start_minute, workday_end_minute
  - working_days_mask
  - lunch config fields
  belongs_to :fleet
  belongs_to :user

Stop                              # Generalization of "Patient"
  - type: delivery | pickup | service_visit | pickup_and_delivery
  - latitude, longitude, address fields
  - service_duration_minutes: integer
  has_many :time_windows          # Hierarchical (see below)
  has_many :load_units            # What needs to move to/from this stop

TimeWindow                        # NEW: hierarchical, typed time windows
  - windowable: polymorphic       # Can belong to Stop, Facility, Driver, etc.
  - layer: po_mabd | dock_appointment | facility_receiving | customer_slot | driver_shift
  - day_of_week: integer          # For recurring windows (nullable for one-time)
  - date: date                    # For specific-date windows (nullable for recurring)
  - start_minute: integer
  - end_minute: integer
  - hard: boolean                 # Hard constraint vs soft (penalized) constraint
  - priority: integer             # For conflict resolution between layers

LoadUnit                          # NEW: what physically moves
  - type: pallet | tote | parcel | bag
  - stop_id: references           # Destination (or origin for pickups)
  - weight_lbs: decimal
  - length_in, width_in, height_in: decimal  # For 3D packing
  - temperature_requirement: frozen | refrigerated | ambient
  - stackable: boolean
  - fragile: boolean
  - unload_sequence_position: integer  # FILO ordering within compartment

Task / Order                      # Generalization of "Visit"
  - type: delivery | pickup | service_call | milk_run_stop
  - status: pending | confirmed | in_transit | completed | failed
  - vehicle_id, driver_id
  - order_ready_at: datetime      # NEW: store readiness timestamp
  - readiness_status: pending_pick | picking | staged | ready | dispatched
  belongs_to :route
  has_many :load_units

RoutePlan                         # Generalization of "WeeklySchedule"
  - planning_horizon: daterange
  - status: draft | optimized | approved | dispatched | in_progress | completed
  - optimization_summary: jsonb
  has_many :routes

Route                             # Single vehicle's daily itinerary
  - vehicle_id, driver_id
  - date: date
  - departure_facility, return_facility: references Facility
  - total_drive_minutes, total_service_minutes, total_wait_minutes
  - return_drive_minutes: integer
  - has_feasible_load: boolean    # NEW: loading feasibility flag
  has_many :tasks (ordered by position)
  has_one :load_plan              # NEW: associated loading solution

LoadPlan                          # NEW: how cargo is arranged on the vehicle
  - route_id: references
  - status: draft | validated | locked
  - reload_count: integer         # Predicted reload events
  - utilization_weight_pct: decimal
  - utilization_volume_pct: decimal
  - layout: jsonb                 # Compartment -> [LoadUnit assignments with positions]

Event                             # NEW: immutable event log
  - event_type: string            # route_planned, feasibility_checked, dispatched, etc.
  - entity_type: string           # polymorphic
  - entity_id: integer
  - payload: jsonb                # Full event data
  - occurred_at: datetime
  - actor_type: system | user | driver
  - actor_id: integer
```

### Hierarchical Time Window Semantics

A stop may have multiple time windows from different layers. Feasibility requires satisfying the **intersection** of all applicable layers:

```
Example: Store #4521 milk run delivery
  Layer 1 (facility_receiving): Mon-Fri 6:00 AM - 2:00 PM
  Layer 2 (dock_appointment):   Wed 8:00 AM - 9:00 AM (specific slot booked)
  Layer 3 (po_mabd):            Must arrive by Wed 11:59 PM

  Feasible window = Wed 8:00 AM - 9:00 AM (intersection of all three)
```

### Migration Strategy

Use parallel tables with adapter/wrapper patterns to maintain backward compatibility:
- `ClinicianProfile` wraps/delegates to `Driver`
- `Patient` wraps/delegates to `Stop`
- `PatientAvailabilityWindow` wraps/delegates to `TimeWindow(layer: :customer_slot)`
- `Visit` wraps/delegates to `Task`
- `WeeklySchedule` wraps/delegates to `RoutePlan`

Physician-specific controllers and serializers continue to work unchanged.

---

## Phase 2: Modular VRP Solver & Feasibility Services

**Goal:** Replace the nearest-neighbor heuristic with a real VRP solver, architected as a **modular system with pluggable feasibility services** rather than a monolithic solver. This mirrors Walmart's Load Planner decomposition.

**Estimated effort:** 3-4 weeks

### Architecture: Solver + Feasibility Services

Walmart's system decomposes into 8 fast modules. We adopt the same pattern:

```
                    +---------------------------+
                    |   Planning Orchestrator    |
                    |   (scenario, wave, batch)  |
                    +-------------+-------------+
                                  |
                                  v
                    +---------------------------+
                    |     VRP Solver Core        |
                    |  (OR-Tools / ALNS engine)  |
                    +--+--------+--------+------+
                       |        |        |
          +------------+   +----+----+   +------------+
          v                v         v                v
  +---------------+ +----------+ +-----------+ +----------+
  | TimeWindow    | | HOS      | | Loading   | | Capacity |
  | Feasibility   | | Feasib.  | | Feasib.   | | Check    |
  | Service       | | Service  | | Service   | | Service  |
  +---------------+ +----------+ +-----------+ +----------+
  (intersection    (millisecond  (pallet fit,  (weight,
   of all layers)   DOT check)   FILO check)   volume)
```

Each feasibility service:
- Accepts a candidate route (stop sequence + timing)
- Returns `{feasible: bool, violations: [], suggested_repairs: []}`
- Runs in **milliseconds** (critical for embedding in iterative search)
- Can be called standalone (API) or embedded in solver loop

### Technology Choice: `or-tools` Ruby Gem (Phase 2), ALNS (Phase 6+)

**Phase 2:** Use the [`or-tools` Ruby gem](https://github.com/ankane/or-tools-ruby) (v0.17.1, 240K+ downloads). Runs in-process via Solid Queue background jobs. Covers VRPTW, CVRP, pickup-delivery, multi-depot, break scheduling.

**Phase 6+ (scale path):** Migrate to custom **Adaptive Large Neighborhood Search (ALNS)** implementation. ALNS (Ropke & Pisinger) uses multiple removal/insertion operators weighted by historical performance. The key advantage over OR-Tools' built-in metaheuristics: you can embed custom feasibility checks (loading validation, complex HOS) directly into the insertion step. This is what Walmart's system does.

**Add to Gemfile:**
```ruby
gem "or-tools"
```

### Supported VRP Variants (All Available via the Gem)

| Variant | OR-Tools Method | Walmart Use Case |
|---------|----------------|-----------------|
| TSP | `RoutingModel` with 1 vehicle | Current physician model |
| CVRP (Capacitated) | `AddDimensionWithVehicleCapacity` | Truck weight/volume limits |
| VRPTW (Time Windows) | `AddDimension` + `CumulVar.SetRange` | Store receiving hours, patient availability |
| Pickup & Delivery | `AddPickupAndDelivery` | Backhaul optimization |
| Multi-depot | Per-vehicle `starts[]` and `ends[]` | Multiple DCs as origins |
| Heterogeneous fleet | Different capacity per vehicle | Mixed fleet (trucks, vans, sedans) |
| Break scheduling | `FixedDurationIntervalVar` | HOS breaks, lunch |
| Penalties / dropped visits | `AddDisjunction` with penalty | Low-priority stops can be skipped |
| Resource constraints | `Cumulative` constraint | Dock door scheduling |

### VrpSolver Service

```ruby
class Scheduling::VrpSolver
  def initialize(stops:, vehicles:, time_matrix:, constraints:, feasibility_services: [])
    @stops = stops
    @vehicles = vehicles
    @time_matrix = time_matrix
    @constraints = constraints
    @feasibility_services = feasibility_services  # Pluggable checkers
  end

  def solve(time_limit_seconds: 30)
    # Build OR-Tools model
    manager = ORTools::RoutingIndexManager.new(num_locations, num_vehicles, depot_indices)
    routing = ORTools::RoutingModel.new(manager)

    # Transit callback (time matrix)
    transit_cb = routing.register_transit_callback(time_lambda)
    routing.set_arc_cost_evaluator_of_all_vehicles(transit_cb)

    # Time dimension with hierarchical windows
    routing.add_dimension(transit_cb, max_wait, max_route_time, false, "Time")
    time_dimension = routing.mutable_dimension("Time")
    apply_time_windows!(time_dimension, manager)

    # Capacity dimensions (weight, volume, pallets)
    add_capacity_dimensions!(routing, manager)

    # Break intervals (lunch, HOS)
    add_break_constraints!(routing, manager)

    # Penalties for optional stops
    add_disjunctions!(routing, manager)

    # Solve
    search_params = ORTools.default_routing_search_parameters
    search_params.first_solution_strategy = :parallel_cheapest_insertion
    search_params.local_search_metaheuristic = :guided_local_search
    search_params.time_limit = time_limit_seconds

    solution = routing.solve_with_parameters(search_params)
    routes = extract_routes(solution, manager, routing)

    # Post-solve: run feasibility services on each route
    routes.each do |route|
      @feasibility_services.each do |service|
        result = service.check(route)
        route.feasibility_results << result
      end
    end

    routes
  end
end
```

### Feasibility Service Interface

```ruby
class Scheduling::FeasibilityServices::Base
  # Must return in < 10ms for embedding in search loops
  def check(route)
    # Returns: { feasible: bool, violations: [], repairs: [] }
    raise NotImplementedError
  end
end

class Scheduling::FeasibilityServices::TimeWindowChecker < Base
  # Validates all hierarchical time window layers are satisfied
end

class Scheduling::FeasibilityServices::HosChecker < Base
  # Validates DOT HOS compliance (see Phase 3)
end

class Scheduling::FeasibilityServices::LoadingChecker < Base
  # Validates pallet/tote fit in vehicle compartments (see Phase 3B)
end

class Scheduling::FeasibilityServices::CapacityChecker < Base
  # Validates weight, volume, pallet count within vehicle limits
end
```

### Integration Pattern

`WeeklyOptimizer` becomes a thin wrapper:
1. Translates physician domain into VRP inputs
2. Instantiates `VrpSolver` with appropriate feasibility services (just TimeWindowChecker for physicians)
3. Translates VRP outputs back into Visit records

### Execution Architecture

```ruby
class OptimizeRoutesJob < ApplicationJob
  queue_as :optimization

  def perform(route_plan_id)
    plan = RoutePlan.find(route_plan_id)
    solver = Scheduling::VrpSolver.new(
      stops: plan.buildable_stops,
      vehicles: plan.available_vehicles,
      time_matrix: plan.build_time_matrix,
      constraints: plan.constraint_set,
      feasibility_services: plan.applicable_feasibility_services
    )
    result = solver.solve(time_limit_seconds: 30)
    plan.apply_solution!(result)

    # Log optimization event
    Event.create!(
      event_type: "route_plan_optimized",
      entity: plan,
      payload: { solve_time_ms: result.solve_time, routes: result.summary },
      actor_type: :system
    )
  end
end
```

---

## Phase 3: Hours of Service & Regulatory Constraints

**Goal:** Build an HOS feasibility service that validates DOT compliance in **milliseconds**, following Walmart's pattern of fast modular checks embedded in iterative search.

**Estimated effort:** 1-2 weeks

### HOS Rules (49 CFR 395, Property-Carrying)

| Rule | Constraint | OR-Tools Modeling | Feasibility Service Check |
|------|-----------|-------------------|--------------------------|
| 11-Hour Driving Limit | Max 11 hrs driving after 10 consecutive hrs off | Cumulative driving dimension, cap 660 min | Sum driving segments, compare to 660 |
| 14-Hour Window | Cannot drive beyond 14th hr after coming on duty | Max route duration 840 min | Route span from first on-duty to last activity |
| 30-Minute Break | Required after 8 cumulative hrs driving | `FixedDurationIntervalVar` | Scan driving segments, verify 30-min break before 8hr mark |
| 10-Hour Off-Duty | 10 consecutive hrs off between duty periods | Inter-route constraint | Check gap between routes |
| 60/70-Hour Limit | 60 hrs in 7 days (or 70 in 8) | Carry-forward from DriverDutyLog | Sum duty hours over rolling window |
| 34-Hour Restart | 34 consecutive hrs off resets 60/70 clock | Planning-level | Check for qualifying rest period |

### HOS Feasibility Service

Designed to run in **< 5 milliseconds** per route evaluation:

```ruby
class Scheduling::FeasibilityServices::HosChecker < Base
  def check(route)
    violations = []
    driver = route.driver
    duty_state = driver.current_duty_state  # From DriverDutyLog

    # Check 14-hour window
    route_span = route.last_activity_end - route.first_activity_start
    if route_span > 14.hours
      violations << { rule: :fourteen_hour_window, excess_minutes: (route_span - 14.hours) / 60 }
    end

    # Check 11-hour driving
    total_driving = route.segments.select(&:driving?).sum(&:duration)
    if total_driving > 11.hours
      violations << { rule: :eleven_hour_driving, excess_minutes: (total_driving - 11.hours) / 60 }
    end

    # Check 30-minute break after 8 hours driving
    cumulative_driving = 0
    break_found = false
    route.segments.each do |seg|
      if seg.driving?
        cumulative_driving += seg.duration
        if cumulative_driving > 8.hours && !break_found
          violations << { rule: :thirty_minute_break, at_segment: seg.position }
        end
      elsif seg.break? && seg.duration >= 30.minutes
        break_found = true
      end
    end

    # Check 60/70-hour rolling window
    rolling_hours = duty_state.cumulative_on_duty_minutes + route.total_on_duty_minutes
    limit = driver.eight_day_cycle? ? 70 * 60 : 60 * 60
    if rolling_hours > limit
      violations << { rule: :weekly_limit, excess_minutes: rolling_hours - limit }
    end

    # Suggest break insertion points for repairs
    repairs = violations.any? ? suggest_break_placements(route) : []

    { feasible: violations.empty?, violations: violations, repairs: repairs }
  end
end
```

### New Model: DriverDutyLog

```
driver_duty_logs
  - driver_id: references
  - status: off_duty | sleeper | driving | on_duty_not_driving
  - started_at: datetime
  - ended_at: datetime
  - cumulative_driving_minutes: integer   # Running total within current duty period
  - cumulative_on_duty_minutes: integer   # Running total within rolling 7/8 day window
```

### Where HOS Breaks Can Be Taken

Routing feasibility depends on **where** breaks can occur (rest areas, safe parking). Model known rest locations as `Facility(type: :rest_area)` with geo-coordinates so the solver can insert breaks at feasible locations along the route. Walmart's description that HOS checking runs "within milliseconds" suggests they use very fast feasibility tests as part of iterative search.

---

## Phase 3B: Integrated Load Planning

**Goal:** Make route feasibility conditional on a feasible truck-load design. This is a critical Walmart constraint -- "each route is guaranteed to have a feasible truck-load design that complies with all truck-loading constraints."

**Estimated effort:** 2-3 weeks

### Why This Matters

Middle-mile route quality is meaningless if the cargo can't physically fit or can't be unloaded efficiently. Key problems identified in Walmart's INFORMS Edelman publication:

1. **Multi-temperature compartments:** Reefer trailers have bulkheads dividing 1-3 compartments at different temperatures. Adjacent compartments have temperature-difference limits.
2. **FILO unload sequencing:** Pallets for the last stop on the route should be loaded first (at the back/door end). If sequencing is wrong, store associates must "reload" -- unload other stores' pallets to access theirs, then reload them. Walmart explicitly links "minimizing reloads" to user experience.
3. **3D packing:** Pallets have dimensions and stackability constraints. Floor space is finite.
4. **Dynamic correction:** When a pallet isn't ready or is placed wrong, the system must re-validate loading feasibility without re-routing.

From an OR literature standpoint, this is consistent with "routing problems with loading constraints," which are NP-hard even before combining with routing. For 3D loading specifically, metaheuristics have been developed for "three-dimensional loading capacitated VRP" where routing and 3D packing must be jointly optimized.

### Loading Feasibility Service

```ruby
class Scheduling::FeasibilityServices::LoadingChecker < Base
  def check(route)
    violations = []
    vehicle = route.vehicle
    trailer = vehicle.trailer
    return { feasible: true, violations: [] } unless trailer

    # Collect all load units for stops in route order
    stop_loads = route.tasks.ordered.map do |task|
      { stop_position: task.position, load_units: task.load_units }
    end

    # Check total weight/volume
    total_weight = stop_loads.flat_map { |sl| sl[:load_units] }.sum(&:weight_lbs)
    if total_weight > trailer.weight_capacity_lbs
      violations << { rule: :weight_exceeded, total: total_weight, capacity: trailer.weight_capacity_lbs }
    end

    # Check per-compartment temperature compatibility
    trailer.compartments.each do |compartment|
      assigned_units = load_units_for_compartment(stop_loads, compartment)
      temp_conflicts = assigned_units.select { |u| u.temperature_requirement != compartment.temperature_zone }
      violations.concat(temp_conflicts.map { |u| { rule: :temp_mismatch, unit: u.id, compartment: compartment.position } })
    end

    # Check FILO unload sequencing
    reload_count = compute_reload_count(stop_loads, trailer)
    if reload_count > 0
      violations << { rule: :reloads_required, count: reload_count, severity: :warning }
    end

    # Check stackability
    stacking_violations = check_stacking_constraints(stop_loads, trailer)
    violations.concat(stacking_violations)

    {
      feasible: violations.none? { |v| v[:severity] != :warning },
      violations: violations,
      reload_count: reload_count,
      utilization: {
        weight_pct: (total_weight / trailer.weight_capacity_lbs * 100).round(1),
        volume_pct: compute_volume_utilization(stop_loads, trailer)
      }
    }
  end
end
```

### Integration with Route Search

The pattern follows Walmart's architecture:

```
Outer loop: ALNS / OR-Tools route search
  For each candidate route modification:
    1. Check time window feasibility     (< 1ms)
    2. Check capacity feasibility        (< 1ms)
    3. Check HOS feasibility             (< 5ms)
    4. Check loading feasibility         (< 10ms)  <-- NEW
    If all pass: accept modification
    If loading fails: try resequencing stops to fix FILO
    If still fails: reject modification and try another
```

### Implementation Phases

Start simple, evolve:
1. **v1:** Weight + volume check only (scalar capacity, already in Phase 2)
2. **v2:** Add temperature compartment assignment
3. **v3:** Add FILO unload sequencing check and reload count
4. **v4:** Add 2D floor-plan packing (pallet positions)
5. **v5:** Full 3D packing with stackability

---

## Phase 4: Walmart-Specific Adaptations

### Phase 4A: InHome Delivery Module (Best Entry Point)

**Goal:** Adapt RouteCare for Walmart InHome recurring delivery scheduling.

**Estimated effort:** 2-3 weeks

**How InHome maps to RouteCare:**

| InHome Concept | RouteCare Equivalent | Changes Needed |
|---------------|---------------------|----------------|
| Store (origin) | Clinician home | Facility model |
| Recurring customer | Patient | Add cargo demand fields |
| Delivery window (9am-1pm or 2pm-6pm) | PatientAvailabilityWindow | Already exists via TimeWindow |
| Delivery van / EV van | Personal car | Vehicle model with capacity |
| W-2 associate driver | Clinician | Driver model (Phase 1) |
| Grocery order | Visit | Task with LoadUnits |
| Smart-lock access | N/A | New integration |
| Customer ETA notifications | Patient SMS confirmation | Adapt messaging pipeline |
| **Order pick/stage readiness** | **N/A** | **NEW: store readiness constraint** |

**Store readiness constraint (from research report):**

Last-mile routes have an upstream dependency: a delivery cannot depart until orders are picked and staged at the store. This depends on:
- Store labor capacity and congestion
- Pick wave scheduling
- Handoff operations (service time + queueing)

Walmart Commerce Technologies' Store Assist has enabled fulfillment of 830M+ orders across 4,700 stores, optimizing picking accuracy/speed and providing seamless handoff between employees and drivers.

**Implementation:**
- Add `order_ready_at` and `readiness_status` to `Task` model
- Route departure time = `MAX(planned_departure, MAX(task.order_ready_at for all tasks on route))`
- Integration point: Store Assist API (or manual "mark ready" UI) updates readiness status
- If store is late, trigger replan event (Phase 5)

**InHome-specific time windows:**
- Two daily slots: 9am-1pm, 2pm-6pm
- Same-day afternoon slot available with morning ordering cutoff
- Model as `TimeWindow(layer: :customer_slot)` with specific date + time ranges

### Phase 4B: GoLocal API Integration

**Goal:** Enable RouteCare as an orchestration layer routing deliveries through Walmart's last-mile network.

**Estimated effort:** 1-2 weeks

**Walmart GoLocal API** (`developer.walmart.com/transportation-carriers`):
- REST API, OAuth2 (client credentials)
- Base URL: `https://developer.api.us.walmart.com/api-proxy/service/supplychain/transportation/v1/daas/v1/`

| Endpoint | Purpose |
|----------|---------|
| `POST /deliveries/location-eligibility` | Check if GoLocal can serve the stop |
| `POST /quote` | Get delivery cost/time estimate |
| `POST /deliveries` | Book a delivery |
| `PUT /deliveries/{id}/update` | Update delivery info |
| `POST /deliveries/{id}/cancel` | Cancel delivery |
| Delivery status webhooks | Real-time status updates |
| Driver location webhooks | Real-time tracking |

**Orchestration decision logic:** For each stop, compare own-fleet cost vs. GoLocal quote, route via cheapest option.

**New service:** `Integrations::GoLocalClient` following the existing `BaseClient` pattern.

### Phase 4C: Middle-Mile DC-to-Store Truck Routing

**Goal:** Full multi-depot, capacitated VRP with HOS + loading constraints for DC-to-store.

**Estimated effort:** 4-5 weeks (includes 2E-VRP assignment layer)

**This phase now includes a two-echelon assignment layer** (from research report):

**Step 1: Facility Assignment (2E-VRP upper echelon)**

Given demand, decide which DC/FC serves which store:
- Input: store demand by product category/temperature, DC inventory levels, DC-to-store distances
- Solve: assignment MIP or heuristic that minimizes total transportation cost while respecting DC capacity
- Output: per-DC stop lists

This maps to the academic Two-Echelon VRP (2E-VRP / 2E-CVRP) literature, which provides modeling classes and solution methods for DC->store->customer flow patterns.

**Step 2: Route within each DC's assigned stops**

For each DC, solve a VRPTW with:
- Multi-stop milk runs (3-6 stores)
- Heterogeneous fleet (dry van, reefer)
- HOS compliance (Phase 3 feasibility service)
- Loading feasibility (Phase 3B feasibility service)
- Dock slot consumption at stores (Phase 1 DockSlot model)
- Backhaul pickups (returns, supplier loads) modeled as pickup-and-delivery pairs
- Store receiving windows (hierarchical time windows)

**Decomposition approach (from research report -- mirrors Walmart's hierarchical decision framing):**
- Region/zone decomposition (by DC service region)
- Time decomposition (rolling horizon)
- Constraint decomposition (route generation + loading/HOS feasibility services)

Do NOT attempt a single monolithic solve.

**Solver configuration:**
```ruby
# Multi-depot: different start/end per vehicle
starts = vehicles.map { |v| manager.node_to_index(v.depot_index) }
ends = vehicles.map { |v| manager.node_to_index(v.depot_index) }
manager = ORTools::RoutingIndexManager.new(
  num_locations, num_vehicles, starts, ends
)

# Multiple capacity dimensions
weight_cb = routing.register_unary_transit_callback(weight_demand_lambda)
routing.add_dimension_with_vehicle_capacity(
  weight_cb, 0, vehicle_weight_caps, true, "Weight"
)

volume_cb = routing.register_unary_transit_callback(volume_demand_lambda)
routing.add_dimension_with_vehicle_capacity(
  volume_cb, 0, vehicle_volume_caps, true, "Volume"
)

# Pickup and delivery pairs (backhaul)
pickup_delivery_pairs.each do |pickup_idx, delivery_idx|
  routing.add_pickup_and_delivery(
    manager.node_to_index(pickup_idx),
    manager.node_to_index(delivery_idx)
  )
end
```

### Phase 4D: Inbound Load Board Integration

**Goal:** Integrate with Walmart's Inbound Load Board API for carrier-side freight bidding.

**Estimated effort:** 1-2 weeks

**Walmart Inbound Load Board API** (at `developer.walmart.com`):
- Retrieve available loads from Walmart
- Accept fixed-price loads
- Create spot bids for unassigned freight
- Search for load information

**Use case:** Automatically identify loads that fit into existing route capacity (backfill empty miles) and bid on them programmatically.

**New service:** `Integrations::LoadBoardClient`

---

## Phase 5: Dynamic Dispatch & Real-Time Re-Optimization

**Goal:** Move from batch optimization to a continuous **commit/replan dispatch loop** for last-mile. This implements the concrete architectural pattern identified in VRP literature and implied by Walmart's operational cadence.

**Estimated effort:** 4-5 weeks

### Commit/Replan Window Architecture

```
+-------------------------------------------------------------------+
| Time ->                                                           |
|                                                                   |
|  [=== COMMITTED ===][======= REPLAN WINDOW ========][  FORECAST ] |
|  (next 15-30 min)   (next 2-6 hours)                (beyond)     |
|  Locked. Dispatched  Continuously improved.          Indicative   |
|  to drivers.         Re-solved on triggers.          only.        |
+-------------------------------------------------------------------+
```

**Rules:**
- **Commit window** (15-30 min): Decisions are locked. Drivers are en route. No changes unless emergency (vehicle breakdown, safety).
- **Replan window** (2-6 hours): Continuously improved by rolling re-optimization. New orders inserted, cancelled orders removed, delayed orders rescheduled.
- **Forecast window** (beyond): Indicative plan for capacity planning. Not dispatched.

### Event-Driven Trigger Loop

```
Ingest events                     Update state
(new orders, cancels,      -->    (orders, inventory,
 driver status, store              capacities, time
 readiness, traffic/weather)       windows)
        |                              |
        v                              v
  Decide: replan now?           Continue execution
        |                       (monitor KPIs + ETAs)
        | Yes                          ^
        v                              |
  Build replan batch             Dispatch to
  (eligible orders +      -->   driver apps /
   available drivers)            ops console
        |                              ^
        v                              |
  Optimize/repair routes         Commit decisions
  (ALNS + fast feasibility) --> (assignments + ETAs)
        |
        v
  Feasibility gates
  (HOS, capacity, loading)
  If fail -> retry with repair
  If pass -> commit
```

### Trigger Events

| Event | Action |
|-------|--------|
| New order placed | Insert into replan batch |
| Order cancelled | Remove from route, trigger replan of remaining stops |
| Driver accepts/rejects offer | Reassign if rejected, confirm if accepted |
| Store marks order ready | Unblock route departure |
| Store delay (order not ready) | Delay route, potentially replan |
| Driver completes stop | Update ETA for remaining stops, push to customers |
| Driver goes offline | Reassign their remaining stops |
| Traffic incident | Update travel times, replan affected routes |
| Weather alert | Update travel times, potentially cancel/delay routes |

### Data Feeds Required

| Feed | Source | Frequency |
|------|--------|-----------|
| Order stream | eCommerce platform, POS | Real-time (event-driven) |
| Driver telemetry | GPS, ELD, driver app | Every 30-60 seconds |
| Store readiness | Store Assist, manual | Event-driven (order ready, delay) |
| Traffic/travel times | Mapbox Traffic API, TomTom | Every 5-15 minutes |
| Weather | NWS API, weather services | Every 15-60 minutes |
| Facility status | Dock management, WMS | Event-driven |

### Implementation Stack

- **Solid Cable** (ActionCable) for WebSocket push to frontend
- **Solid Queue** for background replan jobs
- **ActiveSupport::Notifications** as lightweight event bus
- **Redis** (or Solid Cache) for current-state caching (driver positions, order statuses)
- **PostgreSQL LISTEN/NOTIFY** for cross-process event propagation

### Marketplace Driver Dynamics (Spark-like)

For gig/contractor drivers (not W-2), the dispatch problem includes:
- **Offer-based assignment** -- driver can accept or reject
- **Acceptance probability modeling** -- incentive cost affects acceptance rate
- **Supply forecasting** -- drivers login/logout, creating stochastic capacity
- **Multi-offer batching** -- combining multiple deliveries into a single offer for better driver earnings

Walmart's Spark Driver platform operates across 17,000+ pickup points and can reach 84% of U.S. households, with multiple offer types (curbside, shopping+delivery, returns, GoLocal). Drivers choose delivery zones and can easily switch. This creates a scheduling environment where assignment must respect driver choice/acceptance (marketplace dynamics), supply is time-varying, and the optimization objective includes incentive cost and acceptance probability.

This is fundamentally an **online assignment problem under uncertainty**, not a traditional VRP. Dynamic VRP literature provides the methodological basis for algorithms under evolving information.

---

## Phase 6: Multi-Tenant, Scale & Infrastructure

**Goal:** Support multiple organizations, handle Walmart-scale problem sizes, and build the data infrastructure needed for analytics and simulation.

**Estimated effort:** 3-4 weeks

### Multi-Tenancy

| Concern | Approach |
|---------|----------|
| Data isolation | Organization-scoped with `acts_as_tenant` or manual scoping |
| Role-based access | Organizations, fleet managers, dispatchers, drivers as roles |
| API authentication | Per-organization API keys for external integrations |

### Scale-Out Solver: VROOM + OSRM

When OR-Tools in-process hits limits (consistently >2,000 stops or need sub-second latency):

```
Rails App --HTTP/JSON--> vroom-express (Docker)
                              |
                              v
                         OSRM Server (Docker)
                         (road network distances)
```

**VROOM** (v1.15.0, BSD-2-Clause) solves VRPTW in milliseconds. Internally uses LNS-style heuristics, which is why it's fast. Supports breaks, skills, multi-depot, heterogeneous fleets.

**OSRM** (self-hosted) eliminates dependency on Mapbox/Google distance matrix API rate limits. Precompute road network distances locally.

### Long-Term: Custom ALNS Engine

For maximum flexibility (especially with complex loading constraints), build a custom **Adaptive Large Neighborhood Search (ALNS)** engine:
- Multiple removal operators (random, worst, related, historical)
- Multiple insertion operators (greedy, regret-2, regret-3)
- Operator weights adapted by historical performance (Ropke-Pisinger)
- Custom feasibility checks embedded in insertion step
- LNS/ALNS methods are particularly compatible with Walmart-type constraints because you can embed fast feasibility checkers (HOS, loading feasibility) into insertion steps and quickly repair infeasibilities
- This is what Walmart's system effectively does

### Immutable Event Store

**Every routing decision, feasibility check, and dispatch action gets logged as an immutable event.** This is critical for:

- **Compliance audit** -- prove HOS was checked, loading was validated
- **Post-hoc analysis** -- why was this route chosen? What alternatives were considered?
- **Simulation replay** -- replay historical events with different parameters
- **ML training data** -- learn patterns from historical routing decisions

```
events table:
  - id: bigint (partitioned by month)
  - event_type: string (indexed)
  - entity_type + entity_id: polymorphic
  - payload: jsonb
  - occurred_at: timestamptz (indexed)
  - actor_type + actor_id
  - organization_id (indexed)
```

Consider PostgreSQL table partitioning by month for event volume at scale.

### Distance Matrix Caching

| Scale | Approach |
|-------|----------|
| <500 locations | Mapbox Matrix API + Solid Cache (24h TTL) |
| 500-5,000 locations | Self-hosted OSRM + precomputed nightly matrix |
| 5,000+ locations | OSRM on-demand (callback-based, no pre-computed matrix) |

### APIs to Design Early

Following patterns from Walmart's reference architecture:

- **Plan API**: create/update a planning scenario; request plan for time horizon
- **Feasibility API**: check HOS feasibility; validate loading feasibility for a candidate route (designed to be fast, cacheable)
- **Dispatch API**: assign tasks; publish offers to gig drivers; commit decisions with audit trail
- **Telemetry API**: ingest GPS pings; stop status; delivery proof and timestamping
- **Facility Calendar API**: dock slots, store receiving windows, and appointment confirmation tracking

---

## Phase 7: Simulation & Resilience

**Goal:** Enable what-if scenario analysis for disruption response and capacity planning. Walmart's published work describes a simulation platform for rapid network changes during disruptions (e.g., hurricane forcing reroutes within hours rather than days).

**Estimated effort:** 2-3 weeks

### Capabilities

| Scenario | Description |
|----------|-------------|
| Facility outage | Remove a DC or store from the network; re-solve to see impact |
| Road closure | Mark road segment unavailable; update travel times; re-route |
| Demand surge | Double demand from specific stores; see if fleet capacity suffices |
| Fleet reduction | Remove N vehicles; measure service degradation |
| Weather event | Apply travel time multiplier to affected region; re-plan |
| New facility | Add a DC/store; evaluate routing efficiency gain |

### Implementation

```ruby
class Simulation::ScenarioRunner
  def initialize(base_route_plan:, modifications:)
    @base = base_route_plan
    @modifications = modifications  # Array of scenario modifications
  end

  def run
    # Clone the base plan into a sandbox
    sandbox_plan = @base.deep_clone_for_simulation

    # Apply modifications
    @modifications.each { |mod| mod.apply!(sandbox_plan) }

    # Re-solve with same solver stack
    solver = Scheduling::VrpSolver.new(
      stops: sandbox_plan.buildable_stops,
      vehicles: sandbox_plan.available_vehicles,
      time_matrix: sandbox_plan.build_time_matrix,
      constraints: sandbox_plan.constraint_set,
      feasibility_services: sandbox_plan.applicable_feasibility_services
    )
    result = solver.solve(time_limit_seconds: 30)
    sandbox_plan.apply_solution!(result)

    # Compare metrics
    Simulation::MetricsComparator.new(
      baseline: @base,
      scenario: sandbox_plan
    ).compare
  end
end
```

### Scenario Modifications (Value Objects)

```ruby
Simulation::Modifications::RemoveFacility.new(facility_id: 123)
Simulation::Modifications::CloseRoad.new(from_coords: [...], to_coords: [...])
Simulation::Modifications::ScaleDemand.new(facility_id: 456, multiplier: 2.0)
Simulation::Modifications::ReduceFleet.new(vehicle_ids: [1, 2, 3])
Simulation::Modifications::AdjustTravelTimes.new(region: polygon, multiplier: 1.5)
```

---

## Evaluation Metrics

Three-tier metrics framework aligned to Walmart's published objectives.

### Tier 1: Plan Quality (Optimization Output)

| Metric | Description | Current RouteCare Status |
|--------|-------------|------------------------|
| Total miles / drive time | Sum of all route driving segments | `total_drive_minutes` exists but incomplete (no return) |
| Empty miles | Distance traveled without cargo | Not tracked |
| Trailer utilization (weight %) | Actual weight / capacity | Not modeled |
| Trailer utilization (volume %) | Actual volume / capacity | Not modeled |
| Compartment utilization | Per-temperature-zone fill rate | Not modeled |
| Predicted reload count | Stops requiring out-of-sequence unloading | Not modeled |
| Time window slack | Average minutes of slack in time windows | Not tracked |
| Constraint violation count | Soft constraint overrides | `soft_constraint_overrides` exists |
| Stops served / dropped | How many stops included vs. dropped with penalty | Not modeled (no disjunctions) |
| Route count | Number of routes / vehicles used | 1 (single clinician) |

### Tier 2: Execution Quality (What Actually Happened)

| Metric | Description | Current RouteCare Status |
|--------|-------------|------------------------|
| On-time to receiving window | % of stops arrived within time window | Not tracked (no execution tracking) |
| On-time to customer slot | % of deliveries within promised slot | Visit status exists but no actual arrival time |
| Route adherence | Actual route vs. planned route | Not tracked |
| Mid-route replans | Number of re-optimizations during execution | Not tracked |
| Dock wait time | Time spent waiting at facility docks | Not modeled |
| Driver hours utilization | Productive driving / total on-duty | Not tracked |
| HOS compliance exceptions | Violations or near-violations | Not modeled |
| Store readiness delay | Time between planned pickup and order-ready | Not modeled |

### Tier 3: Business Outcomes

| Metric | Description | Current RouteCare Status |
|--------|-------------|------------------------|
| Cost per stop | (Labor + fuel) / stops served | Not calculated |
| Cost per mile | (Labor + fuel) / miles driven | Not calculated |
| Drive time savings | Optimized vs. unoptimized baseline | Exists but synthetic (`* 1.35`) |
| Emissions proxy | CO2 per mile (fuel type dependent) | Not tracked |
| Customer NPS / satisfaction | Post-delivery survey | Not tracked |
| Failed delivery attempts | Deliveries that couldn't be completed | Visit `declined` status exists |
| Store overtime impact | On-time deliveries reduce store overtime needs | Not modeled |

### Implementation

Add a `Metrics::Calculator` service that computes all applicable metrics for a RoutePlan:

```ruby
class Metrics::Calculator
  def initialize(route_plan:)
    @plan = route_plan
  end

  def calculate
    {
      plan_quality: {
        total_drive_minutes: @plan.routes.sum(&:total_drive_minutes),
        empty_miles: compute_empty_miles,
        avg_utilization_weight_pct: avg_weight_utilization,
        avg_utilization_volume_pct: avg_volume_utilization,
        total_reload_count: @plan.routes.sum { |r| r.load_plan&.reload_count || 0 },
        constraint_violations: count_violations,
        stops_served: @plan.tasks.where.not(status: :dropped).count,
        stops_dropped: @plan.tasks.where(status: :dropped).count,
      },
      execution_quality: { ... },  # Populated after execution
      business_outcomes: { ... }   # Populated from aggregate data
    }
  end
end
```

---

## Algorithmic Strategy

### Phase Progression

| Phase | Algorithm | Problem Class | Why |
|-------|-----------|--------------|-----|
| Current | Nearest-neighbor + insertion penalty | Single-vehicle TSP with time windows | Simple, sufficient for 5-30 patients |
| Phase 2 | OR-Tools (`PARALLEL_CHEAPEST_INSERTION` + `GUIDED_LOCAL_SEARCH`) | VRPTW / CVRPTW | Proven solver, Ruby gem available, handles multi-vehicle + time windows + capacity |
| Phase 3+ | OR-Tools + external feasibility services | VRPTW + HOS + loading constraints | OR-Tools handles routing; feasibility services handle complex constraints |
| Phase 4C | OR-Tools + 2E-VRP decomposition | Two-echelon assignment + routing | Assignment MIP feeds per-DC routing subproblems |
| Phase 5 | Rolling horizon insertion + OR-Tools replan | Dynamic VRP | Batch every 1-5 min, commit next window, improve replan window |
| Phase 6+ | Custom ALNS (Ropke-Pisinger) | Full Walmart-scale VRP with all constraints | Maximum flexibility; embed custom feasibility in insertion; adaptive operator weighting |

### Key Academic References

| Paper/Method | Relevance |
|-------------|-----------|
| Solomon VRPTW benchmarks (1987) | Standard test instances for validating solver behavior |
| Ropke & Pisinger, Adaptive LNS (2006) | Primary heuristic for Walmart-scale routing with custom constraints |
| Shaw, LNS (1998) | Foundation for remove-reinsert neighborhood search |
| 2E-VRP / 2E-CVRP surveys (Cuda et al., Perboli et al.) | Two-echelon routing for DC->store->customer flows |
| VRP with loading constraints (Iori & Martello) | Integrated routing + packing (NP-hard combined) |
| Dynamic VRP surveys (Psaraftis et al., 2016) | Methodological basis for last-mile dispatch under uncertainty |
| 3D loading CVRP (Gendreau et al.) | Joint routing + 3D packing metaheuristics |

### Walmart's Disclosed Approach

Walmart's Edelman-issue work uses: "scalable and fast optimization decision engines" combining **mixed-integer programming, metaheuristics, and simulation**. Their Load Planner runs fast feasibility checks in milliseconds within iterative search, producing routes that are guaranteed to have feasible load designs. The system explicitly frames a hierarchical decision process and a modular execution stack.

---

## Timeline Summary

| Phase | Focus | Key Deliverable | Effort |
|-------|-------|-----------------|--------|
| **0** | Foundation hardening | Real routing/geocoding APIs, tests, return-to-home, honest baseline | 1-2 wk |
| **1** | Domain generalization | Abstract model: vehicle, driver, facility, stop, hierarchical time windows, dock slots, load units, events | 3-4 wk |
| **2** | Modular VRP solver | OR-Tools CVRPTW + pluggable feasibility service architecture | 3-4 wk |
| **3** | HOS constraints | Fast HOS feasibility service (millisecond DOT compliance checks) | 1-2 wk |
| **3B** | Load planning | Loading feasibility service: temp compartments, FILO sequencing, reload minimization | 2-3 wk |
| **4A** | InHome delivery | Walmart InHome adaptation with store readiness constraint | 2-3 wk |
| **4B** | GoLocal integration | API integration for last-mile outsourcing | 1-2 wk |
| **4C** | Middle-mile | 2E-VRP: facility assignment + DC-to-store routing with full constraints | 4-5 wk |
| **4D** | Load Board | Inbound freight bidding integration | 1-2 wk |
| **5** | Dynamic dispatch | Commit/replan window dispatch loop, event-driven triggers, streaming | 4-5 wk |
| **6** | Scale & infrastructure | Multi-tenant, VROOM/OSRM, immutable event store, ALNS engine | 3-4 wk |
| **7** | Simulation | What-if scenarios, disruption response, capacity stress tests | 2-3 wk |

**Total: ~28-39 weeks** for the full roadmap.

**Phases 0-2 (~7-10 weeks)** deliver a dramatically improved physician product with real VRP solver and modular architecture ready for all Walmart adaptations.

---

## Technical References

### OR-Tools
- Ruby gem: https://github.com/ankane/or-tools-ruby
- Google OR-Tools docs: https://developers.google.com/optimization/routing
- VRP examples: https://developers.google.com/optimization/routing/vrptw

### VROOM (Scale-Out)
- GitHub: https://github.com/VROOM-Project/vroom
- Docker: https://github.com/VROOM-Project/vroom-docker
- HTTP API: https://github.com/VROOM-Project/vroom-express

### Walmart APIs & Sources
- Developer portal: https://developer.walmart.com
- GoLocal API: https://developer.walmart.com/transportation-carriers/reference
- Inbound Load Board: https://developer.walmart.com/transportation-carriers/docs/getting-started-with-inbound-load-board
- GoLocal info: https://walmartgolocal.com
- Walmart Route Optimization product: https://corporate.walmart.com/news/2024/03/14/walmart-commerce-technologies-launches-ai-powered-logistics-product
- INFORMS Edelman issue (Load Planner): https://courses.ie.bilkent.edu.tr/ie479/wp-content/uploads/sites/16/2024/11/Optimizing-Walmarts-Supply-Chain-from-Strategy-to-Execution.pdf
- INFORMS Edelman introduction: https://pubsonline.informs.org/doi/pdf/10.1287/inte.2023.intro.v54.n1
- SEC 10-K FY2024: https://www.sec.gov/Archives/edgar/data/104169/000010416924000056/wmt-20240131.htm
- Spark Driver: https://corporate.walmart.com/news/2023/06/07/the-spark-driver-platform-celebrates-5-years-of-growth
- InHome: https://www.walmart.com/help/article/inhome-ordering/e37847e4b61d4335b39eef3382ef2f31
- Store Assist: https://corporate.walmart.com/news/2023/01/12/walmart-commerce-technologies-and-salesforce-team-up-to-unlock-local-fulfillment-and-delivery-solutions-for-retailers
- Sustainability/fleet: https://corporate.walmart.com/news/2023/06/01/walmart-doubles-down-on-reducing-waste-to-create-more-sustainable-omnichannel-fulfillment-network
- Fleet emissions: https://corporate.walmart.com/news/2022/06/08/zero-sum-how-walmart-transportation-is-working-to-reduce-emissions-now-and-in-the-future
- Scheduler 2.0: https://azure-na-assets.contentstack.com/v3/assets/blta7903c6b840b702d/bltd216871ce88e3729/WFS-Scheduler-2.0-User-Guide
- Supplier One OTIF: https://supplierone.helpdocs.io/article/nlk0o081cv-otif-charges
- Supplier One ASN: https://supplierone.helpdocs.io/article/fbv7pn6auf-create-advanced-shipment-notice-asn
- FMCSA HOS summary: https://www.fmcsa.dot.gov/regulations/hours-service/summary-hours-service-regulations
- FMCSA HOS regulations: https://www.fmcsa.dot.gov/regulations/hours-of-service

### Academic / Algorithmic
- Ropke & Pisinger, ALNS for Pickup & Delivery (2006): https://backend.orbit.dtu.dk/ws/portalfiles/portal/3154899/
- Shaw, LNS (1998): https://neo.lcc.uma.es/radi-aeb/WebVRP/data/articles/CP4VRPshaw98.pdf
- Dynamic VRP survey (Psaraftis et al., 2016): https://www.sciencedirect.com/science/article/pii/S0377221712006388
- 2E-VRP survey: https://www.sciencedirect.com/science/article/pii/S030505481400166X
- VRP with loading constraints: https://link.springer.com/article/10.1007/s11750-010-0144-x
- 3D loading CVRP: https://www.sciencedirect.com/science/article/pii/S0377221709002252
- Solomon VRPTW: https://www.sciencedirect.com/science/article/pii/S0377221707005498

### Mapping / Routing APIs
- Mapbox Matrix API: https://docs.mapbox.com/api/navigation/matrix/
- Mapbox Geocoding: https://docs.mapbox.com/api/search/geocoding/
- OSRM (self-hosted): https://project-osrm.org

### Key RouteCare Files
- Core optimizer: `app/services/scheduling/weekly_optimizer.rb`
- Travel matrix: `app/services/scheduling/travel_time_matrix_builder.rb`
- Routing client: `app/services/integrations/routing_client.rb`
- Geocoding client: `app/services/integrations/geocoding_client.rb`
- Route visualization: `app/frontend/components/calendar/RoutePanel.tsx`
- Route map hook: `app/frontend/components/calendar/hooks/useRouteMap.ts`
- Database schema: `db/schema.rb`
