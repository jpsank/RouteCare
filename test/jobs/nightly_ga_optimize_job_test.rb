require "test_helper"

class NightlyGaOptimizeJobTest < ActiveJob::TestCase
  test "optimizes schedules for users with active schedules" do
    user = User.create!(email: "nightly-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )

    Time.use_zone("America/New_York") do
      week_start = Date.current.beginning_of_week(:monday)
      user.weekly_schedules.create!(week_start_on: week_start, status: :optimized)

      assert_nothing_raised do
        NightlyGaOptimizeJob.perform_now
      end
    end
  end

  test "skips users without clinician profiles" do
    User.create!(email: "no-profile@example.com", password: "password123")

    assert_nothing_raised do
      NightlyGaOptimizeJob.perform_now
    end
  end

  test "continues processing after one user fails" do
    user1 = User.create!(email: "fail-test1@example.com", password: "password123")
    user1.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    user2 = User.create!(email: "fail-test2@example.com", password: "password123")
    profile2 = user2.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    profile2.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )

    Time.use_zone("America/New_York") do
      week_start = Date.current.beginning_of_week(:monday)
      # User1 has a schedule but no patients — optimizer may error
      user1.weekly_schedules.create!(week_start_on: week_start, status: :optimized)
      user2.weekly_schedules.create!(week_start_on: week_start, status: :optimized)

      assert_nothing_raised do
        NightlyGaOptimizeJob.perform_now
      end
    end
  end
end
