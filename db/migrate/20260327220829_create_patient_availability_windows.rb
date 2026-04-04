class CreatePatientAvailabilityWindows < ActiveRecord::Migration[8.1]
  def change
    create_table :patient_availability_windows do |t|
      t.references :patient, null: false, foreign_key: true
      t.integer :day_of_week, null: false
      t.integer :start_minute, null: false
      t.integer :end_minute, null: false

      t.timestamps
    end

    add_index :patient_availability_windows,
              [ :patient_id, :day_of_week, :start_minute, :end_minute ],
              unique: true,
              name: "idx_patient_windows_uniqueness"
    add_check_constraint :patient_availability_windows, "day_of_week BETWEEN 0 AND 6", name: "patient_windows_valid_day"
    add_check_constraint :patient_availability_windows, "start_minute >= 0", name: "patient_windows_start_non_negative"
    add_check_constraint :patient_availability_windows, "end_minute <= 1440", name: "patient_windows_end_within_day"
    add_check_constraint :patient_availability_windows, "end_minute > start_minute", name: "patient_windows_end_after_start"
  end
end
