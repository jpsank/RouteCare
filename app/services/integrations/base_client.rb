module Integrations
  class BaseClient
    def initialize(api_key: nil, **_kwargs)
      @api_key = api_key
    end

    def get(_url, **_options)
      {}
    end

    private

    attr_reader :api_key

    def configured?
      api_key.present?
    end

    # Tries each [configured, name, callable] tuple in order, skipping unconfigured
    # providers. Continues to the next provider if a callable raises, or returns a
    # falsy value (e.g. "no result found" rather than "request failed"). Returns
    # nil if every provider is unconfigured, raises, or comes up empty.
    def cascade(providers)
      providers.each do |configured, name, callable|
        next unless configured

        begin
          result = callable.call
          return result if result
        rescue StandardError => e
          Rails.logger.warn("[#{self.class.name}] #{name} failed (#{e.class}), trying next provider: #{e.message}")
        end
      end

      nil
    end

    def haversine_km(...)
      self.class.haversine_km(...)
    end

    class << self
      # ── Haversine (great-circle distance) ────────────────────────────────
      # Public so it has exactly one implementation, shared by RoutingClient's
      # fallback estimate and the test suite's map API stubs.

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
end
