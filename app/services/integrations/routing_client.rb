module Integrations
  class RoutingClient < BaseClient
    # Uses direct haversine fallback so local/dev works without API keys.
    def travel_minutes(origin:, destination:)
      return 0 if origin.blank? || destination.blank?
      return 0 if origin[:lat].blank? || origin[:lng].blank? || destination[:lat].blank? || destination[:lng].blank?

      distance_km = haversine_km(
        origin[:lat].to_f,
        origin[:lng].to_f,
        destination[:lat].to_f,
        destination[:lng].to_f
      )

      # Approximate urban driving speed with small stop-light overhead.
      ((distance_km / 38.0) * 60.0 + 4).round
    end

    private

    def haversine_km(lat1, lng1, lat2, lng2)
      radius_km = 6_371.0
      d_lat = to_rad(lat2 - lat1)
      d_lng = to_rad(lng2 - lng1)
      a = Math.sin(d_lat / 2)**2 +
          Math.cos(to_rad(lat1)) * Math.cos(to_rad(lat2)) * Math.sin(d_lng / 2)**2

      radius_km * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
    end

    def to_rad(value)
      value * Math::PI / 180.0
    end
  end
end
