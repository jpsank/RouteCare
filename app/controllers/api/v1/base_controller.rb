module Api
  module V1
    class BaseController < ApplicationController
      include ApiAuthenticatable

      protect_from_forgery with: :null_session

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

      def current_clinician_profile
        current_user.clinician_profile || current_user.create_clinician_profile!(
          discipline: "Physical Therapist",
          timezone: Time.zone.name
        )
      end
    end
  end
end
