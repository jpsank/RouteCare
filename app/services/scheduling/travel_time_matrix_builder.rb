module Scheduling
  class TravelTimeMatrixBuilder
    # @param patients [Array<Patient>] patients to include in the matrix
    # @param home [Hash, nil] optional home node {lat:, lng:} — included with id :home
    # @param routing_client [Integrations::RoutingClient]
    # @param week_start_on [Date, nil] Monday of the week actually being scheduled.
    #   Anchors each bucket's traffic-aware departure timestamp to a real weekday in
    #   that week instead of "whatever moment the optimize button was clicked" —
    #   see #representative_departure_time.
    def initialize(patients:, home: nil, routing_client: Integrations::RoutingClient.new, week_start_on: nil)
      @patients = patients
      @home = home
      @routing_client = routing_client
      @week_start_on = week_start_on
    end

    # Computes one travel-time matrix per traffic bucket (see TrafficBuckets)
    # and returns them wrapped in a BucketedTravelMatrix.
    #
    # This replaces the old single-flat-matrix approach: rather than one
    # provider call anchored to one representative hour (reused for every
    # visit regardless of actual time of day), we make one batch call per
    # bucket — still bucket_count× the API cost, not leg_count× — so a 9am
    # visit and a 4pm visit on the same day get meaningfully different
    # travel-time numbers instead of identical ones.
    def call
      points = build_points
      return BucketedTravelMatrix.new(empty_matrices(points)) if points.size < 2

      matrices = Scheduling::TrafficBuckets::ALL.each_with_object({}) do |bucket, acc|
        acc[bucket[:name]] = routing_client.travel_matrix(
          points, departure_time: representative_departure_time(bucket[:departure_minute])
        )
      end

      BucketedTravelMatrix.new(matrices)
    end

    private

    attr_reader :patients, :home, :routing_client, :week_start_on

    def build_points
      points = patients.filter_map do |patient|
        next if patient.latitude.blank? || patient.longitude.blank?
        { id: patient.id, lat: patient.latitude, lng: patient.longitude }
      end

      points.unshift({ id: :home, lat: home[:lat], lng: home[:lng] }) if home
      points
    end

    def empty_matrices(points)
      flat = points.each_with_object({}) { |p, m| m[p[:id]] = { p[:id] => 0 } }
      Scheduling::TrafficBuckets::ALL.each_with_object({}) { |b, acc| acc[b[:name]] = flat }
    end

    # Anchors a bucket's traffic-aware departure timestamp to a real weekday
    # within the actual week being scheduled (its Monday, taken from
    # `week_start_on`) at that bucket's representative time of day, rather
    # than defaulting to `Time.current` — which could land at midnight, on a
    # weekend, or any other moment with no relationship to when visits will
    # actually happen.
    #
    # KNOWN LIMITATION: this is still one snapshot of traffic per bucket,
    # reused across the whole week — it does not model Monday-vs-Friday
    # variation, only time-of-day variation within a day.
    def representative_departure_time(departure_minute)
      anchor_date = week_start_on || Time.zone.today
      hour, minute = departure_minute.divmod(60)
      candidate = Time.zone.local(anchor_date.year, anchor_date.month, anchor_date.day, hour, minute, 0)

      # Google's traffic model predicts future travel; a departure timestamp
      # in the past isn't meaningful. This only triggers when re-optimizing a
      # week that has already started (or already passed) — fall back to now.
      candidate < Time.current ? Time.current : candidate
    end
  end
end
