class WeeklyAutoScheduleJob < ApplicationJob
  queue_as :default

  def perform
    User.joins(:clinician_profile).find_each do |user|
      generate_next_week(user)
    end
  end

  private

  def generate_next_week(user)
    profile = user.clinician_profile
    return unless profile.setup_completed_at.present?
    return unless profile.patients.active.any?

    Time.use_zone(profile.timezone) do
      next_week_start = (Date.current + 1.week).beginning_of_week(:monday)

      # Skip if a schedule already exists for next week
      existing = user.weekly_schedules.find_by(week_start_on: next_week_start)
      return if existing

      Scheduling::OptimizeDispatch.call(
        user: user,
        week_start_on: next_week_start
      )
    end
  rescue => e
    Rails.logger.error("Auto-schedule failed for user #{user.id}: #{e.message}")
  end
end
