class MessageDeliveryJob < ApplicationJob
  queue_as :default

  def perform(message_id)
    message = PatientMessage.find(message_id)
    return unless message.status_queued?

    # In production this would call Twilio / email provider adapters.
    message.update!(status: :sent, metadata: message.metadata.merge("sent_at" => Time.current.iso8601))
  end
end
