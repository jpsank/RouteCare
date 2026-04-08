module Scheduling
  # High-level entry point that builds solver input from ActiveRecord,
  # runs a solver backend, and persists the result.
  #
  # Usage:
  #   Scheduling::SolverRunner.run(user:, week_start_on:, backend: :cpsat)
  #
  # CP-SAT: pass +upper_bound:+ (SolverOutputData) for a warm-started re-solve, or
  # +warm_start_from_schedule: true+ with +backend: :cpsat+ to build upper_bound from
  # the existing WeeklySchedule when instance ids align with persisted visits.
  #
  class SolverRunner
    def self.run(user:, week_start_on:, start_point: nil, backend: :cpsat, **options)
      new(user:, week_start_on:, start_point:, backend:, **options).run
    end

    def initialize(user:, week_start_on:, start_point: nil, backend: :cpsat, **options)
      @user = user
      @week_start_on = week_start_on.to_date.beginning_of_week(:monday)
      @start_point = start_point
      @backend = backend
      @options = options
    end

    def run
      input = SolverInput.build(user: @user, week_start_on: @week_start_on, start_point: @start_point)
      opts = @options.dup
      if @backend == :cpsat && opts.delete(:warm_start_from_schedule)
        opts[:upper_bound] ||= WarmStartOutput.from_schedule(
          user: @user, week_start_on: @week_start_on, input: input
        )
      end
      output = Solver.solve(input, backend: @backend, **opts)
      persist(input, output)
    end

    private

    def persist(input, output)
      persister = SchedulePersister.new(
        user: @user, week_start_on: @week_start_on,
        start_point: @start_point, routing_client: Integrations::RoutingClient.new
      )

      # Build day_routes from planned visits (format SchedulePersister expects)
      # Only visits explicitly locked by the clinician are preserved as immovable.
      # This must match the filter in SolverInput.build so the solver and persister agree.
      locked_visits = @user.weekly_schedules
        .find_by(week_start_on: @week_start_on)
        &.visits&.where(status: %w[confirmed completed], clinician_override: true)&.to_a || []

      # Map PlannedVisit structs to the slot hashes the persister expects
      patients_by_id = @user.clinician_profile.patients.index_by(&:id)
      day_routes = {}
      output.planned_visits.each do |pv|
        patient = patients_by_id[pv.patient_id]
        next unless patient

        day_routes[pv.date] ||= []
        day_routes[pv.date] << {
          patient: patient,
          date: pv.date,
          starts_at: pv.starts_at,
          ends_at: pv.ends_at,
          instance_id: pv.instance_id,
          soft_constraint_override: pv.soft_constraint_override
        }
      end

      persister.persist(
        day_routes: day_routes,
        lunch_placements: output.lunch_placements,
        locked_visits: locked_visits,
        metadata: output.metadata,
        travel_matrix: input.travel_matrix
      )
    end
  end
end
