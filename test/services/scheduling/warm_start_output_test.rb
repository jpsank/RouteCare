# frozen_string_literal: true

require "test_helper"

class Scheduling::WarmStartOutputTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "ws-test@example.com", password: "password123")
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

  test "from_schedule returns SolverOutputData matching persisted visits" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      Scheduling::WeeklyOptimizer.new(user: @user, week_start_on: week_start).call
      input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)

      prior = Scheduling::WarmStartOutput.from_schedule(
        user: @user, week_start_on: week_start, input: input
      )

      assert_not_nil prior
      assert_equal input.instances.size, prior.planned_visits.size
      assert prior.planned_visits.all? { |pv| pv.instance_id.match?(/\Apatient_\d+_visit_\d+\z/) }
      assert prior.fitness >= 0
    end
  end
end
