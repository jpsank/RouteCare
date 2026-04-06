module Scheduling
  class FitnessFunction
    DEFAULT_WEIGHTS = {
      drive_time: 1.0,
      spacing: 0.5,
      availability: 0.3,
      density: 0.2,
      lunch: 0.1
    }.freeze

    def initialize(
      travel_matrix:,
      instances:,
      clinician_profile:,
      start_point: nil,
      routing_client: Integrations::RoutingClient.new,
      weights: DEFAULT_WEIGHTS
    )
      @travel_matrix = travel_matrix
      @instances = instances
      @clinician_profile = clinician_profile
      @start_point = start_point
      @routing_client = routing_client
      @weights = weights
    end

    # day_routes: { Date => [{ patient:, starts_at:, ends_at:, instance_id:, ... }] }
    # lunch_placements: { Date => { start_minute:, end_minute: } }
    def score(day_routes:, lunch_placements: {})
      @weights[:drive_time] * drive_time_cost(day_routes) +
        @weights[:spacing] * spacing_penalty(day_routes) +
        @weights[:availability] * availability_penalty(day_routes) +
        @weights[:density] * density_penalty(day_routes) +
        @weights[:lunch] * lunch_penalty(lunch_placements)
    end

    private

    # Sum of all travel times across all days
    def drive_time_cost(day_routes)
      total = 0.0
      day_routes.each_value do |slots|
        sorted = slots.sort_by { |s| s[:starts_at] }
        previous_patient_id = nil

        sorted.each do |slot|
          if previous_patient_id.nil?
            total += travel_from_start(slot[:patient]) if @start_point
          else
            total += (@travel_matrix.dig(previous_patient_id, slot[:patient].id) || 0)
          end
          previous_patient_id = slot[:patient].id
        end
      end
      total
    end

    # Penalty for visits to the same patient being too close or too far apart
    def spacing_penalty(day_routes)
      # Group all visits by patient_id across all days
      patient_days = Hash.new { |h, k| h[k] = [] }
      day_routes.each do |date, slots|
        slots.each do |slot|
          patient_days[slot[:patient].id] << date
        end
      end

      penalty = 0.0
      patient_days.each do |patient_id, dates|
        next if dates.size < 2

        patient = @instances.find { |i| i.patient_id == patient_id }&.patient
        next unless patient

        sorted_dates = dates.sort
        sorted_dates.each_cons(2) do |d1, d2|
          gap = (d2 - d1).to_i
          # Too close
          if gap < patient.min_days_between_visits
            penalty += (patient.min_days_between_visits - gap) * 10.0
          end
          # Too far
          if gap > patient.max_days_between_visits
            penalty += (gap - patient.max_days_between_visits) * 5.0
          end
        end
      end
      penalty
    end

    # Penalty for visits scheduled outside patient availability windows
    def availability_penalty(day_routes)
      penalty = 0.0
      day_routes.each do |date, slots|
        slots.each do |slot|
          instance = @instances.find { |i| i.id == slot[:instance_id] }
          next unless instance

          windows = instance.availability_windows[date.wday]
          next if windows.nil? || windows.empty?

          start_min = slot[:starts_at].hour * 60 + slot[:starts_at].min
          in_window = windows.any? { |w| start_min >= w.start_minute && start_min < w.end_minute }
          penalty += 15.0 unless in_window
        end
      end
      penalty
    end

    # Penalty for schedule density mismatch
    def density_penalty(day_routes)
      density = @clinician_profile.schedule_density
      working_days = @clinician_profile.working_day_wdays
      counts = working_days.map { |wday| 0 }

      day_routes.each do |date, slots|
        idx = working_days.index(date.wday)
        counts[idx] += slots.size if idx
      end

      active_days = counts.count { |c| c > 0 }

      # density=1 wants fewer days (penalize spreading), density=0 wants evenness
      spread_penalty = density * active_days
      variance = counts.empty? ? 0.0 : variance_of(counts)
      evenness_penalty = (1.0 - density) * variance

      spread_penalty + evenness_penalty
    end

    # Penalty for lunch drifting from target
    def lunch_penalty(lunch_placements)
      target = @clinician_profile.lunch_start_minute
      penalty = 0.0

      lunch_placements.each_value do |lunch|
        next unless lunch

        drift = (lunch[:start_minute] - target).abs
        penalty += drift * 0.1
      end
      penalty
    end

    def travel_from_start(patient)
      return 0 unless @start_point

      @routing_client.travel_minutes(
        origin: @start_point,
        destination: { lat: patient.latitude, lng: patient.longitude }
      )
    end

    def variance_of(values)
      return 0.0 if values.empty?

      mean = values.sum.to_f / values.size
      values.sum { |v| (v - mean)**2 } / values.size
    end
  end
end
