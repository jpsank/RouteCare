require "test_helper"

class Scheduling::FeasibilityCheckerTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "fc-test@example.com", password: "password123")
    @profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient_a = @profile.patients.create!(
      full_name: "Patient A", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
    @travel_matrix = {
      @patient_a.id => { @patient_a.id => 0 }
    }
    @date = Date.new(2026, 4, 6)
    @instances = [
      Scheduling::VisitInstance.new(
        id: "patient_#{@patient_a.id}_visit_0",
        patient_id: @patient_a.id, patient: @patient_a,
        location: { lat: @patient_a.latitude, lng: @patient_a.longitude },
        duration: 45, priority: 0, availability_windows: {}
      )
    ]
  end

  test "valid schedule passes all checks" do
    Time.use_zone("America/New_York") do
      starts_at = Time.zone.parse("#{@date} 09:00")
      day_routes = {
        @date => [ { patient: @patient_a, starts_at: starts_at, ends_at: starts_at + 45.minutes, instance_id: @instances[0].id, drive_from_previous_minutes: 0 } ]
      }
      lunch = { @date => { start_minute: 720, end_minute: 750 } }

      result = build_checker.check(day_routes: day_routes, lunch_placements: lunch)
      assert result.feasible, "Expected feasible but got: #{result.violations.map(&:message)}"
    end
  end

  test "detects missing instance assignment" do
    Time.use_zone("America/New_York") do
      result = build_checker.check(day_routes: {}, lunch_placements: {})
      refute result.feasible
      assert result.violations.any? { |v| v.constraint == :all_assigned }
    end
  end

  test "detects duplicate patient on same day" do
    Time.use_zone("America/New_York") do
      starts_at = Time.zone.parse("#{@date} 09:00")
      day_routes = {
        @date => [
          { patient: @patient_a, starts_at: starts_at, ends_at: starts_at + 45.minutes, instance_id: @instances[0].id, drive_from_previous_minutes: 0 },
          { patient: @patient_a, starts_at: starts_at + 2.hours, ends_at: starts_at + 2.hours + 45.minutes, instance_id: "extra", drive_from_previous_minutes: 0 }
        ]
      }
      lunch = { @date => { start_minute: 720, end_minute: 750 } }

      result = build_checker.check(day_routes: day_routes, lunch_placements: lunch)
      assert result.violations.any? { |v| v.constraint == :one_per_day }
    end
  end

  test "detects workday bounds violation" do
    Time.use_zone("America/New_York") do
      starts_at = Time.zone.parse("#{@date} 06:00")
      day_routes = {
        @date => [ { patient: @patient_a, starts_at: starts_at, ends_at: starts_at + 45.minutes, instance_id: @instances[0].id, drive_from_previous_minutes: 0 } ]
      }
      lunch = { @date => { start_minute: 720, end_minute: 750 } }

      result = build_checker.check(day_routes: day_routes, lunch_placements: lunch)
      assert result.violations.any? { |v| v.constraint == :workday_bounds }
    end
  end

  test "detects visit overlapping an unavailable window" do
    Time.use_zone("America/New_York") do
      instances = [
        Scheduling::VisitInstance.new(
          id: "patient_#{@patient_a.id}_visit_0",
          patient_id: @patient_a.id, patient: @patient_a,
          location: { lat: @patient_a.latitude, lng: @patient_a.longitude },
          duration: 45, priority: 0, availability_windows: {},
          unavailability_windows: { @date.wday => [ { start_minute: 540, end_minute: 600 } ] }
        )
      ]
      starts_at = Time.zone.parse("#{@date} 09:00")
      day_routes = {
        @date => [ { patient: @patient_a, starts_at: starts_at, ends_at: starts_at + 45.minutes, instance_id: instances[0].id, drive_from_previous_minutes: 0 } ]
      }
      lunch = { @date => { start_minute: 720, end_minute: 750 } }

      result = build_checker(instances: instances).check(day_routes: day_routes, lunch_placements: lunch)
      assert result.violations.any? { |v| v.constraint == :unavailability_window }
    end
  end

  private

  def build_checker(instances: @instances)
    Scheduling::FeasibilityChecker.new(
      travel_matrix: @travel_matrix,
      clinician_profile: @profile,
      fixed_nodes: [],
      instances: instances
    )
  end
end
