module Scheduling
  class GaOptimizer
    SPECIATION_THRESHOLD = 0.3
    TOURNAMENT_SIZE = 3
    ELITE_COUNT = 5
    STAGNATION_LIMIT = 20

    def initialize(user:, week_start_on:, time_budget: 45, population_size: 50, start_point: nil)
      @user = user
      @week_start_on = week_start_on.to_date.beginning_of_week(:monday)
      @clinician_profile = user.clinician_profile
      @time_budget = time_budget
      @population_size = population_size
      @start_point = start_point
      @routing_client = Integrations::RoutingClient.new
    end

    def call
      raise ArgumentError, "Clinician profile is required" unless @clinician_profile

      solution = generate_solution
      persister = Scheduling::SchedulePersister.new(
        user: @user, week_start_on: @week_start_on, start_point: @start_point, routing_client: @routing_client
      )

      # Load locked visits
      schedule = @user.weekly_schedules.find_by(week_start_on: @week_start_on)
      locked_visits = schedule ? schedule.visits.where(status: %w[confirmed completed]).to_a : []

      # Only persist if this is better than the current schedule
      if schedule && schedule.total_drive_minutes > 0
        current_drive = schedule.total_drive_minutes
        new_drive = solution[:day_routes].values.flatten.sum { |s| s[:drive_from_previous_minutes] || 0 }
        if new_drive >= current_drive && solution[:fitness] >= (solution[:metadata][:greedy_fitness] || Float::INFINITY)
          return schedule
        end
      end

      persister.persist(
        day_routes: solution[:day_routes],
        lunch_placements: solution[:lunch_placements],
        locked_visits: locked_visits,
        metadata: solution[:metadata],
        travel_matrix: solution[:travel_matrix]
      )
    end

    def generate_solution
      raise ArgumentError, "Clinician profile is required" unless @clinician_profile

      setup_shared_state

      # Seed population: 1 greedy + (N-1) random perturbations
      greedy_optimizer = Scheduling::WeeklyOptimizer.new(
        user: @user, week_start_on: @week_start_on, start_point: @start_point
      )
      greedy_solution = greedy_optimizer.generate_solution
      greedy_chromosome = Chromosome.from_greedy(greedy_solution[:day_routes])
      greedy_fitness = greedy_solution[:fitness]

      population = [ greedy_chromosome ]
      (@population_size - 1).times do
        population << Chromosome.random_perturbation(greedy_chromosome, @working_days)
      end

      best_chromosome = greedy_chromosome
      best_fitness = greedy_fitness
      best_decoded = nil
      stagnation = 0
      generation = 0
      mutation_rate = 0.3
      start_time = Process.clock_gettime(Process::CLOCK_MONOTONIC)

      loop do
        elapsed = Process.clock_gettime(Process::CLOCK_MONOTONIC) - start_time
        break if elapsed >= @time_budget
        break if stagnation >= STAGNATION_LIMIT

        generation += 1

        # Evaluate fitness for all chromosomes
        scored = population.filter_map do |chromo|
          decoded = chromo.decode(
            travel_matrix: @travel_matrix,
            instances: @instances,
            fixed_slots_by_date: @fixed_slots_by_date,
            retimer_options: @retimer_options
          )
          next unless decoded[:feasible]

          fitness = @fitness_fn.score(
            day_routes: decoded[:day_routes],
            lunch_placements: decoded[:lunch_placements]
          )

          # Check one-patient-per-day constraint
          valid = decoded[:day_routes].all? do |_date, slots|
            patient_ids = slots.map { |s| s[:patient].id }
            patient_ids.size == patient_ids.uniq.size
          end
          next unless valid

          { chromosome: chromo, fitness: fitness, decoded: decoded }
        end

        # Keep at least the greedy seed
        if scored.empty?
          stagnation += 1
          next
        end

        scored.sort_by! { |s| s[:fitness] }

        # Track best
        if scored.first[:fitness] < best_fitness
          best_chromosome = scored.first[:chromosome]
          best_fitness = scored.first[:fitness]
          best_decoded = scored.first[:decoded]
          stagnation = 0
        else
          stagnation += 1
        end

        # Speciate
        species = speciate(scored)

        elapsed_now = Process.clock_gettime(Process::CLOCK_MONOTONIC) - start_time
        Rails.logger.info(
          "[GA] gen=#{generation} best=#{best_fitness.round(2)} " \
          "feasible=#{scored.size}/#{population.size} species=#{species.size} " \
          "mutation=#{mutation_rate.round(3)} elapsed=#{elapsed_now.round(1)}s"
        )

        # Build next generation
        next_gen = []

        # Elitism: carry forward top individuals
        next_gen.concat(scored.first(ELITE_COUNT).map { |s| s[:chromosome].dup })

        # Fill remaining with offspring
        while next_gen.size < @population_size
          # Tournament selection
          parent_a = tournament_select(species)
          parent_b = tournament_select(species)

          child = parent_a.crossover(parent_b)
          child.mutate!(@working_days, rate: mutation_rate)
          next_gen << child
        end

        population = next_gen
        mutation_rate *= 0.95 # Cool
      end

      # Decode best if we haven't yet
      best_decoded ||= best_chromosome.decode(
        travel_matrix: @travel_matrix,
        instances: @instances,
        fixed_slots_by_date: @fixed_slots_by_date,
        retimer_options: @retimer_options
      )

      {
        day_routes: best_decoded[:day_routes],
        lunch_placements: best_decoded[:lunch_placements],
        travel_matrix: @travel_matrix,
        fitness: best_fitness,
        metadata: {
          optimizer_type: "ga",
          generations_run: generation,
          greedy_fitness: greedy_fitness,
          fitness_improvement_pct: greedy_fitness > 0 ? ((greedy_fitness - best_fitness) / greedy_fitness * 100).round(1) : 0,
          population_size: @population_size,
          time_budget: @time_budget
        }
      }
    end

    private

    def setup_shared_state
      schedule = @user.weekly_schedules.find_by(week_start_on: @week_start_on)
      locked_visits = schedule ? schedule.visits.where(status: %w[confirmed completed]).to_a : []

      @instances = Scheduling::VisitInstanceBuilder.new(
        patients: @clinician_profile.patients.active.includes(:patient_availability_windows),
        locked_visits: locked_visits,
        charting_buffer_minutes: @clinician_profile.charting_buffer_minutes
      ).call

      all_patients = (@instances.map(&:patient) + locked_visits.map(&:patient)).uniq
      @travel_matrix = Scheduling::TravelTimeMatrixBuilder.new(patients: all_patients).call

      @fixed_slots_by_date = locked_visits.group_by { |v| v.starts_at.to_date }

      @working_days = working_days_for_week

      start_pt = @start_point || @clinician_profile.home_point
      @retimer_options = {
        lunch_config: lunch_break_config,
        day_start_minute: @clinician_profile.workday_start_minute,
        day_end_minute: @clinician_profile.workday_end_minute,
        start_point: start_pt,
        routing_client: @routing_client,
        max_continuous_work_minutes: @clinician_profile.max_continuous_work_minutes,
        required_break_minutes: @clinician_profile.required_break_minutes
      }

      @fitness_fn = Scheduling::FitnessFunction.new(
        travel_matrix: @travel_matrix,
        instances: @instances,
        clinician_profile: @clinician_profile,
        start_point: start_pt
      )
    end

    def speciate(scored)
      species = []
      scored.each do |individual|
        placed = false
        species.each do |group|
          representative = group.first[:chromosome]
          if individual[:chromosome].structural_distance(representative) < SPECIATION_THRESHOLD
            group << individual
            placed = true
            break
          end
        end
        species << [ individual ] unless placed
      end
      species
    end

    def tournament_select(species)
      # Flatten all species for selection pool
      pool = species.flatten
      tournament = pool.sample(TOURNAMENT_SIZE)
      tournament.min_by { |s| s[:fitness] }[:chromosome]
    end

    def working_days_for_week
      wdays = @clinician_profile.working_day_wdays
      wdays.map do |wday|
        offset = (wday - 1) % 7
        @week_start_on + offset.days
      end
    end

    def lunch_break_config
      lr = @clinician_profile.lunch_range
      {
        earliest_start: lr[:earliest_start_minute],
        latest_start: lr[:latest_start_minute],
        duration: lr[:duration_minutes]
      }
    end
  end
end
