class CreateWeeklySchedules < ActiveRecord::Migration[8.1]
  def change
    create_table :weekly_schedules do |t|
      t.references :user, null: false, foreign_key: true
      t.date :week_start_on, null: false
      t.string :status, null: false, default: "draft"
      t.integer :total_drive_minutes, null: false, default: 0
      t.integer :baseline_drive_minutes, null: false, default: 0
      t.jsonb :optimization_summary, null: false, default: {}

      t.timestamps
    end

    add_index :weekly_schedules, [ :user_id, :week_start_on ], unique: true
    add_check_constraint :weekly_schedules, "status IN ('draft', 'clinician_approved', 'partially_confirmed', 'finalized')", name: "weekly_schedules_status_check"
    add_check_constraint :weekly_schedules, "total_drive_minutes >= 0", name: "weekly_schedules_non_negative_total_drive"
    add_check_constraint :weekly_schedules, "baseline_drive_minutes >= 0", name: "weekly_schedules_non_negative_baseline_drive"
  end
end
