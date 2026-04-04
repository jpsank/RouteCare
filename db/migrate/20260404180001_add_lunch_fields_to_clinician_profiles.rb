class AddLunchFieldsToClinicianProfiles < ActiveRecord::Migration[8.0]
  def change
    add_column :clinician_profiles, :lunch_start_minute, :integer, default: 720, null: false
    add_column :clinician_profiles, :lunch_duration_minutes, :integer, default: 30, null: false
    add_column :clinician_profiles, :lunch_window_minutes, :integer, default: 90, null: false
  end
end
