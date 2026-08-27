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

      schedule = @user.weekly_schedules.find_by!(week_start_on: week_start)
      instance_ids = schedule.visits.order(:starts_at).filter_map(&:instance_id)
      assert_equal input.instances.map(&:id).sort, instance_ids.sort,
        "optimizer visits should persist instance_id for CP-SAT warm-start"
    end
  end

  test "from_schedule reuses the prior plan when the instance set is genuinely identical" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      Scheduling::WeeklyOptimizer.new(user: @user, week_start_on: week_start).call

      # Rebuilding input from the exact same patients/profile produces the exact same
      # instance id set, so warm-start should be reused.
      input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)

      result = Scheduling::WarmStartOutput.from_schedule(
        user: @user, week_start_on: week_start, input: input
      )

      assert_not_nil result, "expected warm-start reuse when instance ids match exactly"
      assert_equal input.instances.map(&:id).sort, result.planned_visits.map(&:instance_id).sort
    end
  end

  test "from_schedule does not reuse the prior plan when instance count matches but identities differ" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)

      second_patient = @profile.patients.create!(
        full_name: "John Roe", phone: "5550002", address_line1: "200 Main St",
        city: "Boston", state: "MA", postal_code: "02110",
        latitude: 42.36, longitude: -71.06,
        required_visits_per_week: 1, visit_duration_minutes: 30
      )
      @patient.update!(required_visits_per_week: 1)

      Scheduling::WeeklyOptimizer.new(user: @user, week_start_on: week_start).call
      old_schedule = @user.weekly_schedules.find_by!(week_start_on: week_start)
      old_instance_ids = old_schedule.visits.filter_map(&:instance_id).sort
      assert_equal 2, old_instance_ids.size

      # Same total instance count (2 -> 2), but @patient is deactivated and a brand-new
      # patient takes its place — the underlying set of instances is not the same.
      @patient.update!(active: false)
      third_patient = @profile.patients.create!(
        full_name: "New Patient", phone: "5550003", address_line1: "300 Main St",
        city: "Boston", state: "MA", postal_code: "02110",
        latitude: 42.37, longitude: -71.07,
        required_visits_per_week: 1, visit_duration_minutes: 30
      )

      new_input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)
      assert_equal old_instance_ids.size, new_input.instances.size,
        "test setup should keep the total instance count unchanged"
      assert_not_equal old_instance_ids, new_input.instances.map(&:id).sort,
        "test setup should change which instances make up that count"

      result = Scheduling::WarmStartOutput.from_schedule(
        user: @user, week_start_on: week_start, input: new_input
      )

      assert_nil result, "warm-start must not be reused when instance identities differ, even at equal count"
      assert third_patient.persisted?
      assert second_patient.persisted?
    end
  end

  test "from_schedule does not reuse the prior plan when the fingerprint shows the problem changed" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      @patient.update!(required_visits_per_week: 1)

      Scheduling::WeeklyOptimizer.new(user: @user, week_start_on: week_start).call
      schedule = @user.weekly_schedules.find_by!(week_start_on: week_start)

      old_input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)
      # Simulate this schedule having been produced by SolverRunner, which stamps a
      # fingerprint of the solver-relevant input onto the schedule.
      schedule.update!(
        optimization_summary: schedule.optimization_summary.merge(
          "solver_input_fingerprint" => Scheduling::SolverInputFingerprint.compute(old_input)
        )
      )

      # Patient set and required_visits_per_week are unchanged (so the instance id set
      # is byte-identical), but the patient moved far away — a materially different
      # routing problem that the instance-id comparison alone can't detect.
      @patient.update!(latitude: 44.0, longitude: -73.0)
      new_input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)
      assert_equal old_input.instances.map(&:id).sort, new_input.instances.map(&:id).sort,
        "test setup should keep instance ids identical"

      result = Scheduling::WarmStartOutput.from_schedule(
        user: @user, week_start_on: week_start, input: new_input
      )

      assert_nil result, "warm-start must not be reused once the fingerprinted inputs diverge"
    end
  end
end
