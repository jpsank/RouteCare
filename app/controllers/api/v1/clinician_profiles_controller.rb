class Api::V1::ClinicianProfilesController < Api::V1::BaseController
  def show
    profile = current_clinician_profile
    render json: { clinician_profile: serialize_profile(profile) }
  end

  def update
    profile = current_clinician_profile
    attrs = clinician_profile_params.to_h
    if attrs.key?("working_days")
      profile.working_day_wdays = attrs.delete("working_days")
    end
    profile.update!(attrs)
    render json: { clinician_profile: serialize_profile(profile) }
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
  end

  private

  def clinician_profile_params
    params.require(:clinician_profile).permit(
      :display_name,
      :timezone,
      :workday_start_minute,
      :workday_end_minute,
      :home_latitude,
      :home_longitude,
      :working_days_mask,
      :lunch_start_minute,
      :lunch_duration_minutes,
      :lunch_window_minutes,
      :setup_completed_at,
      :max_continuous_work_minutes,
      :required_break_minutes,
      :max_drive_minutes_per_day,
      :schedule_density,
      :charting_buffer_minutes,
      working_days: [],
      per_day_hours: {}
    )
  end

  def serialize_profile(profile)
    {
      id: profile.id,
      display_name: profile.display_name,
      timezone: profile.timezone,
      discipline: profile.discipline,
      workday_start_minute: profile.workday_start_minute,
      workday_end_minute: profile.workday_end_minute,
      home_latitude: profile.home_latitude,
      home_longitude: profile.home_longitude,
      working_days_mask: profile.working_days_mask,
      working_days: profile.working_day_wdays,
      lunch_start_minute: profile.lunch_start_minute,
      lunch_duration_minutes: profile.lunch_duration_minutes,
      lunch_window_minutes: profile.lunch_window_minutes,
      setup_completed_at: profile.setup_completed_at,
      max_continuous_work_minutes: profile.max_continuous_work_minutes,
      required_break_minutes: profile.required_break_minutes,
      max_drive_minutes_per_day: profile.max_drive_minutes_per_day,
      schedule_density: profile.schedule_density,
      charting_buffer_minutes: profile.charting_buffer_minutes,
      per_day_hours: profile.per_day_hours
    }
  end
end
