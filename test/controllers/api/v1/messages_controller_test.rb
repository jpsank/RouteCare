require "test_helper"

class Api::V1::MessagesControllerTest < ActionDispatch::IntegrationTest
  include ActiveJob::TestHelper
  include Devise::Test::IntegrationHelpers

  test "send immediately moves outbound message to pending approval when clinician review is required" do
    user, visit = build_message_context(email: "messages@example.com")

    sign_in user

    assert_no_enqueued_jobs do
      post api_v1_messages_path, params: {
        message: {
          visit_id: visit.id,
          channel: "sms",
          body: "Please confirm your visit.",
          send_immediately: true
        }
      }, as: :json
    end

    assert_response :created
    assert_equal "pending_approval", response.parsed_body.dig("message", "status")
  end

  test "approve queues pending outbound message for delivery" do
    user, visit = build_message_context(email: "approve-message@example.com")
    message = user.patient_messages.create!(
      visit: visit,
      patient: visit.patient,
      direction: :outbound,
      channel: :sms,
      status: :pending_approval,
      body: "Can we confirm your visit?",
      proposed_starts_at: visit.starts_at,
      proposed_ends_at: visit.ends_at,
      requires_approval: true
    )

    sign_in user

    assert_enqueued_with(job: MessageDeliveryJob) do
      post approve_api_v1_message_path(message), as: :json
    end

    assert_response :success
    assert_equal "queued", message.reload.status
    assert_not_nil message.approved_at
  end

  test "inbound reschedule creates automated follow-up draft" do
    user, visit = build_message_context(email: "inbound-reschedule@example.com")
    body = "Can we reschedule to Tuesday afternoon or Thursday after 3pm?"

    sign_in user

    post create_inbound_api_v1_messages_path, params: {
      message: {
        patient_id: visit.patient_id,
        visit_id: visit.id,
        channel: "sms",
        body: body
      }
    }, as: :json

    assert_response :created
    follow_up = response.parsed_body.fetch("follow_up_message")
    inbound_message = user.patient_messages.find(response.parsed_body.dig("message", "id"))
    follow_up_message = user.patient_messages.find(follow_up.fetch("id"))
    alert = user.alerts.order(created_at: :desc).first
    expected_windows = [
      { "day" => "tuesday", "time_of_day" => "afternoon", "time" => "3pm", "qualifier" => "after" },
      { "day" => "thursday", "time_of_day" => "afternoon", "time" => "3pm", "qualifier" => "after" }
    ]

    assert_equal "received", response.parsed_body.dig("message", "status")
    assert_equal "draft", follow_up.fetch("status")
    assert_equal "outbound", follow_up.fetch("direction")
    assert_equal "pending_patient_confirmation", visit.reload.status
    assert_equal "patient_reply_attention", alert.category
    assert_equal visit.id, alert.metadata.fetch("visit_id")
    assert_equal expected_windows, inbound_message.metadata.fetch("proposed_windows")
    assert_equal expected_windows, alert.metadata.fetch("proposed_windows")
    assert_equal expected_windows, follow_up_message.metadata.fetch("proposed_windows")
  end

  test "inbound reschedule auto-queues follow-up when clinician auto send is enabled" do
    user, visit = build_message_context(email: "auto-follow-up@example.com", auto_send_enabled: true)

    sign_in user

    assert_enqueued_with(job: MessageDeliveryJob) do
      post create_inbound_api_v1_messages_path, params: {
        message: {
          patient_id: visit.patient_id,
          visit_id: visit.id,
          channel: "sms",
          body: "I need a different time this week."
        }
      }, as: :json
    end

    assert_response :created
    assert_equal "queued", response.parsed_body.dig("follow_up_message", "status")
  end

  test "select suggestion updates outbound follow up with chosen proposed time" do
    user, visit = build_message_context(email: "select-suggestion@example.com")
    proposed_time = Time.find_zone!("America/New_York").parse("2026-04-08 13:00:00")
    proposed_end = Time.find_zone!("America/New_York").parse("2026-04-08 13:45:00")
    message = user.patient_messages.create!(
      visit: visit,
      patient: visit.patient,
      direction: :outbound,
      channel: :sms,
      status: :draft,
      body: "We can help find another time.",
      requires_approval: true,
      metadata: {
        suggested_visit_times: [
          {
            "starts_at" => proposed_time.iso8601,
            "ends_at" => proposed_end.iso8601,
            "label" => "Wednesday Apr 8 at 1:00 PM"
          }
        ]
      }
    )

    sign_in user

    post select_suggestion_api_v1_message_path(message), params: { suggestion_index: 0 }, as: :json

    assert_response :success
    assert_equal proposed_time, message.reload.proposed_starts_at
    assert_match(/We can offer/, message.body)
  end

  test "confirming selected proposal reschedules visit before confirming" do
    user, visit = build_message_context(email: "confirm-selected-proposal@example.com")
    proposed_starts_at = Time.zone.parse("2026-04-08 13:00:00")
    user.patient_messages.create!(
      visit: visit,
      patient: visit.patient,
      direction: :outbound,
      channel: :sms,
      status: :sent,
      body: "We can offer Wednesday Apr 8 at 1:00 PM.",
      proposed_starts_at: proposed_starts_at,
      proposed_ends_at: proposed_starts_at + 45.minutes,
      requires_approval: false
    )

    sign_in user

    post create_inbound_api_v1_messages_path, params: {
      message: {
        patient_id: visit.patient_id,
        visit_id: visit.id,
        channel: "sms",
        body: "Yes that works"
      }
    }, as: :json

    assert_response :created
    assert_equal proposed_starts_at, visit.reload.starts_at
    assert_equal "confirmed", visit.status
  end

  private

  def build_message_context(email:, auto_send_enabled: false)
    user = User.create!(email:, password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York", auto_send_enabled: auto_send_enabled)
    patient = profile.patients.create!(
      full_name: "Jane Doe",
      phone: "5554001001",
      email: "jane@example.com",
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

    [ user, visit ]
  end
end
