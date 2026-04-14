namespace :playwright do
  desc "Seed a deterministic test user for Playwright E2E runs (setup completed)"
  task seed_user: :environment do
    email = ENV.fetch("PLAYWRIGHT_USER_EMAIL", "playwright@example.com")
    password = ENV.fetch("PLAYWRIGHT_USER_PASSWORD", "playwright-password-123")

    user = User.find_or_initialize_by(email: email)
    user.password = password
    user.password_confirmation = password
    user.save!

    profile = user.clinician_profile || user.create_clinician_profile!(
      discipline: "Physical Therapist",
      timezone: "America/Chicago"
    )
    profile.update!(
      display_name: "Playwright Tester",
      setup_completed_at: Time.current,
      home_latitude: 36.0822,
      home_longitude: -94.1719
    )

    puts "[playwright] user ready: #{user.email}"
  end

  desc "Seed a deterministic test user that has NOT completed setup (for wizard flows)"
  task seed_fresh_user: :environment do
    email = ENV.fetch("PLAYWRIGHT_FRESH_EMAIL", "playwright-fresh@example.com")
    password = ENV.fetch("PLAYWRIGHT_USER_PASSWORD", "playwright-password-123")

    user = User.find_or_initialize_by(email: email)
    user.password = password
    user.password_confirmation = password
    user.save!

    profile = user.clinician_profile || user.create_clinician_profile!(
      discipline: "Physical Therapist",
      timezone: "America/Chicago"
    )
    # Wipe any dependent data so the wizard flow starts clean, then reset the profile.
    Visit.where(patient_id: profile.patients.pluck(:id)).delete_all
    PatientMessage.where(patient_id: profile.patients.pluck(:id)).delete_all
    PatientAvailabilityWindow.where(patient_id: profile.patients.pluck(:id)).delete_all
    profile.patients.delete_all
    profile.update!(
      display_name: nil,
      setup_completed_at: nil,
      home_latitude: nil,
      home_longitude: nil
    )

    puts "[playwright] fresh user ready: #{user.email}"
  end

  desc "Reset the main playwright user's patients/schedule so patient+optimize tests run against a clean slate"
  task reset_main_user: :environment do
    email = ENV.fetch("PLAYWRIGHT_USER_EMAIL", "playwright@example.com")
    user = User.find_by(email: email)
    return unless user

    profile = user.clinician_profile
    return unless profile

    patient_ids = profile.patients.pluck(:id)
    Visit.where(patient_id: patient_ids).delete_all
    PatientMessage.where(patient_id: patient_ids).delete_all
    PatientAvailabilityWindow.where(patient_id: patient_ids).delete_all
    profile.patients.delete_all
    user.weekly_schedules.destroy_all
    Alert.where(user: user).delete_all

    puts "[playwright] reset main user's data"
  end
end
