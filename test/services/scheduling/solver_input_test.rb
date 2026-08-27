require "test_helper"

class Scheduling::SolverInputTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "si-test@example.com", password: "password123")
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

  test "build requests a travel matrix anchored to the scheduled week, not raw Time.current" do
    Time.use_zone("America/New_York") do
      # Frozen "now" is deliberately a moment (Sunday just after midnight) that
      # would be an obviously wrong departure time if it leaked through.
      travel_to(Time.zone.parse("2026-04-05 00:15")) do
        with_google_routing_configured do
          week_start = Date.new(2026, 4, 6) # the Monday being scheduled

          input = Scheduling::SolverInput.build(user: @user, week_start_on: week_start)

          assert_instance_of Scheduling::BucketedTravelMatrix, input.travel_matrix

          # One batch call per traffic bucket, all anchored to the Monday
          # being scheduled (not the frozen Sunday-midnight "now").
          assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", times: 4 do |req|
            departure = Time.iso8601(JSON.parse(req.body)["departureTime"])
            departure.to_date == week_start
          end
        end
      end
    end
  end

  private

  def with_google_routing_configured
    original = ENV["GOOGLE_MAPS_API_KEY"]
    ENV["GOOGLE_MAPS_API_KEY"] = "test-google-key"
    yield
  ensure
    ENV["GOOGLE_MAPS_API_KEY"] = original
  end
end
