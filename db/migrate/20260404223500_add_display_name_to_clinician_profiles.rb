class AddDisplayNameToClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :display_name, :string
  end
end
