module Scheduling
  class Chromosome
    attr_reader :genes

    # genes: { visit_instance_id => Date }
    def initialize(genes)
      @genes = genes.dup
    end

    # Build from a greedy solution's day_routes
    def self.from_greedy(day_routes)
      genes = {}
      day_routes.each do |date, slots|
        slots.each do |slot|
          next unless slot[:instance_id]
          genes[slot[:instance_id]] = date
        end
      end
      new(genes)
    end

    # Create a random perturbation of a base chromosome
    def self.random_perturbation(base, working_days, swap_count: nil)
      child = base.dup
      swap_count ||= [ (child.genes.size * 0.3).ceil, 1 ].max
      instance_ids = child.genes.keys

      swap_count.times do
        id = instance_ids.sample
        child.genes[id] = working_days.sample
      end
      child
    end

    # NEAT-style crossover: align by visit instance ID, randomly inherit from one parent
    def crossover(other)
      child_genes = {}
      all_ids = (genes.keys + other.genes.keys).uniq

      all_ids.each do |id|
        if genes.key?(id) && other.genes.key?(id)
          child_genes[id] = rand < 0.5 ? genes[id] : other.genes[id]
        elsif genes.key?(id)
          child_genes[id] = genes[id]
        else
          child_genes[id] = other.genes[id]
        end
      end

      Chromosome.new(child_genes)
    end

    # Mutate in-place: move random visits to different days, or swap two visits between days
    def mutate!(working_days, rate: 0.3)
      instance_ids = genes.keys

      instance_ids.each do |id|
        next unless rand < rate

        if rand < 0.5
          # Move to random day
          genes[id] = working_days.sample
        else
          # Swap with another random visit's day
          other_id = instance_ids.sample
          genes[id], genes[other_id] = genes[other_id], genes[id]
        end
      end

      self
    end

    # Fraction of differing day assignments (for speciation)
    def structural_distance(other)
      common_ids = genes.keys & other.genes.keys
      return 1.0 if common_ids.empty?

      differing = common_ids.count { |id| genes[id] != other.genes[id] }
      differing.to_f / common_ids.size
    end

    # Decode chromosome into day_routes using nearest-neighbor ordering + retimer
    # instances: [VisitInstance] — indexed by id
    # fixed_slots_by_date: { Date => [{ patient:, starts_at:, ends_at: }] }
    # max_drive_minutes_per_day: Integer|nil — hard cap on daily drive time
    # Returns { day_routes: {Date => [slots]}, lunch_placements: {}, feasible: bool }
    def decode(travel_matrix:, instances:, fixed_slots_by_date: {}, retimer_options: {}, max_drive_minutes_per_day: nil)
      instance_index = instances.index_by(&:id)

      # Group free instances by assigned day
      day_groups = Hash.new { |h, k| h[k] = [] }
      genes.each do |instance_id, date|
        inst = instance_index[instance_id]
        next unless inst

        day_groups[date] << {
          patient: inst.patient,
          duration: inst.duration,
          instance_id: instance_id,
          soft_constraint_override: false
        }
      end

      day_routes = {}
      lunch_placements = {}
      feasible = true

      all_dates = (day_groups.keys + fixed_slots_by_date.keys).uniq
      all_dates.each do |date|
        slots = day_groups[date] || []

        # Nearest-neighbor ordering
        ordered = nearest_neighbor_order(slots, travel_matrix, retimer_options[:start_point])

        retimer = Scheduling::Retimer.new(
          travel_matrix: travel_matrix,
          locked_visits: fixed_slots_by_date[date] || [],
          **retimer_options
        )
        result = retimer.call(ordered, date)

        day_routes[date] = result[:slots] if result[:slots].any?
        lunch_placements[date.to_s] = result[:lunch] if result[:lunch]
        feasible = false unless result[:feasible]

        # Check max drive time per day
        if max_drive_minutes_per_day && result[:slots].any?
          day_drive = estimate_day_drive(result[:slots], travel_matrix)
          feasible = false if day_drive > max_drive_minutes_per_day
        end
      end

      # Check min_days_between_visits (hard constraint)
      patient_days = Hash.new { |h, k| h[k] = [] }
      day_routes.each do |date, slots|
        slots.each { |s| patient_days[s[:patient].id] << date }
      end
      patient_days.each do |patient_id, dates|
        next if dates.size < 2

        patient = instance_index.values.find { |i| i.patient_id == patient_id }&.patient
        next unless patient

        sorted = dates.sort
        sorted.each_cons(2) do |d1, d2|
          feasible = false if (d2 - d1).to_i < patient.min_days_between_visits
        end
      end

      { day_routes: day_routes, lunch_placements: lunch_placements, feasible: feasible }
    end

    def dup
      Chromosome.new(genes)
    end

    private

    def estimate_day_drive(slots, travel_matrix)
      sorted = slots.sort_by { |s| s[:starts_at] }
      total = 0
      sorted.each_cons(2) do |prev_slot, next_slot|
        total += (travel_matrix.dig(prev_slot[:patient].id, next_slot[:patient].id) || 0)
      end
      total
    end

    def nearest_neighbor_order(slots, travel_matrix, start_point = nil)
      return slots if slots.size <= 1

      remaining = slots.dup
      ordered = []
      previous_patient_id = nil

      while remaining.any?
        closest = remaining.min_by do |slot|
          if previous_patient_id.nil?
            0 # First visit — no preference
          else
            travel_matrix.dig(previous_patient_id, slot[:patient].id) || Float::INFINITY
          end
        end

        ordered << closest
        remaining.delete(closest)
        previous_patient_id = closest[:patient].id
      end

      ordered
    end
  end
end
