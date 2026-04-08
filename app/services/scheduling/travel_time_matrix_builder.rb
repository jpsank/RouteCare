module Scheduling
  class TravelTimeMatrixBuilder
    # @param patients [Array<Patient>] patients to include in the matrix
    # @param home [Hash, nil] optional home node {lat:, lng:} — included with id :home
    # @param routing_client [Integrations::RoutingClient]
    def initialize(patients:, home: nil, routing_client: Integrations::RoutingClient.new)
      @patients = patients
      @home = home
      @routing_client = routing_client
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

      routing_client.travel_matrix(points)
    end

    private

    attr_reader :patients, :home, :routing_client

    def empty_matrix(points)
      points.each_with_object({}) { |p, m| m[p[:id]] = { p[:id] => 0 } }
    end
  end
end
