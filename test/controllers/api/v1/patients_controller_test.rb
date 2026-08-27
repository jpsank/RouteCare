require "test_helper"

class Api::V1::PatientsControllerTest < ActionDispatch::IntegrationTest
  include Devise::Test::IntegrationHelpers

  test "update can clear availability windows" do
    user = User.create!(email: "patients@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    patient = profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001001",
      email: "jane@example.com",
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 2,
      visit_duration_minutes: 45
    )
    patient.patient_availability_windows.create!(day_of_week: 1, start_minute: 540, end_minute: 600)

    sign_in user
    assert_not_empty patient.patient_availability_windows

    patch api_v1_patient_path(patient), params: { patient: { full_name: patient.full_name }, availability_windows: [] }, as: :json

    assert_response :success
    assert_empty patient.reload.patient_availability_windows
  end

  test "update can add a new availability window" do
    user = User.create!(email: "patients2@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    patient = profile.patients.create!(
      full_name: "John Smith",
      phone: "5554001002",
      email: "john@example.com",
      address_line1: "200 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 2,
      visit_duration_minutes: 45
    )

    sign_in user

    patch api_v1_patient_path(patient),
      params: {
        patient: { full_name: patient.full_name },
        availability_windows: [ { day_of_week: 1, start_minute: 540, end_minute: 1020, available: true } ]
      },
      as: :json

    assert_response :success
    windows = patient.reload.patient_availability_windows
    assert_equal 1, windows.size
    assert_equal 540, windows.first.start_minute
    assert_equal true, windows.first.available
  end
end
