# frozen_string_literal: true

require "test_helper"

class Scheduling::SolverInputFingerprintTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "fp-test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    @patient = @profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
  end

  def build_input(week_start)
    Scheduling::SolverInput.build(user: @user, week_start_on: week_start)
  end

  test "is stable across rebuilds with no underlying changes" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      a = Scheduling::SolverInputFingerprint.compute(build_input(week_start))
      b = Scheduling::SolverInputFingerprint.compute(build_input(week_start))
      assert_equal a, b
    end
  end

  test "changes when a patient's location changes" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      before = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      @patient.update!(latitude: 44.0, longitude: -73.0)
      after = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      assert_not_equal before, after
    end
  end

  test "changes when the clinician's working hours change" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      before = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      @profile.update!(workday_start_minute: @profile.workday_start_minute + 60)
      after = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      assert_not_equal before, after
    end
  end

  test "changes when a calendar block is added" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      before = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      @user.calendar_blocks.create!(
        source: "manual_block",
        starts_at: week_start.to_time.change(hour: 9),
        ends_at: week_start.to_time.change(hour: 10)
      )
      after = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      assert_not_equal before, after
    end
  end

  test "changes when a patient's unavailability (blackout) window changes" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      before = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      @patient.patient_availability_windows.create!(
        day_of_week: 1, start_minute: 600, end_minute: 720, available: false
      )
      after = Scheduling::SolverInputFingerprint.compute(build_input(week_start))

      assert_not_equal before, after
    end
  end

  test "is unaffected by patient enumeration order" do
    Time.use_zone("America/New_York") do
      week_start = Date.new(2026, 4, 6)
      second = @profile.patients.create!(
        full_name: "John Roe", phone: "5550002", address_line1: "200 Main St",
        city: "Boston", state: "MA", postal_code: "02110",
        latitude: 42.36, longitude: -71.06,
        required_visits_per_week: 1, visit_duration_minutes: 30
      )

      input = build_input(week_start)
      fingerprint = Scheduling::SolverInputFingerprint.compute(input)

      reordered = input.with(patients: input.patients.reverse)
      assert_equal fingerprint, Scheduling::SolverInputFingerprint.compute(reordered)
      assert second.persisted?
    end
  end
end
