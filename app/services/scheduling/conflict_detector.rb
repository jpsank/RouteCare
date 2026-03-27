module Scheduling
  class ConflictDetector
    def initialize(user:)
      @user = user
    end

    def run(schedule: latest_schedule)
      return [] if schedule.blank?

      windows = CalendarBlock.where(user: @user)
                             .where(starts_at: schedule.week_start_on.beginning_of_day..(schedule.week_start_on + 6.days).end_of_day)
      conflicts = []

      schedule.visits.each do |visit|
        overlap = windows.find { |block| overlaps?(visit.starts_at, visit.ends_at, block.starts_at, block.ends_at) }
        next unless overlap

        conflicts << {
          visit_id: visit.id,
          patient_id: visit.patient_id,
          block_id: overlap.id,
          block_title: overlap.title,
          visit: visit,
          message: "Visit with #{visit.patient.full_name} conflicts with '#{overlap.title || "calendar event"}'."
        }
      end

      conflicts
    end

    private

    def latest_schedule
      @user.weekly_schedules.order(week_start_on: :desc).first
    end

    def overlaps?(a_start, a_end, b_start, b_end)
      a_start < b_end && b_start < a_end
    end
  end
end
