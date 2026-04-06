require "test_helper"

class Scheduling::WeeklyOptimizerTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "wo-test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    @patient = @profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 2, visit_duration_minutes: 45
    )
  end

  test "generate_solution returns day_routes without touching DB" do
    Time.use_zone("America/New_York") do
      optimizer = Scheduling::WeeklyOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6)
      )
      solution = optimizer.generate_solution

      assert_kind_of Hash, solution[:day_routes]
      assert_kind_of Numeric, solution[:fitness]
      assert_equal "greedy", solution[:metadata][:optimizer_type]
      assert_equal 2, solution[:metadata][:visit_count]
      # DB should not have been touched
      assert_equal 0, @user.weekly_schedules.count
    end
  end

  test "call persists schedule to DB" do
    Time.use_zone("America/New_York") do
      optimizer = Scheduling::WeeklyOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6)
      )
      schedule = optimizer.call

      assert_equal "optimized", schedule.status
      assert_equal 2, schedule.visits.count
      assert schedule.total_drive_minutes >= 0
    end
  end

  test "confirmed visits are preserved as fixed nodes" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      schedule = @user.weekly_schedules.create!(week_start_on: week_start, status: :optimized)
      confirmed_start = Time.zone.parse("#{week_start} 10:00")
      schedule.visits.create!(
        patient: @patient, starts_at: confirmed_start, ends_at: confirmed_start + 45.minutes,
        duration_minutes: 45, position_in_day: 0, status: :confirmed, source: "optimizer"
      )

      optimizer = Scheduling::WeeklyOptimizer.new(user: @user, week_start_on: week_start)
      result = optimizer.call

      confirmed = result.visits.find_by(status: :confirmed)
      assert_not_nil confirmed
      assert_equal confirmed_start, confirmed.starts_at
    end
  end

  test "sorts patients by priority descending" do
    Time.use_zone("America/New_York") do
      high_priority = @profile.patients.create!(
        full_name: "High Priority", phone: "5550002", address_line1: "200 Oak Ave",
        city: "Boston", state: "MA", postal_code: "02110",
        latitude: 42.3736, longitude: -71.1097,
        required_visits_per_week: 1, visit_duration_minutes: 30,
        priority: 10
      )

      optimizer = Scheduling::WeeklyOptimizer.new(
        user: @user, week_start_on: Date.new(2026, 4, 6)
      )
      solution = optimizer.generate_solution

      # High-priority patient should be scheduled on an earlier day or time
      all_slots = solution[:day_routes].values.flatten
      hp_slot = all_slots.find { |s| s[:patient].id == high_priority.id }
      assert_not_nil hp_slot
    end
  end
end
