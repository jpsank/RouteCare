require "test_helper"

module Integrations
  class RoutingClientTest < ActiveSupport::TestCase
    ORIGIN = { lat: 42.3601, lng: -71.0589 }.freeze
    DESTINATION = { lat: 42.3736, lng: -71.1097 }.freeze

    test "travel_minutes prefers Google (traffic-aware) over Mapbox when both are configured" do
      client = RoutingClient.new(mapbox_token: "test-mapbox-token", google_api_key: "test-google-key")

      client.travel_minutes(origin: ORIGIN, destination: DESTINATION)

      assert_requested :post, "https://routes.googleapis.com/directions/v2:computeRoutes"
      assert_not_requested :get, %r{\Ahttps://api\.mapbox\.com/directions-matrix/v1/mapbox/driving/}
    end

    test "travel_minutes requests traffic-aware Google routing with a departure time" do
      client = RoutingClient.new(mapbox_token: nil, google_api_key: "test-google-key")

      client.travel_minutes(origin: ORIGIN, destination: DESTINATION)

      assert_requested :post, "https://routes.googleapis.com/directions/v2:computeRoutes" do |req|
        body = JSON.parse(req.body)
        body["travelMode"] == "DRIVE" &&
          body["routingPreference"] == "TRAFFIC_AWARE" &&
          body["departureTime"].present?
      end
    end

    test "travel_minutes falls back to Mapbox when Google is unconfigured, then OSM when Mapbox fails" do
      WebMock.stub_request(:get, %r{\Ahttps://api\.mapbox\.com/directions-matrix/v1/mapbox/driving/}).to_return(status: 401)

      client = RoutingClient.new(mapbox_token: "bad-token", google_api_key: nil)
      minutes = client.travel_minutes(origin: ORIGIN, destination: DESTINATION)

      assert minutes.positive?
      assert_requested :get, %r{\Ahttps://router\.project-osrm\.org/table/v1/driving/}
    end

    test "travel_matrix prefers Google over Mapbox when both are configured" do
      client = RoutingClient.new(mapbox_token: "test-mapbox-token", google_api_key: "test-google-key")
      points = [
        { id: "a", lat: ORIGIN[:lat], lng: ORIGIN[:lng] },
        { id: "b", lat: DESTINATION[:lat], lng: DESTINATION[:lng] }
      ]

      client.travel_matrix(points)

      assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
      assert_not_requested :get, %r{\Ahttps://api\.mapbox\.com/directions-matrix/v1/mapbox/driving/}
    end

    test "travel_matrix requests traffic-aware Google routing even without an explicit departure time" do
      client = RoutingClient.new(mapbox_token: nil, google_api_key: "test-google-key")
      points = [
        { id: "a", lat: ORIGIN[:lat], lng: ORIGIN[:lng] },
        { id: "b", lat: DESTINATION[:lat], lng: DESTINATION[:lng] }
      ]

      client.travel_matrix(points)

      assert_requested :post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix" do |req|
        JSON.parse(req.body)["departureTime"].present?
      end
    end
  end
end
