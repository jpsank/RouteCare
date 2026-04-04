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
        Alert.find_or_create_by!(
          user: user,
          category: "unconfirmed_visit",
          status: "open",
          message: "Visit with #{visit.patient.full_name} is unconfirmed and within 48 hours.",
          due_at: visit.starts_at,
          metadata: { visit_id: visit.id }
        )
      end
    end

    def generate_calendar_conflict_alerts
      conflicts = Scheduling::ConflictDetector.new(user:).run
      conflicts.each do |conflict|
        Alert.find_or_create_by!(
          user:,
          category: "calendar_conflict",
          status: "open",
          message: conflict.fetch(:message),
          due_at: conflict[:visit]&.starts_at,
          metadata: conflict.except(:message)
        )
      end
    end

    def generate_significant_change_alerts
      schedule = user.weekly_schedules.order(created_at: :desc).first
      return if schedule.blank?

      baseline = schedule.baseline_drive_minutes
      optimized = schedule.total_drive_minutes
      return unless baseline.positive?

      percentage = (((baseline - optimized).to_f / baseline) * 100).round
      return unless percentage.abs >= 20

      Alert.find_or_create_by!(
        user: user,
        category: "schedule_shift",
        status: "open",
        message: "Route efficiency changed by #{percentage}% for week of #{schedule.week_start_on}.",
        metadata: {
          weekly_schedule_id: schedule.id,
          baseline_drive_minutes: baseline,
          optimized_drive_minutes: optimized
        }
      )
    end
  end
end
