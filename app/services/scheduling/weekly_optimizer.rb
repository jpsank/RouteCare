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

      # Build travel matrix BEFORE placement — includes home node for consistent distance lookups
      all_patients = clinician_profile.patients.active.includes(:patient_availability_windows).to_a
      build_travel_matrix(all_patients)
      @charting_buffer = clinician_profile.charting_buffer_minutes

      visit_plan, soft_constraint_count, unschedulable = build_visit_plan(locked_patient_days:)

      # Build day routes with nearest-neighbor ordering and retiming
      grouped = visit_plan.group_by { |slot| slot[:date] }
      locked_by_date = @locked_visits.group_by { |v| v.starts_at.to_date }
      day_routes = {}
      lunch_placements = {}

      all_dates = (grouped.keys + locked_by_date.keys).uniq
      all_dates.each do |date|
        new_slots = grouped[date] || []
        locked = locked_by_date[date] || []

        ordered_slots = nearest_neighbor_order(new_slots)

        retimer = Scheduling::Retimer.new(
          travel_matrix: @travel_matrix,
          locked_visits: locked,
          lunch_config: lunch_break_config,
          day_start_minute: day_start_minute,
          day_end_minute: day_end_minute,
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

      # Fill in lunch for working days with no visits
      if lunch_break_config
        weekly_days.each do |date|
          next if lunch_placements.key?(date.to_s)

          lunch_placements[date.to_s] = {
            start_minute: lunch_break_config[:earliest_start],
            end_minute: lunch_break_config[:earliest_start] + lunch_break_config[:duration]
          }
        end
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
          optimizer_type: "greedy"
        }
      }
    end

    private

    attr_reader :user, :week_start_on, :clinician_profile, :start_point, :routing_client

    # Build travel matrix including a HOME_NODE_ID entry for home→patient distances
    def build_travel_matrix(all_patients)
      unique_patients = (all_patients + @locked_visits.map(&:patient)).uniq
      @travel_matrix = Scheduling::TravelTimeMatrixBuilder.new(patients: unique_patients).call

      # Add home node to the matrix
      home = start_point_for_day
      if home
        @travel_matrix[HOME_NODE_ID] = {}
        unique_patients.each do |patient|
          time = routing_client.travel_minutes(origin: home, destination: { lat: patient.latitude, lng: patient.longitude })
          @travel_matrix[HOME_NODE_ID][patient.id] = time
          @travel_matrix[patient.id] ||= {}
          @travel_matrix[patient.id][HOME_NODE_ID] = time
        end
      end
    end

    def build_visit_plan(locked_patient_days: {})
      blocked_ranges = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      weekly_days.each { |date| blocked_ranges[date] ||= [] }

      # Block locked visits with charting buffer so new visits don't overlap the gap
      @locked_visits.each do |visit|
        date = visit.starts_at.to_date
        blocked_ranges[date] ||= []
        footprint_end = visit.starts_at + (visit.duration_minutes + @charting_buffer).minutes
        blocked_ranges[date] << (visit.starts_at...footprint_end)
      end

      plan = []
      soft_constraint_count = 0
      unschedulable = []

      # Sort by priority descending so high-acuity patients get first pick
      patients = clinician_profile.patients.active.includes(:patient_availability_windows).order(priority: :desc)

      patients.each do |patient|
        locked_days = locked_patient_days[patient.id] || Set.new
        remaining_visits = patient.required_visits_per_week - locked_days.size
        next if remaining_visits <= 0

        target_day_offsets = evenly_spaced_day_offsets(remaining_visits, patient: patient)

        remaining_visits.times do |visit_index|
          slot = find_best_slot(
            patient: patient,
            blocked_ranges: blocked_ranges,
            current_plan: plan,
            target_day_offset: target_day_offsets[visit_index],
            excluded_days: locked_days
          )
          if slot
            slot[:instance_id] = "patient_#{patient.id}_visit_#{visit_index}"
            soft_constraint_count += 1 if slot[:soft_constraint_override]
            plan << slot
            # Block the full footprint (visit + charting buffer)
            footprint = patient.visit_duration_minutes + @charting_buffer
            blocked_ranges[slot[:date]] << (slot[:starts_at]...(slot[:starts_at] + footprint.minutes))
          else
            unschedulable << { patient_name: patient.full_name, patient_id: patient.id, visit_index: visit_index }
          end
        end
      end

      [ plan.sort_by { |slot| slot[:starts_at] }, soft_constraint_count, unschedulable ]
    end

    # Day-level assignment: pick the best day for this visit, then find a valid start time on that day.
    # The exact start time is provisional — the Retimer rewrites all times after nearest-neighbor ordering.
    def find_best_slot(patient:, blocked_ranges:, current_plan:, target_day_offset:, excluded_days: Set.new)
      existing_patient_days = existing_days_for_patient(patient, current_plan)
      slot_footprint = patient.visit_duration_minutes + @charting_buffer
      min_gap = patient.min_days_between_visits
      max_drive = clinician_profile.max_drive_minutes_per_day

      # First pass: respect all constraints
      day_candidates = score_days(
        patient: patient, current_plan: current_plan,
        existing_patient_days: existing_patient_days,
        target_day_offset: target_day_offset, min_gap: min_gap,
        max_drive: max_drive, excluded_days: excluded_days,
        strict_spacing: true
      )

      # Relaxed pass: drop spacing and drive constraints, keep one-patient-per-day
      if day_candidates.empty?
        day_candidates = score_days(
          patient: patient, current_plan: current_plan,
          existing_patient_days: existing_patient_days,
          target_day_offset: target_day_offset, min_gap: min_gap,
          max_drive: nil, excluded_days: Set.new,
          strict_spacing: false
        )
      end

      return nil if day_candidates.empty?

      # Pick the best day, then find a valid start time on it
      best_day = day_candidates.min_by { |d| d[:score] }
      find_start_on_day(
        patient: patient, date: best_day[:date], blocked_ranges: blocked_ranges,
        current_plan: current_plan, slot_footprint: slot_footprint,
        soft_constraint_override: best_day[:soft_constraint_override]
      ) || fallback_any_day(
        patient: patient, blocked_ranges: blocked_ranges, current_plan: current_plan,
        existing_patient_days: existing_patient_days, slot_footprint: slot_footprint
      )
    end

    # Score each eligible day for a patient, considering spacing, clustering, and drive budget.
    def score_days(patient:, current_plan:, existing_patient_days:, target_day_offset:,
                   min_gap:, max_drive:, excluded_days:, strict_spacing:)
      candidates = []

      weekly_days.each do |date|
        next if existing_patient_days.include?(date)
        next if excluded_days.include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY
        next if strict_spacing && min_gap && too_close_to_existing?(date, existing_patient_days, min_gap)
        next if max_drive && day_drive_total(current_plan, date) >= max_drive

        patient_windows_for_day = preferred_windows_for(patient, date)
        has_preferred = patient_windows_for_day.present?

        # Check that at least one time slot is available on this day
        window_set = has_preferred ? patient_windows_for_day : fallback_windows_for(date)
        has_availability = window_set.any? do |window|
          (window.end_minute - window.start_minute) >= (patient.visit_duration_minutes + @charting_buffer)
        end
        next unless has_availability

        spacing = spacing_score_for(date, existing_patient_days, min_gap, patient.max_days_between_visits)
        cluster = geographic_cluster_score(patient, date, current_plan)
        # High-priority patients: prefer earlier days, deprioritize clustering
        cluster_weight = patient.priority > 0 ? 0.3 : 1.0

        score = [
          (day_offset_for(date) - target_day_offset).abs,
          spacing,
          -(cluster * cluster_weight),
          date
        ]

        candidates << { date: date, score: score, soft_constraint_override: !has_preferred }
      end

      candidates
    end

    # Find a valid start time on a specific day
    def find_start_on_day(patient:, date:, blocked_ranges:, current_plan:, slot_footprint:, soft_constraint_override:)
      patient_windows_for_day = preferred_windows_for(patient, date)
      window_set = patient_windows_for_day.presence || fallback_windows_for(date)

      window_set.each do |window|
        start_minute = find_first_available_start(
          window: window, date: date, slot_footprint: slot_footprint,
          blocked_ranges: blocked_ranges[date], current_plan: current_plan, patient: patient
        )
        next unless start_minute

        starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
        ends_at = starts_at + patient.visit_duration_minutes.minutes

        return {
          patient: patient, date: date, starts_at: starts_at, ends_at: ends_at,
          soft_constraint_override: soft_constraint_override
        }
      end

      nil
    end

    # Last resort: any open slot on any day (still enforces one-patient-per-day)
    def fallback_any_day(patient:, blocked_ranges:, current_plan:, existing_patient_days:, slot_footprint:)
      weekly_days.each do |date|
        next if existing_patient_days.include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        start_minute = day_start_minute
        while start_minute + slot_footprint <= day_end_minute
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
          footprint_end = starts_at + slot_footprint.minutes
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          unless overlaps_blocked?(starts_at, footprint_end, blocked_ranges[date]) || overlaps_plan?(starts_at, footprint_end, current_plan, patient)
            return {
              patient: patient, date: date, starts_at: starts_at, ends_at: ends_at,
              soft_constraint_override: true
            }
          end
          start_minute += SLOT_STEP_MINUTES
        end
      end

      nil
    end

    # Scan for the earliest 15-min-aligned start time that fits within the window
    def find_first_available_start(window:, date:, slot_footprint:, blocked_ranges:, current_plan:, patient:)
      latest_start = window.end_minute - slot_footprint
      return nil if latest_start < window.start_minute

      minute = window.start_minute
      while minute <= latest_start
        starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(minute)}")
        footprint_end = starts_at + slot_footprint.minutes

        unless overlaps_blocked?(starts_at, footprint_end, blocked_ranges) || overlaps_plan?(starts_at, footprint_end, current_plan, patient)
          return minute
        end

        minute += SLOT_STEP_MINUTES
      end

      nil
    end

    def preferred_windows_for(patient, date)
      patient.patient_availability_windows
             .select { |window| window.day_of_week == date.wday }
             .map { |window| Scheduling::TimeWindow.new(window.start_minute, window.end_minute) }
    end

    def fallback_windows_for(_date)
      [ Scheduling::TimeWindow.new(day_start_minute, day_end_minute) ]
    end

    def day_start_minute
      clinician_profile&.workday_start_minute || DEFAULT_DAY_START_MINUTE
    end

    def day_end_minute
      clinician_profile&.workday_end_minute || DEFAULT_DAY_END_MINUTE
    end

    # Nearest-neighbor ordering using travel matrix
    # Considers return-to-home: if adding a visit as last would create a long return trip,
    # prefer ordering that ends closer to home.
    def nearest_neighbor_order(slots)
      return slots if slots.size <= 1

      remaining = slots.dup
      ordered = []
      current_id = HOME_NODE_ID

      while remaining.any?
        closest = remaining.min_by do |slot|
          travel_between(current_id, slot[:patient].id)
        end

        ordered << closest
        remaining.delete(closest)
        current_id = closest[:patient].id
      end

      # 2-opt improvement: try swapping adjacent pairs to reduce total route cost including return home
      improved = true
      while improved
        improved = false
        (0...(ordered.size - 1)).each do |i|
          swapped = ordered.dup
          swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
          if route_cost(swapped) < route_cost(ordered)
            ordered = swapped
            improved = true
          end
        end
      end

      ordered
    end

    # Total route cost including home→first and last→home legs
    def route_cost(ordered_slots)
      return 0.0 if ordered_slots.empty?

      cost = travel_between(HOME_NODE_ID, ordered_slots.first[:patient].id)
      ordered_slots.each_cons(2) do |a, b|
        cost += travel_between(a[:patient].id, b[:patient].id)
      end
      cost += travel_between(ordered_slots.last[:patient].id, HOME_NODE_ID)
      cost
    end

    def lunch_break_config
      return nil if clinician_profile.blank?

      lr = clinician_profile.lunch_range
      {
        earliest_start: lr[:earliest_start_minute],
        latest_start:   lr[:latest_start_minute],
        duration:        lr[:duration_minutes]
      }
    end

    def start_point_for_day
      return start_point if start_point.present?
      return if clinician_profile.blank? || clinician_profile.home_latitude.blank? || clinician_profile.home_longitude.blank?

      {
        lat: clinician_profile.home_latitude,
        lng: clinician_profile.home_longitude
      }
    end

    def overlaps_blocked?(starts_at, ends_at, blocked_ranges)
      blocked_ranges.any? { |range| starts_at < range.end && ends_at > range.begin }
    end

    def overlaps_plan?(starts_at, ends_at, current_plan, patient)
      current_plan.any? do |slot|
        slot_footprint_end = slot[:starts_at] + (slot[:patient].visit_duration_minutes + @charting_buffer).minutes

        if starts_at <= slot[:starts_at]
          travel = travel_between(patient.id, slot[:patient].id)
          ends_at + travel.minutes > slot[:starts_at]
        else
          travel = travel_between(slot[:patient].id, patient.id)
          slot_footprint_end + travel.minutes > starts_at
        end
      end
    end

    def weekly_days
      available_workday_offsets.map { |offset| week_start_on + offset.days }
    end

    def minute_to_hhmm(minute)
      "%<hour>02d:%<minute>02d" % { hour: minute / 60, minute: minute % 60 }
    end

    def evenly_spaced_day_offsets(required_visits_per_week, patient: nil)
      visit_count = required_visits_per_week.to_i
      day_offsets = available_workday_offsets
      return [ 0 ] if day_offsets.empty?
      return [ day_offsets.first ] if visit_count <= 1

      density = clinician_profile.schedule_density
      min_gap = patient&.min_days_between_visits || 1

      # The step must be at least min_gap (patient spacing) and at least 1 (can't skip 0 days).
      # density=0 → max spread (use all available days), density=1 → cluster tight (use min_gap).
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

    # How close is this patient to patients already on that day?
    # Returns a score 0-10 where higher = better clustering.
    def geographic_cluster_score(patient, date, current_plan)
      same_day = current_plan.select { |slot| slot[:date] == date }
      return 0.0 if same_day.empty?

      total_travel = same_day.sum { |slot| travel_between(patient.id, slot[:patient].id) }
      avg_travel = total_travel.to_f / same_day.size

      [ 10.0 - (avg_travel / 3.0), 0.0 ].max
    end

    # Total drive minutes on a day including home→first leg
    def day_drive_total(current_plan, date)
      same_day = current_plan.select { |slot| slot[:date] == date }
      return 0 if same_day.empty?

      sorted = same_day.sort_by { |s| s[:starts_at] }
      total = travel_between(HOME_NODE_ID, sorted.first[:patient].id)
      sorted.each_cons(2) do |a, b|
        total += travel_between(a[:patient].id, b[:patient].id)
      end
      total
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

    def travel_between(from_id, to_id)
      return 0 if from_id.nil? || to_id.nil? || from_id == to_id

      @travel_matrix.dig(from_id, to_id) || 0
    end
  end
end
