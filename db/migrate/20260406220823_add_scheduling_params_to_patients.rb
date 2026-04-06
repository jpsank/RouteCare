class AddSchedulingParamsToPatients < ActiveRecord::Migration[8.1]
  def change
    add_column :patients, :min_days_between_visits, :integer, default: 1, null: false
    add_column :patients, :max_days_between_visits, :integer, default: 7, null: false
    add_column :patients, :priority, :integer, default: 0, null: false

    add_check_constraint :patients, "min_days_between_visits >= 1 AND min_days_between_visits <= 6", name: "chk_patients_min_days_between_visits"
    add_check_constraint :patients, "max_days_between_visits >= 1 AND max_days_between_visits <= 7", name: "chk_patients_max_days_between_visits"
    add_check_constraint :patients, "min_days_between_visits <= max_days_between_visits", name: "chk_patients_min_max_days_consistency"
    add_check_constraint :patients, "priority >= 0", name: "chk_patients_priority"
  end
end
