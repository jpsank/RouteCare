module Scheduling
  class GaOptimizer
    SPECIATION_THRESHOLD = 0.3
    TOURNAMENT_SIZE = 3
    ELITE_COUNT = 5
    STAGNATION_LIMIT = 50
    DIVERSITY_RESTART_THRESHOLD = 15  # Inject diversity after this many stagnant generations

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
            retimer_options: @retimer_options,
            max_drive_minutes_per_day: @clinician_profile.max_drive_minutes_per_day
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

        # Diversity injection: when stagnating, replace part of the population
        # with random individuals to break homogeneity
        if stagnation > 0 && (stagnation % DIVERSITY_RESTART_THRESHOLD).zero?
          mutation_rate = [ mutation_rate * 2.0, 0.5 ].min # Boost mutation temporarily
        end

        # Build next generation
        next_gen = []

        # Elitism: keep best, but also keep the most diverse individuals
        elites = scored.first(ELITE_COUNT).map { |s| s[:chromosome].dup }
        next_gen.concat(elites)

        # Diversity survivors: from remaining, pick individuals most different from elites
        remaining_scored = scored.drop(ELITE_COUNT)
        if remaining_scored.size > 2
          diverse = remaining_scored.sort_by do |s|
            -elites.map { |e| s[:chromosome].structural_distance(e) }.min
          end
          next_gen.concat(diverse.first(2).map { |s| s[:chromosome].dup })
        end

        # Fill remaining with offspring
        while next_gen.size < @population_size
          parent_a = tournament_select(species)
          parent_b = tournament_select(species)

          child = parent_a.crossover(parent_b)
          child.mutate!(@working_days, rate: mutation_rate)
          child = educate(child)

          next_gen << child
        end

        # Inject fresh random individuals when stagnating
        if stagnation > 0 && (stagnation % DIVERSITY_RESTART_THRESHOLD).zero?
          inject_count = (@population_size * 0.3).ceil
          inject_count.times do
            random = Chromosome.random_perturbation(best_chromosome, @working_days, swap_count: (best_chromosome.genes.size * 0.5).ceil)
            random = educate(random)
            next_gen.pop # Remove last offspring to make room
            next_gen << random
          end
        end

        population = next_gen
        mutation_rate = [ mutation_rate * 0.97, 0.05 ].max # Slower cooling
      end

      # Decode best if we haven't yet
      best_decoded ||= best_chromosome.decode(
        travel_matrix: @travel_matrix,
        instances: @instances,
        fixed_slots_by_date: @fixed_slots_by_date,
        retimer_options: @retimer_options,
        max_drive_minutes_per_day: @clinician_profile.max_drive_minutes_per_day
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
        required_break_minutes: @clinician_profile.required_break_minutes,
        charting_buffer_minutes: @clinician_profile.charting_buffer_minutes
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
      pool = species.flatten
      tournament = pool.sample(TOURNAMENT_SIZE)
      tournament.min_by { |s| s[:fitness] }[:chromosome]
    end

    # HGS-inspired education: local search on a child's day assignments.
    # Tries relocate and swap moves, keeping improvements. Makes every
    # individual a local optimum before it enters the population.
    def educate(chromosome)
      instance_index = @instances.index_by(&:id)
      genes = chromosome.genes
      ids = genes.keys

      improved = true
      max_passes = 3
      pass = 0

      while improved && pass < max_passes
        improved = false
        pass += 1

        # Relocate: try moving each visit to a better day
        ids.each do |visit_id|
          current_day = genes[visit_id]
          current_cost = day_gene_cost(genes, current_day, instance_index)
          patient_id = instance_index[visit_id]&.patient_id

          @working_days.each do |candidate_day|
            next if candidate_day == current_day
            # One-patient-per-day check
            next if genes.any? { |vid, d| d == candidate_day && instance_index[vid]&.patient_id == patient_id && vid != visit_id }

            # Try the move
            old_candidate_cost = day_gene_cost(genes, candidate_day, instance_index)
            genes[visit_id] = candidate_day
            new_from_cost = day_gene_cost(genes, current_day, instance_index)
            new_to_cost = day_gene_cost(genes, candidate_day, instance_index)

            if (new_from_cost + new_to_cost) < (current_cost + old_candidate_cost)
              current_day = candidate_day
              current_cost = new_to_cost
              improved = true
            else
              genes[visit_id] = current_day # revert
            end
          end
        end

        # Swap: try exchanging two visits between different days
        ids.each_with_index do |id_a, i|
          ((i + 1)...ids.size).each do |j|
            id_b = ids[j]
            day_a = genes[id_a]
            day_b = genes[id_b]
            next if day_a == day_b

            pid_a = instance_index[id_a]&.patient_id
            pid_b = instance_index[id_b]&.patient_id

            # Check one-patient-per-day after swap
            next if genes.any? { |vid, d| d == day_b && instance_index[vid]&.patient_id == pid_a && vid != id_a && vid != id_b }
            next if genes.any? { |vid, d| d == day_a && instance_index[vid]&.patient_id == pid_b && vid != id_a && vid != id_b }

            old_cost = day_gene_cost(genes, day_a, instance_index) + day_gene_cost(genes, day_b, instance_index)
            genes[id_a] = day_b
            genes[id_b] = day_a
            new_cost = day_gene_cost(genes, day_a, instance_index) + day_gene_cost(genes, day_b, instance_index)

            if new_cost < old_cost
              improved = true
            else
              genes[id_a] = day_a
              genes[id_b] = day_b
            end
          end
        end
      end

      chromosome
    end

    # Route cost for a day based on chromosome genes (no decoding needed)
    def day_gene_cost(genes, date, instance_index)
      patient_ids = genes.select { |_, d| d == date }.filter_map { |vid, _| instance_index[vid]&.patient_id }
      return 0.0 if patient_ids.empty?

      # Nearest-neighbor order using travel matrix
      ordered = []
      remaining = patient_ids.dup
      current_id = :home

      while remaining.any?
        closest = remaining.min_by { |pid| @travel_matrix.dig(current_id, pid) || Float::INFINITY }
        ordered << closest
        remaining.delete_at(remaining.index(closest))
        current_id = closest
      end

      cost = @travel_matrix.dig(:home, ordered.first) || 0
      ordered.each_cons(2) { |a, b| cost += (@travel_matrix.dig(a, b) || 0) }
      cost += (@travel_matrix.dig(ordered.last, :home) || 0)
      cost.to_f
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
