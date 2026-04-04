class Api::V1::VisitsController < Api::V1::BaseController
  def index
    visits = scoped_visits.includes(:patient, :weekly_schedule).order(:starts_at)
    render json: { visits: visits.map { |visit| serialize_visit(visit) } }
  end

  def create
    patient = current_clinician_profile.patients.find(visit_create_params[:patient_id])
    starts_at = Time.zone.parse(visit_create_params[:starts_at])
    duration_minutes = visit_create_params[:duration_minutes].presence&.to_i || patient.visit_duration_minutes
    ends_at = starts_at + duration_minutes.minutes

    weekly_schedule = current_user.weekly_schedules.find_or_create_by!(
      week_start_on: starts_at.to_date.beginning_of_week(:monday)
    ) do |schedule|
      schedule.status = :draft
    end

    visit = weekly_schedule.visits.create!(
      patient: patient,
      starts_at: starts_at,
      ends_at: ends_at,
      duration_minutes: duration_minutes,
      status: visit_create_params[:status].presence || "pending_patient_confirmation",
      position_in_day: 0,
      drive_from_previous_minutes: 0,
      clinician_override: true,
      source: "manual"
    )

    resequence_day!(visit)
    render json: { visit: serialize_visit(visit.reload) }, status: :created
  rescue ArgumentError
    render_error("Invalid starts_at", :bad_request)
  rescue ActiveRecord::RecordNotFound
    render_not_found("Patient not found")
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
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
      visit: visit,
      starts_at: starts_at,
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
    start_point = clinician_start_point
    day_visits.each_with_index do |day_visit, index|
      drive_time =
        if previous
          Integrations::RoutingClient.new.travel_minutes(
            origin: { lat: previous.patient.latitude, lng: previous.patient.longitude },
            destination: { lat: day_visit.patient.latitude, lng: day_visit.patient.longitude }
          )
        elsif start_point
          Integrations::RoutingClient.new.travel_minutes(
            origin: start_point,
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
      source: visit.source
    }
  end

  def visit_params
    params.require(:visit).permit(:starts_at, :ends_at, :status, :position_in_day)
  end

  def visit_create_params
    params.require(:visit).permit(:patient_id, :starts_at, :duration_minutes, :status)
  end

  def clinician_start_point
    profile = current_user.clinician_profile
    return if profile.blank? || profile.home_latitude.blank? || profile.home_longitude.blank?

    { lat: profile.home_latitude, lng: profile.home_longitude }
  end
end
