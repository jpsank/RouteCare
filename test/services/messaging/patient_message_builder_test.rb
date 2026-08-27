require "test_helper"

class Messaging::PatientMessageBuilderTest < ActiveSupport::TestCase
  test "uses llm drafted message when available" do
    user = User.create!(email: "builder-llm@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
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

    llm_singleton = Messaging::LlmAssistant.singleton_class
    original = llm_singleton.instance_method(:draft_message)
    llm_singleton.define_method(:draft_message) do |context:|
      _ = context
      "Hi Jane, does a late-morning visit work for you?"
    end

    begin
      message = Messaging::PatientMessageBuilder.new(visit: visit, channel: :sms).call
      assert_equal "Hi Jane, does a late-morning visit work for you?", message
    ensure
      llm_singleton.define_method(:draft_message, original)
    end
  end

  test "fallback template uses clinician display name when available" do
    user = User.create!(email: "builder-display-name@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York", display_name: "Alex")
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

    llm_singleton = Messaging::LlmAssistant.singleton_class
    original = llm_singleton.instance_method(:draft_message)
    llm_singleton.define_method(:draft_message) do |context:|
      _ = context
      nil
    end

    begin
      message = Messaging::PatientMessageBuilder.new(visit: visit, channel: :sms).call
      assert_match(/this is Alex\./, message)
    ensure
      llm_singleton.define_method(:draft_message, original)
    end
  end

  test "fallback template does not greet a single-word-named patient with a blank name" do
    # first_name has presence validation, so a blank first_name can't be created
    # through the app today — this reproduces how it actually happens in the
    # wild: the one-time full_name-split migration backfills pre-existing
    # single-word names straight into last_name via raw SQL, bypassing
    # model validations entirely. Simulate that with update_column here.
    user = User.create!(email: "builder-mononym@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "America/New_York")
    patient = profile.patients.create!(
      first_name: "Placeholder",
      last_name: "Doe",
      phone: "5554001001",
      email: "cher@example.com",
      address_line1: "100 Main St",
      city: "Boston",
      state: "MA",
      postal_code: "02110",
      required_visits_per_week: 1,
      visit_duration_minutes: 45
    )
    patient.update_columns(first_name: "", last_name: "Cher")
    assert_equal "", patient.reload.first_name, "sanity check: simulating the migration's single-word backfill"

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

    llm_singleton = Messaging::LlmAssistant.singleton_class
    original = llm_singleton.instance_method(:draft_message)
    llm_singleton.define_method(:draft_message) do |context:|
      _ = context
      nil
    end

    begin
      message = Messaging::PatientMessageBuilder.new(visit: visit, channel: :sms).call
      assert_match(/Hi Cher,/, message)
      assert_no_match(/Hi ,/, message)
    ensure
      llm_singleton.define_method(:draft_message, original)
    end
  end
end
