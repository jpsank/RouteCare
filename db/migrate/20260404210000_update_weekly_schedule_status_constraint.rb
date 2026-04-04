class UpdateWeeklyScheduleStatusConstraint < ActiveRecord::Migration[8.1]
  def change
    remove_check_constraint :weekly_schedules, name: "weekly_schedules_status_check"
    add_check_constraint :weekly_schedules,
      "status IN ('draft', 'optimized', 'approved', 'archived')",
      name: "weekly_schedules_status_check"
  end
end
