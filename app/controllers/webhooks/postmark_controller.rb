module Webhooks
  class PostmarkController < ActionController::Base
    skip_forgery_protection
    before_action :verify_postmark_token!

    def inbound
      from_email = extract_email(params["FromFull"]&.dig("Email") || params["From"])
      body = (params["TextBody"] || params["HtmlBody"]).to_s.strip

      @patient = Patient.where("LOWER(email) = ?", from_email.to_s.downcase).first
      unless @patient
        Rails.logger.warn("[Webhooks::Postmark] Unrecognized email sender")
        head :ok
        return
      end

      @user = @patient.clinician_profile.user
      visit = @patient.visits.order(starts_at: :desc).first

      unless visit
        create_processing_alert!(
          "Email reply from #{@patient.full_name} could not be processed: no active visit found.",
          { from: from_email, body: body.first(200) }
        )
        head :ok
        return
      end

      Messaging::InboundReplyProcessor.new(
        user: @user,
        visit: visit,
        channel: :email,
        body: body
      ).call

      head :ok
    rescue StandardError => e
      Rails.logger.error("[Webhooks::Postmark] #{e.class}: #{e.message}")
      Sentry.capture_exception(e, tags: { webhook: "postmark_inbound" }) if defined?(Sentry) && Sentry.initialized?
      create_processing_alert!(
        "Failed to process email reply: #{e.message}",
        { from: (params["From"]).to_s, body: body.to_s.first(200) }
      ) if @user
      head :ok
    end

    private

    def verify_postmark_token!
      expected_token = ENV["ROUTECARE_POSTMARK_INBOUND_TOKEN"].to_s
      return if expected_token.blank?

      # Postmark inbound webhooks can be secured by checking the inbound
      # hash token included in the webhook URL path, or by verifying a
      # shared secret header. We use a simple token comparison approach.
      provided_token = params[:token].to_s

      unless ActiveSupport::SecurityUtils.secure_compare(expected_token, provided_token)
        Rails.logger.warn("[Webhooks::Postmark] Invalid token from #{request.remote_ip}")
        head :forbidden
      end
    end

    def create_processing_alert!(message, metadata = {})
      return unless @user

      Alert.create!(
        user: @user,
        category: "webhook_processing_error",
        severity: "high",
        status: "open",
        message: message,
        metadata: metadata
      )
    rescue StandardError => e
      Rails.logger.error("[Webhooks::Postmark] Could not create processing alert: #{e.message}")
    end

    def extract_email(value)
      match = value.to_s.match(/<([^>]+)>/)
      match ? match[1] : value.to_s.strip
    end
  end
end
