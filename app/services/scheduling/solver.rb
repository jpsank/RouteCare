module Scheduling
  # Solver dispatch. Backends receive a SolverInputData struct (no ActiveRecord)
  # and return a SolverOutputData struct. SchedulePersister handles DB persistence.
  #
  # Implementations:
  #   :cpsat    — CP-SAT decomposed solver (Python microservice, full constraints)
  #   :greedy   — Regret insertion + ALNS (Ruby, fast fallback)
  #
  module Solver
    def self.solve(input, backend: :cpsat, **options)
      solver = case backend
      when :cpsat    then Solvers::Cpsat.new(input, **options)
      when :greedy   then Solvers::Greedy.new(input, **options)
      else raise ArgumentError, "Unknown solver backend: #{backend}"
      end
      solver.solve
    end
  end
end
