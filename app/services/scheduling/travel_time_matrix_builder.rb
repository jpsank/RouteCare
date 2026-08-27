module Scheduling
  class TravelTimeMatrixBuilder
    # Representative hour-of-day used for the traffic-aware departure timestamp.
    # 9am is a realistic mid-morning weekday driving time — not free-flowing
    # like the 5-6am hours, not the sharpest peak of rush hour either.
    REPRESENTATIVE_HOUR = 9

    # @param patients [Array<Patient>] patients to include in the matrix
    # @param home [Hash, nil] optional home node {lat:, lng:} — included with id :home
    # @param routing_client [Integrations::RoutingClient]
    # @param week_start_on [Date, nil] Monday of the week actually being scheduled.
    #   Anchors the traffic-aware departure timestamp to a real weekday in that
    #   week instead of "whatever moment the optimize button was clicked" —
    #   see #representative_departure_time.
    def initialize(patients:, home: nil, routing_client: Integrations::RoutingClient.new, week_start_on: nil)
      @patients = patients
      @home = home
      @routing_client = routing_client
      @week_start_on = week_start_on
    end

    def call
      points = patients.filter_map do |patient|
        next if patient.latitude.blank? || patient.longitude.blank?
        { id: patient.id, lat: patient.latitude, lng: patient.longitude }
      end

      if home
        points.unshift({ id: :home, lat: home[:lat], lng: home[:lng] })
      end

      return empty_matrix(points) if points.size < 2

      routing_client.travel_matrix(points, departure_time: representative_departure_time)
    end

    private

    attr_reader :patients, :home, :routing_client, :week_start_on

    def empty_matrix(points)
      points.each_with_object({}) { |p, m| m[p[:id]] = { p[:id] => 0 } }
    end

    # KNOWN LIMITATION: a single travel-time matrix is computed once per
    # optimization run and reused for every (patient, patient) pair regardless
    # of which day of the week or time of day that leg will actually be
    # driven. Genuinely time-of-day/day-of-week-aware routing would require a
    # separate provider call per exact departure timestamp for every possible
    # slot, which is prohibitively expensive — the matrix is computed once,
    # up front, before the solver evaluates a single candidate schedule.
    #
    # As a pragmatic middle ground, we anchor the one Google traffic-aware
    # request to a real, representative weekday morning within the actual
    # week being scheduled (its Monday, taken from `week_start_on`) rather
    # than defaulting to `Time.current` — which could land at midnight, on a
    # weekend, or any other moment with no relationship to when visits will
    # actually happen. This is strictly more accurate than "right now" but is
    # still one snapshot of traffic reused across the whole week and every
    # time of day within a day; it does not model rush-hour-vs-midday
    # variation, nor Monday-vs-Friday variation.
    def representative_departure_time
      anchor_date = week_start_on || Time.zone.today
      candidate = Time.zone.local(anchor_date.year, anchor_date.month, anchor_date.day, REPRESENTATIVE_HOUR, 0, 0)

      # Google's traffic model predicts future travel; a departure timestamp
      # in the past isn't meaningful. This only triggers when re-optimizing a
      # week that has already started (or already passed) — fall back to now.
      candidate < Time.current ? Time.current : candidate
    end
  end
end
