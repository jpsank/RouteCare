class CreateVisits < ActiveRecord::Migration[8.1]
  def change
    create_table :visits do |t|
      t.references :weekly_schedule, null: false, foreign_key: true
      t.references :patient, null: false, foreign_key: true
      t.datetime :starts_at, null: false
      t.datetime :ends_at, null: false
      t.integer :duration_minutes, null: false
      t.string :status, null: false, default: "pending_patient_confirmation"
      t.integer :position_in_day, null: false
      t.integer :drive_from_previous_minutes, null: false, default: 0
      t.boolean :clinician_override, null: false, default: false
      t.boolean :soft_constraint_override, null: false, default: false
      t.string :source, null: false, default: "optimizer"
      t.string :external_calendar_event_id

      t.timestamps
    end

    add_index :visits, [ :weekly_schedule_id, :starts_at ]
    add_index :visits, [ :patient_id, :starts_at ]
    add_index :visits, [ :weekly_schedule_id, :starts_at, :patient_id ], unique: true
    add_check_constraint :visits, "ends_at > starts_at", name: "visits_end_after_start"
    add_check_constraint :visits, "duration_minutes > 0", name: "visits_positive_duration"
    add_check_constraint :visits, "position_in_day >= 0", name: "visits_non_negative_position"
    add_check_constraint :visits, "drive_from_previous_minutes >= 0", name: "visits_non_negative_drive"
  end
end
