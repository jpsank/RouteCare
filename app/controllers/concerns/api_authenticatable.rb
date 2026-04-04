module ApiAuthenticatable
  extend ActiveSupport::Concern

  included do
    before_action :authenticate_user!
    before_action :ensure_json_request
    before_action :set_current_user_timezone
  end

  private

  def ensure_json_request
    request.format = :json
  end

  def set_current_user_timezone
    return unless current_user&.clinician_profile&.timezone.present?

    Time.zone = current_user.clinician_profile.timezone
  rescue StandardError
    Time.zone = "UTC"
  end
end
