require "test_helper"

class PatientAvailabilityWindowTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "paw-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient = profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
  end

  test "defaults to available" do
    window = @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600)
    assert window.available
  end

  test "can be marked unavailable" do
    window = @patient.patient_availability_windows.create!(
      day_of_week: 1, start_minute: 480, end_minute: 600, available: false
    )
    refute window.available
  end

  test "available and unavailable scopes filter correctly" do
    available = @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600)
    unavailable = @patient.patient_availability_windows.create!(
      day_of_week: 1, start_minute: 600, end_minute: 660, available: false
    )

    assert_equal [ available ], @patient.patient_availability_windows.available.to_a
    assert_equal [ unavailable ], @patient.patient_availability_windows.unavailable.to_a
  end

  test "allows identical time ranges for available and unavailable windows on the same day" do
    @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600, available: true)
    duplicate = @patient.patient_availability_windows.build(
      day_of_week: 1, start_minute: 480, end_minute: 600, available: false
    )
    assert duplicate.valid?
  end

  test "rejects duplicate windows with the same day, range, and availability" do
    @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600, available: true)
    duplicate = @patient.patient_availability_windows.build(
      day_of_week: 1, start_minute: 480, end_minute: 600, available: true
    )
    assert_raises(ActiveRecord::RecordNotUnique) { duplicate.save!(validate: false) }
  end

  test "#to_h returns start and end minute" do
    window = @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600)
    assert_equal({ start_minute: 480, end_minute: 600 }, window.to_h)
  end
end
