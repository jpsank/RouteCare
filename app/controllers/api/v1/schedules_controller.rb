class Api::V1::SchedulesController < Api::V1::BaseController
  def show
    schedule = current_user.weekly_schedules.includes(visits: :patient).find_by(week_start_on: parsed_week_start(params[:week_start_on]))
    return render_not_found("Schedule") if schedule.blank?

    render json: { schedule: ScheduleSerializer.as_json(schedule) }
  end

  def optimize
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = Scheduling::WeeklyOptimizer.new(
      user: current_user,
      week_start_on:
    ).call
    Alerts::Generator.new(user: current_user).run!

    render json: {
      schedule: ScheduleSerializer.as_json(schedule),
      generated: true
    }, status: :created
  end

  def approve
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = current_user.weekly_schedules.find_by!(week_start_on:)
    schedule.update!(status: :clinician_approved)

    AuditLog.create!(
      user: current_user,
      auditable_type: "WeeklySchedule",
      auditable_id: schedule.id,
      action: "schedule_approved",
      ip_address: request.remote_ip,
      metadata: { week_start_on: week_start_on.iso8601 }
    )

    render json: { schedule: ScheduleSerializer.as_json(schedule) }
  rescue ActiveRecord::RecordNotFound
    render_not_found("Schedule")
  end

  private

  def parsed_week_start(input)
    return Date.current.beginning_of_week(:monday) if input.blank?

    Date.iso8601(input).beginning_of_week(:monday)
  rescue ArgumentError
    raise ActionController::BadRequest, "Invalid week_start_on"
  end
end
