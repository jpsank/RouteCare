module Scheduling
  class FeasibilityChecker
    FeasibilityResult = Data.define(:feasible, :violations)
    Violation = Data.define(:constraint, :node_id, :message)

    def initialize(
      travel_matrix:,
      clinician_profile:,
      fixed_nodes:,
      instances:
    )
      @travel_matrix = travel_matrix
      @clinician_profile = clinician_profile
      @fixed_nodes = fixed_nodes
      @instances = instances
    end

    # day_routes: { Date => [{ patient:, starts_at:, ends_at:, instance_id:, ... }] }
    # lunch_placements: { Date => { start_minute:, end_minute: } }
    def check(day_routes:, lunch_placements: {})
      violations = []

      violations.concat(check_all_instances_assigned(day_routes))
      violations.concat(check_one_patient_per_day(day_routes))
      violations.concat(check_workday_bounds(day_routes))
      violations.concat(check_transit_feasibility(day_routes))
      violations.concat(check_no_overlap_with_fixed(day_routes))
      violations.concat(check_unavailability_windows(day_routes))
      violations.concat(check_max_drive_per_day(day_routes))
      violations.concat(check_mandatory_breaks(day_routes))
      violations.concat(check_lunch_exists(day_routes, lunch_placements))

      FeasibilityResult.new(feasible: violations.empty?, violations: violations)
    end

    private

    # Constraint 1: every free instance assigned exactly once
    def check_all_instances_assigned(day_routes)
      assigned_ids = day_routes.values.flatten.filter_map { |s| s[:instance_id] }
      expected_ids = @instances.map(&:id)

      missing = expected_ids - assigned_ids
      duplicates = assigned_ids.select { |id| assigned_ids.count(id) > 1 }.uniq

      violations = []
      missing.each { |id| violations << Violation.new(constraint: :all_assigned, node_id: id, message: "Instance #{id} not assigned") }
      duplicates.each { |id| violations << Violation.new(constraint: :all_assigned, node_id: id, message: "Instance #{id} assigned multiple times") }
      violations
    end

    # Constraint 3: at most one instance of each patient per day
    def check_one_patient_per_day(day_routes)
      violations = []
      day_routes.each do |date, slots|
        patient_ids = slots.map { |s| s[:patient].id }
        patient_ids.select { |id| patient_ids.count(id) > 1 }.uniq.each do |pid|
          violations << Violation.new(constraint: :one_per_day, node_id: "patient_#{pid}", message: "Patient #{pid} has multiple visits on #{date}")
        end
      end
      violations
    end

    # Constraint 6: workday bounds
    def check_workday_bounds(day_routes)
      ws = @clinician_profile.workday_start_minute
      we = @clinician_profile.workday_end_minute
      violations = []

      day_routes.each do |date, slots|
        slots.each do |slot|
          start_min = minute_of_day(slot[:starts_at])
          end_min = minute_of_day(slot[:ends_at])
          node_id = slot[:instance_id] || "visit_#{slot[:patient].id}_#{date}"

          if start_min < ws
            violations << Violation.new(constraint: :workday_bounds, node_id: node_id, message: "Visit starts at #{start_min} before workday start #{ws}")
          end
          if end_min > we
            violations << Violation.new(constraint: :workday_bounds, node_id: node_id, message: "Visit ends at #{end_min} after workday end #{we}")
          end
        end
      end
      violations
    end

    # Constraint 4: transit feasibility — gap between consecutive nodes covers travel time
    def check_transit_feasibility(day_routes)
      violations = []
      day_routes.each do |_date, slots|
        sorted = slots.sort_by { |s| s[:starts_at] }
        sorted.each_cons(2) do |prev_slot, next_slot|
          gap_minutes = (next_slot[:starts_at] - prev_slot[:ends_at]) / 60.0
          travel_time = @travel_matrix.dig(prev_slot[:patient].id, next_slot[:patient].id) || 0

          if gap_minutes < travel_time
            node_id = next_slot[:instance_id] || "visit_#{next_slot[:patient].id}"
            violations << Violation.new(
              constraint: :transit_feasibility,
              node_id: node_id,
              message: "Gap of #{gap_minutes.round(1)}min insufficient for #{travel_time}min travel"
            )
          end
        end
      end
      violations
    end

    # Constraint 5: no overlap with fixed nodes (calendar blocks + confirmed visits)
    def check_no_overlap_with_fixed(day_routes)
      violations = []
      day_routes.each do |date, slots|
        fixed_on_day = @fixed_nodes.select { |f| f[:date] == date }
        slots.each do |slot|
          fixed_on_day.each do |fixed|
            if slot[:starts_at] < fixed[:ends_at] && slot[:ends_at] > fixed[:starts_at]
              node_id = slot[:instance_id] || "visit_#{slot[:patient].id}_#{date}"
              violations << Violation.new(constraint: :no_overlap_fixed, node_id: node_id, message: "Overlaps with fixed node #{fixed[:id]}")
            end
          end
        end
      end
      violations
    end

    # Constraint: visits must never overlap a patient's unavailable (blackout) window
    def check_unavailability_windows(day_routes)
      instances_by_id = @instances.index_by(&:id)
      violations = []

      day_routes.each do |date, slots|
        slots.each do |slot|
          instance = instances_by_id[slot[:instance_id]]
          next unless instance
          next unless instance.respond_to?(:unavailability_windows)

          windows = instance.unavailability_windows[date.wday]
          next if windows.blank?

          start_min = minute_of_day(slot[:starts_at])
          end_min = minute_of_day(slot[:ends_at])

          windows.each do |w|
            w_start = w[:start_minute] || w["start_minute"]
            w_end = w[:end_minute] || w["end_minute"]
            next unless start_min < w_end && end_min > w_start

            node_id = slot[:instance_id] || "visit_#{slot[:patient].id}_#{date}"
            violations << Violation.new(
              constraint: :unavailability_window,
              node_id: node_id,
              message: "Visit [#{start_min},#{end_min}) overlaps unavailable window [#{w_start},#{w_end}) on #{date}"
            )
          end
        end
      end
      violations
    end

    # Constraint 7: max drive time per day
    def check_max_drive_per_day(day_routes)
      max_drive = @clinician_profile.max_drive_minutes_per_day
      return [] unless max_drive

      violations = []
      day_routes.each do |date, slots|
        sorted = slots.sort_by { |s| s[:starts_at] }
        total_drive = sorted.sum { |s| s[:drive_from_previous_minutes] || 0 }

        if total_drive > max_drive
          violations << Violation.new(constraint: :max_drive, node_id: "day_#{date}", message: "Drive time #{total_drive}min exceeds max #{max_drive}min")
        end
      end
      violations
    end

    # Constraint 8: mandatory break after continuous work
    def check_mandatory_breaks(day_routes)
      max_continuous = @clinician_profile.max_continuous_work_minutes
      return [] if max_continuous.blank?

      violations = []
      day_routes.each do |date, slots|
        sorted = slots.sort_by { |s| s[:starts_at] }
        accumulated = 0

        sorted.each_cons(2) do |prev_slot, next_slot|
          duration = (prev_slot[:ends_at] - prev_slot[:starts_at]) / 60.0
          gap = (next_slot[:starts_at] - prev_slot[:ends_at]) / 60.0
          accumulated += duration

          if gap >= @clinician_profile.required_break_minutes
            accumulated = 0
          elsif accumulated > max_continuous
            violations << Violation.new(
              constraint: :mandatory_break,
              node_id: "day_#{date}",
              message: "#{accumulated.round(0)}min continuous work exceeds #{max_continuous}min without #{@clinician_profile.required_break_minutes}min break"
            )
            accumulated = 0
          end
        end
      end
      violations
    end

    # Constraint 9: lunch must occur within configured window
    def check_lunch_exists(day_routes, lunch_placements)
      lunch_range = @clinician_profile.lunch_range
      violations = []

      day_routes.each_key do |date|
        lunch = lunch_placements[date] || lunch_placements[date.to_s]
        unless lunch
          violations << Violation.new(constraint: :lunch_exists, node_id: "day_#{date}", message: "No lunch break scheduled")
          next
        end

        earliest = lunch_range[:earliest_start_minute]
        latest = lunch_range[:latest_start_minute] + lunch_range[:duration_minutes]
        if lunch[:start_minute] < earliest || lunch[:end_minute] > latest
          violations << Violation.new(constraint: :lunch_exists, node_id: "day_#{date}", message: "Lunch at #{lunch[:start_minute]}-#{lunch[:end_minute]} outside window #{earliest}-#{latest}")
        end
      end
      violations
    end

    def minute_of_day(time)
      time.hour * 60 + time.min
    end
  end
end
