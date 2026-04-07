require "test_helper"

class Api::V1::SchedulesControllerTest < ActionDispatch::IntegrationTest
  include Devise::Test::IntegrationHelpers

  test "optimize marks generated schedule as optimized" do
    user = build_user(email: "optimizer@example.com")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001001",
      email: "jane@example.com",
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 1,
      visit_duration_minutes: 45,
      latitude: 42.3589,
      longitude: -71.0589
    )

    sign_in user

    post optimize_api_v1_schedule_path, params: { client_timezone: "America/New_York" }, as: :json

    assert_response :created
    assert_equal "optimized", user.weekly_schedules.order(:created_at).last.status
  end

  test "optimize falls back to greedy when CP-SAT path fails" do
    user = build_user(email: "optimizer-fallback@example.com")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001002",
      email: "jane2@example.com",
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 1,
      visit_duration_minutes: 45,
      latitude: 42.3589,
      longitude: -71.0589
    )

    ENV["ROUTECARE_SCHEDULER_BACKEND"] = "cpsat"
    ENV["ROUTECARE_TEST_CPSAT_FAIL"] = "1"

    sign_in user

    post optimize_api_v1_schedule_path, params: { client_timezone: "America/New_York" }, as: :json

    assert_response :created
    schedule = user.weekly_schedules.order(:created_at).last
    assert_equal "optimized", schedule.status
    assert_equal true, schedule.optimization_summary["scheduler_fallback"]
    assert_equal "greedy", schedule.optimization_summary["optimizer_type"]
  ensure
    ENV.delete("ROUTECARE_SCHEDULER_BACKEND")
    ENV.delete("ROUTECARE_TEST_CPSAT_FAIL")
  end

  test "approve updates schedule status to approved" do
    user = build_user(email: "scheduler@example.com")
    schedule = user.weekly_schedules.create!(week_start_on: Date.new(2026, 4, 6), status: :draft)

    sign_in user

    post approve_api_v1_schedule_path, params: { week_start_on: schedule.week_start_on.iso8601 }

    assert_response :success
    assert_equal "approved", schedule.reload.status
    assert AuditLog.exists?(user: user, action: "schedule_approved", auditable_type: "WeeklySchedule", auditable_id: schedule.id)
  end

  test "show defaults week using client timezone" do
    travel_to Time.utc(2026, 4, 6, 0, 30, 0) do
      user = build_user(email: "timezone@example.com")
      schedule = user.weekly_schedules.create!(week_start_on: Date.new(2026, 3, 30), status: :optimized)

      sign_in user

      get api_v1_schedule_path, params: { client_timezone: "Pacific/Honolulu" }

      assert_response :success
      assert_equal schedule.id, response.parsed_body.dig("schedule", "id")
    end
  end

  private

  def build_user(email:)
    User.create!(email:, password: "password123")
  end
end
