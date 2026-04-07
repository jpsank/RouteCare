# frozen_string_literal: true

module Scheduling
  # Routes weekly optimization to the Ruby greedy pipeline or the CP-SAT microservice.
  #
  # ENV:
  #   ROUTECARE_SCHEDULER_BACKEND — +greedy+ (default) or +cpsat+
  #   ROUTECARE_SCHEDULE_QUALITY — +fast+ (30s), +balanced+ (60s), +deep+ (120s); when set, overrides
  #     default for CP-SAT unless +ROUTECARE_CPSAT_TIME_BUDGET+ is also set (explicit seconds win).
  #   ROUTECARE_CPSAT_TIME_BUDGET — explicit CP-SAT seconds (default 60 if no quality preset, clamped 10..7200)
  # Test-only: ROUTECARE_TEST_CPSAT_FAIL=1 (with +RAILS_ENV=test+ and backend +cpsat+) simulates
  # RemoteSolverError for greedy fallback (see integration test).
  #
  # Greedy fallback runs only for Scheduling::RemoteSolverError (HTTP/connection/bad JSON from Python).
  class OptimizeDispatch
    def self.call(user:, week_start_on:, start_point: nil)
      case backend
      when :cpsat
        begin
          if Rails.env.test? && ENV["ROUTECARE_TEST_CPSAT_FAIL"] == "1"
            raise RemoteSolverError, "simulated CP-SAT failure (test)"
          end

          SolverRunner.run(
            user: user,
            week_start_on: week_start_on,
            start_point: start_point,
            backend: :cpsat,
            time_budget: cpsat_time_budget,
            warm_start_from_schedule: true
          )
        rescue RemoteSolverError => e
          Rails.logger.warn(
            "[OptimizeDispatch] CP-SAT failed (#{e.class}: #{e.message}); using greedy optimizer"
          )
          schedule = WeeklyOptimizer.new(
            user: user, week_start_on: week_start_on, start_point: start_point
          ).call
          augment_fallback_metadata!(schedule, e)
          schedule
        end
      else
        WeeklyOptimizer.new(user: user, week_start_on: week_start_on, start_point: start_point).call
      end
    end

    def self.backend
      case ENV.fetch("ROUTECARE_SCHEDULER_BACKEND", "greedy").to_s.strip.downcase
      when "cpsat" then :cpsat
      else :greedy
      end
    end

    def self.cpsat_time_budget
      if ENV.key?("ROUTECARE_CPSAT_TIME_BUDGET")
        return ENV.fetch("ROUTECARE_CPSAT_TIME_BUDGET").to_i.clamp(10, 7200)
      end

      case ENV.fetch("ROUTECARE_SCHEDULE_QUALITY", "").to_s.strip.downcase
      when "fast" then 30
      when "balanced" then 60
      when "deep" then 120
      else 60
      end.clamp(10, 7200)
    end

    def self.augment_fallback_metadata!(schedule, error)
      summary = (schedule.optimization_summary || {}).stringify_keys
      summary["scheduler_fallback"] = true
      summary["scheduler_fallback_reason"] = error.message.to_s.truncate(500)
      summary["scheduler_fallback_error_class"] = error.class.name
      schedule.update!(optimization_summary: summary)
    end
    private_class_method :augment_fallback_metadata!
  end
end
