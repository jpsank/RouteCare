require "test_helper"

class PatientCsvImporterTest < ActiveSupport::TestCase
  setup do
    @user = User.create!(email: "import_test@example.com", password: "password123")
    @profile = @user.create_clinician_profile!(discipline: "Physical Therapist", timezone: "UTC")
  end

  test "fuzzy matches varied header names" do
    csv = <<~CSV
      Patient Name,Phone Number,Email Address,Street,City,State,Zip,Visits/week,Duration,Notes
      Alice Chen,555-123-4567,alice@example.com,100 Main St,Fayetteville,AR,72704,3,45,Post-op
    CSV

    result = PatientCsvImporter.call(profile: @profile, content: csv)

    assert_equal 1, result.imported
    assert_empty result.errors

    mapped = result.header_map
    assert_equal "full_name", mapped["Patient Name"]
    assert_equal "phone", mapped["Phone Number"]
    assert_equal "email", mapped["Email Address"]
    assert_equal "address_line1", mapped["Street"]
    assert_equal "postal_code", mapped["Zip"]
    assert_equal "required_visits_per_week", mapped["Visits/week"]
    assert_equal "visit_duration_minutes", mapped["Duration"]
    assert_equal "notes", mapped["Notes"]

    patient = @profile.patients.find_by(full_name: "Alice Chen")
    assert_equal "555-123-4567", patient.phone
    assert_equal "alice@example.com", patient.email
    assert_equal 3, patient.required_visits_per_week
    assert_equal 45, patient.visit_duration_minutes
  end

  test "raises when required columns missing" do
    csv = "Name,Email\nAlice,alice@example.com\n"
    error = assert_raises(ArgumentError) do
      PatientCsvImporter.call(profile: @profile, content: csv)
    end
    assert_match(/Missing required column/, error.message)
  end

  test "applies integer defaults when columns absent" do
    csv = <<~CSV
      name,phone,address,city,state,zip
      Jane Doe,5551112222,1 Park Ave,Rogers,AR,72756
    CSV

    result = PatientCsvImporter.call(profile: @profile, content: csv)
    assert_equal 1, result.imported
    patient = @profile.patients.find_by(full_name: "Jane Doe")
    assert_equal 2, patient.required_visits_per_week
    assert_equal 60, patient.visit_duration_minutes
    assert_equal 1, patient.min_days_between_visits
    assert_equal 7, patient.max_days_between_visits
  end
end
