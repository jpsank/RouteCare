require "openssl"

module Webhooks
  class MailgunController < ActionController::Base
    skip_forgery_protection
    before_action :verify_mailgun_signature!

    def inbound
      from_email = extract_email(params["from"] || params["sender"])
      body = (params["stripped-text"] || params["body-plain"]).to_s.strip

      @patient = Patient.where("LOWER(email) = ?", from_email.to_s.downcase).first
      unless @patient
        Rails.logger.warn("[Webhooks::Mailgun] Unrecognized email sender")
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
      Rails.logger.error("[Webhooks::Mailgun] #{e.class}: #{e.message}")
      create_processing_alert!(
        "Failed to process email reply: #{e.message}",
        { from: (params["from"] || params["sender"]).to_s, body: body.to_s.first(200) }
      ) if @user
      head :ok
    end

    private

    def verify_mailgun_signature!
      signing_key = ENV["ROUTECARE_MAILGUN_WEBHOOK_SIGNING_KEY"].to_s
      # Skip validation when key is not configured (development/test)
      return if signing_key.blank?

      timestamp = params["timestamp"].to_s
      token     = params["token"].to_s
      signature = params["signature"].to_s

      if timestamp.blank? || token.blank? || signature.blank?
        Rails.logger.warn("[Webhooks::Mailgun] Missing signature fields from #{request.remote_ip}")
        head :forbidden
        return
      end

      expected = OpenSSL::HMAC.hexdigest("sha256", signing_key, "#{timestamp}#{token}")

      unless ActiveSupport::SecurityUtils.secure_compare(expected, signature)
        Rails.logger.warn("[Webhooks::Mailgun] Invalid signature from #{request.remote_ip}")
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
      Rails.logger.error("[Webhooks::Mailgun] Could not create processing alert: #{e.message}")
    end

    def extract_email(value)
      match = value.to_s.match(/<([^>]+)>/)
      match ? match[1] : value.to_s.strip
    end
  end
end
