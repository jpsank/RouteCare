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

  test "requests one matrix per traffic bucket, each with a distinct representative departure time" do
    Time.use_zone("America/New_York") do
      travel_to(Time.zone.parse("2026-04-05 00:15")) do
        week_start = Date.new(2026, 4, 6) # the Monday being scheduled

        client = Integrations::RoutingClient.new(google_api_key: "test-google-key")

        result = Scheduling::TravelTimeMatrixBuilder.new(
          patients: [ @patient_a, @patient_b ],
          routing_client: client,
          week_start_on: week_start
        ).call

        assert_instance_of Scheduling::BucketedTravelMatrix, result
        assert_equal Scheduling::TrafficBuckets::ALL.map { |b| b[:name] }.sort, result.to_h.keys.sort

        departure_times = []
        assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", times: 4 do |req|
          departure = Time.iso8601(JSON.parse(req.body)["departureTime"])
          departure_times << departure
          # Anchored to the Monday of the scheduled week, not to the frozen
          # "now" (a Sunday midnight).
          departure.to_date == week_start
        end

        # Four distinct departure times were actually requested — not one
        # flat call reused for every bucket.
        assert_equal 4, departure_times.uniq.size
        assert_equal Scheduling::TrafficBuckets::ALL.map { |b| b[:departure_minute] }.sort,
          departure_times.map { |t| t.hour * 60 + t.min }.sort
      end
    end
  end

  test "falls back to Time.current per bucket when the scheduled week has already passed" do
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

        assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix", times: 4 do |req|
          departure = Time.iso8601(JSON.parse(req.body)["departureTime"])
          departure >= frozen_now - 1.second
        end
      end
    end
  end

  test "falls through the same cascade per bucket when Google isn't configured" do
    # No Google key: each of the 4 bucket calls should cascade to
    # Mapbox -> OSM -> haversine exactly like the old single-matrix flow,
    # just once per bucket instead of once total.
    client = Integrations::RoutingClient.new(google_api_key: nil, mapbox_token: nil)

    stub_request(:get, %r{router\.project-osrm\.org}).to_timeout

    result = Scheduling::TravelTimeMatrixBuilder.new(
      patients: [ @patient_a, @patient_b ],
      routing_client: client,
      week_start_on: Date.new(2026, 4, 6)
    ).call

    assert_instance_of Scheduling::BucketedTravelMatrix, result
    Scheduling::TrafficBuckets::ALL.each do |bucket|
      matrix = result.to_h.fetch(bucket[:name])
      # Haversine fallback still produces a positive, finite estimate.
      assert matrix[@patient_a.id][@patient_b.id] > 0
    end
  end
end
