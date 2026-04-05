module Alerts
  class Generator
    def initialize(user:)
      @user = user
    end

    def run!
      generate_unconfirmed_visit_alerts
      generate_calendar_conflict_alerts
      generate_significant_change_alerts
    end

    private

    attr_reader :user

    def generate_unconfirmed_visit_alerts
      window_end = 48.hours.from_now
      visits = Visit.joins(:weekly_schedule)
        .where(weekly_schedules: { user_id: user.id })
        .where(status: "pending_patient_confirmation")
        .where(starts_at: Time.current..window_end)

      visits.find_each do |visit|
        alert = user.alerts.find_or_initialize_by(
          category: "unconfirmed_visit",
          metadata: { "visit_id" => visit.id }
        )
        next if alert.persisted? && alert.status_resolved?

        alert.assign_attributes(
          status: alert.new_record? ? "open" : alert.status,
          severity: "medium",
          message: "Visit with #{visit.patient.full_name} is unconfirmed and within 48 hours.",
          due_at: visit.starts_at
        )
        alert.save!
      end
    end

    def generate_calendar_conflict_alerts
      conflicts = Scheduling::ConflictDetector.new(user:).run
      conflicts.each do |conflict|
        visit = conflict[:visit]
        alert = user.alerts.find_or_initialize_by(
          category: "calendar_conflict",
          metadata: { "visit_id" => visit&.id }
        )
        next if alert.persisted? && alert.status_resolved?

        alert.assign_attributes(
          status: alert.new_record? ? "open" : alert.status,
          severity: "high",
          message: conflict.fetch(:message),
          due_at: visit&.starts_at
        )
        alert.save!
      end
    rescue StandardError => e
      Rails.logger.error("[Alerts::Generator] ConflictDetector error: #{e.message}")
    end

    def generate_significant_change_alerts
      schedule = user.weekly_schedules.order(created_at: :desc).first
      return if schedule.blank?

      baseline = schedule.baseline_drive_minutes
      optimized = schedule.total_drive_minutes
      return unless baseline.positive?

      percentage = (((baseline - optimized).to_f / baseline) * 100).round
      return unless percentage.abs >= 20

      alert = user.alerts.find_or_initialize_by(
        category: "schedule_shift",
        metadata: { "weekly_schedule_id" => schedule.id }
      )
      return if alert.persisted? && alert.status_resolved?

      alert.assign_attributes(
        status: alert.new_record? ? "open" : alert.status,
        severity: "medium",
        message: "Route efficiency changed by #{percentage}% for week of #{schedule.week_start_on}.",
        due_at: schedule.week_start_on.beginning_of_day
      )
      alert.save!
    end
  end
end
