class Api::V1::SchedulesController < Api::V1::BaseController
  def show
    schedule = current_user.weekly_schedules.includes(visits: :patient).find_by(week_start_on: parsed_week_start(params[:week_start_on]))
    return render_not_found("Schedule") if schedule.blank?

    render json: { schedule: ScheduleSerializer.as_json(schedule) }
  end

  def optimize
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = Time.use_zone(request_timezone) do
      optimized_schedule = Scheduling::WeeklyOptimizer.new(
        user: current_user,
        week_start_on: week_start_on,
        start_point: optimization_start_point
      ).call
      Alerts::Generator.new(user: current_user).run!
      optimized_schedule
    end

    render json: {
      schedule: ScheduleSerializer.as_json(schedule),
      generated: true
    }, status: :created
  end

  def approve
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = current_user.weekly_schedules.find_by!(week_start_on: week_start_on)
    schedule.update!(status: :approved)

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

  def optimization_start_point
    latitude = params[:start_latitude]
    longitude = params[:start_longitude]
    return if latitude.blank? || longitude.blank?

    { lat: Float(latitude), lng: Float(longitude) }
  rescue ArgumentError
    nil
  end
end
