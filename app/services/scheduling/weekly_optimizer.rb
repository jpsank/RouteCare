module Scheduling
  class WeeklyOptimizer
    DAY_START_MINUTE = 8 * 60
    DAY_END_MINUTE = 18 * 60

    def initialize(user:, week_start_on:)
      @user = user
      @week_start_on = week_start_on.to_date.beginning_of_week(:monday)
      @clinician_profile = user.clinician_profile
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

    attr_reader :user, :week_start_on, :clinician_profile

    def build_visit_plan
      blocked_ranges = Scheduling::CalendarConstraints.new(user:, week_start_on:).blocked_ranges_by_day
      plan = []
      soft_constraint_count = 0

      clinician_profile.patients.active.includes(:patient_availability_windows).each do |patient|
        patient.required_visits_per_week.times do
          slot = find_best_slot(patient:, blocked_ranges:, current_plan: plan)
          soft_constraint_count += 1 if slot[:soft_constraint_override]
          plan << slot
          blocked_ranges[slot[:date]] << (slot[:starts_at]...slot[:ends_at])
        end
      end

      [plan.sort_by { |slot| slot[:starts_at] }, soft_constraint_count]
    end

    def find_best_slot(patient:, blocked_ranges:, current_plan:)
      candidates = []
      weekly_days.each do |date|
        patient_windows_for_day = preferred_windows_for(patient, date)
        window_set = patient_windows_for_day.presence || fallback_windows_for(date)

        window_set.each do |window|
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(window.start_minute)}")
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          next if ends_at > Time.zone.parse("#{date} #{minute_to_hhmm(window.end_minute)}")
          next if overlaps_blocked?(starts_at, ends_at, blocked_ranges[date])
          next if overlaps_plan?(starts_at, ends_at, current_plan)

          candidates << {
            patient:,
            date:,
            starts_at:,
            ends_at:,
            soft_constraint_override: patient_windows_for_day.blank?
          }
        end
      end

      if candidates.empty?
        fallback_slot(patient:, blocked_ranges:, current_plan:)
      else
        candidates.min_by { |candidate| [candidate[:date], candidate[:starts_at]] }
      end
    end

    def fallback_slot(patient:, blocked_ranges:, current_plan:)
      weekly_days.each do |date|
        start_minute = DAY_START_MINUTE
        while start_minute + patient.visit_duration_minutes <= DAY_END_MINUTE
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(start_minute)}")
          ends_at = starts_at + patient.visit_duration_minutes.minutes
          unless overlaps_blocked?(starts_at, ends_at, blocked_ranges[date]) || overlaps_plan?(starts_at, ends_at, current_plan)
            return {
              patient:,
              date:,
              starts_at:,
              ends_at:,
              soft_constraint_override: true
            }
          end
          start_minute += 30
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
      [Scheduling::TimeWindow.new(DAY_START_MINUTE, DAY_END_MINUTE)]
    end

    def create_visits!(schedule, visit_plan, travel_matrix)
      grouped = visit_plan.group_by { |slot| slot[:starts_at].to_date }

      grouped.each_value do |slots|
        sorted = slots.sort_by { |slot| [slot[:patient].latitude || 999, slot[:patient].longitude || 999, slot[:starts_at]] }
        previous_patient_id = nil

        sorted.each_with_index do |slot, index|
          drive_minutes = if previous_patient_id.nil?
                            0
                          else
                            travel_matrix.dig(previous_patient_id, slot[:patient].id) || 0
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
        end
      end
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

    def overlaps_plan?(starts_at, ends_at, current_plan)
      current_plan.any? { |slot| starts_at < slot[:ends_at] && ends_at > slot[:starts_at] }
    end

    def weekly_days
      (0..6).map { |offset| week_start_on + offset.days }
    end

    def minute_to_hhmm(minute)
      "%<hour>02d:%<minute>02d" % { hour: minute / 60, minute: minute % 60 }
    end
  end
end
