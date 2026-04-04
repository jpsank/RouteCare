require "test_helper"

class MessageDeliveryJobTest < ActiveJob::TestCase
  test "delivers queued sms messages" do
    message = build_message(channel: :sms)

    MessageDeliveryJob.perform_now(message.id)

    message.reload
    assert_equal "sent", message.status
    assert_match(/test-sms-/, message.metadata["provider_message_id"])
    assert message.metadata["sent_at"].present?
  end

  test "delivers queued email messages" do
    ActionMailer::Base.deliveries.clear
    message = build_message(channel: :email)

    MessageDeliveryJob.perform_now(message.id)

    message.reload
    assert_equal "sent", message.status
    assert_equal 1, ActionMailer::Base.deliveries.size
  end

  test "marks email delivery failed when patient email is missing" do
    message = build_message(channel: :email, patient_email: nil)

    MessageDeliveryJob.perform_now(message.id)

    message.reload
    assert_equal "failed", message.status
    assert_equal "Recipient email is missing", message.metadata["delivery_error"]
  end

  private

  def build_message(channel:, patient_email: "jane@example.com")
    user = User.create!(email: "job-#{SecureRandom.hex(4)}@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    patient = profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001001",
      email: patient_email,
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 1,
      visit_duration_minutes: 45
    )
    schedule = user.weekly_schedules.create!(week_start_on: Date.new(2026, 4, 6), status: :draft)
    visit = schedule.visits.create!(
      patient: patient,
      starts_at: Time.zone.parse("2026-04-07 09:00:00"),
      ends_at: Time.zone.parse("2026-04-07 09:45:00"),
      duration_minutes: 45,
      status: :pending_patient_confirmation,
      position_in_day: 0,
      drive_from_previous_minutes: 0,
      clinician_override: false,
      soft_constraint_override: false,
      source: "manual"
    )

    user.patient_messages.create!(
      visit: visit,
      patient: patient,
      direction: :outbound,
      channel: channel,
      status: :queued,
      body: "RouteCare test message",
      requires_approval: false,
      metadata: {}
    )
  end
end
