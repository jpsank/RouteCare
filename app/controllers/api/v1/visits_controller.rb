class Api::V1::VisitsController < Api::V1::BaseController
  def index
    visits = scoped_visits.includes(:patient, :weekly_schedule).order(:starts_at)
    render json: { visits: visits.map { |visit| serialize_visit(visit) } }
  end

  def update
    visit = scoped_visits.find_by(id: params[:id])
    return render_not_found("Visit not found") unless visit

    visit.update!(visit_params.merge(clinician_override: true, source: "manual"))
    resequence_day!(visit)
    render json: { visit: serialize_visit(visit.reload) }
  end

  def reschedule
    visit = scoped_visits.find_by(id: params[:id])
    return render_not_found("Visit not found") unless visit

    starts_at = Time.zone.parse(params.require(:requested_starts_at))
    updated_visit = Scheduling::Rescheduler.new(
      visit:,
      starts_at:,
      actor: current_user
    ).call
    render json: { visit: serialize_visit(updated_visit) }
  rescue ArgumentError
    render_error("Invalid requested_starts_at", :bad_request)
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
  end

  private

  def scoped_visits
    Visit.joins(:weekly_schedule).where(weekly_schedules: { user_id: current_user.id })
  end

  def resequence_day!(visit)
    day_visits = visit.weekly_schedule.visits.for_day(visit.starts_at.to_date)
    previous = nil
    day_visits.each_with_index do |day_visit, index|
      drive_time = if previous
                     Integrations::RoutingClient.new.travel_minutes(
                       origin: { lat: previous.patient.latitude, lng: previous.patient.longitude },
                       destination: { lat: day_visit.patient.latitude, lng: day_visit.patient.longitude }
                     )
                   else
                     0
                   end

      day_visit.update!(
        position_in_day: index,
        drive_from_previous_minutes: drive_time
      )
      previous = day_visit
    end
  end

  def serialize_visit(visit)
    {
      id: visit.id,
      patient_id: visit.patient_id,
      patient_name: visit.patient.full_name,
      starts_at: visit.starts_at,
      ends_at: visit.ends_at,
      duration_minutes: visit.duration_minutes,
      status: visit.status,
      position_in_day: visit.position_in_day,
      drive_from_previous_minutes: visit.drive_from_previous_minutes,
      clinician_override: visit.clinician_override,
      soft_constraint_override: visit.soft_constraint_override,
      source: visit.source
    }
  end

  def visit_params
    params.require(:visit).permit(:starts_at, :ends_at, :status, :position_in_day)
  end
end
