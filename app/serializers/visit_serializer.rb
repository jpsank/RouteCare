class VisitSerializer
  def self.as_json(visit)
    {
      id: visit.id,
      instance_id: visit.instance_id,
      patient_id: visit.patient_id,
      patient_name: visit.patient.full_name,
      patient_address: visit.patient.address,
      patient_latitude: visit.patient.latitude,
      patient_longitude: visit.patient.longitude,
      starts_at: visit.starts_at,
      ends_at: visit.ends_at,
      duration_minutes: visit.duration_minutes,
      status: visit.status,
      position_in_day: visit.position_in_day,
      drive_from_previous_minutes: visit.drive_from_previous_minutes,
      clinician_override: visit.clinician_override,
      soft_constraint_override: visit.soft_constraint_override,
      source: visit.source,
      external_calendar_event_id: visit.external_calendar_event_id
    }
  end
end
