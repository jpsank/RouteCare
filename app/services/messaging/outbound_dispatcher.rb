module Messaging
  class OutboundDispatcher
    def initialize(message)
      @message = message
    end

    def call
      return message if message.status_sent? || message.status_queued?
      return message unless message.direction_outbound?

      if message.requires_approval? && message.approved_at.blank?
        message.update!(status: :pending_approval)
        return message
      end

      transport = message.channel_sms? ? :telnyx : :postmark
      metadata = (message.metadata || {}).merge("transport" => transport.to_s, "queued_at" => Time.current.iso8601)

      message.update!(status: :queued, metadata:)
      MessageDeliveryJob.perform_later(message.id)
      message
    end

    private

    attr_reader :message
  end
end
