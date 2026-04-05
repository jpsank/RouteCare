module Api
  module V1
    class BaseController < ApplicationController
      include ApiAuthenticatable

      protect_from_forgery with: :null_session

      rescue_from ActiveRecord::RecordInvalid do |exception|
        render json: { errors: exception.record.errors.full_messages }, status: :unprocessable_entity
      end

      rescue_from ActiveRecord::RecordNotFound do |exception|
        render json: { error: exception.message }, status: :not_found
      end

      private

      def render_not_found(resource = "resource")
        render json: { error: "#{resource} not found" }, status: :not_found
      end

      def render_unprocessable(errors)
        render json: { errors: Array(errors) }, status: :unprocessable_entity
      end

      def render_error(message, status = :unprocessable_entity)
        render json: { error: message }, status: status
      end

      def parsed_week_start(input)
        return Time.use_zone(request_timezone) { Time.zone.today.beginning_of_week(:monday) } if input.blank?

        Date.iso8601(input).beginning_of_week(:monday)
      rescue ArgumentError
        raise ActionController::BadRequest, "Invalid week_start_on"
      end

      def request_timezone
        requested = params[:client_timezone]
        return requested if requested.present? && ActiveSupport::TimeZone[requested].present?

        profile_timezone = current_user&.clinician_profile&.timezone
        return profile_timezone if profile_timezone.present? && ActiveSupport::TimeZone[profile_timezone].present?

        Time.zone.name
      end

      def current_clinician_profile
        current_user.clinician_profile || current_user.create_clinician_profile!(
          discipline: "Physical Therapist",
          timezone: Time.zone.name
        )
      end
    end
  end
end
