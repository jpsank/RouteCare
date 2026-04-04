class AddWorkdayMinutesToClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :workday_start_minute, :integer, null: false, default: 8 * 60
    add_column :clinician_profiles, :workday_end_minute, :integer, null: false, default: 18 * 60

    add_check_constraint :clinician_profiles, "workday_start_minute >= 0 AND workday_start_minute <= 1439", name: "clinician_profiles_valid_workday_start"
    add_check_constraint :clinician_profiles, "workday_end_minute >= 1 AND workday_end_minute <= 1440", name: "clinician_profiles_valid_workday_end"
    add_check_constraint :clinician_profiles, "workday_end_minute > workday_start_minute", name: "clinician_profiles_workday_end_after_start"
  end
end
