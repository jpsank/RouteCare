require "test_helper"

class Scheduling::GaOptimizerTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "ga-test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    @profile.patients.create!(
      full_name: "Patient A", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 2, visit_duration_minutes: 45
    )
    @profile.patients.create!(
      full_name: "Patient B", phone: "5550002", address_line1: "200 Oak Ave",
      city: "Cambridge", state: "MA", postal_code: "02139",
      latitude: 42.3736, longitude: -71.1097,
      required_visits_per_week: 1, visit_duration_minutes: 60
    )
  end

  test "generate_solution produces valid schedule" do
    Time.use_zone("America/New_York") do
      optimizer = Scheduling::GaOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6),
        time_budget: 5, population_size: 10
      )
      solution = optimizer.generate_solution

      assert_kind_of Hash, solution[:day_routes]
      assert_kind_of Numeric, solution[:fitness]
      assert_equal "ga", solution[:metadata][:optimizer_type]
      assert solution[:metadata][:generations_run] >= 1

      # Should have 3 total visits (2 for patient A + 1 for patient B)
      total_visits = solution[:day_routes].values.flatten.size
      assert_equal 3, total_visits
    end
  end

  test "GA never produces worse result than greedy" do
    Time.use_zone("America/New_York") do
      optimizer = Scheduling::GaOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6),
        time_budget: 5, population_size: 10
      )
      solution = optimizer.generate_solution

      greedy = Scheduling::WeeklyOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6)
      ).generate_solution

      assert solution[:fitness] <= greedy[:fitness],
        "GA fitness #{solution[:fitness]} should be <= greedy #{greedy[:fitness]}"
    end
  end

  test "respects time budget" do
    Time.use_zone("America/New_York") do
      start_time = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      optimizer = Scheduling::GaOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6),
        time_budget: 3, population_size: 10
      )
      optimizer.generate_solution
      elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - start_time

      # Allow some overhead beyond the budget
      assert elapsed < 10, "Should finish within reasonable time (took #{elapsed.round(1)}s)"
    end
  end

  test "call persists schedule to DB" do
    Time.use_zone("America/New_York") do
      optimizer = Scheduling::GaOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6),
        time_budget: 3, population_size: 10
      )
      schedule = optimizer.call

      assert_equal "optimized", schedule.status
      assert schedule.visits.count > 0
    end
  end
end
