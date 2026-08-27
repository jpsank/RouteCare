module Scheduling
  module Solvers
    class Greedy
      SLOT_STEP_MINUTES = 15
      MAX_VISITS_PER_DAY = 5
      HOME_NODE_ID = :home
      ALNS_MAX_ITERATIONS = 30
      ALNS_DESTROY_FRACTION_MIN = 0.2
      ALNS_DESTROY_FRACTION_MAX = 0.4

      # Adapter so PatientData structs respond to methods the Retimer expects
      PatientAdapter = Struct.new(:id, :latitude, :longitude, :visit_duration_minutes, :min_days_between_visits, :max_days_between_visits, :priority, :name, :required_visits_per_week, :availability_windows, :unavailability_windows, keyword_init: true) do
        def unavailable_windows_for_wday(wday)
          unavailability_windows&.[](wday) || []
        end
      end

      def self.adapt_patient(patient_data)
        loc = patient_data.location
        PatientAdapter.new(
          id: patient_data.id, name: patient_data.name,
          latitude: loc&.lat, longitude: loc&.lng,
          visit_duration_minutes: patient_data.visit_duration_minutes,
          required_visits_per_week: patient_data.required_visits_per_week,
          min_days_between_visits: patient_data.min_days_between_visits,
          max_days_between_visits: patient_data.max_days_between_visits,
          priority: patient_data.priority,
          availability_windows: patient_data.availability_windows,
          unavailability_windows: patient_data.unavailability_windows || {}
        )
      end

      def initialize(input, **_options)
        @input = input
        @clinician = input.clinician
        @travel_matrix = input.travel_matrix
        @instances = input.instances
        @locked_visits = input.locked_visits
        @calendar_blocks = input.calendar_blocks
        @working_days = input.working_days
        @adapted_patients = input.patients.map { |p| self.class.adapt_patient(p) }
        @patients_by_id = @adapted_patients.index_by(&:id)
        @charting_buffer = @clinician.charting_buffer_minutes
        compute_global_avg_travel
      end

      def solve
        visit_plan, soft_constraint_count, unschedulable = build_visit_plan
        visit_plan = post_optimize(visit_plan)

        # Build day routes with ordering + retiming
        grouped = visit_plan.group_by { |slot| slot[:date] }
        locked_by_date = @locked_visits.group_by(&:date)
        day_routes = {}
        lunch_placements = {}

        all_dates = (grouped.keys + locked_by_date.keys).uniq
        all_dates.each do |date|
          new_slots = grouped[date] || []
          locked = locked_by_date[date] || []

          ordered_slots = two_opt_order(nearest_neighbor_order(new_slots))

          day_start, day_end = day_bounds(date)
          retimer = Scheduling::Retimer.new(
            travel_matrix: @travel_matrix,
            locked_visits: locked,
            lunch_config: lunch_break_config(date),
            day_start_minute: day_start,
            day_end_minute: day_end,
            start_point: home_point,
            max_continuous_work_minutes: @clinician.max_continuous_work_minutes,
            required_break_minutes: @clinician.required_break_minutes,
            charting_buffer_minutes: @clinician.charting_buffer_minutes
          )
          result = retimer.call(ordered_slots, date)

          day_routes[date] = result[:slots] if result[:slots].any?
          lunch_placements[date.to_s] = result[:lunch] if result[:lunch]
        end

        # Max drive violations (soft warning)
        max_drive = @clinician.max_drive_minutes_per_day
        drive_violations = max_drive ? check_max_drive_violations(day_routes, locked_by_date, max_drive) : []

        # Return-home per day
        return_home_by_day = {}
        day_routes.each do |date, slots|
          locked = locked_by_date[date] || []
          all_day = slots + locked.map { |v| { patient_id: v.patient_id } }
          next if all_day.empty?
          ordered = nearest_neighbor_order(all_day)
          last_id = ordered.last[:patient_id] || ordered.last.dig(:patient, :id)
          return_home_by_day[date.to_s] = travel_between(last_id, HOME_NODE_ID) if last_id
        end

        # Fill lunch for working days with no visits
        if lunch_break_config
          @working_days.each do |date|
            next if lunch_placements.key?(date.to_s)
            lunch_placements[date.to_s] = {
              start_minute: lunch_break_config[:earliest_start],
              end_minute: lunch_break_config[:earliest_start] + lunch_break_config[:duration]
            }
          end
        end

        # Convert to SolverOutputData
        planned_visits = day_routes.flat_map do |date, slots|
          slots.map do |slot|
            Scheduling::PlannedVisit.new(
              instance_id: slot[:instance_id],
              patient_id: slot[:patient_id] || slot.dig(:patient, :id),
              date: date,
              starts_at: slot[:starts_at],
              ends_at: slot[:ends_at],
              soft_constraint_override: slot[:soft_constraint_override] || false
            )
          end
        end

        Scheduling::SolverOutputData.new(
          planned_visits: planned_visits,
          lunch_placements: lunch_placements,
          fitness: 0.0, # Computed by caller if needed
          metadata: {
            soft_constraint_overrides: soft_constraint_count,
            patient_count: visit_plan.map { |s| s[:patient_id] || s.dig(:patient, :id) }.compact.uniq.size,
            visit_count: visit_plan.size,
            unschedulable: unschedulable,
            drive_violations: drive_violations,
            return_home_by_day: return_home_by_day,
            optimizer_type: "greedy"
          }
        )
      end

      private

      # ── Helpers ──────────────────────────────────────────────────────────

      def home_point
        loc = @clinician.home_location
        loc ? { lat: loc.lat, lng: loc.lng } : nil
      end

      def travel_between(from_id, to_id)
        return 0 if from_id.nil? || to_id.nil? || from_id == to_id
        @travel_matrix.dig(from_id, to_id) || 0
      end

      def compute_global_avg_travel
        all_times = []
        @travel_matrix.each do |from_id, dests|
          next if from_id == HOME_NODE_ID
          dests.each do |to_id, time|
            next if to_id == HOME_NODE_ID || from_id == to_id
            all_times << time
          end
        end
        @global_avg_travel = all_times.empty? ? 1.0 : (all_times.sum.to_f / all_times.size)
      end

      # Returns [start_minute, end_minute] for a given date, respecting per_day_hours overrides.
      def day_bounds(date)
        override = @clinician.per_day_hours[date.wday.to_s]
        if override.present?
          [ override["start"] || @clinician.workday_start_minute,
            override["end"]   || @clinician.workday_end_minute ]
        else
          [ @clinician.workday_start_minute, @clinician.workday_end_minute ]
        end
      end

      def lunch_break_config(date)
        lr = @clinician
        day_start, day_end = day_bounds(date)
        half_window = lr.lunch_window_minutes / 2
        earliest = [ lr.lunch_start_minute - half_window, day_start ].max
        latest = [ lr.lunch_start_minute + half_window, day_end - lr.lunch_duration_minutes ].min
        { earliest_start: earliest, latest_start: latest, duration: lr.lunch_duration_minutes }
      end

      def patient_for(id)
        @patients_by_id[id]
      end

      # ── Visit Plan: Regret-Based Insertion ──────────────────────────────

      def build_visit_plan
        blocked_ranges = build_blocked_ranges

        # Build unplaced list with target day offsets
        unplaced = []
        sorted_patients = @adapted_patients.sort_by { |p| -p.priority }
        sorted_patients.each do |patient|
          locked_count = @locked_visits.count { |v| v.patient_id == patient.id }
          remaining = patient.required_visits_per_week - locked_count
          next if remaining <= 0

          targets = evenly_spaced_day_offsets(remaining, patient)
          remaining.times do |i|
            unplaced << {
              patient_id: patient.id,
              patient: patient,
              target_day_offset: targets[i],
              instance_id: "patient_#{patient.id}_visit_#{i}",
              slot_footprint: patient.visit_duration_minutes + @charting_buffer
            }
          end
        end

        plan = []
        soft_constraint_count = 0
        unschedulable = []

        while unplaced.any?
          best_regret = -Float::INFINITY
          best_idx = nil
          best_slot = nil

          unplaced.each_with_index do |visit_info, idx|
            slots = ranked_day_slots(visit_info, plan, blocked_ranges)
            next if slots.empty?

            regret = if slots.size == 1
              Float::INFINITY
            else
              slots[1][:score] - slots[0][:score]
            end
            regret += visit_info[:patient].priority * 2.0
            regret += (1.0 / [ slots.size, 1 ].max) * 5.0

            if regret > best_regret
              best_regret = regret
              best_idx = idx
              best_slot = slots[0][:slot]
            end
          end

          if best_idx.nil?
            unplaced.each do |vi|
              slot = fallback_any_day(vi, plan, blocked_ranges)
              if slot
                slot[:instance_id] = vi[:instance_id]
                soft_constraint_count += 1
                plan << slot
                add_to_blocked(blocked_ranges, slot)
              else
                unschedulable << { patient_name: vi[:patient].name, patient_id: vi[:patient_id] }
              end
            end
            break
          end

          vi = unplaced.delete_at(best_idx)
          best_slot[:instance_id] = vi[:instance_id]
          soft_constraint_count += 1 if best_slot[:soft_constraint_override]
          plan << best_slot
          add_to_blocked(blocked_ranges, best_slot)
        end

        [ plan.sort_by { |s| s[:starts_at] }, soft_constraint_count, unschedulable ]
      end

      def build_blocked_ranges
        blocked = Hash.new { |h, k| h[k] = [] }
        @working_days.each { |d| blocked[d] ||= [] }

        @calendar_blocks.each { |b| blocked[b.date] << (b.starts_at...b.ends_at) }

        @locked_visits.each do |v|
          footprint_end = v.starts_at + (v.duration_minutes + @charting_buffer).minutes
          blocked[v.date] << (v.starts_at...footprint_end)
        end

        blocked
      end

      def add_to_blocked(blocked_ranges, slot)
        footprint = (slot[:patient]&.visit_duration_minutes || slot[:duration] || 60) + @charting_buffer
        blocked_ranges[slot[:date]] << (slot[:starts_at]...(slot[:starts_at] + footprint.minutes))
      end

      # ── Day Scoring ─────────────────────────────────────────────────────

      def ranked_day_slots(visit_info, plan, blocked_ranges)
        results = score_days(visit_info, plan, blocked_ranges, strict: true)
        results = score_days(visit_info, plan, blocked_ranges, strict: false) if results.empty?
        results.sort_by { |r| r[:score] }
      end

      def score_days(visit_info, plan, blocked_ranges, strict:)
        patient = visit_info[:patient]
        existing_days = existing_days_for(patient.id, plan)
        min_gap = patient.min_days_between_visits
        max_drive = @clinician.max_drive_minutes_per_day
        footprint = visit_info[:slot_footprint]
        target = visit_info[:target_day_offset]
        results = []

        @working_days.each do |date|
          next if existing_days.include?(date)
          next if day_visit_count(plan, date) >= MAX_VISITS_PER_DAY

          spacing_violation = too_close?(date, existing_days, min_gap)
          drive_violation = max_drive && day_drive_with(plan, date, patient.id) > max_drive

          if strict
            next if spacing_violation
            next if drive_violation
          end

          slot = find_start_on_day(patient, date, footprint, blocked_ranges, plan)
          next unless slot

          spacing = spacing_score(date, existing_days, min_gap, patient.max_days_between_visits)
          cluster = cluster_score(patient.id, date, plan)
          cluster_weight = patient.priority > 0 ? 0.3 : 1.0

          score = (day_offset(date) - target).abs * 10.0 +
                  spacing * 5.0 -
                  cluster * cluster_weight * 3.0
          score += 100.0 if spacing_violation
          score += 50.0 if drive_violation

          windows = patient.availability_windows[date.wday]
          slot[:soft_constraint_override] = windows.nil? || windows.empty? || !strict
          slot[:patient_id] = patient.id
          slot[:patient] = patient

          results << { slot: slot, score: score }
        end

        results
      end

      def find_start_on_day(patient, date, footprint, blocked_ranges, plan)
        windows = patient.availability_windows[date.wday]
        window_set = if windows && !windows.empty?
          windows.map { |w| Scheduling::TimeWindow.new(w[:start_minute], w[:end_minute]) }
        else
          day_start, day_end = day_bounds(date)
          [ Scheduling::TimeWindow.new(day_start, day_end) ]
        end

        unavailable = patient.unavailable_windows_for_wday(date.wday)
        window_set = Scheduling::TimeWindow.subtract(window_set, unavailable) if unavailable.present?

        window_set.each do |window|
          minute = window.start_minute
          latest = window.end_minute - footprint
          while minute <= latest
            starts_at = Time.zone.parse("#{date} #{fmt(minute)}")
            footprint_end = starts_at + footprint.minutes
            ends_at = starts_at + patient.visit_duration_minutes.minutes

            blocked = blocked_ranges[date]&.any? { |r| starts_at < r.end && footprint_end > r.begin }
            plan_conflict = plan.any? do |s|
              s_end = s[:starts_at] + ((s[:patient]&.visit_duration_minutes || 60) + @charting_buffer).minutes
              starts_at < s_end && footprint_end > s[:starts_at]
            end

            unless blocked || plan_conflict
              return { date: date, starts_at: starts_at, ends_at: ends_at }
            end

            minute += SLOT_STEP_MINUTES
          end
        end

        nil
      end

      def fallback_any_day(visit_info, plan, blocked_ranges)
        patient = visit_info[:patient]
        existing_days = existing_days_for(patient.id, plan)
        footprint = visit_info[:slot_footprint]

        # Try with spacing first, then without
        [ true, false ].each do |enforce_spacing|
          @working_days.each do |date|
            next if existing_days.include?(date)
            next if enforce_spacing && too_close?(date, existing_days, patient.min_days_between_visits)
            next if day_visit_count(plan, date) >= MAX_VISITS_PER_DAY

            slot = find_start_on_day(patient, date, footprint, blocked_ranges, plan)
            if slot
              slot[:patient_id] = patient.id
              slot[:patient] = patient
              slot[:soft_constraint_override] = true
              return slot
            end
          end
        end

        nil
      end

      # ── ALNS Post-Optimization ──────────────────────────────────────────

      def post_optimize(plan)
        return plan if plan.size < 3

        best_plan = plan.dup
        best_cost = total_cost(best_plan)
        current_plan = plan.dup

        strategies = { worst_cost: 1.0, geographic_cluster: 1.0, random: 1.0, same_patient: 1.0, full_day: 1.0 }

        ALNS_MAX_ITERATIONS.times do
          strategy = pick_strategy(strategies)
          count = rand((plan.size * ALNS_DESTROY_FRACTION_MIN).ceil..(plan.size * ALNS_DESTROY_FRACTION_MAX).ceil)
          count = [ count, current_plan.size ].min

          removed, remaining = send(:"destroy_#{strategy}", current_plan, count)
          next if removed.empty?

          repaired = regret_repair(remaining, removed)
          next unless repaired
          next unless plan_feasible?(repaired)

          repaired_cost = total_cost(repaired)
          if repaired_cost < best_cost
            current_plan = repaired
            best_plan = repaired.dup
            best_cost = repaired_cost
            strategies[strategy] = [ strategies[strategy] * 1.3, 5.0 ].min
          else
            strategies[strategy] = [ strategies[strategy] * 0.95, 0.2 ].max
          end
        end

        best_plan
      end

      def pick_strategy(strategies)
        total = strategies.values.sum
        r = rand * total
        cumulative = 0.0
        strategies.each do |s, w|
          cumulative += w
          return s if r <= cumulative
        end
        strategies.keys.last
      end

      def destroy_worst_cost(plan, count)
        scored = plan.map do |slot|
          day_slots = plan.select { |s| s[:date] == slot[:date] }
          ids = day_slots.map { |s| s[:patient_id] || s.dig(:patient, :id) }
          pid = slot[:patient_id] || slot.dig(:patient, :id)
          idx = ids.index(pid)
          prev_id = idx > 0 ? ids[idx - 1] : HOME_NODE_ID
          next_id = idx < ids.size - 1 ? ids[idx + 1] : HOME_NODE_ID
          marginal = travel_between(prev_id, pid) + travel_between(pid, next_id) - travel_between(prev_id, next_id)
          { slot: slot, marginal: marginal }
        end
        to_remove = scored.sort_by { |s| -s[:marginal] }.first(count).map { |s| s[:slot] }
        ids = to_remove.map(&:object_id).to_set
        [ to_remove, plan.reject { |s| ids.include?(s.object_id) } ]
      end

      def destroy_geographic_cluster(plan, count)
        seed = plan.sample
        return [ [], plan ] unless seed
        seed_id = seed[:patient_id] || seed.dig(:patient, :id)
        scored = plan.map { |s| { slot: s, dist: travel_between(seed_id, s[:patient_id] || s.dig(:patient, :id)) } }
        to_remove = scored.sort_by { |s| s[:dist] }.first(count).map { |s| s[:slot] }
        ids = to_remove.map(&:object_id).to_set
        [ to_remove, plan.reject { |s| ids.include?(s.object_id) } ]
      end

      def destroy_random(plan, count)
        to_remove = plan.sample(count)
        ids = to_remove.map(&:object_id).to_set
        [ to_remove, plan.reject { |s| ids.include?(s.object_id) } ]
      end

      def destroy_same_patient(plan, count)
        pids = plan.map { |s| s[:patient_id] || s.dig(:patient, :id) }.uniq.shuffle
        to_remove = []
        pids.each { |pid| break if to_remove.size >= count; to_remove.concat(plan.select { |s| (s[:patient_id] || s.dig(:patient, :id)) == pid }) }
        to_remove = to_remove.first(count)
        ids = to_remove.map(&:object_id).to_set
        [ to_remove, plan.reject { |s| ids.include?(s.object_id) } ]
      end

      def destroy_full_day(plan, count)
        date = plan.map { |s| s[:date] }.uniq.sample
        return [ [], plan ] unless date
        to_remove = plan.select { |s| s[:date] == date }.first(count)
        ids = to_remove.map(&:object_id).to_set
        [ to_remove, plan.reject { |s| ids.include?(s.object_id) } ]
      end

      def regret_repair(plan, removed)
        plan = plan.dup
        required = plan.size + removed.size
        blocked = rebuild_blocked(plan)

        unplaced = removed.map do |slot|
          pid = slot[:patient_id] || slot.dig(:patient, :id)
          patient = patient_for(pid)
          { patient_id: pid, patient: patient, target_day_offset: day_offset(slot[:date]),
            instance_id: slot[:instance_id], slot_footprint: (patient&.visit_duration_minutes || 60) + @charting_buffer }
        end

        while unplaced.any?
          best_regret = -Float::INFINITY
          best_idx = nil
          best_slot = nil

          unplaced.each_with_index do |vi, idx|
            slots = ranked_day_slots(vi, plan, blocked)
            next if slots.empty?
            regret = slots.size == 1 ? Float::INFINITY : slots[1][:score] - slots[0][:score]
            regret += (vi[:patient]&.priority || 0) * 2.0
            regret += (1.0 / [ slots.size, 1 ].max) * 5.0
            if regret > best_regret
              best_regret = regret
              best_idx = idx
              best_slot = slots[0][:slot]
            end
          end

          return nil unless best_idx

          vi = unplaced.delete_at(best_idx)
          best_slot[:instance_id] = vi[:instance_id]
          plan << best_slot
          add_to_blocked(blocked, best_slot)
        end

        plan.size == required ? plan : nil
      end

      def rebuild_blocked(plan)
        blocked = build_blocked_ranges
        plan.each { |slot| add_to_blocked(blocked, slot) }
        blocked
      end

      def plan_feasible?(plan)
        max_drive = @clinician.max_drive_minutes_per_day

        plan.group_by { |s| s[:date] }.each do |date, slots|
          pids = slots.map { |s| s[:patient_id] || s.dig(:patient, :id) }
          return false if pids.size != pids.uniq.size
          return false if slots.size + @locked_visits.count { |v| v.date == date } > MAX_VISITS_PER_DAY
          return false if max_drive && day_route_cost(all_day_slots(plan, date)) > max_drive
        end

        patient_dates = Hash.new { |h, k| h[k] = [] }
        plan.each { |s| patient_dates[s[:patient_id] || s.dig(:patient, :id)] << s[:date] }
        @locked_visits.each { |v| patient_dates[v.patient_id] << v.date }
        patient_dates.each do |pid, dates|
          next if dates.size < 2
          patient = patient_for(pid)
          min_gap = patient&.min_days_between_visits || 1
          dates.uniq.sort.each_cons(2) { |d1, d2| return false if (d2 - d1).to_i < min_gap }
        end

        true
      end

      # ── Route Ordering ──────────────────────────────────────────────────

      def nearest_neighbor_order(slots)
        return slots if slots.size <= 1
        remaining = slots.dup
        ordered = []
        current_id = HOME_NODE_ID
        while remaining.any?
          closest = remaining.min_by { |s| travel_between(current_id, s[:patient_id] || s.dig(:patient, :id)) }
          ordered << closest
          remaining.delete(closest)
          current_id = closest[:patient_id] || closest.dig(:patient, :id)
        end
        ordered
      end

      def two_opt_order(ordered)
        return ordered if ordered.size < 3
        improved = true
        while improved
          improved = false
          (0...(ordered.size - 1)).each do |i|
            ((i + 2)...ordered.size).each do |j|
              candidate = ordered[0..i] + ordered[(i + 1)..j].reverse + ordered[(j + 1)..]
              if route_cost(candidate) < route_cost(ordered)
                ordered = candidate
                improved = true
              end
            end
          end
        end
        ordered
      end

      def route_cost(slots)
        return 0.0 if slots.empty?
        ids = slots.map { |s| s[:patient_id] || s.dig(:patient, :id) }
        cost = travel_between(HOME_NODE_ID, ids.first)
        ids.each_cons(2) { |a, b| cost += travel_between(a, b) }
        cost += travel_between(ids.last, HOME_NODE_ID)
        cost.to_f
      end

      def day_route_cost(slots)
        return 0.0 if slots.empty?
        route_cost(nearest_neighbor_order(slots))
      end

      def total_cost(plan)
        all_dates = (plan.map { |s| s[:date] } + @locked_visits.map(&:date)).uniq
        all_dates.sum { |d| day_route_cost(all_day_slots(plan, d)) }
      end

      # ── Scoring Helpers ─────────────────────────────────────────────────

      def all_day_slots(plan, date)
        plan_slots = plan.select { |s| s[:date] == date }
        locked_slots = @locked_visits.select { |v| v.date == date }.map { |v| { patient_id: v.patient_id, date: date } }
        plan_slots + locked_slots
      end

      def day_visit_count(plan, date)
        @locked_visits.count { |v| v.date == date } + plan.count { |s| s[:date] == date }
      end

      def day_drive_with(plan, date, patient_id)
        simulated = all_day_slots(plan, date) + [ { patient_id: patient_id, date: date } ]
        day_route_cost(simulated)
      end

      def existing_days_for(patient_id, plan)
        plan_days = plan.select { |s| (s[:patient_id] || s.dig(:patient, :id)) == patient_id }.map { |s| s[:date] }
        locked_days = @locked_visits.select { |v| v.patient_id == patient_id }.map(&:date)
        (plan_days + locked_days).uniq
      end

      def too_close?(date, existing_days, min_gap)
        return false if min_gap <= 1
        existing_days.any? { |d| (date - d).to_i.abs < min_gap }
      end

      def spacing_score(date, existing_days, min_gap, max_gap)
        return 0 if existing_days.empty?
        existing_days.sum do |d|
          gap = (date - d).to_i.abs
          (gap < min_gap ? (min_gap - gap) * 10 : 0) + (gap > max_gap ? (gap - max_gap) * 5 : 0)
        end
      end

      def cluster_score(patient_id, date, plan)
        same_day = plan.select { |s| s[:date] == date }
        return 0.0 if same_day.empty?
        total = same_day.sum { |s| travel_between(patient_id, s[:patient_id] || s.dig(:patient, :id)) }
        avg = total.to_f / same_day.size
        [ 1.0 - (avg / @global_avg_travel), 0.0 ].max
      end

      def day_offset(date)
        (date - @input.week_start_on).to_i
      end

      def evenly_spaced_day_offsets(count, patient)
        offsets = @working_days.map { |d| day_offset(d) }
        return [ 0 ] if offsets.empty?
        return [ offsets.first ] if count <= 1

        density = @clinician.schedule_density
        min_gap = patient.min_days_between_visits
        max_step = (offsets.length - 1).to_f / (count - 1)
        min_step = [ min_gap.to_f, 1.0 ].max
        step = [ max_step - (density * (max_step - min_step)), min_step ].max

        (0...count).map { |i| offsets[(i * step).round.clamp(0, offsets.length - 1)] }
      end

      def check_max_drive_violations(day_routes, locked_by_date, max_drive)
        violations = []
        day_routes.each do |date, slots|
          locked = locked_by_date[date] || []
          all_day = slots + locked.map { |v| { patient_id: v.patient_id, date: date } }
          cost = route_cost(nearest_neighbor_order(all_day))
          violations << { date: date.to_s, drive_minutes: cost.round, max_drive: max_drive } if cost > max_drive
        end
        violations
      end

      def fmt(minute)
        "%02d:%02d" % [ minute / 60, minute % 60 ]
      end
    end
  end
end
