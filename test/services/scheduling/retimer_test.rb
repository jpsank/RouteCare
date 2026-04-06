require "test_helper"

class Scheduling::RetimerTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "retimer-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient_a = profile.patients.create!(
      full_name: "Patient A", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
    @patient_b = profile.patients.create!(
      full_name: "Patient B", phone: "5550002", address_line1: "200 Oak Ave",
      city: "Cambridge", state: "MA", postal_code: "02139",
      latitude: 42.3736, longitude: -71.1097,
      required_visits_per_week: 1, visit_duration_minutes: 60
    )
    @travel_matrix = {
      @patient_a.id => { @patient_a.id => 0, @patient_b.id => 20 },
      @patient_b.id => { @patient_a.id => 20, @patient_b.id => 0 }
    }
    @date = Date.new(2026, 4, 6)
  end

  test "assigns start times respecting transit between visits" do
    Time.use_zone("America/New_York") do
      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      result = build_retimer.call(slots, @date)

      assert result[:feasible]
      assert_equal 2, result[:slots].size
      gap = (result[:slots][1][:starts_at] - result[:slots][0][:ends_at]) / 60.0
      assert gap >= 20, "Gap #{gap}min should be >= 20min transit"
    end
  end

  test "returns feasible false when visits exceed workday" do
    Time.use_zone("America/New_York") do
      slots = Array.new(8) { { patient: @patient_a, duration: 90 } }
      result = build_retimer(day_end_minute: 600).call(slots, @date)
      refute result[:feasible]
    end
  end

  test "inserts lunch break" do
    Time.use_zone("America/New_York") do
      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      lunch_config = { earliest_start: 720, latest_start: 780, duration: 30 }
      result = build_retimer(lunch_config: lunch_config).call(slots, @date)

      assert result[:lunch]
      assert result[:lunch][:start_minute] >= 720
    end
  end

  test "works with empty slots" do
    Time.use_zone("America/New_York") do
      result = build_retimer.call([], @date)
      assert result[:feasible]
      assert_empty result[:slots]
    end
  end

  private

  def build_retimer(day_start_minute: 480, day_end_minute: 1080, lunch_config: nil)
    Scheduling::Retimer.new(
      travel_matrix: @travel_matrix,
      locked_visits: [],
      lunch_config: lunch_config,
      day_start_minute: day_start_minute,
      day_end_minute: day_end_minute,
      start_point: nil,
      max_continuous_work_minutes: nil
    )
  end
end
