require "test_helper"

class Api::V1::VisitsControllerTest < ActionDispatch::IntegrationTest
  include Devise::Test::IntegrationHelpers

  test "update does not lock visit from optimizer when clinician_override is omitted" do
    user, visit = build_visit_context(email: "no-lock@example.com", clinician_override: false)

    sign_in user

    patch api_v1_visit_path(visit), params: { visit: { status: "confirmed" } }, as: :json

    assert_response :success
    visit.reload
    assert_equal "confirmed", visit.status
    assert_equal false, visit.clinician_override, "editing an unrelated field must not flip clinician_override"
  end

  test "update leaves clinician_override unchanged when it was already true and not sent" do
    user, visit = build_visit_context(email: "already-locked@example.com", clinician_override: true)

    sign_in user

    patch api_v1_visit_path(visit), params: { visit: { status: "completed" } }, as: :json

    assert_response :success
    visit.reload
    assert_equal "completed", visit.status
    assert_equal true, visit.clinician_override
  end

  test "update sets clinician_override when the client explicitly sends it" do
    user, visit = build_visit_context(email: "explicit-lock@example.com", clinician_override: false)

    sign_in user

    patch api_v1_visit_path(visit), params: { visit: { clinician_override: true } }, as: :json

    assert_response :success
    assert_equal true, visit.reload.clinician_override
  end

  test "update unlocks visit when the client explicitly sends clinician_override false" do
    user, visit = build_visit_context(email: "explicit-unlock@example.com", clinician_override: true)

    sign_in user

    patch api_v1_visit_path(visit), params: { visit: { clinician_override: false } }, as: :json

    assert_response :success
    assert_equal false, visit.reload.clinician_override
  end

  private

  def build_visit_context(email:, clinician_override:)
    user = User.create!(email:, password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    patient = profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001001",
      email: "jane@example.com",
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 1,
      visit_duration_minutes: 45
    )
    schedule = user.weekly_schedules.create!(week_start_on: Date.new(2026, 4, 6), status: :draft)
    visit = schedule.visits.create!(
      patient: patient,
      starts_at: Time.zone.parse("2026-04-07 09:00:00"),
      ends_at: Time.zone.parse("2026-04-07 09:45:00"),
      duration_minutes: 45,
      status: :pending_patient_confirmation,
      position_in_day: 0,
      drive_from_previous_minutes: 0,
      clinician_override: clinician_override,
      source: "manual"
    )

    [ user, visit ]
  end
end
