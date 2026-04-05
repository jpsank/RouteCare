class AddSetupCompletedAtToClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :setup_completed_at, :datetime
  end
end
