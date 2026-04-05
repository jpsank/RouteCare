require "openssl"
require "base64"

module Webhooks
  class TwilioController < ActionController::Base
    skip_forgery_protection
    before_action :verify_twilio_signature!

    def sms
      from = params["From"].to_s.gsub(/\D/, "")
      body = params["Body"].to_s.strip

      @patient = Patient.find_by("REPLACE(phone, '-', '') = ? OR phone = ?", from, from)
      unless @patient
        Rails.logger.warn("[Webhooks::Twilio] Unrecognized number: #{from.first(6)}***")
        head :ok
        return
      end

      @user = @patient.clinician_profile.user
      visit = @patient.visits.order(starts_at: :desc).first

      unless visit
        create_processing_alert!(
          "SMS reply from #{@patient.full_name} could not be processed: no active visit found.",
          { from: from, body: body.first(200) }
        )
        head :ok
        return
      end

      Messaging::InboundReplyProcessor.new(
        user: @user,
        visit: visit,
        channel: :sms,
        body: body
      ).call

      head :ok
    rescue StandardError => e
      Rails.logger.error("[Webhooks::Twilio] #{e.class}: #{e.message}")
      create_processing_alert!(
        "Failed to process SMS reply: #{e.message}",
        { from: params["From"].to_s, body: params["Body"].to_s.first(200) }
      ) if @user
      head :ok
    end

    private

    def verify_twilio_signature!
      auth_token = ENV["ROUTECARE_TWILIO_AUTH_TOKEN"].to_s
      # Skip validation when token is not configured (development/test)
      return if auth_token.blank?

      expected = compute_twilio_signature(auth_token, request.original_url, request.POST)
      provided  = request.headers["X-Twilio-Signature"].to_s

      unless ActiveSupport::SecurityUtils.secure_compare(expected, provided)
        Rails.logger.warn("[Webhooks::Twilio] Invalid signature from #{request.remote_ip}")
        head :forbidden
      end
    end

    # Twilio signature: Base64( HMAC-SHA1( auth_token, url + sorted_params ) )
    # https://www.twilio.com/docs/usage/webhooks/webhooks-security
    def compute_twilio_signature(auth_token, url, params)
      data = url.dup
      params.keys.sort.each { |k| data += "#{k}#{params[k]}" }
      digest = OpenSSL::HMAC.digest("sha1", auth_token, data)
      Base64.strict_encode64(digest)
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
      Rails.logger.error("[Webhooks::Twilio] Could not create processing alert: #{e.message}")
    end
  end
end
