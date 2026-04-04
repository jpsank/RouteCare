module Scheduling
  class WeeklyOptimizer
    DEFAULT_DAY_START_MINUTE = 8 * 60
    DEFAULT_DAY_END_MINUTE = 18 * 60
    SLOT_STEP_MINUTES = 15
    MAX_VISITS_PER_DAY = 5

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

      schedule = user.weekly_schedules.find_or_initialize_by(week_start_on: week_start_on)
      schedule.status = :draft

      visit_plan, soft_constraint_count = build_visit_plan
      travel_matrix = Scheduling::TravelTimeMatrixBuilder.new(
        patients: visit_plan.map { |slot| slot[:patient] }.uniq
      ).call

      ActiveRecord::Base.transaction do
        schedule.visits.delete_all
        create_visits!(schedule, visit_plan, travel_matrix)
        calculate_drive_metrics!(schedule)
        schedule.optimization_summary = {
          generated_at: Time.current,
          soft_constraint_overrides: soft_constraint_count,
          patient_count: visit_plan.map { |slot| slot[:patient].id }.uniq.size,
          visit_count: schedule.visits.size
        }
        schedule.save!
      end

      schedule
    end

    private

    attr_reader :user, :week_start_on, :clinician_profile, :start_point, :routing_client

    def build_visit_plan
      blocked_ranges = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      weekly_days.each { |date| blocked_ranges[date] ||= [] }
      plan = []
      soft_constraint_count = 0

      clinician_profile.patients.active.includes(:patient_availability_windows).each do |patient|
        target_day_offsets = evenly_spaced_day_offsets(patient.required_visits_per_week)

        patient.required_visits_per_week.times do |visit_index|
          slot = find_best_slot(
            patient: patient,
            blocked_ranges: blocked_ranges,
            current_plan: plan,
            target_day_offset: target_day_offsets[visit_index]
          )
          soft_constraint_count += 1 if slot[:soft_constraint_override]
          plan << slot
          blocked_ranges[slot[:date]] << (slot[:starts_at]...slot[:ends_at])
        end
      end

      [ plan.sort_by { |slot| slot[:starts_at] }, soft_constraint_count ]
    end

    def find_best_slot(patient:, blocked_ranges:, current_plan:, target_day_offset:)
      candidates = []
      existing_patient_days = existing_days_for_patient(patient, current_plan)

      weekly_days.each do |date|
        next if existing_patient_days.include?(date)
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        patient_windows_for_day = preferred_windows_for(patient, date)
        window_set = patient_windows_for_day.presence || fallback_windows_for(date)

        window_set.each do |window|
          each_possible_start_minute(window: window, duration_minutes: patient.visit_duration_minutes).each do |start_minute|
            starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
            ends_at = starts_at + patient.visit_duration_minutes.minutes
            next if overlaps_blocked?(starts_at, ends_at, blocked_ranges[date])
            next if overlaps_plan?(starts_at, ends_at, current_plan, patient)

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

      if candidates.empty?
        weekly_days.each do |date|
          next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

          patient_windows_for_day = preferred_windows_for(patient, date)
          window_set = patient_windows_for_day.presence || fallback_windows_for(date)

          window_set.each do |window|
            each_possible_start_minute(window: window, duration_minutes: patient.visit_duration_minutes).each do |start_minute|
              starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
              ends_at = starts_at + patient.visit_duration_minutes.minutes
              next if overlaps_blocked?(starts_at, ends_at, blocked_ranges[date])
              next if overlaps_plan?(starts_at, ends_at, current_plan, patient)

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
          [
            (day_offset_for(candidate[:date]) - target_day_offset).abs,
            insertion_route_penalty(candidate: candidate, current_plan: current_plan),
            candidate[:date],
            candidate[:starts_at]
          ]
        end
      end
    end

    def fallback_slot(patient:, blocked_ranges:, current_plan:)
      weekly_days.each do |date|
        next if day_visit_count(current_plan, date) >= MAX_VISITS_PER_DAY

        start_minute = day_start_minute
        while start_minute + patient.visit_duration_minutes <= day_end_minute
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          unless overlaps_blocked?(starts_at, ends_at, blocked_ranges[date]) || overlaps_plan?(starts_at, ends_at, current_plan, patient)
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

    def create_visits!(schedule, visit_plan, travel_matrix)
      grouped = visit_plan.group_by { |slot| slot[:date] }

      grouped.each_value do |slots|
        ordered_slots = nearest_neighbor_order(slots, travel_matrix)
        retimed_slots = retime_slots(ordered_slots, travel_matrix)

        current_point = start_point_for_day
        previous_patient_id = nil

        retimed_slots.each_with_index do |slot, index|
          drive_minutes =
            if previous_patient_id.nil?
              travel_minutes_from_point(current_point, slot[:patient])
            else
              (travel_matrix.dig(previous_patient_id, slot[:patient].id) || 0)
            end

          schedule.visits.create!(
            patient: slot[:patient],
            starts_at: slot[:starts_at],
            ends_at: slot[:ends_at],
            duration_minutes: ((slot[:ends_at] - slot[:starts_at]) / 60).to_i,
            status: :pending_patient_confirmation,
            position_in_day: index,
            drive_from_previous_minutes: drive_minutes,
            soft_constraint_override: slot[:soft_constraint_override],
            source: "optimizer"
          )

          previous_patient_id = slot[:patient].id
          current_point = point_for(slot[:patient])
        end
      end
    end

    def nearest_neighbor_order(slots, travel_matrix)
      return slots if slots.size <= 1

      remaining = slots.dup
      ordered = []
      current_point = start_point_for_day

      while remaining.any?
        closest = if current_point.blank?
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

    def retime_slots(ordered_slots, travel_matrix)
      return ordered_slots if ordered_slots.empty?

      date = ordered_slots.first[:date]
      current_minute = day_start_minute
      previous_patient_id = nil
      retimed = []

      ordered_slots.each do |slot|
        transit = if previous_patient_id.nil?
                    origin = start_point_for_day
                    origin.present? ? travel_minutes_from_point(origin, slot[:patient]) : 0
                  else
                    travel_matrix.dig(previous_patient_id, slot[:patient].id) || 0
                  end

        earliest_start = current_minute + transit
        duration = slot[:patient].visit_duration_minutes

        if earliest_start + duration > day_end_minute
          retimed << slot
        else
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(earliest_start)}")
          ends_at = starts_at + duration.minutes
          retimed << slot.merge(starts_at: starts_at, ends_at: ends_at)
          current_minute = earliest_start + duration
        end

        previous_patient_id = slot[:patient].id
      end

      retimed
    end

    def next_closest_slot(remaining:, current_point:)
      return remaining.first if current_point.blank?

      remaining.min_by do |slot|
        [
          travel_minutes_from_point(current_point, slot[:patient]),
          slot[:starts_at]
        ]
      end
    end

    def travel_minutes_from_point(origin_point, patient)
      return 0 if origin_point.blank?

      routing_client.travel_minutes(
        origin: origin_point,
        destination: point_for(patient)
      )
    end

    def point_for(patient)
      { lat: patient.latitude, lng: patient.longitude }
    end

    def start_point_for_day
      return start_point if start_point.present?
      return if clinician_profile.blank? || clinician_profile.home_latitude.blank? || clinician_profile.home_longitude.blank?

      {
        lat: clinician_profile.home_latitude,
        lng: clinician_profile.home_longitude
      }
    end

    def calculate_drive_metrics!(schedule)
      total_drive = schedule.visits.sum(:drive_from_previous_minutes)
      baseline = (total_drive * 1.35).ceil
      schedule.total_drive_minutes = total_drive
      schedule.baseline_drive_minutes = baseline
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

    def minute_to_hhmm(minute)
      "%<hour>02d:%<minute>02d" % { hour: minute / 60, minute: minute % 60 }
    end

    def evenly_spaced_day_offsets(required_visits_per_week)
      visit_count = required_visits_per_week.to_i
      day_offsets = available_workday_offsets
      return [ 0 ] if day_offsets.empty?
      return [ day_offsets.first ] if visit_count <= 1

      # Spread visit targets across selected working days only.
      step = (day_offsets.length - 1).to_f / (visit_count - 1)
      (0...visit_count).map do |index|
        day_offsets[(index * step).round]
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
      current_plan.count { |slot| slot[:date] == date }
    end

    def existing_days_for_patient(patient, current_plan)
      current_plan
        .select { |slot| slot[:patient].id == patient.id }
        .map { |slot| slot[:date] }
        .uniq
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
