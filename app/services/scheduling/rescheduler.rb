module Scheduling
  class Rescheduler
    def initialize(visit:, starts_at:, actor:)
      @visit = visit
      @starts_at = starts_at
      @actor = actor
    end

    def call
      ensure_no_calendar_conflict!

      duration = visit.duration_minutes.minutes
      visit.starts_at = starts_at
      visit.ends_at = starts_at + duration
      visit.source = "reschedule"
      visit.status = "pending_patient_confirmation"
      visit.clinician_override = true
      visit.save!

      resequence_day!
      visit
    end

    private

    attr_reader :visit, :starts_at, :actor

    def ensure_no_calendar_conflict!
      overlaps = CalendarBlock.where(user: visit.weekly_schedule.user)
                              .where("starts_at < ? AND ends_at > ?", starts_at + visit.duration_minutes.minutes, starts_at)
      return if overlaps.none?

      raise ActiveRecord::RecordInvalid, visit.tap { |v| v.errors.add(:base, "Proposed time overlaps a blocked calendar event") }
    end

    def resequence_day!
      day_visits = visit.weekly_schedule.visits
        .where(starts_at: starts_at.beginning_of_day..starts_at.end_of_day)
        .order(:starts_at)

      previous = nil
      day_visits.each_with_index do |day_visit, index|
        day_visit.update!(
          position_in_day: index,
          drive_from_previous_minutes: previous ? 15 : 0
        )
        previous = day_visit
      end
    end
  end
end
