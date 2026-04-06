class NightlyGaOptimizeJob < ApplicationJob
  queue_as :default

  def perform
    User.joins(:clinician_profile).find_each do |user|
      optimize_for_user(user)
    end
  end

  private

  def optimize_for_user(user)
    Time.use_zone(user.clinician_profile.timezone) do
      week_start = Date.current.beginning_of_week(:monday)
      schedule = user.weekly_schedules.find_by(week_start_on: week_start)
      return unless schedule&.optimized? || schedule&.draft?

      Scheduling::GaOptimizer.new(
        user: user,
        week_start_on: week_start,
        time_budget: 120,
        population_size: 50
      ).call
    end
  rescue => e
    Rails.logger.error("GA optimize failed for user #{user.id}: #{e.message}")
  end
end
