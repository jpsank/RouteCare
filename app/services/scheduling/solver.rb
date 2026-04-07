module Scheduling
  # Abstract solver interface. All solver backends implement this contract.
  #
  # Solvers receive a SolverInputData struct (no ActiveRecord) and return
  # a SolverOutputData struct. The SchedulePersister handles DB persistence.
  #
  # Implementations:
  #   Scheduling::Solvers::Greedy  — regret insertion + ALNS (fast, on-demand)
  #   Scheduling::Solvers::Ga      — genetic algorithm (slower, nightly)
  #   (future) Scheduling::Solvers::Hgs   — HGS via Python microservice
  #   (future) Scheduling::Solvers::Bcp   — Branch-Cut-and-Price via PyVRP
  #
  module Solver
    def self.solve(input, backend: :greedy, **options)
      solver = case backend
               when :greedy then Solvers::Greedy.new(input, **options)
               when :ga     then Solvers::Ga.new(input, **options)
               else raise ArgumentError, "Unknown solver backend: #{backend}"
               end
      solver.solve
    end
  end
end
