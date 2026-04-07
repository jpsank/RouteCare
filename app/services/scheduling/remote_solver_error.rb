# frozen_string_literal: true

module Scheduling
  # Raised when the Python solver microservice is unreachable or returns HTTP errors / bad JSON.
  # Used so OptimizeDispatch falls back to greedy only for transport/API failures, not app bugs.
  class RemoteSolverError < StandardError
  end
end
