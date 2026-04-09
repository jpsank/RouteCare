require "test_helper"

class Messaging::SuggestedVisitTimesBuilderTest < ActiveSupport::TestCase
  test "skips candidate times that are not feasible with travel from neighboring visits" do
    user = User.create!(email: "builder@example.com", password: "password123")
    profile = user.create_clinician_profile!(
      discipline: "Physical Therapist",
      timezone: "America/New_York",
      home_latitude: 42.3601,
      home_longitude: -71.0589
    )
    current_patient = profile.patients.create!(
      full_name: "Target Patient",
      phone: "5554001001",
      email: "target@example.com",
      address_line1: "100 Main St",
      city: "Worcester",
      state: "MA",
      postal_code: "01608",
      required_visits_per_week: 1,
      visit_duration_minutes: 45,
      latitude: 42.2626,
      longitude: -71.8023
    )
    previous_patient = profile.patients.create!(
      full_name: "Earlier Patient",
      phone: "5554001002",
      email: "earlier@example.com",
      address_line1: "1 Beacon St",
      city: "Boston",
      state: "MA",
      postal_code: "02108",
      required_visits_per_week: 1,
      visit_duration_minutes: 45,
      latitude: 42.3601,
      longitude: -71.0589
    )
    schedule = user.weekly_schedules.create!(week_start_on: Date.new(2026, 4, 6), status: :draft)
    visit = schedule.visits.create!(
      patient: current_patient,
      starts_at: Time.zone.parse("2026-04-07 09:00:00"),
      ends_at: Time.zone.parse("2026-04-07 09:45:00"),
      duration_minutes: 45,
      status: :pending_patient_confirmation,
      position_in_day: 0,
      drive_from_previous_minutes: 0,
      clinician_override: false,
      soft_constraint_override: false,
      source: "manual"
    )
    schedule.visits.create!(
      patient: previous_patient,
      starts_at: Time.zone.parse("2026-04-07 13:00:00"),
      ends_at: Time.zone.parse("2026-04-07 13:45:00"),
      duration_minutes: 45,
      status: :confirmed,
      position_in_day: 1,
      drive_from_previous_minutes: 0,
      clinician_override: false,
      soft_constraint_override: false,
      source: "manual"
    )

    # Stub travel time to a known value (60 min) so the test is
    # deterministic and doesn't depend on external APIs.
    Integrations::RoutingClient.define_method(:travel_minutes) { |**| 60 }

    suggestions = Messaging::SuggestedVisitTimesBuilder.new(
      visit: visit,
      proposed_windows: [ { "day" => "tuesday", "time_of_day" => "afternoon" } ]
    ).call

    # Previous visit ends at 13:45 + 60 min travel + 5 min buffer = 14:50,
    # so first feasible 15-min slot is 15:00.
    assert_not_empty suggestions
    assert_operator Time.iso8601(suggestions.first.fetch("starts_at")), :>=, Time.zone.parse("2026-04-07 15:00:00")
  ensure
    Integrations::RoutingClient.remove_method(:travel_minutes)
  end
end
