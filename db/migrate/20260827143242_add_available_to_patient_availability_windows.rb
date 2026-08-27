class AddAvailableToPatientAvailabilityWindows < ActiveRecord::Migration[8.1]
  def change
    # true  => an "available" window (visit must fall within one of these, existing semantics)
    # false => an "unavailable"/blackout window (visit must never overlap this range)
    # Existing rows are all "available" windows, so default true preserves current behavior.
    add_column :patient_availability_windows, :available, :boolean, default: true, null: false

    remove_index :patient_availability_windows,
      %i[patient_id day_of_week start_minute end_minute],
      name: "idx_patient_windows_uniqueness"
    add_index :patient_availability_windows,
      %i[patient_id day_of_week start_minute end_minute available],
      unique: true,
      name: "idx_patient_windows_uniqueness"
  end
end
