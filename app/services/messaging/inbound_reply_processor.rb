module Messaging
  class InboundReplyProcessor
    Result = Struct.new(:message, :follow_up_message, keyword_init: true)

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
        metadata: {
          parsed_intent: parsed.intent.to_s,
          proposed_windows: parsed.proposed_windows
        }
      )

      follow_up_message = nil

      case parsed.intent
      when :confirm
        apply_confirmed_proposal!
      when :decline
        visit.update!(status: :declined)
      when :reschedule
        visit.update!(status: :pending_patient_confirmation)
        Messaging::AvailabilityWindowUpdater.new(
          patient: visit.patient,
          proposed_windows: parsed.proposed_windows
        ).call
        Alert.create!(
          user: user,
          category: "patient_reply_attention",
          severity: "high",
          status: "open",
          message: "#{visit.patient.full_name} asked to reschedule.",
          metadata: {
            visit_id: visit.id,
            message_id: message.id,
            proposed_windows: parsed.proposed_windows
          }
        )
        follow_up_message = create_reschedule_follow_up!(message, parsed)
      end

      Result.new(message:, follow_up_message:)
    end

    private

    attr_reader :user, :visit, :channel, :body

    def create_reschedule_follow_up!(inbound_message, parsed)
      message = user.patient_messages.create!(
        visit: visit,
        patient: visit.patient,
        direction: :outbound,
        channel: channel,
        status: :draft,
        body: Messaging::PatientMessageBuilder.new(visit: visit, channel: channel, kind: :reschedule_follow_up).call,
        proposed_starts_at: visit.starts_at,
        proposed_ends_at: visit.ends_at,
        requires_approval: !user.clinician_profile&.auto_send_enabled?,
        metadata: {
          automated: true,
          reason: "patient_requested_reschedule",
          inbound_message_id: inbound_message.id,
          proposed_windows: parsed.proposed_windows,
          suggested_visit_times: Messaging::SuggestedVisitTimesBuilder.new(visit: visit, proposed_windows: parsed.proposed_windows).call
        }
      )

      Messaging::OutboundDispatcher.new(message).call if user.clinician_profile&.auto_send_enabled?
      message.reload
    end

    def apply_confirmed_proposal!
      proposal_message = latest_outbound_proposal_message
      if proposal_message&.proposed_starts_at.present? && proposal_message.proposed_starts_at != visit.starts_at
        Scheduling::Rescheduler.new(visit: visit, starts_at: proposal_message.proposed_starts_at, actor: user).call
      end

      visit.update!(status: :confirmed)
    end

    def latest_outbound_proposal_message
      user.patient_messages
        .where(visit: visit, direction: :outbound)
        .where.not(proposed_starts_at: nil)
        .where.not(status: :failed)
        .order(created_at: :desc)
        .first
    end
  end
end
