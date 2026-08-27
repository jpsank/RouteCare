class WeeklyAutoScheduleJob < ApplicationJob
  queue_as :default

  # How many weeks ahead of "now" auto-scheduling keeps populated with a generated
  # schedule. This job runs weekly (see config/recurring.yml), and each run tries to
  # extend the horizon by one more week -- so the effective behavior is a rolling
  # N-week window that always sits HORIZON_WEEKS ahead of the current week, for as
  # long as a clinician/patient stays active (see #generate_week's activity checks).
  #
  # Override via ENV for ops tuning without a deploy; falls back to this default.
  HORIZON_WEEKS = ENV.fetch("ROUTECARE_AUTO_SCHEDULE_HORIZON_WEEKS", 4).to_i

  # Entry point invoked by the recurring schedule (no arguments). Fans out into one
  # job per week offset so that Solid Queue retries/failures are isolated per week --
  # a failure generating week 3 doesn't block or re-run week 1, 2, or 4 -- and each
  # week's generation across all clinicians is a separate, independently-retryable
  # unit of work rather than one long synchronous loop.
  #
  # When called with an explicit week_offset (by the fan-out above, or directly in
  # tests), it does the actual per-user generation work for that single week.
  def perform(week_offset = nil)
    if week_offset.nil?
      (1..HORIZON_WEEKS).each { |offset| self.class.perform_later(offset) }
      return
    end

    User.joins(:clinician_profile).find_each do |user|
      generate_week(user, week_offset)
    end
  end

  private

  def generate_week(user, week_offset)
    profile = user.clinician_profile
    return unless profile.setup_completed_at.present?
    return unless profile.patients.active.any?

    Time.use_zone(profile.timezone) do
      week_start = (Date.current + week_offset.weeks).beginning_of_week(:monday)

      # Skip if a schedule already exists for this week. This is what makes the
      # rolling horizon additive rather than destructive: once a week has been
      # generated (by a prior run reaching this offset), we never touch it again
      # here, so a clinician's review/edits/approval on that week's schedule are
      # never silently clobbered by a later auto-schedule run. The horizon only
      # ever grows by generating the newly-in-range week at the far edge; nearer
      # weeks were already filled in by earlier runs.
      existing = user.weekly_schedules.find_by(week_start_on: week_start)
      return if existing

      Scheduling::OptimizeDispatch.call(
        user: user,
        week_start_on: week_start
      )
    end
  rescue => e
    Rails.logger.error("Auto-schedule failed for user #{user.id}, week_offset #{week_offset}: #{e.message}")
  end
end
