class UpdateMinDaysBetweenVisitsCheckConstraint < ActiveRecord::Migration[8.1]
  def up
    remove_check_constraint :patients, name: "chk_patients_min_days_between_visits"
    add_check_constraint :patients,
      "min_days_between_visits >= 0 AND min_days_between_visits <= 6",
      name: "chk_patients_min_days_between_visits"
  end

  def down
    remove_check_constraint :patients, name: "chk_patients_min_days_between_visits"
    add_check_constraint :patients,
      "min_days_between_visits >= 1 AND min_days_between_visits <= 6",
      name: "chk_patients_min_days_between_visits"
  end
end
