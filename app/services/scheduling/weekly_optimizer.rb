module Scheduling
  class WeeklyOptimizer
    DEFAULT_DAY_START_MINUTE = 8 * 60
    DEFAULT_DAY_END_MINUTE = 18 * 60
    SLOT_STEP_MINUTES = 15
    MAX_VISITS_PER_DAY = 5
    HOME_NODE_ID = :home

    def initialize(user:, week_start_on:, start_point: nil)
      @user = user
      @week_start_on = week_start_on.to_date.beginning_of_week(:monday)
      @clinician_profile = user.clinician_profile
      @start_point = start_point
      @routing_client = Integrations::RoutingClient.new
    end

    def call
      raise ArgumentError, "Clinician profile is required" unless clinician_profile

      solution = generate_solution
      persister = Scheduling::SchedulePersister.new(
        user: user, week_start_on: week_start_on, start_point: start_point, routing_client: routing_client
      )

      persister.persist(
        day_routes: solution[:day_routes],
        lunch_placements: solution[:lunch_placements],
        locked_visits: @locked_visits,
        metadata: solution[:metadata],
        travel_matrix: solution[:travel_matrix]
      )
    end

    def generate_solution
      raise ArgumentError, "Clinician profile is required" unless clinician_profile

      schedule = user.weekly_schedules.find_by(week_start_on: week_start_on)
      @locked_visits = schedule ? schedule.visits.where(status: %w[confirmed completed]).to_a : []

      locked_patient_days = @locked_visits.each_with_object({}) do |visit, hash|
        day = visit.starts_at.to_date
        (hash[visit.patient_id] ||= Set.new) << day
      end

      all_patients = clinician_profile.patients.active.includes(:patient_availability_windows).to_a
      build_travel_matrix(all_patients)
      @charting_buffer = clinician_profile.charting_buffer_minutes
      compute_global_avg_travel

      visit_plan, soft_constraint_count, unschedulable = build_visit_plan(locked_patient_days:)

      # Post-optimization: relocate + swap moves to improve day assignments
      visit_plan = post_optimize(visit_plan)

      # Build day routes with nearest-neighbor + 2-opt ordering, then retime
      grouped = visit_plan.group_by { |slot| slot[:date] }
      locked_by_date = @locked_visits.group_by { |v| v.starts_at.to_date }
      day_routes = {}
      lunch_placements = {}

      max_drive = clinician_profile.max_drive_minutes_per_day

      all_dates = (grouped.keys + locked_by_date.keys).uniq
      all_dates.each do |date|
        new_slots = grouped[date] || []
        locked = locked_by_date[date] || []

        ordered_slots = two_opt_order(nearest_neighbor_order(new_slots))

        ds, de = day_bounds_for(date)
        retimer = Scheduling::Retimer.new(
          travel_matrix: @travel_matrix,
          locked_visits: locked,
          lunch_config: lunch_break_config_for(date),
          day_start_minute: ds,
          day_end_minute: de,
          start_point: start_point_for_day,
          routing_client: routing_client,
          max_continuous_work_minutes: clinician_profile.max_continuous_work_minutes,
          required_break_minutes: clinician_profile.required_break_minutes,
          charting_buffer_minutes: clinician_profile.charting_buffer_minutes
        )
        result = retimer.call(ordered_slots, date)

        day_routes[date] = result[:slots] if result[:slots].any?
        lunch_placements[date.to_s] = result[:lunch] if result[:lunch]
      end

      # Check for max drive violations on final routes (soft warning, no shedding)
      drive_violations = max_drive ? check_max_drive_violations(day_routes, locked_by_date, max_drive) : []

      # Compute return-home drive per day (last visit → home)
      return_home_by_day = {}
      day_routes.each do |date, slots|
        locked = locked_by_date[date] || []
        all_day = slots + locked.map { |v| { patient: v.patient } }
        next if all_day.empty?

        ordered = nearest_neighbor_order(all_day)
        last_id = ordered.last[:patient].id
        return_home_by_day[date.to_s] = travel_between(last_id, HOME_NODE_ID)
      end

      # Fill in lunch for working days with no visits
      weekly_days.each do |date|
        next if lunch_placements.key?(date.to_s)
        lbc = lunch_break_config_for(date)
        next unless lbc

        lunch_placements[date.to_s] = {
          start_minute: lbc[:earliest_start],
          end_minute: lbc[:earliest_start] + lbc[:duration]
        }
      end

      # Compute fitness
      instances = Scheduling::VisitInstanceBuilder.new(
        patients: all_patients,
        locked_visits: @locked_visits,
        charting_buffer_minutes: clinician_profile.charting_buffer_minutes
      ).call

      fitness_fn = Scheduling::FitnessFunction.new(
        travel_matrix: @travel_matrix,
        instances: instances,
        clinician_profile: clinician_profile,
        start_point: start_point_for_day
      )
      fitness = fitness_fn.score(day_routes: day_routes, lunch_placements: lunch_placements)

      {
        day_routes: day_routes,
        lunch_placements: lunch_placements,
        travel_matrix: @travel_matrix,
        fitness: fitness,
        metadata: {
          soft_constraint_overrides: soft_constraint_count,
          patient_count: visit_plan.map { |slot| slot[:patient].id }.uniq.size,
          visit_count: visit_plan.size,
          unschedulable: unschedulable,
          drive_violations: drive_violations,
          return_home_by_day: return_home_by_day,
          optimizer_type: "greedy"
        }
      }
    end

    private

    attr_reader :user, :week_start_on, :clinician_profile, :start_point, :routing_client

    # ── Travel Matrix ──────────────────────────────────────────────────────

    def build_travel_matrix(all_patients)
      unique_patients = (all_patients + @locked_visits.map(&:patient)).uniq
      @travel_matrix = Scheduling::TravelTimeMatrixBuilder.new(
        patients: unique_patients,
        home: start_point_for_day,
        routing_client: routing_client
      ).call
    end

    def compute_global_avg_travel
      all_times = []
      @travel_matrix.each do |from_id, destinations|
        next if from_id == HOME_NODE_ID
        destinations.each do |to_id, time|
          next if to_id == HOME_NODE_ID || from_id == to_id
          all_times << time
        end
      end
      @global_avg_travel = all_times.empty? ? 1.0 : (all_times.sum.to_f / all_times.size)
    end

    def travel_between(from_id, to_id)
      return 0 if from_id.nil? || to_id.nil? || from_id == to_id
      @travel_matrix.dig(from_id, to_id) || 0
    end

    # ── Visit Plan: Regret-Based Insertion ─────────────────────────────────

    def build_visit_plan(locked_patient_days: {})
      @blocked_ranges = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      weekly_days.each { |date| @blocked_ranges[date] ||= [] }

      @locked_visits.each do |visit|
        date = visit.starts_at.to_date
        @blocked_ranges[date] ||= []
        footprint_end = visit.starts_at + (visit.duration_minutes + @charting_buffer).minutes
        @blocked_ranges[date] << (visit.starts_at...footprint_end)
      end

      # Build all visit instances that need scheduling
      patients = clinician_profile.patients.active.includes(:patient_availability_windows).order(priority: :desc)
      unplaced = []
      patients.each do |patient|
        locked_days = locked_patient_days[patient.id] || Set.new
        remaining = patient.required_visits_per_week - locked_days.size
        next if remaining <= 0

        targets = evenly_spaced_day_offsets(remaining, patient: patient)
        remaining.times do |i|
          unplaced << {
            patient: patient,
            target_day_offset: targets[i],
            excluded_days: locked_days,
            instance_id: "patient_#{patient.id}_visit_#{i}",
            slot_footprint: patient.visit_duration_minutes + @charting_buffer
          }
        end
      end

      plan = []
      soft_constraint_count = 0
      unschedulable = []

      # Regret-based insertion: place the visit with the most to lose first
      while unplaced.any?
        best_regret = -Float::INFINITY
        best_visit_idx = nil
        best_slot = nil

        unplaced.each_with_index do |visit_info, idx|
          slots = ranked_day_slots(visit_info, plan)
          next if slots.empty?

          if slots.size == 1
            # Only one option — infinite regret (must place now or lose it)
            regret = Float::INFINITY
          else
            regret = slots[1][:day_score_value] - slots[0][:day_score_value]
          end

          # Bias regret by priority: high-priority visits get a regret boost
          regret += visit_info[:patient].priority * 2.0

          # Bias by time window tightness: fewer eligible days = higher urgency
          urgency = 1.0 / [ slots.size, 1 ].max
          regret += urgency * 5.0

          if regret > best_regret
            best_regret = regret
            best_visit_idx = idx
            best_slot = slots[0][:slot]
          end
        end

        if best_visit_idx.nil?
          # Remaining visits can't be placed — try fallback for each
          unplaced.each do |visit_info|
            existing_patient_days = existing_days_for_patient(visit_info[:patient], plan)
            slot = fallback_any_day(
              patient: visit_info[:patient], current_plan: plan,
              existing_patient_days: existing_patient_days, slot_footprint: visit_info[:slot_footprint]
            )
            if slot
              slot[:instance_id] = visit_info[:instance_id]
              soft_constraint_count += 1
              plan << slot
              @blocked_ranges[slot[:date]] << (slot[:starts_at]...(slot[:starts_at] + visit_info[:slot_footprint].minutes))
            else
              unschedulable << { patient_name: visit_info[:patient].full_name, patient_id: visit_info[:patient].id, visit_index: 0 }
            end
          end
          break
        end

        visit_info = unplaced.delete_at(best_visit_idx)
        best_slot[:instance_id] = visit_info[:instance_id]
        soft_constraint_count += 1 if best_slot[:soft_constraint_override]
        plan << best_slot
        @blocked_ranges[best_slot[:date]] << (best_slot[:starts_at]...(best_slot[:starts_at] + visit_info[:slot_footprint].minutes))
      end

      [ plan.sort_by { |slot| slot[:starts_at] }, soft_constraint_count, unschedulable ]
    end

    # Returns ranked day options for a visit, each with a concrete slot and a comparable score.
    # Tries strict pass first (all constraints as filters), then relaxed pass (spacing/drive as penalties).
    def ranked_day_slots(visit_info, current_plan)
      results = score_candidate_days(visit_info, current_plan, strict: true)
      results = score_candidate_days(visit_info, current_plan, strict: false) if results.empty?
      results.sort_by { |r| r[:day_score_value] }
    end

    def score_candidate_days(visit_info, current_plan, strict:)
      patient = visit_info[:patient]
      existing_patient_days = existing_days_for_patient(patient, current_plan)
      min_gap = patient.min_days_between_visits
      max_drive = clinician_profile.max_drive_minutes_per_day
      slot_footprint = visit_info[:slot_footprint]
      target_day_offset = visit_info[:target_day_offset]

      results = []

      weekly_days.each do |date|
        # Hard constraints: always enforced
        next if existing_patient_days.include?(date)
        next if visit_info[:excluded_days].include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        # Soft constraints: filter in strict mode, penalty in relaxed mode
        spacing_violation = too_close_to_existing?(date, existing_patient_days, min_gap)
        drive_violation = max_drive && day_drive_with_patient(current_plan, date, patient) > max_drive

        if strict
          next if spacing_violation
          next if drive_violation
        end

        # Verify a slot actually exists on this day
        slot = find_start_on_day(
          patient: patient, date: date, current_plan: current_plan,
          slot_footprint: slot_footprint
        )
        next unless slot

        spacing = spacing_score_for(date, existing_patient_days, min_gap, patient.max_days_between_visits)
        cluster = geographic_cluster_score(patient, date, current_plan)
        cluster_weight = patient.priority > 0 ? 0.3 : 1.0

        # Scalar score for regret comparison (lower = better)
        day_score = (day_offset_for(date) - target_day_offset).abs * 10.0 +
                    spacing * 5.0 -
                    cluster * cluster_weight * 3.0

        # In relaxed mode, add heavy penalties for violated soft constraints
        day_score += 100.0 if spacing_violation
        day_score += 50.0 if drive_violation

        patient_windows = preferred_windows_for(patient, date)
        slot[:soft_constraint_override] = patient_windows.blank? || !strict

        results << { slot: slot, day_score_value: day_score }
      end

      results
    end

    # Find a valid start time on a specific day (temporal overlap only, no transit padding)
    def find_start_on_day(patient:, date:, current_plan:, slot_footprint:)
      patient_windows = preferred_windows_for(patient, date)
      window_set = patient_windows.presence || fallback_windows_for(date)

      window_set.each do |window|
        start_minute = find_first_available_start(
          window: window, date: date, slot_footprint: slot_footprint,
          blocked_ranges: @blocked_ranges[date], current_plan: current_plan, patient: patient
        )
        next unless start_minute

        starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
        ends_at = starts_at + patient.visit_duration_minutes.minutes

        return { patient: patient, date: date, starts_at: starts_at, ends_at: ends_at }
      end

      nil
    end

    # Last resort: any open slot on any day. First tries with spacing, then without.
    def fallback_any_day(patient:, current_plan:, existing_patient_days:, slot_footprint:)
      # Try with spacing first
      slot = fallback_scan(patient: patient, current_plan: current_plan,
                           existing_patient_days: existing_patient_days,
                           slot_footprint: slot_footprint, enforce_spacing: true)
      return slot if slot

      # Drop spacing as last resort (still enforces one-patient-per-day)
      fallback_scan(patient: patient, current_plan: current_plan,
                    existing_patient_days: existing_patient_days,
                    slot_footprint: slot_footprint, enforce_spacing: false)
    end

    def fallback_scan(patient:, current_plan:, existing_patient_days:, slot_footprint:, enforce_spacing:)
      min_gap = patient.min_days_between_visits

      weekly_days.each do |date|
        next if existing_patient_days.include?(date)
        next if enforce_spacing && too_close_to_existing?(date, existing_patient_days, min_gap)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        ds, de = day_bounds_for(date)
        start_minute = ds
        while start_minute + slot_footprint <= de
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
          footprint_end = starts_at + slot_footprint.minutes
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          unless overlaps_blocked?(starts_at, footprint_end, @blocked_ranges[date]) || overlaps_plan_temporal?(starts_at, footprint_end, current_plan)
            return { patient: patient, date: date, starts_at: starts_at, ends_at: ends_at, soft_constraint_override: true }
          end
          start_minute += SLOT_STEP_MINUTES
        end
      end

      nil
    end

    def find_first_available_start(window:, date:, slot_footprint:, blocked_ranges:, current_plan:, patient:)
      latest_start = window.end_minute - slot_footprint
      return nil if latest_start < window.start_minute

      minute = window.start_minute
      while minute <= latest_start
        starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(minute)}")
        footprint_end = starts_at + slot_footprint.minutes

        # Temporal overlap only — no transit padding. The Retimer handles transit gaps.
        unless overlaps_blocked?(starts_at, footprint_end, blocked_ranges) || overlaps_plan_temporal?(starts_at, footprint_end, current_plan)
          return minute
        end

        minute += SLOT_STEP_MINUTES
      end

      nil
    end

    # ── Post-Optimization: Adaptive Large Neighborhood Search (ALNS) ──────

    ALNS_MAX_ITERATIONS = 30
    ALNS_DESTROY_FRACTION_MIN = 0.2
    ALNS_DESTROY_FRACTION_MAX = 0.4

    # Destroy/repair loop: remove a chunk of visits, re-insert with regret heuristic.
    # Adapts strategy weights based on which destroy methods produce improvements.
    def post_optimize(plan)
      return plan if plan.size < 3

      best_plan = plan.dup
      best_cost = total_plan_cost(best_plan)
      current_plan = plan.dup
      current_cost = best_cost

      # Strategy weights — adapted over iterations
      strategies = {
        worst_cost: 1.0,
        geographic_cluster: 1.0,
        random: 1.0,
        same_patient: 1.0,
        full_day: 1.0
      }

      ALNS_MAX_ITERATIONS.times do |iteration|
        # Pick destroy strategy weighted by past success
        strategy = pick_strategy(strategies)
        destroy_count = rand(
          (plan.size * ALNS_DESTROY_FRACTION_MIN).ceil..(plan.size * ALNS_DESTROY_FRACTION_MAX).ceil
        )
        destroy_count = [ destroy_count, current_plan.size ].min

        # Destroy: remove visits from the plan
        removed, remaining = send(:"destroy_#{strategy}", current_plan, destroy_count)
        next if removed.empty?

        # Repair: re-insert removed visits using regret heuristic
        repaired = regret_repair(remaining, removed)

        # Reject if repair couldn't place all visits or constraints violated
        next unless repaired
        next unless plan_feasible?(repaired)

        repaired_cost = total_plan_cost(repaired)

        if repaired_cost < current_cost
          current_plan = repaired
          current_cost = repaired_cost
          # Reward strategy
          strategies[strategy] = [ strategies[strategy] * 1.3, 5.0 ].min

          if repaired_cost < best_cost
            best_plan = repaired.dup
            best_cost = repaired_cost
          end
        else
          # Decay strategy weight slightly
          strategies[strategy] = [ strategies[strategy] * 0.95, 0.2 ].max
        end
      end

      best_plan
    end

    def pick_strategy(strategies)
      total = strategies.values.sum
      r = rand * total
      cumulative = 0.0
      strategies.each do |strategy, weight|
        cumulative += weight
        return strategy if r <= cumulative
      end
      strategies.keys.last
    end

    # ── Destroy Strategies ─────────────────────────────────────────────────

    # Remove visits that contribute the most drive time
    def destroy_worst_cost(plan, count)
      scored = plan.map do |slot|
        day_slots = plan.select { |s| s[:date] == slot[:date] }
        ids = day_slots.map { |s| s[:patient].id }
        idx = ids.index(slot[:patient].id)

        # Cost of edges touching this visit
        prev_id = idx > 0 ? ids[idx - 1] : HOME_NODE_ID
        next_id = idx < ids.size - 1 ? ids[idx + 1] : HOME_NODE_ID
        cost_with = travel_between(prev_id, slot[:patient].id) + travel_between(slot[:patient].id, next_id)
        cost_without = travel_between(prev_id, next_id)
        { slot: slot, marginal_cost: cost_with - cost_without }
      end

      # Remove highest marginal cost visits
      sorted = scored.sort_by { |s| -s[:marginal_cost] }
      to_remove = sorted.first(count).map { |s| s[:slot] }
      removed_ids = to_remove.map { |s| s.object_id }.to_set
      remaining = plan.reject { |s| removed_ids.include?(s.object_id) }
      [ to_remove, remaining ]
    end

    # Remove a geographic cluster: pick a random visit, remove its nearest neighbors
    def destroy_geographic_cluster(plan, count)
      seed = plan.sample
      return [ [], plan ] unless seed

      scored = plan.map do |slot|
        dist = travel_between(seed[:patient].id, slot[:patient].id)
        { slot: slot, distance: dist }
      end
      sorted = scored.sort_by { |s| s[:distance] }
      to_remove = sorted.first(count).map { |s| s[:slot] }
      removed_ids = to_remove.map { |s| s.object_id }.to_set
      remaining = plan.reject { |s| removed_ids.include?(s.object_id) }
      [ to_remove, remaining ]
    end

    # Remove random visits
    def destroy_random(plan, count)
      to_remove = plan.sample(count)
      removed_ids = to_remove.map { |s| s.object_id }.to_set
      remaining = plan.reject { |s| removed_ids.include?(s.object_id) }
      [ to_remove, remaining ]
    end

    # Remove all visits for a random subset of patients
    def destroy_same_patient(plan, count)
      patient_ids = plan.map { |s| s[:patient].id }.uniq.shuffle
      to_remove = []
      patient_ids.each do |pid|
        break if to_remove.size >= count
        to_remove.concat(plan.select { |s| s[:patient].id == pid })
      end
      to_remove = to_remove.first(count)
      removed_ids = to_remove.map { |s| s.object_id }.to_set
      remaining = plan.reject { |s| removed_ids.include?(s.object_id) }
      [ to_remove, remaining ]
    end

    # Remove all visits on a random day
    def destroy_full_day(plan, count)
      dates = plan.map { |s| s[:date] }.uniq
      target_date = dates.sample
      return [ [], plan ] unless target_date

      to_remove = plan.select { |s| s[:date] == target_date }.first(count)
      removed_ids = to_remove.map { |s| s.object_id }.to_set
      remaining = plan.reject { |s| removed_ids.include?(s.object_id) }
      [ to_remove, remaining ]
    end

    # ── Repair: Regret-Based Re-Insertion ──────────────────────────────────

    # Returns repaired plan, or nil if not all visits could be re-inserted.
    def regret_repair(plan, removed)
      plan = plan.dup
      required_count = plan.size + removed.size

      # Rebuild blocked ranges from scratch for the remaining plan
      repair_blocked = rebuild_blocked_ranges(plan)

      unplaced = removed.map do |slot|
        {
          patient: slot[:patient],
          target_day_offset: day_offset_for(slot[:date]),
          excluded_days: Set.new,
          instance_id: slot[:instance_id],
          slot_footprint: slot[:patient].visit_duration_minutes + @charting_buffer
        }
      end

      while unplaced.any?
        best_regret = -Float::INFINITY
        best_idx = nil
        best_slot = nil

        unplaced.each_with_index do |visit_info, idx|
          slots = ranked_day_slots_with(visit_info, plan, repair_blocked)
          next if slots.empty?

          regret = if slots.size == 1
            Float::INFINITY
          else
            slots[1][:day_score_value] - slots[0][:day_score_value]
          end
          regret += visit_info[:patient].priority * 2.0
          regret += (1.0 / [ slots.size, 1 ].max) * 5.0

          if regret > best_regret
            best_regret = regret
            best_idx = idx
            best_slot = slots[0][:slot]
          end
        end

        # Can't place remaining visits — repair failed
        return nil unless best_idx

        visit_info = unplaced.delete_at(best_idx)
        best_slot[:instance_id] = visit_info[:instance_id]
        plan << best_slot
        footprint = visit_info[:slot_footprint]
        repair_blocked[best_slot[:date]] ||= []
        repair_blocked[best_slot[:date]] << (best_slot[:starts_at]...(best_slot[:starts_at] + footprint.minutes))
      end

      # Sanity: must have the same total count as before destroy
      return nil unless plan.size == required_count

      plan
    end

    # Rebuild blocked ranges from calendar constraints, locked visits, and a given plan
    def rebuild_blocked_ranges(plan)
      blocked = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      weekly_days.each { |date| blocked[date] ||= [] }

      @locked_visits.each do |visit|
        date = visit.starts_at.to_date
        blocked[date] ||= []
        footprint_end = visit.starts_at + (visit.duration_minutes + @charting_buffer).minutes
        blocked[date] << (visit.starts_at...footprint_end)
      end

      plan.each do |slot|
        footprint = slot[:patient].visit_duration_minutes + @charting_buffer
        blocked[slot[:date]] ||= []
        blocked[slot[:date]] << (slot[:starts_at]...(slot[:starts_at] + footprint.minutes))
      end

      blocked
    end

    # Like ranked_day_slots but uses custom blocked ranges (for repair context)
    def ranked_day_slots_with(visit_info, current_plan, blocked_ranges)
      results = score_repair_days(visit_info, current_plan, blocked_ranges, strict: true)
      results = score_repair_days(visit_info, current_plan, blocked_ranges, strict: false) if results.empty?
      results.sort_by { |r| r[:day_score_value] }
    end

    def score_repair_days(visit_info, current_plan, blocked_ranges, strict:)
      patient = visit_info[:patient]
      existing_patient_days = existing_days_for_patient(patient, current_plan)
      min_gap = patient.min_days_between_visits
      max_drive = clinician_profile.max_drive_minutes_per_day
      slot_footprint = visit_info[:slot_footprint]
      target_day_offset = visit_info[:target_day_offset]

      results = []

      weekly_days.each do |date|
        next if existing_patient_days.include?(date)
        next if visit_info[:excluded_days].include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        spacing_violation = too_close_to_existing?(date, existing_patient_days, min_gap)
        drive_violation = max_drive && day_drive_with_patient(current_plan, date, patient) > max_drive

        if strict
          next if spacing_violation
          next if drive_violation
        end

        patient_windows = preferred_windows_for(patient, date)
        window_set = patient_windows.presence || fallback_windows_for(date)

        slot = nil
        window_set.each do |window|
          start_minute = find_first_available_start(
            window: window, date: date, slot_footprint: slot_footprint,
            blocked_ranges: blocked_ranges[date] || [], current_plan: current_plan, patient: patient
          )
          if start_minute
            starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
            ends_at = starts_at + patient.visit_duration_minutes.minutes
            slot = { patient: patient, date: date, starts_at: starts_at, ends_at: ends_at,
                     soft_constraint_override: patient_windows.blank? || !strict }
            break
          end
        end
        next unless slot

        spacing = spacing_score_for(date, existing_patient_days, min_gap, patient.max_days_between_visits)
        cluster = geographic_cluster_score(patient, date, current_plan)
        cluster_weight = patient.priority > 0 ? 0.3 : 1.0

        day_score = (day_offset_for(date) - target_day_offset).abs * 10.0 +
                    spacing * 5.0 -
                    cluster * cluster_weight * 3.0
        day_score += 100.0 if spacing_violation
        day_score += 50.0 if drive_violation

        results << { slot: slot, day_score_value: day_score }
      end

      results
    end

    # ── Plan Validation and Costing ────────────────────────────────────────

    def plan_feasible?(plan)
      max_drive = clinician_profile.max_drive_minutes_per_day

      plan.group_by { |s| s[:date] }.each do |date, slots|
        # One patient per day
        patient_ids = slots.map { |s| s[:patient].id }
        return false if patient_ids.size != patient_ids.uniq.size

        # Max visits per day
        return false if slots.size + @locked_visits.count { |v| v.starts_at.to_date == date } > MAX_VISITS_PER_DAY

        # Max drive time per day (including locked visits on this day)
        return false if max_drive && day_route_cost_for(all_day_slots(plan, date)) > max_drive
      end

      # Min days between visits (per patient)
      patient_slots = Hash.new { |h, k| h[k] = [] }
      plan.each { |s| patient_slots[s[:patient].id] << { date: s[:date], patient: s[:patient] } }
      @locked_visits.each { |v| patient_slots[v.patient_id] << { date: v.starts_at.to_date, patient: v.patient } }

      patient_slots.each do |_patient_id, entries|
        next if entries.size < 2
        min_gap = entries.first[:patient].min_days_between_visits
        sorted = entries.map { |e| e[:date] }.uniq.sort
        sorted.each_cons(2) do |d1, d2|
          return false if (d2 - d1).to_i < min_gap
        end
      end

      true
    end

    def total_plan_cost(plan)
      all_dates = (plan.map { |s| s[:date] } + @locked_visits.map { |v| v.starts_at.to_date }).uniq
      all_dates.sum do |date|
        day_route_cost_for(all_day_slots(plan, date))
      end
    end

    # Full round-trip drive cost for a day (including return home)
    def day_route_cost_for(slots)
      return 0.0 if slots.empty?
      ordered = nearest_neighbor_order(slots)
      route_cost(ordered)
    end

    # ── Route Ordering ─────────────────────────────────────────────────────

    def nearest_neighbor_order(slots)
      return slots if slots.size <= 1

      remaining = slots.dup
      ordered = []
      current_id = HOME_NODE_ID

      while remaining.any?
        closest = remaining.min_by { |slot| travel_between(current_id, slot[:patient].id) }
        ordered << closest
        remaining.delete(closest)
        current_id = closest[:patient].id
      end

      ordered
    end

    # True 2-opt: try reversing every subsequence, not just adjacent swaps
    def two_opt_order(ordered)
      return ordered if ordered.size < 3

      improved = true
      while improved
        improved = false
        (0...(ordered.size - 1)).each do |i|
          ((i + 2)...ordered.size).each do |j|
            # Reverse the segment between i+1 and j
            candidate = ordered[0..i] + ordered[(i + 1)..j].reverse + ordered[(j + 1)..]
            if route_cost(candidate) < route_cost(ordered)
              ordered = candidate
              improved = true
            end
          end
        end
      end

      ordered
    end

    # Full round-trip cost (used for route optimization: 2-opt, ALNS comparisons)
    def route_cost(ordered_slots)
      return 0.0 if ordered_slots.empty?

      cost = travel_between(HOME_NODE_ID, ordered_slots.first[:patient].id)
      ordered_slots.each_cons(2) { |a, b| cost += travel_between(a[:patient].id, b[:patient].id) }
      cost += travel_between(ordered_slots.last[:patient].id, HOME_NODE_ID)
      cost
    end


    # ── Max Drive Reporting ──────────────────────────────────────────────

    # After routes are finalized, check which days exceed max drive and report them.
    # Does NOT remove visits — max drive is a hard constraint during day assignment
    # but a soft warning on the final route (don't drop patients who were already placed).
    def check_max_drive_violations(day_routes, locked_by_date, max_drive)
      violations = []
      day_routes.each do |date, slots|
        locked = locked_by_date[date] || []
        locked_as_slots = locked.map { |v| { patient: v.patient, date: date } }
        all_day = slots + locked_as_slots
        cost = route_cost(nearest_neighbor_order(all_day))
        if cost > max_drive
          violations << { date: date.to_s, drive_minutes: cost.round, max_drive: max_drive }
        end
      end
      violations
    end

    # ── Overlap Checks ─────────────────────────────────────────────────────

    def overlaps_blocked?(starts_at, ends_at, blocked_ranges)
      blocked_ranges.any? { |range| starts_at < range.end && ends_at > range.begin }
    end

    # Temporal overlap only — no transit padding. The Retimer handles transit gaps.
    def overlaps_plan_temporal?(starts_at, footprint_end, current_plan)
      current_plan.any? do |slot|
        slot_footprint_end = slot[:starts_at] + (slot[:patient].visit_duration_minutes + @charting_buffer).minutes
        starts_at < slot_footprint_end && footprint_end > slot[:starts_at]
      end
    end

    # ── Time Helpers ───────────────────────────────────────────────────────

    def preferred_windows_for(patient, date)
      patient.patient_availability_windows
             .select { |window| window.day_of_week == date.wday }
             .map { |window| Scheduling::TimeWindow.new(window.start_minute, window.end_minute) }
    end

    def fallback_windows_for(date)
      ds, de = day_bounds_for(date)
      [ Scheduling::TimeWindow.new(ds, de) ]
    end

    def day_start_minute
      clinician_profile&.workday_start_minute || DEFAULT_DAY_START_MINUTE
    end

    def day_end_minute
      clinician_profile&.workday_end_minute || DEFAULT_DAY_END_MINUTE
    end

    def day_bounds_for(date)
      return [ day_start_minute, day_end_minute ] if clinician_profile.blank?

      override = clinician_profile.per_day_hours[date.wday.to_s]
      if override.present?
        [ override["start"] || day_start_minute, override["end"] || day_end_minute ]
      else
        [ day_start_minute, day_end_minute ]
      end
    end

    def lunch_break_config_for(date)
      return nil if clinician_profile.blank?
      ds, de = day_bounds_for(date)
      lr = clinician_profile.lunch_range
      earliest = [ lr[:earliest_start_minute], ds ].max
      latest = [ lr[:latest_start_minute], de - lr[:duration_minutes] ].min
      { earliest_start: earliest, latest_start: latest, duration: lr[:duration_minutes] }
    end

    def lunch_break_config
      lunch_break_config_for(week_start_on)
    end

    def start_point_for_day
      return start_point if start_point.present?
      return if clinician_profile.blank? || clinician_profile.home_latitude.blank? || clinician_profile.home_longitude.blank?
      { lat: clinician_profile.home_latitude, lng: clinician_profile.home_longitude }
    end

    def weekly_days
      available_workday_offsets.map { |offset| week_start_on + offset.days }
    end

    def minute_to_hhmm(minute)
      "%<hour>02d:%<minute>02d" % { hour: minute / 60, minute: minute % 60 }
    end

    # ── Day Scoring Helpers ────────────────────────────────────────────────

    def evenly_spaced_day_offsets(required_visits_per_week, patient: nil)
      visit_count = required_visits_per_week.to_i
      day_offsets = available_workday_offsets
      return [ 0 ] if day_offsets.empty?
      return [ day_offsets.first ] if visit_count <= 1

      density = clinician_profile.schedule_density
      min_gap = patient&.min_days_between_visits || 1

      max_step = (day_offsets.length - 1).to_f / (visit_count - 1)
      min_step = [ min_gap.to_f, 1.0 ].max
      step = max_step - (density * (max_step - min_step))
      step = [ step, min_step ].max

      (0...visit_count).map do |index|
        idx = (index * step).round.clamp(0, day_offsets.length - 1)
        day_offsets[idx]
      end
    end

    def available_workday_offsets
      (clinician_profile.working_day_wdays.presence || ClinicianProfile::DEFAULT_WORKING_DAY_WDAYS)
        .map { |wday| (wday - 1) % 7 }
        .sort
    end

    def day_offset_for(date)
      (date - week_start_on).to_i
    end

    def day_visit_count(current_plan, date)
      locked_count = @locked_visits.count { |v| v.starts_at.to_date == date }
      current_plan.count { |slot| slot[:date] == date } + locked_count
    end

    def too_close_to_existing?(date, existing_days, min_gap)
      return false if min_gap <= 1
      existing_days.any? { |d| (date - d).to_i.abs < min_gap }
    end

    def spacing_score_for(date, existing_days, min_gap, max_gap)
      return 0 if existing_days.empty?

      penalty = 0
      existing_days.each do |d|
        gap = (date - d).to_i.abs
        penalty += (min_gap - gap) * 10 if gap < min_gap
        penalty += (gap - max_gap) * 5 if gap > max_gap
      end
      penalty
    end

    # Normalized geographic cluster score: ratio of this patient's avg travel to global avg.
    # Score 0-1 where 1 = perfect cluster (much closer than average).
    def geographic_cluster_score(patient, date, current_plan)
      same_day = current_plan.select { |slot| slot[:date] == date }
      return 0.0 if same_day.empty?

      total_travel = same_day.sum { |slot| travel_between(patient.id, slot[:patient].id) }
      avg_travel = total_travel.to_f / same_day.size

      # Ratio: 0 = as far as avg, 1 = right on top of them
      [ 1.0 - (avg_travel / @global_avg_travel), 0.0 ].max
    end

    # All slots on a day: plan + locked visits (as slot-like hashes)
    def all_day_slots(current_plan, date)
      plan_slots = current_plan.select { |slot| slot[:date] == date }
      locked_slots = @locked_visits
        .select { |v| v.starts_at.to_date == date }
        .map { |v| { patient: v.patient, date: date } }
      plan_slots + locked_slots
    end

    # Total drive minutes on a day including home→first and last→home legs
    def day_drive_total(current_plan, date)
      day_route_cost_for(all_day_slots(current_plan, date))
    end

    # Projected drive minutes if a patient were added to a day
    def day_drive_with_patient(current_plan, date, patient)
      simulated = all_day_slots(current_plan, date) + [ { patient: patient, date: date } ]
      day_route_cost_for(simulated)
    end

    def existing_days_for_patient(patient, current_plan)
      plan_days = current_plan
        .select { |slot| slot[:patient].id == patient.id }
        .map { |slot| slot[:date] }
      locked_days = @locked_visits
        .select { |v| v.patient_id == patient.id }
        .map { |v| v.starts_at.to_date }
      (plan_days + locked_days).uniq
    end
  end
end
