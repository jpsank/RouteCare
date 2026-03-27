module Messaging
  class InboundReplyProcessor
    def initialize(user:, visit:, channel:, body:)
      @user = user
      @visit = visit
      @channel = channel
      @body = body
    end

    def call
      parsed = Messaging::ReplyParser.parse(body)

      message = PatientMessage.create!(
        visit: visit,
        patient: visit.patient,
        user: user,
        direction: :inbound,
        channel: channel,
        status: :received,
        body: body,
        requires_approval: false,
        metadata: { parsed_intent: parsed.intent.to_s }
      )

      case parsed.intent
      when :confirm
        visit.update!(status: :confirmed)
      when :decline
        visit.update!(status: :declined)
      when :reschedule
        visit.update!(status: :pending_patient_confirmation)
        Alert.create!(
          user: user,
          category: "patient_reply_attention",
          severity: "high",
          status: "open",
          message: "#{visit.patient.full_name} asked to reschedule.",
          metadata: { visit_id: visit.id, message_id: message.id }
        )
      end

      message
    end

    private

    attr_reader :user, :visit, :channel, :body
  end
end
