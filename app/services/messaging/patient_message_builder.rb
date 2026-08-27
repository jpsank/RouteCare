module Messaging
  class PatientMessageBuilder
    def initialize(visit:, channel:, kind: :proposal, proposed_starts_at: nil)
      @visit = visit
      @channel = channel
      @kind = kind
      @proposed_starts_at = proposed_starts_at
    end

    def call
      llm_message = llm_message_for_kind
      return llm_message if llm_message.present?

      case kind.to_sym
      when :proposal then confirmation_text
      when :reminder then reminder_text
      when :reschedule_follow_up then reschedule_follow_up_text
      when :reschedule_proposal then reschedule_proposal_text
      else confirmation_text
      end
    end

    private

    attr_reader :visit, :channel, :kind, :proposed_starts_at

    def confirmation_text
      clinician_name = clinician_name_for_intro
      friendly_start = visit.starts_at.strftime("%A %b %-d at %-I:%M %p")

      "Hi #{patient_first_name}, this is #{clinician_name}. "\
        "I wanted to confirm your home visit for #{friendly_start}. "\
        "If another time is better, just let us know what works."
    end

    def reminder_text
      friendly_start = visit.starts_at.strftime("%A at %-I:%M %p")

      "Reminder: your RouteCare visit is scheduled for #{friendly_start}, #{patient_first_name}. "\
        "Reply if you need to reschedule."
    end

    def reschedule_follow_up_text
      "Thanks #{patient_first_name}. We can help find a better time for your visit. "\
        "Reply with a few windows that work for you this week, and we'll follow up with updated options."
    end

    def reschedule_proposal_text
      friendly_start = proposed_starts_at.strftime("%A %b %-d at %-I:%M %p")

      "Thanks #{patient_first_name}. We can offer #{friendly_start} for your visit. "\
        "Let us know if this works for you, or share a better window and we can adjust."
    end

    def llm_message_for_kind
      Messaging::LlmAssistant.draft_message(
        context: {
          kind: kind,
          channel: channel,
          patient_first_name: patient_first_name,
          clinician_display_name: visit.weekly_schedule.user.clinician_profile&.display_name,
          clinician_discipline: visit.weekly_schedule.user.clinician_profile&.discipline,
          scheduled_starts_at: visit.starts_at&.iso8601,
          proposed_starts_at: proposed_starts_at&.iso8601
        }
      )
    end

    def clinician_name_for_intro
      profile = visit.weekly_schedule.user.clinician_profile
      profile&.display_name.presence || profile&.discipline.presence || "your clinician"
    end

    # A single-word patient name (a mononym, a business name, or just
    # whatever a CSV import/API caller provided) lands entirely in last_name
    # with first_name blank (see Patient#full_name=) — fall back to the full
    # name rather than greeting the patient with an empty string.
    def patient_first_name
      visit.patient.first_name.presence || visit.patient.full_name
    end
  end
end
