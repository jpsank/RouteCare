module Scheduling
  module Solvers
    # Calls the Python solver microservice with backend=hgs (PyVRP HGS).
    class Hgs
      def initialize(input, time_budget: 30, **_options)
        @input = input
        @time_budget = time_budget
      end

      def solve
        response = HTTParty.post(
          "#{solver_url}/solve",
          query: { backend: "hgs", time_budget: @time_budget },
          body: request_body,
          headers: { "Content-Type" => "application/json" },
          timeout: @time_budget + 30,
          format: :json
        )

        unless response.success?
          raise "Python solver returned #{response.code}: #{response.body}"
        end

        deserialize_output(response.parsed_response)
      end

      private

      def solver_url
        ENV.fetch("PYTHON_SOLVER_URL", "http://localhost:8000")
      end

      def request_body
        serialize_input(@input)
      end

      def serialize_input(input)
        {
          patients: input.patients.map { |p| serialize_patient(p) },
          clinician: serialize_clinician(input.clinician),
          instances: input.instances.map { |i| serialize_instance(i) },
          locked_visits: input.locked_visits.map { |v| serialize_locked(v) },
          calendar_blocks: input.calendar_blocks.map { |b| serialize_block(b) },
          travel_matrix: serialize_matrix(input.travel_matrix),
          week_start_on: input.week_start_on.to_s,
          working_days: input.working_days.map(&:to_s)
        }.to_json
      end

      def serialize_patient(p)
        {
          id: p.id, name: p.name,
          location: p.location ? { lat: p.location.lat, lng: p.location.lng } : nil,
          visit_duration_minutes: p.visit_duration_minutes,
          required_visits_per_week: p.required_visits_per_week,
          min_days_between_visits: p.min_days_between_visits,
          max_days_between_visits: p.max_days_between_visits,
          priority: p.priority,
          availability_windows: p.availability_windows
        }
      end

      def serialize_clinician(c)
        {
          home_location: c.home_location ? { lat: c.home_location.lat, lng: c.home_location.lng } : nil,
          workday_start_minute: c.workday_start_minute,
          workday_end_minute: c.workday_end_minute,
          working_days: c.working_days,
          lunch_start_minute: c.lunch_start_minute,
          lunch_duration_minutes: c.lunch_duration_minutes,
          lunch_window_minutes: c.lunch_window_minutes,
          max_continuous_work_minutes: c.max_continuous_work_minutes,
          required_break_minutes: c.required_break_minutes,
          max_drive_minutes_per_day: c.max_drive_minutes_per_day,
          schedule_density: c.schedule_density,
          charting_buffer_minutes: c.charting_buffer_minutes
        }
      end

      def serialize_instance(i)
        {
          id: i.id, patient_id: i.patient_id,
          location: i.location ? { lat: i.location.lat, lng: i.location.lng } : nil,
          duration: i.duration, priority: i.priority,
          availability_windows: i.availability_windows
        }
      end

      def serialize_locked(v)
        { patient_id: v.patient_id, date: v.date.to_s,
          starts_at: v.starts_at.iso8601, ends_at: v.ends_at.iso8601,
          duration_minutes: v.duration_minutes }
      end

      def serialize_block(b)
        { date: b.date.to_s, starts_at: b.starts_at.iso8601, ends_at: b.ends_at.iso8601 }
      end

      def serialize_matrix(matrix)
        matrix.transform_keys(&:to_s).transform_values { |v| v.transform_keys(&:to_s) }
      end

      def deserialize_output(json)
        planned = (json["planned_visits"] || []).map do |v|
          Scheduling::PlannedVisit.new(
            instance_id: v["instance_id"],
            patient_id: v["patient_id"],
            date: Date.parse(v["date"]),
            starts_at: Time.zone.parse(v["starts_at"]),
            ends_at: Time.zone.parse(v["ends_at"]),
            soft_constraint_override: v["soft_constraint_override"] || false
          )
        end

        lunch = (json["lunch_placements"] || {}).transform_values do |v|
          { start_minute: v["start_minute"], end_minute: v["end_minute"] }
        end

        Scheduling::SolverOutputData.new(
          planned_visits: planned,
          lunch_placements: lunch,
          fitness: json["fitness"] || 0.0,
          metadata: (json["metadata"] || {}).symbolize_keys
        )
      end
    end
  end
end
