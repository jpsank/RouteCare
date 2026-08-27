require "test_helper"

class PatientTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "patient-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient = profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
  end

  test "#unavailable_windows_for_wday returns only unavailable windows for the given weekday" do
    @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 480, end_minute: 600, available: true)
    @patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 600, end_minute: 660, available: false)
    @patient.patient_availability_windows.create!(day_of_week: 2, start_minute: 480, end_minute: 540, available: false)

    result = @patient.unavailable_windows_for_wday(1)

    assert_equal [ { start_minute: 600, end_minute: 660 } ], result
  end

  test "#unavailable_windows_for_wday returns empty array when none defined" do
    assert_equal [], @patient.unavailable_windows_for_wday(1)
  end
end
