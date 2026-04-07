module Scheduling
  # Solver-agnostic input format. Any solver backend (Ruby greedy, Ruby GA,
  # Python HGS, C++ BCP) receives this data structure — no ActiveRecord objects.
  #
  # Usage:
  #   input = Scheduling::SolverInput.build(user:, week_start_on:, start_point:)
  #   solution = SomeSolver.solve(input)
  #   Scheduling::SchedulePersister.persist_from_solution(...)

  Location = Data.define(:lat, :lng)

  PatientData = Data.define(
    :id, :name, :location,
    :visit_duration_minutes, :required_visits_per_week,
    :min_days_between_visits, :max_days_between_visits,
    :priority, :availability_windows # { day_of_week => [{ start_minute:, end_minute: }] }
  )

  ClinicianData = Data.define(
    :home_location,          # Location | nil
    :workday_start_minute,
    :workday_end_minute,
    :working_days,           # [Integer] day-of-week numbers
    :lunch_start_minute,
    :lunch_duration_minutes,
    :lunch_window_minutes,
    :max_continuous_work_minutes,
    :required_break_minutes,
    :max_drive_minutes_per_day, # Integer | nil
    :schedule_density,
    :charting_buffer_minutes
  )

  LockedVisitData = Data.define(
    :patient_id, :date, :starts_at, :ends_at, :duration_minutes
  )

  CalendarBlockData = Data.define(
    :date, :starts_at, :ends_at
  )

  # A visit instance to be scheduled (patient needing k visits → k instances)
  VisitInstanceData = Data.define(
    :id, :patient_id, :location,
    :duration, :priority,
    :availability_windows # { day_of_week => [{ start_minute:, end_minute: }] }
  )

  SolverInputData = Data.define(
    :patients,          # [PatientData]
    :clinician,         # ClinicianData
    :instances,         # [VisitInstanceData] — visits to schedule
    :locked_visits,     # [LockedVisitData] — fixed, immovable
    :calendar_blocks,   # [CalendarBlockData] — blocked time ranges
    :travel_matrix,     # { id => { id => minutes } } — includes :home node
    :week_start_on,     # Date
    :working_days       # [Date] — actual dates for this week
  )

  # ── Solver Output ──────────────────────────────────────────────────────

  PlannedVisit = Data.define(
    :instance_id, :patient_id, :date, :starts_at, :ends_at,
    :soft_constraint_override
  )

  SolverOutputData = Data.define(
    :planned_visits,      # [PlannedVisit]
    :lunch_placements,    # { "date_string" => { start_minute:, end_minute: } }
    :fitness,             # Float
    :metadata             # Hash — optimizer_type, unschedulable, drive_violations, etc.
  )

  # ── Builder ────────────────────────────────────────────────────────────

  class SolverInput
    def self.build(user:, week_start_on:, start_point: nil)
      profile = user.clinician_profile
      raise ArgumentError, "Clinician profile is required" unless profile

      week_start = week_start_on.to_date.beginning_of_week(:monday)

      # Clinician data
      home = start_point || profile.home_point
      home_location = home ? Location.new(lat: home[:lat], lng: home[:lng]) : nil

      clinician = ClinicianData.new(
        home_location: home_location,
        workday_start_minute: profile.workday_start_minute,
        workday_end_minute: profile.workday_end_minute,
        working_days: profile.working_day_wdays,
        lunch_start_minute: profile.lunch_start_minute,
        lunch_duration_minutes: profile.lunch_duration_minutes,
        lunch_window_minutes: profile.lunch_window_minutes,
        max_continuous_work_minutes: profile.max_continuous_work_minutes,
        required_break_minutes: profile.required_break_minutes,
        max_drive_minutes_per_day: profile.max_drive_minutes_per_day,
        schedule_density: profile.schedule_density,
        charting_buffer_minutes: profile.charting_buffer_minutes
      )

      # Patients
      active_patients = profile.patients.active.includes(:patient_availability_windows).to_a
      patients = active_patients.map do |p|
        windows = p.patient_availability_windows.group_by(&:day_of_week).transform_values do |wins|
          wins.map { |w| { start_minute: w.start_minute, end_minute: w.end_minute } }
        end

        PatientData.new(
          id: p.id, name: p.full_name,
          location: (p.latitude && p.longitude) ? Location.new(lat: p.latitude, lng: p.longitude) : nil,
          visit_duration_minutes: p.visit_duration_minutes,
          required_visits_per_week: p.required_visits_per_week,
          min_days_between_visits: p.min_days_between_visits,
          max_days_between_visits: p.max_days_between_visits,
          priority: p.priority,
          availability_windows: windows
        )
      end

      # Locked visits
      schedule = user.weekly_schedules.find_by(week_start_on: week_start)
      locked_ar = schedule ? schedule.visits.where(status: %w[confirmed completed]).includes(:patient).to_a : []
      locked_visits = locked_ar.map do |v|
        LockedVisitData.new(
          patient_id: v.patient_id, date: v.starts_at.to_date,
          starts_at: v.starts_at, ends_at: v.ends_at, duration_minutes: v.duration_minutes
        )
      end

      # Calendar blocks
      week_end = week_start + 6.days
      blocks_ar = CalendarConstraints.new(user: user, week_start_on: week_start).blocked_ranges_by_day
      calendar_blocks = blocks_ar.flat_map do |date, ranges|
        ranges.map { |r| CalendarBlockData.new(date: date, starts_at: r.begin, ends_at: r.end) }
      end

      # Travel matrix (includes home node)
      routing_client = Integrations::RoutingClient.new
      all_patients_for_matrix = (active_patients + locked_ar.map(&:patient)).uniq
      travel_matrix = TravelTimeMatrixBuilder.new(patients: all_patients_for_matrix, routing_client: routing_client).call

      if home_location
        travel_matrix[:home] = {}
        all_patients_for_matrix.each do |p|
          next unless p.latitude && p.longitude
          time = routing_client.travel_minutes(
            origin: { lat: home_location.lat, lng: home_location.lng },
            destination: { lat: p.latitude, lng: p.longitude }
          )
          travel_matrix[:home][p.id] = time
          travel_matrix[p.id] ||= {}
          travel_matrix[p.id][:home] = time
        end
      end

      # Visit instances
      locked_counts = locked_visits.each_with_object(Hash.new(0)) { |v, h| h[v.patient_id] += 1 }
      instances = []
      patients.each do |patient|
        remaining = patient.required_visits_per_week - locked_counts.fetch(patient.id, 0)
        next if remaining <= 0

        remaining.times do |i|
          instances << VisitInstanceData.new(
            id: "patient_#{patient.id}_visit_#{i}",
            patient_id: patient.id,
            location: patient.location,
            duration: patient.visit_duration_minutes + clinician.charting_buffer_minutes,
            priority: patient.priority,
            availability_windows: patient.availability_windows
          )
        end
      end

      # Working days as actual dates
      working_day_dates = clinician.working_days.map do |wday|
        offset = (wday - 1) % 7
        week_start + offset.days
      end

      SolverInputData.new(
        patients: patients,
        clinician: clinician,
        instances: instances,
        locked_visits: locked_visits,
        calendar_blocks: calendar_blocks,
        travel_matrix: travel_matrix,
        week_start_on: week_start,
        working_days: working_day_dates
      )
    end
  end
end
