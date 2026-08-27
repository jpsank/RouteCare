require "zlib"
require "cgi"

# Default stubs for the Mapbox/Google APIs called by Integrations::GeocodingClient
# and Integrations::RoutingClient, so the test suite never makes real (billable)
# network requests to these providers.
#
# Coordinates are derived deterministically from the input address (rather than a
# single fixed point) and matrix/route durations are computed from the haversine
# distance between the actual request coordinates (rather than a flat constant),
# so tests that depend on some patients being closer/farther than others still
# exercise real relative-distance behavior instead of getting diluted to a no-op.
module MapsApiStubs
  BASE_LAT = 37.7749
  BASE_LNG = -122.4194
  AVERAGE_KMH = 38.0

  def self.install!
    stub_mapbox_geocoding
    stub_mapbox_matrix
    stub_google_geocoding
    stub_google_directions
    stub_google_routes_matrix
    stub_osm_geocoding
    stub_osrm_matrix
    stub_osrm_route
  end

  def self.pseudo_coords_for(address)
    # Two independent CRC32s (of the address and its reverse) give distinct,
    # deterministic lat/lng offsets without pulling in a cryptographic digest.
    lat_offset = (Zlib.crc32(address.to_s) / 0xFFFFFFFF.to_f - 0.5) * 2.0 # +/- 1 degree (~111km)
    lng_offset = (Zlib.crc32(address.to_s.reverse) / 0xFFFFFFFF.to_f - 0.5) * 2.0
    { lat: BASE_LAT + lat_offset, lng: BASE_LNG + lng_offset }
  end

  def self.haversine_km(lat1, lng1, lat2, lng2)
    Integrations::BaseClient.haversine_km(lat1, lng1, lat2, lng2)
  end

  def self.haversine_minutes(lat1, lng1, lat2, lng2)
    ((haversine_km(lat1, lng1, lat2, lng2) / AVERAGE_KMH) * 60.0).round
  end

  def self.stub_mapbox_geocoding
    WebMock.stub_request(:get, %r{\Ahttps://api\.mapbox\.com/search/geocode/v6/forward}).to_return do |request|
      address = CGI.parse(URI(request.uri).query.to_s)["q"]&.first.to_s
      coords = pseudo_coords_for(address)
      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: { features: [ { geometry: { coordinates: [ coords[:lng], coords[:lat] ] } } ] }.to_json
      }
    end
  end

  def self.stub_mapbox_matrix
    WebMock.stub_request(:get, %r{\Ahttps://api\.mapbox\.com/directions-matrix/v1/mapbox/driving/}).to_return do |request|
      points = request.uri.path.split("/").last.split(";").map { |pair| pair.split(",").map(&:to_f) } # [lng, lat]
      durations = points.map do |lng1, lat1|
        points.map do |lng2, lat2|
          [ lng1, lat1 ] == [ lng2, lat2 ] ? 0 : haversine_minutes(lat1, lng1, lat2, lng2) * 60
        end
      end

      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: { durations: durations }.to_json
      }
    end
  end

  def self.stub_google_geocoding
    WebMock.stub_request(:get, %r{\Ahttps://maps\.googleapis\.com/maps/api/geocode/json}).to_return do |request|
      address = CGI.parse(URI(request.uri).query.to_s)["address"]&.first.to_s
      coords = pseudo_coords_for(address)
      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: {
          status: "OK",
          results: [ { geometry: { location: { lat: coords[:lat], lng: coords[:lng] } } } ]
        }.to_json
      }
    end
  end

  def self.stub_google_directions
    WebMock.stub_request(:post, "https://routes.googleapis.com/directions/v2:computeRoutes").to_return do |request|
      body = JSON.parse(request.body)
      origin = body.dig("origin", "location", "latLng")
      destination = body.dig("destination", "location", "latLng")
      minutes = haversine_minutes(origin["latitude"], origin["longitude"], destination["latitude"], destination["longitude"])

      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: { routes: [ { duration: "#{minutes * 60}s" } ] }.to_json
      }
    end
  end

  def self.stub_google_routes_matrix
    WebMock.stub_request(:post, "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix").to_return do |request|
      body = JSON.parse(request.body)
      origins = body["origins"]
      destinations = body["destinations"]

      entries = []
      origins.each_with_index do |origin, i|
        destinations.each_with_index do |destination, j|
          next if i == j

          o = origin.dig("waypoint", "location", "latLng")
          d = destination.dig("waypoint", "location", "latLng")
          minutes = haversine_minutes(o["latitude"], o["longitude"], d["latitude"], d["longitude"])
          entries << { originIndex: i, destinationIndex: j, duration: "#{minutes * 60}s" }
        end
      end

      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: entries.to_json
      }
    end
  end

  def self.stub_osm_geocoding
    WebMock.stub_request(:get, %r{\Ahttps://nominatim\.openstreetmap\.org/search}).to_return do |request|
      address = CGI.parse(URI(request.uri).query.to_s)["q"]&.first.to_s
      coords = pseudo_coords_for(address)
      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: [ { lat: coords[:lat].to_s, lon: coords[:lng].to_s } ].to_json
      }
    end
  end

  def self.stub_osrm_matrix
    WebMock.stub_request(:get, %r{\Ahttps://router\.project-osrm\.org/table/v1/driving/}).to_return do |request|
      points = request.uri.path.split("/").last.split(";").map { |pair| pair.split(",").map(&:to_f) } # [lng, lat]
      durations = points.map do |lng1, lat1|
        points.map do |lng2, lat2|
          [ lng1, lat1 ] == [ lng2, lat2 ] ? 0 : haversine_minutes(lat1, lng1, lat2, lng2) * 60
        end
      end

      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: { code: "Ok", durations: durations }.to_json
      }
    end
  end

  def self.stub_osrm_route
    WebMock.stub_request(:get, %r{\Ahttps://router\.project-osrm\.org/route/v1/driving/}).to_return do |request|
      points = request.uri.path.split("/").last.split(";").map { |pair| pair.split(",").map(&:to_f) } # [lng, lat]
      coordinates = points # already [lng, lat] pairs, matching GeoJSON order
      distance_m = 0
      minutes = 0
      points.each_cons(2) do |(lng1, lat1), (lng2, lat2)|
        distance_km = haversine_km(lat1, lng1, lat2, lng2)
        distance_m += distance_km * 1000
        minutes += ((distance_km / AVERAGE_KMH) * 60.0).round
      end

      {
        status: 200,
        headers: { "Content-Type" => "application/json" },
        body: {
          code: "Ok",
          routes: [ { geometry: { type: "LineString", coordinates: coordinates }, duration: minutes * 60, distance: distance_m } ]
        }.to_json
      }
    end
  end
end
