module Integrations
  class GeocodingClient < BaseClient
    MAPBOX_GEOCODING_URL = "https://api.mapbox.com/search/geocode/v6/forward".freeze
    GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json".freeze
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search".freeze
    # Nominatim's usage policy requires a descriptive User-Agent identifying the app.
    NOMINATIM_USER_AGENT = "RouteCare/1.0 (#{ENV.fetch('ROUTECARE_MAILER_FROM', 'support@routecare.example')})".freeze

    def initialize(mapbox_token: ENV["MAPBOX_ACCESS_TOKEN"], google_api_key: ENV["GOOGLE_MAPS_API_KEY"])
      super()
      @mapbox_token = mapbox_token
      @google_api_key = google_api_key
    end

    # Cascades Mapbox -> Google -> OSM Nominatim (free, keyless, always tried last).
    # Returns nil only if all three fail to find a match.
    def geocode(address)
      return nil if address.blank?

      cascade([
        [ @mapbox_token.present?, "Mapbox", -> { mapbox_geocode(address) } ],
        [ @google_api_key.present?, "Google", -> { google_geocode(address) } ],
        [ true, "OSM", -> { osm_geocode(address) } ]
      ])
    end

    private

    def mapbox_geocode(address)
      response = HTTParty.get(
        MAPBOX_GEOCODING_URL,
        query: { q: address, access_token: @mapbox_token, limit: 1 },
        headers: { "Accept" => "application/json" },
        format: :json,
        timeout: 5
      )
      raise "Mapbox geocoding returned #{response.code}" unless response.success?

      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      coords = body.dig("features", 0, "geometry", "coordinates")
      return nil unless coords&.length == 2

      validated_coordinates(lat: coords[1], lng: coords[0])
    end

    def google_geocode(address)
      response = HTTParty.get(
        GOOGLE_GEOCODING_URL,
        query: { address: address, key: @google_api_key },
        timeout: 5
      )
      raise "Google geocoding returned #{response.code}" unless response.success?

      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      raise "Google geocoding status #{body['status']}" unless body["status"] == "OK"

      location = body.dig("results", 0, "geometry", "location")
      return nil unless location

      validated_coordinates(lat: location["lat"], lng: location["lng"])
    end

    def osm_geocode(address)
      response = HTTParty.get(
        NOMINATIM_URL,
        query: { q: address, format: "json", limit: 1 },
        headers: { "User-Agent" => NOMINATIM_USER_AGENT, "Accept" => "application/json" },
        timeout: 5
      )
      raise "Nominatim returned #{response.code}" unless response.success?

      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      result = body.is_a?(Array) ? body.first : nil
      return nil unless result&.key?("lat") && result.key?("lon")

      validated_coordinates(lat: result["lat"].to_f, lng: result["lon"].to_f)
    end

    # Guards against a provider returning a technically-successful response
    # with missing/malformed coordinates (e.g. present-but-nil lat/lng, or the
    # (0, 0) null-island sentinel some APIs emit on a bad match) — treating
    # that as a real result would silently corrupt a patient's location
    # instead of falling through to the next provider in the cascade.
    def validated_coordinates(lat:, lng:)
      return nil unless lat.is_a?(Numeric) && lng.is_a?(Numeric)
      return nil if lat.zero? && lng.zero?

      { lat: lat, lng: lng }
    end
  end
end
