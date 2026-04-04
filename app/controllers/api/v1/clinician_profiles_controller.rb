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
      :timezone,
      :workday_start_minute,
      :workday_end_minute,
      :home_latitude,
      :home_longitude,
      :working_days_mask,
      working_days: []
    )
  end

  def serialize_profile(profile)
    {
      id: profile.id,
      timezone: profile.timezone,
      discipline: profile.discipline,
      workday_start_minute: profile.workday_start_minute,
      workday_end_minute: profile.workday_end_minute,
      home_latitude: profile.home_latitude,
      home_longitude: profile.home_longitude,
      working_days_mask: profile.working_days_mask,
      working_days: profile.working_day_wdays
    }
  end
end
