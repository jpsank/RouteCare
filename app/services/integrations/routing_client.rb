module Integrations
  class RoutingClient < BaseClient
    MAPBOX_MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving".freeze
    MAPBOX_MATRIX_MAX = 25 # Max sources/destinations per request
    GOOGLE_ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix".freeze
    OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving".freeze
    # Kept short because up to 3 of these can stack sequentially in the cascade
    # before falling back to haversine — a synchronous web request (patient
    # save, visit resequencing) shouldn't block for 30s+ waiting on a provider
    # that's down. A working provider responds in well under this regardless.
    PROVIDER_TIMEOUT = 5

    def initialize(mapbox_token: ENV["MAPBOX_ACCESS_TOKEN"], google_api_key: ENV["GOOGLE_MAPS_API_KEY"])
      super()
      @mapbox_token = mapbox_token
      @google_api_key = google_api_key
    end

    # Single pair travel time (minutes). Used for home→patient lookups.
    # Cascades Google (time-of-day aware) -> Mapbox -> OSRM (free, keyless) -> haversine.
    # Google goes first because it's the only provider that actually accounts
    # for traffic; Mapbox/OSRM give static estimates regardless of order.
    def travel_minutes(origin:, destination:, departure_time: nil)
      return 0 if origin.blank? || destination.blank?
      return 0 if origin[:lat].blank? || origin[:lng].blank? || destination[:lat].blank? || destination[:lng].blank?

      cascade([
        [ @google_api_key.present?, "Google", -> { google_travel_minutes(origin, destination, departure_time || Time.current) } ],
        [ @mapbox_token.present?, "Mapbox", -> { mapbox_single_pair(origin, destination) } ],
        [ true, "OSM", -> { osrm_single_pair(origin, destination) } ]
      ]) || haversine_estimate(origin, destination)
    end

    # Batch matrix: given an array of points [{lat:, lng:, id:}],
    # returns a nested hash { id_a => { id_b => minutes } }.
    # Cascades Google Routes Matrix -> Mapbox Matrix -> OSRM (free, keyless) -> haversine.
    def travel_matrix(points, departure_time: nil)
      return haversine_matrix(points) if points.size < 2

      cascade([
        [ @google_api_key.present?, "Google", -> { google_matrix(points, departure_time: departure_time || Time.current) } ],
        [ @mapbox_token.present?, "Mapbox", -> { mapbox_matrix(points) } ],
        [ true, "OSM", -> { osrm_matrix(points) } ]
      ]) || haversine_matrix(points)
    end

    private

    # ── Mapbox Single-Pair ────────────────────────────────────────────────

    def mapbox_single_pair(origin, destination)
      points = [
        { id: "o", lat: origin[:lat], lng: origin[:lng] },
        { id: "d", lat: destination[:lat], lng: destination[:lng] }
      ]
      result = mapbox_matrix_chunk(points)
      result.dig("o", "d") or raise "Mapbox matrix returned no duration"
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
        access_token: @mapbox_token,
        annotations: "duration"
      }, timeout: PROVIDER_TIMEOUT)

      unless response.success?
        raise "Mapbox Matrix API returned #{response.code}"
      end

      parse_duration_matrix(points, response, provider: "Mapbox Matrix API")
    end

    # Shared by mapbox_matrix_chunk and osrm_matrix: both APIs return
    # { "durations": [[seconds, ...], ...] } in the same request-coordinate
    # order, with per-pair haversine fallback when a cell comes back null.
    def parse_duration_matrix(points, response, provider:)
      body = response.parsed_response
      body = JSON.parse(body) if body.is_a?(String)
      durations = body["durations"]

      unless durations&.size == points.size
        raise "#{provider} returned unexpected format"
      end

      result = {}
      points.each_with_index do |from, i|
        result[from[:id]] = {}
        points.each_with_index do |to, j|
          if i == j
            result[from[:id]][to[:id]] = 0
          else
            seconds = durations[i][j]
            # A distinct pair should never legitimately come back at exactly
            # zero (or negative) seconds — treat that as a bad cell rather
            # than a real duration, same as geocoding's null-island guard.
            valid = seconds.is_a?(Numeric) && seconds.positive?
            result[from[:id]][to[:id]] = valid ? (seconds / 60.0).round : haversine_estimate(from, to)
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
        timeout: PROVIDER_TIMEOUT
      )

      raise "Google Routes API returned #{response.code}" unless response.success?

      body = response.parsed_response
      duration_str = body.dig("routes", 0, "duration") # e.g. "1234s"
      raise "Google Routes API did not return a duration" unless duration_str

      seconds = duration_str.delete_suffix("s").to_i
      raise "Google Routes API returned a non-positive duration" unless seconds.positive?

      (seconds / 60.0).round
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
        timeout: PROVIDER_TIMEOUT
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
        seconds = duration_str&.delete_suffix("s")&.to_i
        valid = seconds.is_a?(Numeric) && seconds.positive?
        result[points[oi][:id]][points[di][:id]] =
          valid ? (seconds / 60.0).round : haversine_estimate(points[oi], points[di])
      end

      result
    end

    # ── OSRM (free, keyless public demo server) ────────────────────────────

    def osrm_single_pair(origin, destination)
      points = [
        { id: "o", lat: origin[:lat], lng: origin[:lng] },
        { id: "d", lat: destination[:lat], lng: destination[:lng] }
      ]
      result = osrm_matrix(points)
      result.dig("o", "d") or raise "OSRM table returned no duration"
    end

    def osrm_matrix(points)
      coords = points.map { |p| "#{p[:lng]},#{p[:lat]}" }.join(";")
      response = HTTParty.get("#{OSRM_TABLE_URL}/#{coords}", query: { annotations: "duration" }, timeout: PROVIDER_TIMEOUT)

      unless response.success?
        raise "OSRM table API returned #{response.code}"
      end

      parse_duration_matrix(points, response, provider: "OSRM table API")
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
  end
end
