module Scheduling
  class TravelTimeMatrixBuilder
    def initialize(patients:, routing_client: Integrations::RoutingClient.new)
      @patients = patients
      @routing_client = routing_client
    end

    def call
      patients.each_with_object({}) do |origin, matrix|
        matrix[origin.id] = {}

        patients.each do |destination|
          matrix[origin.id][destination.id] =
            if origin.id == destination.id
              0
            else
              routing_client.travel_minutes(
                origin: point_for(origin),
                destination: point_for(destination)
              )
            end
        end
      end
    end

    private

    attr_reader :patients, :routing_client

    def point_for(patient)
      { lat: patient.latitude, lng: patient.longitude }
    end
  end
end
