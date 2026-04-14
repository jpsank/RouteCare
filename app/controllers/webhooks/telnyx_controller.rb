module Webhooks
  class TelnyxController < ActionController::Base
    skip_forgery_protection
    before_action :verify_telnyx_signature!

    def sms
      payload = params["data"]
      return head(:ok) unless payload&.dig("event_type") == "message.received"

      msg = payload.dig("payload") || {}
      from = msg["from"]&.dig("phone_number").to_s.gsub(/\D/, "")
      body = msg.dig("text").to_s.strip

      @patient = Patient.find_by("REPLACE(phone, '-', '') = ? OR phone = ?", from, from)
      unless @patient
        Rails.logger.warn("[Webhooks::Telnyx] Unrecognized number: #{from.first(6)}***")
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
      Rails.logger.error("[Webhooks::Telnyx] #{e.class}: #{e.message}")
      Sentry.capture_exception(e, tags: { webhook: "telnyx_sms" }) if defined?(Sentry) && Sentry.initialized?
      create_processing_alert!(
        "Failed to process SMS reply: #{e.message}",
        { from: from.to_s, body: body.to_s.first(200) }
      ) if @user
      head :ok
    end

    private

    def verify_telnyx_signature!
      public_key_base64 = ENV["ROUTECARE_TELNYX_PUBLIC_KEY"].to_s
      return if public_key_base64.blank?

      timestamp = request.headers["telnyx-timestamp"].to_s
      signature = request.headers["telnyx-signature-ed25519"].to_s
      payload = request.raw_post

      if timestamp.blank? || signature.blank?
        Rails.logger.warn("[Webhooks::Telnyx] Missing signature fields from #{request.remote_ip}")
        head :forbidden
        return
      end

      signed_payload = "#{timestamp}|#{payload}"
      public_key_bytes = Base64.decode64(public_key_base64)

      verify_key = Ed25519::VerifyKey.new(public_key_bytes)
      verify_key.verify(Base64.decode64(signature), signed_payload)
    rescue Ed25519::VerifyError
      Rails.logger.warn("[Webhooks::Telnyx] Invalid signature from #{request.remote_ip}")
      head :forbidden
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
      Rails.logger.error("[Webhooks::Telnyx] Could not create processing alert: #{e.message}")
    end
  end
end
