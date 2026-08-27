require "test_helper"

class WeeklyAutoScheduleJobTest < ActiveJob::TestCase
  setup do
    # Force the greedy backend: these tests exercise job/horizon behavior, not the
    # CP-SAT solver integration (which requires the Python microservice), so avoid
    # any dependency on it being reachable in the test environment.
    ENV["ROUTECARE_SCHEDULER_BACKEND"] = "greedy"
    @user = build_active_user(email: "auto-schedule@example.com")
  end

  teardown do
    ENV.delete("ROUTECARE_SCHEDULER_BACKEND")
  end

  test "no-arg perform fans out one job per horizon week" do
    assert_enqueued_jobs WeeklyAutoScheduleJob::HORIZON_WEEKS, only: WeeklyAutoScheduleJob do
      WeeklyAutoScheduleJob.perform_now
    end

    enqueued_offsets = enqueued_jobs
      .select { |job| job["job_class"] == "WeeklyAutoScheduleJob" }
      .map { |job| job["arguments"].first }
      .sort

    assert_equal (1..WeeklyAutoScheduleJob::HORIZON_WEEKS).to_a, enqueued_offsets
  end

  test "generates schedules for multiple upcoming weeks, one per offset" do
    Time.use_zone(@user.clinician_profile.timezone) do
      today = Date.current

      (1..WeeklyAutoScheduleJob::HORIZON_WEEKS).each do |offset|
        WeeklyAutoScheduleJob.perform_now(offset)
      end

      expected_weeks = (1..WeeklyAutoScheduleJob::HORIZON_WEEKS).map do |offset|
        (today + offset.weeks).beginning_of_week(:monday)
      end

      actual_weeks = @user.weekly_schedules.pluck(:week_start_on).sort
      assert_equal expected_weeks.sort, actual_weeks
      assert_equal WeeklyAutoScheduleJob::HORIZON_WEEKS, @user.weekly_schedules.count
    end
  end

  test "does not generate a week for a clinician with no active patients (deactivated)" do
    @user.clinician_profile.patients.update_all(active: false)

    WeeklyAutoScheduleJob.perform_now(1)

    assert_equal 0, @user.weekly_schedules.count
  end

  test "does not generate a week for a clinician whose setup is incomplete" do
    @user.clinician_profile.update!(setup_completed_at: nil)

    WeeklyAutoScheduleJob.perform_now(1)

    assert_equal 0, @user.weekly_schedules.count
  end

  test "does not regenerate or clobber a week that already has a schedule" do
    Time.use_zone(@user.clinician_profile.timezone) do
      week_start = (Date.current + 1.week).beginning_of_week(:monday)
      existing = @user.weekly_schedules.create!(
        week_start_on: week_start, status: :approved,
        total_drive_minutes: 0, baseline_drive_minutes: 0,
        optimization_summary: { "sentinel" => true }
      )

      WeeklyAutoScheduleJob.perform_now(1)

      existing.reload
      assert_equal 1, @user.weekly_schedules.count
      assert_equal "approved", existing.status
      assert_equal({ "sentinel" => true }, existing.optimization_summary)
    end
  end

  private

  def build_active_user(email:)
    user = User.create!(email: email, password: "password123")
    profile = user.create_clinician_profile!(
      discipline: "Physical Therapist", timezone: "America/New_York",
      setup_completed_at: Time.current,
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    profile.patients.create!(
      full_name: "Jane Doe", phone: "5554001001", email: "jane@example.com",
      address_line1: "100 Main St", city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45, active: true
    )
    user
  end
end
