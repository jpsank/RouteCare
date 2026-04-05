module Integrations
  class GeocodingClient < BaseClient
    MAPBOX_GEOCODING_URL = "https://api.mapbox.com/search/geocode/v6/forward".freeze
    FALLBACK_TOKEN = "pk.eyJ1IjoicHVmZnlib2EiLCJhIjoiY2sxbXNqbng1MDQ1cDNocWQ1bGVucGwxYyJ9.BsdxpULi2RpbCiaEyW3rgA".freeze

    def initialize(access_token: ENV.fetch("MAPBOX_ACCESS_TOKEN", FALLBACK_TOKEN))
      super()
      @access_token = access_token
    end

    def geocode(address)
      return nil if address.blank?

      response = HTTParty.get(
        MAPBOX_GEOCODING_URL,
        query: { q: address, access_token: @access_token, limit: 1 },
        headers: { "Accept" => "application/json" },
        format: :json,
        timeout: 5
      )

      return nil unless response.success?

      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      feature = body.dig("features", 0)
      return nil unless feature

      coords = feature.dig("geometry", "coordinates")
      return nil unless coords&.length == 2

      { lat: coords[1], lng: coords[0] }
    rescue StandardError => e
      Rails.logger.warn("[GeocodingClient] Geocode failed for '#{address}': #{e.message}")
      nil
    end
  end
end
