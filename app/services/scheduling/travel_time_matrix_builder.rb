module Scheduling
  class TravelTimeMatrixBuilder
    def initialize(patients:, routing_client: Integrations::RoutingClient.new)
      @patients = patients
      @routing_client = routing_client
    end

    def call
      points = patients.filter_map do |patient|
        next if patient.latitude.blank? || patient.longitude.blank?

        { id: patient.id, lat: patient.latitude, lng: patient.longitude }
      end

      return empty_matrix if points.size < 2

      routing_client.travel_matrix(points)
    end

    private

    attr_reader :patients, :routing_client

    def empty_matrix
      patients.each_with_object({}) do |patient, matrix|
        matrix[patient.id] = { patient.id => 0 }
      end
    end
  end
end
