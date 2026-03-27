module Messaging
  class PatientMessageBuilder
    def initialize(visit:, channel:, kind: :proposal)
      @visit = visit
      @channel = channel
      @kind = kind
    end

    def call
      case kind.to_sym
      when :proposal then confirmation_text
      when :reminder then reminder_text
      else confirmation_text
      end
    end

    def confirmation_text
      patient_first_name = visit.patient.full_name.split.first
      clinician_name = visit.weekly_schedule.user.clinician_profile&.discipline || "your clinician"
      friendly_start = visit.starts_at.strftime("%A %b %-d at %-I:%M %p")

      "Hi #{patient_first_name}, this is #{clinician_name}. "\
        "Can we confirm your home visit for #{friendly_start}? "\
        "Reply YES to confirm, NO to decline, or suggest another time."
    end

    def reminder_text
      patient_first_name = visit.patient.full_name.split.first
      friendly_start = visit.starts_at.strftime("%A at %-I:%M %p")

      "Reminder: your RouteCare visit is scheduled for #{friendly_start}, #{patient_first_name}. "\
        "Reply if you need to reschedule."
    end

    private

    attr_reader :visit, :channel, :kind
  end
end
