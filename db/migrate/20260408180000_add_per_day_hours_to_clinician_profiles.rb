class AddPerDayHoursToClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :per_day_hours, :jsonb, default: {}, null: false
  end
end
