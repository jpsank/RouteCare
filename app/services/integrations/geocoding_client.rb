module Integrations
  class GeocodingClient < BaseClient
    require "zlib"

    def geocode(address)
      return nil if address.blank?

      # In production, connect to Google/HERE geocoding API here.
      # For now we return deterministic pseudo coordinates for local dev.
      seed = Zlib.crc32(address)
      {
        lat: 40.0 + ((seed % 10_000) / 10_000.0),
        lng: -74.0 - (((seed / 10_000) % 10_000) / 10_000.0)
      }
    end
  end
end
