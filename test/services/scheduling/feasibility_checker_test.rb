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
    @patient_b = @profile.patients.create!(
      full_name: "Patient B", phone: "5550002", address_line1: "200 Oak Ave",
      city: "Cambridge", state: "MA", postal_code: "02139",
      latitude: 42.3736, longitude: -71.1097,
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

  test "checks transit feasibility against the bucket matching the previous visit's actual end time" do
    Time.use_zone("America/New_York") do
      bucketed_matrix = Scheduling::BucketedTravelMatrix.new(
        morning_rush: {
          @patient_a.id => { @patient_a.id => 0, @patient_b.id => 5 },
          @patient_b.id => { @patient_a.id => 5, @patient_b.id => 0 }
        },
        off_peak: {
          @patient_a.id => { @patient_a.id => 0, @patient_b.id => 50 },
          @patient_b.id => { @patient_a.id => 50, @patient_b.id => 0 }
        }
      )

      # Patient A's visit ends at 8:00am (480min, inside morning rush) with
      # only a 10min gap before patient B — enough for the 5min morning-rush
      # transit but nowhere near the 50min off-peak transit.
      first_ends_at = Time.zone.parse("#{@date} 08:00")
      day_routes = {
        @date => [
          { patient: @patient_a, starts_at: first_ends_at - 45.minutes, ends_at: first_ends_at, instance_id: @instances[0].id, drive_from_previous_minutes: 0 },
          { patient: @patient_b, starts_at: first_ends_at + 10.minutes, ends_at: first_ends_at + 10.minutes + 45.minutes, instance_id: "patient_b_visit", drive_from_previous_minutes: 5 }
        ]
      }
      lunch = { @date => { start_minute: 720, end_minute: 750 } }

      instances = @instances + [
        Scheduling::VisitInstance.new(
          id: "patient_b_visit", patient_id: @patient_b.id, patient: @patient_b,
          location: { lat: @patient_b.latitude, lng: @patient_b.longitude },
          duration: 45, priority: 0, availability_windows: {}
        )
      ]

      checker = Scheduling::FeasibilityChecker.new(
        travel_matrix: bucketed_matrix, clinician_profile: @profile, fixed_nodes: [], instances: instances
      )
      result = checker.check(day_routes: day_routes, lunch_placements: lunch)

      refute result.violations.any? { |v| v.constraint == :transit_feasibility },
        "Expected the morning-rush bucket (5min) to make the 10min gap feasible, but got: #{result.violations.map(&:message)}"
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
