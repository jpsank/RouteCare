module Integrations
  class RoutingClient < BaseClient
    MAPBOX_MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving".freeze
    MAPBOX_MATRIX_MAX = 25 # Max sources/destinations per request
    FALLBACK_TOKEN = "pk.eyJ1IjoicHVmZnlib2EiLCJhIjoiY2sxbXNqbng1MDQ1cDNocWQ1bGVucGwxYyJ9.BsdxpULi2RpbCiaEyW3rgA".freeze
    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix".freeze

    def initialize(access_token: ENV.fetch("MAPBOX_ACCESS_TOKEN", FALLBACK_TOKEN))
      super()
      @access_token = access_token
      @google_api_key = ENV["GOOGLE_MAPS_API_KEY"]
    end

    # Single pair travel time (minutes). Used for home→patient lookups.
    # When Google Maps API key is set, uses time-of-day aware routing.
    def travel_minutes(origin:, destination:, departure_time: nil)
      return 0 if origin.blank? || destination.blank?
      return 0 if origin[:lat].blank? || origin[:lng].blank? || destination[:lat].blank? || destination[:lng].blank?

      if @google_api_key.present? && departure_time
        google_travel_minutes(origin, destination, departure_time)
      elsif @access_token.present?
        mapbox_single_pair(origin, destination)
      else
        haversine_estimate(origin, destination)
      end
    end

    # Batch matrix: given an array of points [{lat:, lng:, id:}],
    # returns a nested hash { id_a => { id_b => minutes } }.
    # Uses Google Routes API (with traffic) > Mapbox Matrix > haversine.
    def travel_matrix(points, departure_time: nil)
      return haversine_matrix(points) if points.size < 2

      if @google_api_key.present?
        return google_matrix(points, departure_time: departure_time)
      end

      return haversine_matrix(points) if @access_token.blank?

      mapbox_matrix(points)
    rescue StandardError => e
      Rails.logger.warn("[RoutingClient] Matrix API failed (#{e.class}), using haversine: #{e.message}")
      haversine_matrix(points)
    end

    private

    # ── Mapbox Single-Pair ────────────────────────────────────────────────

    def mapbox_single_pair(origin, destination)
      points = [
        { id: "o", lat: origin[:lat], lng: origin[:lng] },
        { id: "d", lat: destination[:lat], lng: destination[:lng] }
      ]
      result = mapbox_matrix_chunk(points)
      result.dig("o", "d") || haversine_estimate(origin, destination)
    rescue StandardError
      haversine_estimate(origin, destination)
    end

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

    # ── Google Routes API (time-of-day aware) ──────────────────────────

    def google_travel_minutes(origin, destination, departure_time)
      response = HTTParty.post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers: {
          "X-Goog-Api-Key" => @google_api_key,
          "X-Goog-FieldMask" => "routes.duration",
          "Content-Type" => "application/json"
        },
        body: {
          origin: { location: { latLng: { latitude: origin[:lat].to_f, longitude: origin[:lng].to_f } } },
          destination: { location: { latLng: { latitude: destination[:lat].to_f, longitude: destination[:lng].to_f } } },
          travelMode: "DRIVE",
          routingPreference: "TRAFFIC_AWARE",
          departureTime: departure_time.iso8601
        }.to_json,
        timeout: 10
      )

      return haversine_estimate(origin, destination) unless response.success?

      body = response.parsed_response
      duration_str = body.dig("routes", 0, "duration") # e.g. "1234s"
      return haversine_estimate(origin, destination) unless duration_str

      seconds = duration_str.delete_suffix("s").to_i
      (seconds / 60.0).round
    rescue StandardError => e
      Rails.logger.warn("[RoutingClient] Google Routes API failed: #{e.message}")
      haversine_estimate(origin, destination)
    end

    def google_matrix(points, departure_time: nil)
      # Google Routes Matrix API: compute all pairs
      origins = points.map do |p|
        { waypoint: { location: { latLng: { latitude: p[:lat].to_f, longitude: p[:lng].to_f } } } }
      end
      destinations = origins.dup

      body = {
        origins: origins,
        destinations: destinations,
        travelMode: "DRIVE",
        routingPreference: "TRAFFIC_AWARE"
      }
      body[:departureTime] = departure_time.iso8601 if departure_time

      response = HTTParty.post(
        GOOGLE_ROUTES_URL,
        headers: {
          "X-Goog-Api-Key" => @google_api_key,
          "X-Goog-FieldMask" => "originIndex,destinationIndex,duration",
          "Content-Type" => "application/json"
        },
        body: body.to_json,
        timeout: 30
      )

      unless response.success?
        raise "Google Routes Matrix API returned #{response.code}"
      end

      result = {}
      points.each { |p| result[p[:id]] = {} }

      # Initialize with zeros on diagonal
      points.each { |p| result[p[:id]][p[:id]] = 0 }

      entries = response.parsed_response
      entries = [ entries ] unless entries.is_a?(Array)

      entries.each do |entry|
        oi = entry["originIndex"]
        di = entry["destinationIndex"]
        next if oi.nil? || di.nil? || oi == di

        duration_str = entry["duration"] # e.g. "1234s"
        if duration_str
          seconds = duration_str.delete_suffix("s").to_i
          result[points[oi][:id]][points[di][:id]] = (seconds / 60.0).round
        else
          result[points[oi][:id]][points[di][:id]] = haversine_estimate(points[oi], points[di])
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
