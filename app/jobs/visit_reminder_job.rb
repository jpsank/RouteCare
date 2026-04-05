class VisitReminderJob < ApplicationJob
  queue_as :default

  def perform
    reminder_window = 23.hours.from_now..25.hours.from_now

    Visit.joins(weekly_schedule: { user: :clinician_profile })
         .where(status: :confirmed)
         .where(starts_at: reminder_window)
         .includes(patient: [], weekly_schedule: { user: :clinician_profile })
         .find_each do |visit|
      send_reminder(visit)
    rescue StandardError => e
      Rails.logger.error("[VisitReminderJob] Failed for visit #{visit.id}: #{e.message}")
    end
  end

  private

  def send_reminder(visit)
    user = visit.weekly_schedule.user
    return unless user.clinician_profile&.auto_send_enabled?

    # Skip if a reminder was already sent for this visit
    return if user.patient_messages.exists?(visit: visit, direction: :outbound, metadata: { "kind" => "reminder" })

    channel = visit.patient.preferred_message_channel
    body = Messaging::PatientMessageBuilder.new(visit: visit, channel: channel, kind: :reminder).call

    message = user.patient_messages.create!(
      visit: visit,
      patient: visit.patient,
      direction: :outbound,
      channel: channel,
      status: :draft,
      body: body,
      proposed_starts_at: visit.starts_at,
      proposed_ends_at: visit.ends_at,
      requires_approval: false,
      metadata: { "kind" => "reminder" }
    )

    Messaging::OutboundDispatcher.new(message).call
  end
end
