module Scheduling
  module Solvers
    # Calls the Python solver microservice with backend=bcp (VRPSolverEasy BCP).
    # Same serialization as Hgs — delegates to the same Python service.
    class Bcp < Hgs
      def initialize(input, time_budget: 3600, **_options)
        super(input, time_budget: time_budget)
        @backend = "bcp"
      end

      def solve
        response = HTTParty.post(
          "#{solver_url}/solve",
          query: { backend: @backend, time_budget: @time_budget },
          body: serialize_input(@input),
          headers: { "Content-Type" => "application/json" },
          timeout: @time_budget + 60,
          format: :json
        )

        unless response.success?
          raise "Python solver returned #{response.code}: #{response.body}"
        end

        deserialize_output(response.parsed_response)
      end
    end

    # Runs HGS first, feeds result to BCP as upper bound.
    class Pipeline < Hgs
      def initialize(input, time_budget: 3600, **_options)
        super(input, time_budget: time_budget)
        @backend = "pipeline"
      end

      def solve
        response = HTTParty.post(
          "#{solver_url}/solve",
          query: { backend: "pipeline", time_budget: @time_budget },
          body: serialize_input(@input),
          headers: { "Content-Type" => "application/json" },
          timeout: @time_budget + 60,
          format: :json
        )

        unless response.success?
          raise "Python solver returned #{response.code}: #{response.body}"
        end

        deserialize_output(response.parsed_response)
      end
    end
  end
end
