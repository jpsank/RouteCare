module Scheduling
  # Abstract solver interface. All solver backends implement this contract.
  #
  # Solvers receive a SolverInputData struct (no ActiveRecord) and return
  # a SolverOutputData struct. The SchedulePersister handles DB persistence.
  #
  # Implementations:
  #   :greedy   — Regret insertion + ALNS (Ruby, fast, on-demand)
  #   :ga       — Genetic algorithm with HGS-inspired education (Ruby, nightly)
  #   :hgs      — PyVRP Hybrid Genetic Search (Python microservice, near-optimal)
  #   :bcp      — VRPSolverEasy Branch-Cut-and-Price (Python microservice, exact)
  #   :pipeline — HGS first, then BCP with upper bound (Python microservice, nightly)
  #
  module Solver
    def self.solve(input, backend: :greedy, **options)
      solver = case backend
               when :greedy   then Solvers::Greedy.new(input, **options)
               when :hgs      then Solvers::Hgs.new(input, **options)
               when :bcp      then Solvers::Bcp.new(input, **options)
               when :pipeline then Solvers::Pipeline.new(input, **options)
               else raise ArgumentError, "Unknown solver backend: #{backend}"
               end
      solver.solve
    end
  end
end
