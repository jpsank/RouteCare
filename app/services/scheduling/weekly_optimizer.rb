module Scheduling
  class WeeklyOptimizer
    DEFAULT_DAY_START_MINUTE = 8 * 60
    DEFAULT_DAY_END_MINUTE = 18 * 60
    SLOT_STEP_MINUTES = 15
    MAX_VISITS_PER_DAY = 5
    TRANSIT_BUFFER_MINUTES = 5

    def initialize(user:, week_start_on:, start_point: nil)
      @user = user
      @week_start_on = week_start_on.to_date.beginning_of_week(:monday)
      @clinician_profile = user.clinician_profile
      @start_point = start_point
      @routing_client = Integrations::RoutingClient.new
      @transit_minutes_cache = {}
    end

    def call
      raise ArgumentError, "Clinician profile is required" unless clinician_profile

      solution = generate_solution
      persister = Scheduling::SchedulePersister.new(
        user: user, week_start_on: week_start_on, start_point: start_point, routing_client: routing_client
      )

      schedule = persister.persist(
        day_routes: solution[:day_routes],
        lunch_placements: solution[:lunch_placements],
        locked_visits: @locked_visits,
        metadata: solution[:metadata],
        travel_matrix: solution[:travel_matrix]
      )

      schedule
    end

    # Returns a pure data solution without touching the DB.
    # { day_routes: {Date => [slots]}, lunch_placements: {}, metadata: {}, travel_matrix: {}, fitness: Float }
    def generate_solution
      raise ArgumentError, "Clinician profile is required" unless clinician_profile

      schedule = user.weekly_schedules.find_by(week_start_on: week_start_on)
      @locked_visits = schedule ? schedule.visits.where(status: %w[confirmed completed]).to_a : []

      locked_patient_days = @locked_visits.each_with_object({}) do |visit, hash|
        day = visit.starts_at.to_date
        (hash[visit.patient_id] ||= Set.new) << day
      end

      visit_plan, soft_constraint_count = build_visit_plan(locked_patient_days:)
      all_patients = (visit_plan.map { |slot| slot[:patient] } + @locked_visits.map(&:patient)).uniq
      travel_matrix = Scheduling::TravelTimeMatrixBuilder.new(patients: all_patients).call

      # Build day routes with nearest-neighbor ordering and retiming
      grouped = visit_plan.group_by { |slot| slot[:date] }
      locked_by_date = @locked_visits.group_by { |v| v.starts_at.to_date }
      day_routes = {}
      lunch_placements = {}

      all_dates = (grouped.keys + locked_by_date.keys).uniq
      all_dates.each do |date|
        new_slots = grouped[date] || []
        locked = locked_by_date[date] || []

        ordered_slots = nearest_neighbor_order(new_slots, travel_matrix)

        retimer = Scheduling::Retimer.new(
          travel_matrix: travel_matrix,
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
        patients: clinician_profile.patients.active.includes(:patient_availability_windows),
        locked_visits: @locked_visits,
        charting_buffer_minutes: clinician_profile.charting_buffer_minutes
      ).call

      fitness_fn = Scheduling::FitnessFunction.new(
        travel_matrix: travel_matrix,
        instances: instances,
        clinician_profile: clinician_profile,
        start_point: start_point_for_day
      )
      fitness = fitness_fn.score(day_routes: day_routes, lunch_placements: lunch_placements)

      {
        day_routes: day_routes,
        lunch_placements: lunch_placements,
        travel_matrix: travel_matrix,
        fitness: fitness,
        metadata: {
          soft_constraint_overrides: soft_constraint_count,
          patient_count: visit_plan.map { |slot| slot[:patient].id }.uniq.size,
          visit_count: visit_plan.size,
          optimizer_type: "greedy"
        }
      }
    end

    private

    attr_reader :user, :week_start_on, :clinician_profile, :start_point, :routing_client

    def build_visit_plan(locked_patient_days: {})
      blocked_ranges = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      weekly_days.each { |date| blocked_ranges[date] ||= [] }

      @locked_visits.each do |visit|
        date = visit.starts_at.to_date
        blocked_ranges[date] ||= []
        blocked_ranges[date] << (visit.starts_at...visit.ends_at)
      end

      plan = []
      soft_constraint_count = 0
      @charting_buffer = clinician_profile.charting_buffer_minutes

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
          slot[:instance_id] = "patient_#{patient.id}_visit_#{visit_index}"
          soft_constraint_count += 1 if slot[:soft_constraint_override]
          plan << slot
          blocked_ranges[slot[:date]] << (slot[:starts_at]...slot[:ends_at])
        end
      end

      [ plan.sort_by { |slot| slot[:starts_at] }, soft_constraint_count ]
    end

    def find_best_slot(patient:, blocked_ranges:, current_plan:, target_day_offset:, excluded_days: Set.new)
      candidates = []
      existing_patient_days = existing_days_for_patient(patient, current_plan)
      # Effective duration includes charting buffer for spacing/overlap checks,
      # but the visit itself is only visit_duration_minutes long.
      slot_footprint = patient.visit_duration_minutes + @charting_buffer
      min_gap = patient.min_days_between_visits
      max_drive = clinician_profile.max_drive_minutes_per_day

      weekly_days.each do |date|
        next if excluded_days.include?(date)
        next if existing_patient_days.include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY
        next if too_close_to_existing?(date, existing_patient_days, min_gap)
        next if max_drive && day_drive_total(current_plan, date) >= max_drive

        patient_windows_for_day = preferred_windows_for(patient, date)
        window_set = patient_windows_for_day.presence || fallback_windows_for(date)

        window_set.each do |window|
          each_possible_start_minute(window: window, duration_minutes: slot_footprint).each do |start_minute|
            starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
            footprint_end = starts_at + slot_footprint.minutes
            ends_at = starts_at + patient.visit_duration_minutes.minutes
            next if overlaps_blocked?(starts_at, footprint_end, blocked_ranges[date])
            next if overlaps_plan?(starts_at, footprint_end, current_plan, patient)

            candidates << {
              patient: patient,
              date: date,
              starts_at: starts_at,
              ends_at: ends_at,
              soft_constraint_override: patient_windows_for_day.blank?
            }
          end
        end
      end

      # Relaxed pass: drop spacing constraints but still enforce one-patient-per-day
      if candidates.empty?
        weekly_days.each do |date|
          next if existing_patient_days.include?(date)
          next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

          patient_windows_for_day = preferred_windows_for(patient, date)
          window_set = patient_windows_for_day.presence || fallback_windows_for(date)

          window_set.each do |window|
            each_possible_start_minute(window: window, duration_minutes: slot_footprint).each do |start_minute|
              starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
              footprint_end = starts_at + slot_footprint.minutes
              ends_at = starts_at + patient.visit_duration_minutes.minutes
              next if overlaps_blocked?(starts_at, footprint_end, blocked_ranges[date])
              next if overlaps_plan?(starts_at, footprint_end, current_plan, patient)

              candidates << {
                patient: patient,
                date: date,
                starts_at: starts_at,
                ends_at: ends_at,
                soft_constraint_override: patient_windows_for_day.blank?
              }
            end
          end
        end
      end

      if candidates.empty?
        fallback_slot(patient:, blocked_ranges:, current_plan:)
      else
        candidates.min_by do |candidate|
          spacing_penalty = spacing_score_for(candidate[:date], existing_patient_days, min_gap, patient.max_days_between_visits)
          [
            (day_offset_for(candidate[:date]) - target_day_offset).abs,
            spacing_penalty,
            insertion_route_penalty(candidate: candidate, current_plan: current_plan),
            candidate[:date],
            candidate[:starts_at]
          ]
        end
      end
    end

    def fallback_slot(patient:, blocked_ranges:, current_plan:)
      slot_footprint = patient.visit_duration_minutes + @charting_buffer

      weekly_days.each do |date|
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        start_minute = day_start_minute
        while start_minute + slot_footprint <= day_end_minute
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
          footprint_end = starts_at + slot_footprint.minutes
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          unless overlaps_blocked?(starts_at, footprint_end, blocked_ranges[date]) || overlaps_plan?(starts_at, footprint_end, current_plan, patient)
            return {
              patient:,
              date:,
              starts_at:,
              ends_at:,
              soft_constraint_override: true
            }
          end
          start_minute += SLOT_STEP_MINUTES
        end
      end

      raise "Unable to schedule all visits for #{patient.full_name}"
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

    def nearest_neighbor_order(slots, _travel_matrix)
      return slots if slots.size <= 1

      remaining = slots.dup
      ordered = []
      current_point = start_point_for_day

      while remaining.any?
        closest =
          if current_point.blank?
            remaining.first
          else
            remaining.min_by do |slot|
              patient_point = point_for(slot[:patient])
              point_distance(current_point, patient_point)
            end
          end

        ordered << closest
        remaining.delete(closest)
        current_point = point_for(closest[:patient])
      end

      ordered
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
        slot_patient = slot[:patient]

        if starts_at <= slot[:starts_at]
          required_gap = transit_minutes_between_patients(patient, slot_patient).minutes
          ends_at + required_gap > slot[:starts_at]
        else
          required_gap = transit_minutes_between_patients(slot_patient, patient).minutes
          slot[:ends_at] + required_gap > starts_at
        end
      end
    end

    def weekly_days
      available_workday_offsets.map { |offset| week_start_on + offset.days }
    end

    def round_up_to_interval(minute)
      remainder = minute % SLOT_STEP_MINUTES
      remainder.zero? ? minute : minute + (SLOT_STEP_MINUTES - remainder)
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

      # High density (→1): cluster visits into fewer days, use smaller step
      # Low density (→0): spread evenly across all days
      max_step = (day_offsets.length - 1).to_f / (visit_count - 1)
      min_step = 1.0 # cluster as tight as possible
      step = max_step - (density * (max_step - min_step))
      step = [ step, 1.0 ].max

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

    # Check if a candidate date is within min_gap days of any existing visit for this patient
    def too_close_to_existing?(date, existing_days, min_gap)
      return false if min_gap <= 1

      existing_days.any? { |d| (date - d).to_i.abs < min_gap }
    end

    # Scoring penalty for spacing: prefer dates that respect min/max gap
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

    # Estimate total drive minutes already scheduled on a day
    def day_drive_total(current_plan, date)
      same_day = current_plan.select { |slot| slot[:date] == date }
      return 0 if same_day.empty?

      total = 0
      sorted = same_day.sort_by { |s| s[:starts_at] }
      prev_id = nil
      sorted.each do |slot|
        if prev_id
          total += (@transit_minutes_cache["#{prev_id}:#{slot[:patient].id}"] || 0)
        end
        prev_id = slot[:patient].id
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

    def each_possible_start_minute(window:, duration_minutes:)
      latest_start = window.end_minute - duration_minutes
      return [] if latest_start < window.start_minute

      starts = []
      minute = window.start_minute
      while minute <= latest_start
        starts << minute
        minute += SLOT_STEP_MINUTES
      end
      starts
    end

    def insertion_route_penalty(candidate:, current_plan:)
      same_day = current_plan
                 .select { |slot| slot[:date] == candidate[:date] }
                 .sort_by { |slot| slot[:starts_at] }
      return 0.0 if same_day.empty?

      previous_slot = same_day.select { |slot| slot[:starts_at] <= candidate[:starts_at] }.max_by { |slot| slot[:starts_at] }
      next_slot = same_day.select { |slot| slot[:starts_at] > candidate[:starts_at] }.min_by { |slot| slot[:starts_at] }

      candidate_point = point_for(candidate[:patient])
      return 0.0 if candidate_point.blank?

      origin_point = previous_slot ? point_for(previous_slot[:patient]) : start_point_for_day
      next_point = next_slot ? point_for(next_slot[:patient]) : nil

      penalty = 0.0
      penalty += point_distance(origin_point, candidate_point) if origin_point.present?
      penalty += point_distance(candidate_point, next_point) if next_point.present?
      if origin_point.present? && next_point.present?
        penalty -= point_distance(origin_point, next_point)
      end
      penalty
    end

    def point_for(patient)
      { lat: patient.latitude, lng: patient.longitude }
    end

    def point_distance(from, to)
      return 0.0 if from.blank? || to.blank?

      lat_scale = 111.0
      lng_scale = 111.0 * Math.cos(((from[:lat].to_f + to[:lat].to_f) / 2.0) * Math::PI / 180.0)
      d_lat = (from[:lat].to_f - to[:lat].to_f) * lat_scale
      d_lng = (from[:lng].to_f - to[:lng].to_f) * lng_scale
      Math.sqrt((d_lat * d_lat) + (d_lng * d_lng))
    end

    def transit_minutes_between_patients(from_patient, to_patient)
      return 0 if from_patient.id == to_patient.id

      cache_key = "#{from_patient.id}:#{to_patient.id}"
      @transit_minutes_cache[cache_key] ||= routing_client.travel_minutes(
        origin: point_for(from_patient),
        destination: point_for(to_patient)
      )
    rescue StandardError
      0
    end
  end
end
