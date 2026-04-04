class AddWorkingDaysMaskToClinicianProfiles < ActiveRecord::Migration[8.1]
  DEFAULT_MONDAY_TO_FRIDAY_MASK = 62 # Mon-Fri bits on Date#wday scale (Sun=0)

  def change
    add_column :clinician_profiles, :working_days_mask, :integer, null: false, default: DEFAULT_MONDAY_TO_FRIDAY_MASK
    add_check_constraint :clinician_profiles, "working_days_mask >= 1 AND working_days_mask <= 127", name: "clinician_profiles_valid_working_days_mask"
  end
end
