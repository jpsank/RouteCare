require "test_helper"

class Scheduling::TravelTimeMatrixBuilderTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "ttmb-test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(
      discipline: "PT", timezone: "America/New_York",
      home_latitude: 42.3601, home_longitude: -71.0589
    )
    @patient_a = @profile.patients.create!(
      full_name: "Patient A", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 30
    )
    @patient_b = @profile.patients.create!(
      full_name: "Patient B", phone: "5550002", address_line1: "200 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3736, longitude: -71.1097,
      required_visits_per_week: 1, visit_duration_minutes: 30
    )
  end

  test "passes a departure_time derived from week_start_on, not raw Time.current" do
    Time.use_zone("America/New_York") do
      # Freeze "now" somewhere that would clearly be wrong if leaked through
      # (a Sunday just after midnight) so an accidental Time.current default
      # is easy to distinguish from the representative Monday-morning value.
      travel_to(Time.zone.parse("2026-04-05 00:15")) do
        week_start = Date.new(2026, 4, 6) # the Monday being scheduled

        client = Integrations::RoutingClient.new(google_api_key: "test-google-key")

        Scheduling::TravelTimeMatrixBuilder.new(
          patients: [ @patient_a, @patient_b ],
          routing_client: client,
          week_start_on: week_start
        ).call

        assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix" do |req|
          departure = Time.iso8601(JSON.parse(req.body)["departureTime"])
          # Anchored to the Monday of the scheduled week at the representative
          # hour, not to the frozen "now" (a Sunday midnight).
          departure.to_date == week_start &&
            departure.hour == Scheduling::TravelTimeMatrixBuilder::REPRESENTATIVE_HOUR
        end
      end
    end
  end

  test "falls back to Time.current when the scheduled week has already passed" do
    Time.use_zone("America/New_York") do
      frozen_now = Time.zone.parse("2026-04-10 10:00")
      travel_to(frozen_now) do
        past_week_start = Date.new(2026, 4, 6) # Monday of a week already underway

        client = Integrations::RoutingClient.new(google_api_key: "test-google-key")

        Scheduling::TravelTimeMatrixBuilder.new(
          patients: [ @patient_a, @patient_b ],
          routing_client: client,
          week_start_on: past_week_start
        ).call

        assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix" do |req|
          departure = Time.iso8601(JSON.parse(req.body)["departureTime"])
          departure >= frozen_now - 1.second
        end
      end
    end
  end
end
