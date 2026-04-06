class AddSchedulingParamsToClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :max_continuous_work_minutes, :integer, default: 480, null: false
    add_column :clinician_profiles, :required_break_minutes, :integer, default: 15, null: false
    add_column :clinician_profiles, :max_drive_minutes_per_day, :integer
    add_column :clinician_profiles, :schedule_density, :float, default: 0.5, null: false
    add_column :clinician_profiles, :charting_buffer_minutes, :integer, default: 0, null: false

    add_check_constraint :clinician_profiles, "max_continuous_work_minutes >= 60 AND max_continuous_work_minutes <= 720", name: "chk_cp_max_continuous_work"
    add_check_constraint :clinician_profiles, "required_break_minutes >= 5 AND required_break_minutes <= 60", name: "chk_cp_required_break"
    add_check_constraint :clinician_profiles, "max_drive_minutes_per_day IS NULL OR (max_drive_minutes_per_day >= 0 AND max_drive_minutes_per_day <= 720)", name: "chk_cp_max_drive"
    add_check_constraint :clinician_profiles, "schedule_density >= 0.0 AND schedule_density <= 1.0", name: "chk_cp_schedule_density"
    add_check_constraint :clinician_profiles, "charting_buffer_minutes >= 0 AND charting_buffer_minutes <= 60", name: "chk_cp_charting_buffer"
  end
end
