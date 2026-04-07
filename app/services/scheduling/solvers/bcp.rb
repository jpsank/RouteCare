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
        response = solver_post(
          query: { backend: @backend, time_budget: @time_budget },
          timeout: @time_budget + 60
        )
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
        response = solver_post(
          query: { backend: "pipeline", time_budget: @time_budget },
          timeout: @time_budget + 60
        )
        deserialize_output(response.parsed_response)
      end
    end
  end
end
