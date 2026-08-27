class PatientSerializer
  class << self
    def as_json(patient)
      {
        id: patient.id,
        first_name: patient.first_name,
        last_name: patient.last_name,
        full_name: patient.full_name,
        phone: patient.phone,
        email: patient.email,
        address_line1: patient.address_line1,
        address_line2: patient.address_line2,
        city: patient.city,
        state: patient.state,
        postal_code: patient.postal_code,
        address: patient.address,
        required_visits_per_week: patient.required_visits_per_week,
        visit_duration_minutes: patient.visit_duration_minutes,
        active: patient.active,
        notes: patient.notes,
        latitude: patient.latitude,
        longitude: patient.longitude,
        min_days_between_visits: patient.min_days_between_visits,
        max_days_between_visits: patient.max_days_between_visits,
        priority: patient.priority,
        availability_windows: patient.patient_availability_windows
          .order(:day_of_week, :start_minute)
          .map do |window|
            {
              id: window.id,
              day_of_week: window.day_of_week,
              start_minute: window.start_minute,
              end_minute: window.end_minute
            }
          end
      }
    end
  end
end
