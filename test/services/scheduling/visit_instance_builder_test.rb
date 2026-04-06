require "test_helper"

class Scheduling::VisitInstanceBuilderTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "vib-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient = profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 2, visit_duration_minutes: 45
    )
  end

  test "builds correct number of instances for a patient" do
    instances = build(patients: [ @patient ])
    assert_equal 2, instances.size
    assert_equal "patient_#{@patient.id}_visit_0", instances[0].id
    assert_equal "patient_#{@patient.id}_visit_1", instances[1].id
  end

  test "subtracts locked visits from required count" do
    locked = [ Struct.new(:patient_id).new(@patient.id) ]
    instances = build(patients: [ @patient ], locked_visits: locked)
    assert_equal 1, instances.size
  end

  test "returns empty when all visits are locked" do
    locked = Array.new(2) { Struct.new(:patient_id).new(@patient.id) }
    instances = build(patients: [ @patient ], locked_visits: locked)
    assert_empty instances
  end

  test "adds charting buffer to duration" do
    instances = build(patients: [ @patient ], charting_buffer_minutes: 15)
    assert_equal 60, instances[0].duration
  end

  test "sets location from patient coordinates" do
    instances = build(patients: [ @patient ])
    assert_equal({ lat: @patient.latitude, lng: @patient.longitude }, instances[0].location)
  end

  test "sets priority from patient" do
    @patient.update_column(:priority, 5)
    instances = build(patients: [ @patient.reload ])
    assert_equal 5, instances[0].priority
  end

  private

  def build(patients:, locked_visits: [], charting_buffer_minutes: 0)
    Scheduling::VisitInstanceBuilder.new(
      patients: patients, locked_visits: locked_visits, charting_buffer_minutes: charting_buffer_minutes
    ).call
  end
end
