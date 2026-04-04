class ScheduleSerializer
  def self.as_json(schedule)
    {
      id: schedule.id,
      week_start_on: schedule.week_start_on,
      status: schedule.status,
      total_drive_minutes: schedule.total_drive_minutes,
      baseline_drive_minutes: schedule.baseline_drive_minutes,
      drive_minutes_saved: schedule.baseline_drive_minutes - schedule.total_drive_minutes,
      optimization_summary: schedule.optimization_summary,
      visits: schedule.visits.includes(:patient).order(:starts_at).map { |visit| VisitSerializer.as_json(visit) }
    }
  end
end
