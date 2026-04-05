class MessageDeliveryJob < ApplicationJob
  queue_as :default

  retry_on StandardError, wait: :polynomially_longer, attempts: 3 do |_job, error|
    Rails.logger.error("[MessageDeliveryJob] Exhausted retries: #{error.message}")
  end

  def perform(message_id)
    message = PatientMessage.find(message_id)
    return unless message.status_queued?

    result = if message.channel_sms?
      deliver_sms(message)
    else
      deliver_email(message)
    end

    metadata = (message.metadata || {}).merge(
      "sent_at" => Time.current.iso8601,
      "provider_message_id" => result[:provider_message_id],
      "delivery_error" => result[:error]
    ).compact

    message.update!(status: result[:ok] ? :sent : :failed, metadata:)
  end

  private

  def deliver_sms(message)
    Integrations::TelnyxSmsClient.new.deliver(to: message.patient.phone, body: message.body)
  end

  def deliver_email(message)
    return { ok: false, error: "Recipient email is missing" } if message.patient.email.blank?

    delivery = PatientMessageMailer.with(message: message).outbound_message.deliver_now
    { ok: true, provider_message_id: delivery.message_id }
  rescue StandardError => e
    Rails.logger.error("[MessageDeliveryJob] Email delivery failed: #{e.class}: #{e.message}")
    { ok: false, error: e.message }
  end
end
