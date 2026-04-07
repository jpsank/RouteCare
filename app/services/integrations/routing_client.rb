module Integrations
  class RoutingClient < BaseClient
    MAPBOX_MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving".freeze
    MAPBOX_MATRIX_MAX = 25 # Max sources/destinations per request
    FALLBACK_TOKEN = "pk.eyJ1IjoicHVmZnlib2EiLCJhIjoiY2sxbXNqbng1MDQ1cDNocWQ1bGVucGwxYyJ9.BsdxpULi2RpbCiaEyW3rgA".freeze

    def initialize(access_token: ENV.fetch("MAPBOX_ACCESS_TOKEN", FALLBACK_TOKEN))
      super()
      @access_token = access_token
    end

    # Single pair travel time (minutes). Used for home→patient lookups.
    def travel_minutes(origin:, destination:)
      return 0 if origin.blank? || destination.blank?
      return 0 if origin[:lat].blank? || origin[:lng].blank? || destination[:lat].blank? || destination[:lng].blank?

      haversine_estimate(origin, destination)
    end

    # Batch matrix: given an array of points [{lat:, lng:, id:}],
    # returns a nested hash { id_a => { id_b => minutes } }.
    # Uses Mapbox Matrix API when available, falls back to haversine.
    def travel_matrix(points)
      return haversine_matrix(points) if points.size < 2
      return haversine_matrix(points) if @access_token.blank?

      mapbox_matrix(points)
    rescue StandardError => e
      Rails.logger.warn("[RoutingClient] Mapbox Matrix API failed, using haversine: #{e.message}")
      haversine_matrix(points)
    end

    private

    # ── Mapbox Matrix API ────────────────────────────────────────────────

    def mapbox_matrix(points)
      # Mapbox supports up to 25 coordinates per request
      if points.size <= MAPBOX_MATRIX_MAX
        return mapbox_matrix_chunk(points)
      end

      # For larger sets, compute pairwise in chunks and merge
      result = {}
      points.each { |p| result[p[:id]] = {} }

      points.each_slice(MAPBOX_MATRIX_MAX) do |chunk|
        partial = mapbox_matrix_chunk(chunk)
        partial.each do |from_id, destinations|
          result[from_id] ||= {}
          result[from_id].merge!(destinations)
        end
      end

      # Fill in cross-chunk pairs with haversine (Mapbox chunks only cover within-chunk)
      points_by_id = points.index_by { |p| p[:id] }
      points.each do |from|
        points.each do |to|
          next if from[:id] == to[:id]
          next if result.dig(from[:id], to[:id])

          result[from[:id]][to[:id]] = haversine_estimate(from, to)
        end
      end

      result
    end

    def mapbox_matrix_chunk(points)
      coords = points.map { |p| "#{p[:lng]},#{p[:lat]}" }.join(";")
      url = "#{MAPBOX_MATRIX_URL}/#{coords}"

      response = HTTParty.get(url, query: {
        access_token: @access_token,
        annotations: "duration"
      }, timeout: 10)

      unless response.success?
        raise "Mapbox Matrix API returned #{response.code}"
      end

      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      durations = body["durations"]

      unless durations&.size == points.size
        raise "Mapbox Matrix API returned unexpected format"
      end

      result = {}
      points.each_with_index do |from, i|
        result[from[:id]] = {}
        points.each_with_index do |to, j|
          if i == j
            result[from[:id]][to[:id]] = 0
          else
            # Mapbox returns seconds, convert to minutes (rounded)
            seconds = durations[i][j]
            result[from[:id]][to[:id]] = seconds ? (seconds / 60.0).round : haversine_estimate(from, to)
          end
        end
      end

      result
    end

    # ── Haversine Fallback ───────────────────────────────────────────────

    def haversine_matrix(points)
      result = {}
      points.each do |from|
        result[from[:id]] = {}
        points.each do |to|
          result[from[:id]][to[:id]] = from[:id] == to[:id] ? 0 : haversine_estimate(from, to)
        end
      end
      result
    end

    def haversine_estimate(origin, destination)
      distance_km = haversine_km(
        origin[:lat].to_f, origin[:lng].to_f,
        destination[:lat].to_f, destination[:lng].to_f
      )
      ((distance_km / 38.0) * 60.0 + 4).round
    end

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
