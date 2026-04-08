# frozen_string_literal: true

require "test_helper"

class Scheduling::OptimizeDispatchTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "od-test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    @profile.patients.create!(
      full_name: "Jane Doe", phone: "5550101", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
    @week = Date.new(2026, 4, 6)
  end

  test "backend defaults to cpsat" do
    ENV.delete("ROUTECARE_SCHEDULER_BACKEND")
    assert_equal :cpsat, Scheduling::OptimizeDispatch.backend
  end

  test "backend greedy when ENV set" do
    ENV["ROUTECARE_SCHEDULER_BACKEND"] = "greedy"
    assert_equal :greedy, Scheduling::OptimizeDispatch.backend
  ensure
    ENV.delete("ROUTECARE_SCHEDULER_BACKEND")
  end

  test "cpsat_time_budget defaults to 60" do
    ENV.delete("ROUTECARE_CPSAT_TIME_BUDGET")
    ENV.delete("ROUTECARE_SCHEDULE_QUALITY")
    assert_equal 60, Scheduling::OptimizeDispatch.cpsat_time_budget
  end

  test "cpsat_time_budget is clamped to minimum 10" do
    ENV["ROUTECARE_CPSAT_TIME_BUDGET"] = "3"
    assert_equal 10, Scheduling::OptimizeDispatch.cpsat_time_budget
  ensure
    ENV.delete("ROUTECARE_CPSAT_TIME_BUDGET")
  end

  test "cpsat_time_budget uses schedule quality preset when time budget not set" do
    ENV.delete("ROUTECARE_CPSAT_TIME_BUDGET")
    ENV["ROUTECARE_SCHEDULE_QUALITY"] = "deep"
    assert_equal 120, Scheduling::OptimizeDispatch.cpsat_time_budget
  ensure
    ENV.delete("ROUTECARE_SCHEDULE_QUALITY")
  end

  test "explicit cpsat time budget wins over schedule quality" do
    ENV["ROUTECARE_SCHEDULE_QUALITY"] = "fast"
    ENV["ROUTECARE_CPSAT_TIME_BUDGET"] = "90"
    assert_equal 90, Scheduling::OptimizeDispatch.cpsat_time_budget
  ensure
    ENV.delete("ROUTECARE_SCHEDULE_QUALITY")
    ENV.delete("ROUTECARE_CPSAT_TIME_BUDGET")
  end

  test "cpsat path falls back to greedy when solver raises" do
    ENV["ROUTECARE_SCHEDULER_BACKEND"] = "cpsat"
    ENV["ROUTECARE_TEST_CPSAT_FAIL"] = "1"

    Time.use_zone("America/New_York") do
      schedule = Scheduling::OptimizeDispatch.call(user: @user, week_start_on: @week)

      assert_equal "optimized", schedule.status
      assert_equal true, schedule.optimization_summary["scheduler_fallback"]
      assert_equal "Scheduling::RemoteSolverError", schedule.optimization_summary["scheduler_fallback_error_class"]
      assert_match(/simulated CP-SAT failure/, schedule.optimization_summary["scheduler_fallback_reason"])
      assert_equal "greedy", schedule.optimization_summary["optimizer_type"]
    end
  ensure
    ENV.delete("ROUTECARE_SCHEDULER_BACKEND")
    ENV.delete("ROUTECARE_TEST_CPSAT_FAIL")
  end
end
