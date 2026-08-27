module Scheduling
  class VisitInstanceBuilder
    def initialize(patients:, locked_visits:, charting_buffer_minutes: 0)
      @patients = patients
      @locked_visits = locked_visits
      @charting_buffer_minutes = charting_buffer_minutes
    end

    def call
      locked_counts = @locked_visits.each_with_object(Hash.new(0)) do |visit, counts|
        counts[visit.patient_id] += 1
      end

      instances = []

      @patients.each do |patient|
        remaining = patient.required_visits_per_week - locked_counts.fetch(patient.id, 0)
        next if remaining <= 0

        duration = patient.visit_duration_minutes + @charting_buffer_minutes
        location = patient.latitude && patient.longitude ? { lat: patient.latitude, lng: patient.longitude } : nil
        windows = patient.patient_availability_windows.select(&:available).group_by(&:day_of_week)
        unavailable_windows = patient.patient_availability_windows.reject(&:available).group_by(&:day_of_week)

        remaining.times do |index|
          instances << VisitInstance.new(
            id: "patient_#{patient.id}_visit_#{index}",
            patient_id: patient.id,
            patient: patient,
            location: location,
            duration: duration,
            priority: patient.priority,
            availability_windows: windows,
            unavailability_windows: unavailable_windows
          )
        end
      end

      instances
    end
  end
end
