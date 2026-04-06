require "test_helper"

class Scheduling::FitnessFunctionTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "ff-test@example.com", password: "password123")
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
      required_visits_per_week: 1, visit_duration_minutes: 60
    )
    @travel_matrix = {
      @patient_a.id => { @patient_a.id => 0, @patient_b.id => 20 },
      @patient_b.id => { @patient_a.id => 20, @patient_b.id => 0 }
    }
    @instances = [
      Scheduling::VisitInstance.new(
        id: "patient_#{@patient_a.id}_visit_0", patient_id: @patient_a.id, patient: @patient_a,
        location: { lat: @patient_a.latitude, lng: @patient_a.longitude },
        duration: 45, priority: 0, availability_windows: {}
      ),
      Scheduling::VisitInstance.new(
        id: "patient_#{@patient_b.id}_visit_0", patient_id: @patient_b.id, patient: @patient_b,
        location: { lat: @patient_b.latitude, lng: @patient_b.longitude },
        duration: 60, priority: 0, availability_windows: {}
      )
    ]
  end

  test "returns a numeric score" do
    Time.use_zone("America/New_York") do
      date = Date.new(2026, 4, 6)
      day_routes = {
        date => [
          { patient: @patient_a, starts_at: Time.zone.parse("#{date} 09:00"), ends_at: Time.zone.parse("#{date} 09:45"), instance_id: @instances[0].id },
          { patient: @patient_b, starts_at: Time.zone.parse("#{date} 10:30"), ends_at: Time.zone.parse("#{date} 11:30"), instance_id: @instances[1].id }
        ]
      }
      score = build_fitness.score(day_routes: day_routes)
      assert_kind_of Numeric, score
      assert score > 0
    end
  end

  test "lower drive time produces lower score" do
    Time.use_zone("America/New_York") do
      date_mon = Date.new(2026, 4, 6)
      date_wed = Date.new(2026, 4, 8)

      same_day_routes = {
        date_mon => [
          { patient: @patient_a, starts_at: Time.zone.parse("#{date_mon} 09:00"), ends_at: Time.zone.parse("#{date_mon} 09:45"), instance_id: @instances[0].id },
          { patient: @patient_b, starts_at: Time.zone.parse("#{date_mon} 10:30"), ends_at: Time.zone.parse("#{date_mon} 11:30"), instance_id: @instances[1].id }
        ]
      }
      split_routes = {
        date_mon => [ { patient: @patient_a, starts_at: Time.zone.parse("#{date_mon} 09:00"), ends_at: Time.zone.parse("#{date_mon} 09:45"), instance_id: @instances[0].id } ],
        date_wed => [ { patient: @patient_b, starts_at: Time.zone.parse("#{date_wed} 09:00"), ends_at: Time.zone.parse("#{date_wed} 10:00"), instance_id: @instances[1].id } ]
      }

      fitness = Scheduling::FitnessFunction.new(
        travel_matrix: @travel_matrix, instances: @instances, clinician_profile: @profile,
        weights: { drive_time: 1.0, spacing: 0.0, availability: 0.0, density: 0.0, lunch: 0.0 }
      )

      assert fitness.score(day_routes: same_day_routes) > fitness.score(day_routes: split_routes)
    end
  end

  test "spacing penalty increases when visits are too close" do
    Time.use_zone("America/New_York") do
      @patient_a.update_columns(required_visits_per_week: 2, min_days_between_visits: 2)
      instances_2 = Scheduling::VisitInstanceBuilder.new(patients: [ @patient_a.reload ], locked_visits: [], charting_buffer_minutes: 0).call

      date_mon = Date.new(2026, 4, 6)
      date_tue = Date.new(2026, 4, 7)

      day_routes = {
        date_mon => [ { patient: @patient_a, starts_at: Time.zone.parse("#{date_mon} 09:00"), ends_at: Time.zone.parse("#{date_mon} 09:45"), instance_id: instances_2[0].id } ],
        date_tue => [ { patient: @patient_a, starts_at: Time.zone.parse("#{date_tue} 09:00"), ends_at: Time.zone.parse("#{date_tue} 09:45"), instance_id: instances_2[1].id } ]
      }

      fitness = Scheduling::FitnessFunction.new(
        travel_matrix: @travel_matrix, instances: instances_2, clinician_profile: @profile,
        weights: { drive_time: 0.0, spacing: 1.0, availability: 0.0, density: 0.0, lunch: 0.0 }
      )

      score = fitness.score(day_routes: day_routes)
      assert score > 0, "Should have spacing penalty for 1-day gap with min_days=2"
    end
  end

  private

  def build_fitness
    Scheduling::FitnessFunction.new(
      travel_matrix: @travel_matrix, instances: @instances, clinician_profile: @profile
    )
  end
end
